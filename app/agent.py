"""Agent runtime: Claude tool-use loop where every tool call goes through the policy gate.

Flow per tool call:  propose -> resolve real amount/payee -> policy.evaluate -> ALLOW: execute
                                                                              -> DENY: error result to agent
                                                                              -> NEEDS_APPROVAL: pause run
A paused run resumes (in a new thread) once every pending call in that turn has a human decision.
Runs live in memory; the audit log in SQLite is the durable record.
"""
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import anthropic
import stripe

from . import audit, config, policy
from .prompts import SYSTEM_PROMPT
from .tools import REGISTRY, Ctx, Money, missing_required, tool_schemas

_client: anthropic.Anthropic | None = None


def claude() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None)
    return _client


@dataclass
class Pending:
    tool_use_id: str
    tool: str
    args: dict
    money: Money | None
    decision: policy.Decision
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    state: str = "awaiting"            # awaiting | scheduled (approved, inside the undo window)
    execute_at: float | None = None    # epoch seconds when a scheduled approval executes
    approver: str = ""
    comment: str = ""
    override: bool = False             # human decided against the agent's recommendation
    timer: threading.Timer | None = None
    ui_error: str = ""                 # shown on the approval card after a refused decision
    needs_override: bool = False       # card shows the "I'm overriding" checkbox

    @property
    def recommendation(self) -> str | None:
        return self.args.get("recommendation")


@dataclass
class Run:
    id: str
    task: str
    status: str = "running"          # running | awaiting_approval | done | error
    messages: list = field(default_factory=list)
    transcript: list = field(default_factory=list)   # UI-friendly log of what happened
    pending: dict = field(default_factory=dict)      # tool_use_id -> Pending
    results: dict = field(default_factory=dict)      # tool_use_id -> tool_result block (current turn)
    order: list = field(default_factory=list)        # tool_use ids of current turn, in order
    turns: int = 0
    final: str = ""
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    lock: threading.RLock = field(default_factory=threading.RLock)

    def note(self, kind: str, text: str, **extra):
        self.transcript.append({"kind": kind, "text": text, "ts": datetime.now(timezone.utc).isoformat(), **extra})


RUNS: dict[str, Run] = {}


# --------------------------------------------------------------------------- public API

def start_run(task: str, background: bool = True) -> Run:
    run = Run(id=uuid.uuid4().hex[:8], task=task)
    run.messages.append({"role": "user", "content": task})
    RUNS[run.id] = run
    _, version = policy.load_policy()
    audit.log("run_started", "human", run.id, detail=task, policy_version=version)
    run.note("user", task)
    _spawn(run, background)
    return run


def pending_approvals() -> list[tuple[Run, Pending]]:
    return [(r, p) for r in RUNS.values() for p in r.pending.values()]


def check_decision(p: Pending, approve: bool, comment: str, typed_amount: str = "",
                   confirm_override: bool = False) -> str | None:
    """Human-error safeguards. Returns an error to show on the card, or None if the decision may proceed."""
    rec = p.recommendation
    p.needs_override = rec in ("approve", "reject") and (rec == "approve") != approve
    if p.needs_override and (not confirm_override or len(comment.strip()) < 10):
        return (f"Oscar recommended {rec.upper()}: {p.args.get('rationale', '')} - you're deciding the opposite. "
                f"Tick 'I'm overriding' and give your reason (10+ characters).")
    confirm_above = (policy.load_policy()[0].get("approval") or {}).get("confirm_above")
    m = p.money
    if approve and m and m.amount is not None and confirm_above is not None and m.amount > confirm_above:
        if not _amount_matches(typed_amount, m.amount):
            return f"Type the exact amount ({policy.fmt_money(m.amount, m.currency)}) to approve this."
    return None


def resolve_approval(run_id: str, tool_use_id: str, approve: bool, approver: str, comment: str = "",
                     typed_amount: str = "", confirm_override: bool = False, delay: float | None = None,
                     background: bool = True, resume: bool = True) -> str | None:
    """Apply a human decision. Returns an error message (and changes nothing) if a safeguard refuses it.
    Approvals wait `approval.undo_seconds` (or `delay`) before executing; undo_approval() cancels them."""
    run = RUNS[run_id]
    with run.lock:
        p = run.pending.get(tool_use_id)
        if p is None or p.state != "awaiting":
            return None
        err = check_decision(p, approve, comment, typed_amount, confirm_override)
        if err:
            p.ui_error = err
            return err
        p.ui_error, p.approver, p.comment = "", approver.strip() or "human", comment.strip()
        p.override = p.needs_override
        m = p.money or Money(None, None, None)
        common = dict(tool=p.tool, args=p.args, amount=m.amount, currency=m.currency, payee=m.payee,
                      policy_version=p.decision.policy_version, action_id=tool_use_id)
        audit.log("approval_granted" if approve else "approval_rejected", "human", run.id,
                  decision="APPROVED" if approve else "REJECTED", rationale=p.comment,
                  detail={"approver": p.approver, "agent_recommendation": p.recommendation, "override": p.override},
                  **common)
        if p.override:
            audit.log("recommendation_override", "human", run.id, decision="APPROVED" if approve else "REJECTED",
                      rationale=p.comment, detail={"approver": p.approver, "agent_recommendation": p.recommendation,
                                                   "agent_reason": p.args.get("rationale")}, **common)
        verb = "APPROVED" if approve else "REJECTED"
        flag = f" (overriding Oscar's '{p.recommendation}')" if p.override else ""
        run.note("human", f"{p.approver} {verb} {p.tool}{flag}" + (f": {p.comment}" if p.comment else ""))

        if not approve:
            run.pending.pop(tool_use_id)
            msg = f"REJECTED by human approver {p.approver}. Reason: {p.comment or '(none)'}. Do not retry this action."
            if p.override:
                msg += f" This overrides your recommendation ('{p.recommendation}'); record both reasons in your report."
            _result(run, tool_use_id, msg, error=True)
            _maybe_resume(run, background, resume)
            return None

        if delay is None:
            delay = float((policy.load_policy()[0].get("approval") or {}).get("undo_seconds") or 0)
        if delay <= 0:
            _finalize(run, tool_use_id, background, resume)
            return None
        p.state, p.execute_at = "scheduled", datetime.now(timezone.utc).timestamp() + delay
        p.timer = threading.Timer(delay, _finalize_later, args=(run.id, tool_use_id, background, resume))
        p.timer.daemon = True
        p.timer.start()
        run.note("system", f"{p.tool} executes in {delay:.0f}s unless undone.")
        return None


def undo_approval(run_id: str, tool_use_id: str, who: str = "human") -> bool:
    """Cancel an approval still inside its undo window; the action goes back to the approval queue."""
    run = RUNS.get(run_id)
    if not run:
        return False
    with run.lock:
        p = run.pending.get(tool_use_id)
        if p is None or p.state != "scheduled":
            return False
        if p.timer:
            p.timer.cancel()
        m = p.money or Money(None, None, None)
        audit.log("approval_undone", "human", run.id, tool=p.tool, args=p.args, decision="UNDONE",
                  amount=m.amount, currency=m.currency, payee=m.payee, detail={"by": who.strip() or "human"},
                  policy_version=p.decision.policy_version, action_id=tool_use_id)
        run.note("human", f"{who.strip() or 'human'} UNDID the approval of {p.tool}; it is back in the queue.")
        p.state, p.execute_at, p.timer, p.override, p.needs_override = "awaiting", None, None, False, False
        return True


def _finalize_later(run_id: str, tool_use_id: str, background: bool, resume: bool):
    run = RUNS.get(run_id)
    if run:
        with run.lock:
            p = run.pending.get(tool_use_id)
            if p is not None and p.state == "scheduled":
                _finalize(run, tool_use_id, background, resume)


def _finalize(run: "Run", tool_use_id: str, background: bool, resume: bool):
    p = run.pending.pop(tool_use_id)
    note = ""
    if p.override:
        note = (f"NOTE: human approver {p.approver} approved this against your recommendation "
                f"('{p.recommendation}'). Their reason: {p.comment}. Record both reasons in your report.\n")
    _execute(run, tool_use_id, p.tool, p.args, p.money, approved_by=p.approver, note=note)
    _maybe_resume(run, background, resume)


def _maybe_resume(run: "Run", background: bool, resume: bool):
    if not run.pending and resume and run.status != "manual":
        _flush_results(run)
        run.status = "running"
        _spawn(run, background)


def _amount_matches(typed: str, amount: int) -> bool:
    digits = re.sub(r"[^\d.]", "", typed or "")
    try:
        return round(float(digits) * 100) == amount
    except ValueError:
        return False


def propose(run: Run, name: str, args: dict, tool_use_id: str | None = None) -> str:
    """Push one tool call through the gate without the LLM (scripts/tests). Returns the tool_use_id."""
    tool_use_id = tool_use_id or f"manual_{uuid.uuid4().hex[:10]}"
    with run.lock:
        _handle_tool_use(run, tool_use_id, name, args)
    return tool_use_id


def manual_run(label: str) -> Run:
    """A run with no LLM attached, for driving the gate from scripts/tests."""
    run = Run(id=uuid.uuid4().hex[:8], task=label, status="manual")
    RUNS[run.id] = run
    audit.log("run_started", "system", run.id, detail=label, policy_version=policy.load_policy()[1])
    return run


# --------------------------------------------------------------------------- loop

def _spawn(run: Run, background: bool):
    if background:
        threading.Thread(target=_loop, args=(run,), daemon=True).start()
    else:
        _loop(run)


def _call_model(run: Run):
    p, _ = policy.load_policy()
    kwargs = dict(
        model=config.MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=tool_schemas(p.get("enabled_tools") or None),
        messages=run.messages,
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": config.EFFORT},
    )
    if config.USE_FALLBACKS:
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    return claude().beta.messages.create(**kwargs)


def _loop(run: Run):
    with run.lock:
        try:
            while run.turns < config.MAX_AGENT_TURNS:
                run.turns += 1
                response = _call_model(run)
                # Append the full content unchanged (thinking blocks must be preserved for the next turn).
                run.messages.append({"role": "assistant", "content": response.content})
                for b in response.content:
                    if b.type == "thinking" and getattr(b, "thinking", ""):
                        run.note("thinking", b.thinking)
                    elif b.type == "text" and b.text.strip() and response.stop_reason == "tool_use":
                        run.note("agent", b.text)  # the final answer is shown once, as "final"

                if response.stop_reason == "refusal":
                    return _finish(run, "error", "Model declined the request (refusal).")
                if response.stop_reason == "max_tokens":
                    return _finish(run, "error", "Model hit max_tokens.")
                if response.stop_reason != "tool_use":
                    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                    return _finish(run, "done", text)

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                run.order = [b.id for b in tool_uses]
                run.results = {}
                for tu in tool_uses:
                    _handle_tool_use(run, tu.id, tu.name, dict(tu.input or {}))
                if run.pending:
                    run.status = "awaiting_approval"
                    run.note("system", f"Paused: {len(run.pending)} action(s) awaiting human approval.")
                    return
                _flush_results(run)
            _finish(run, "error", f"Stopped after {config.MAX_AGENT_TURNS} turns.")
        except anthropic.APIStatusError as e:
            _finish(run, "error", f"Claude API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError as e:
            _finish(run, "error", f"Claude API connection error: {e}")
        except Exception as e:  # keep the server alive; surface everything in the UI + audit log
            _finish(run, "error", f"{type(e).__name__}: {e}")


def _finish(run: Run, status: str, text: str):
    run.status, run.final = status, text
    run.note("final" if status == "done" else "error", text or "(no final message)")
    audit.log("run_finished", "agent" if status == "done" else "system", run.id, decision=status.upper(),
              detail=text, policy_version=policy.load_policy()[1])


def _handle_tool_use(run: Run, tool_use_id: str, name: str, args: dict):
    t = REGISTRY.get(name)
    if t is None:
        return _result(run, tool_use_id, f"Unknown tool {name}", error=True)
    missing = missing_required(t, args)
    if missing:
        return _result(run, tool_use_id, f"Missing required arguments: {', '.join(missing)}", error=True)

    audit.log("tool_proposed", "agent", run.id, tool=name, args=args, rationale=args.get("rationale"),
              action_id=tool_use_id)

    money = None
    if t.money:
        try:
            money = t.resolve(args)
        except stripe.StripeError as e:
            audit.log("tool_failed", "system", run.id, tool=name, args=args, detail=f"resolve failed: {e}",
                      action_id=tool_use_id)
            return _result(run, tool_use_id, f"Could not look up the object: {e.user_message or e}", error=True)

    d = policy.evaluate(t, args, money)
    m = money or Money(None, None, None)
    audit.log("policy_decision", "policy", run.id, tool=name, args=args, decision=d.verdict, rules=d.rules,
              rationale=args.get("rationale"), amount=m.amount, currency=m.currency, payee=m.payee,
              detail=d.summary(), policy_version=d.policy_version, action_id=tool_use_id)
    amount_txt = f" {policy.fmt_money(m.amount, m.currency)} to {m.payee_label or m.payee}" if money else ""
    if not (t.category == "read" and d.verdict == policy.ALLOW):  # reads are audited but not shown
        run.note("policy", f"{d.verdict}: {name}{amount_txt} - {d.summary()}", verdict=d.verdict, tool=name)

    if d.verdict == policy.ALLOW:
        _execute(run, tool_use_id, name, args, money)
    elif d.verdict == policy.DENY:
        _result(run, tool_use_id, f"BLOCKED BY POLICY ({', '.join(d.rules)}): {d.summary()}", error=True)
    else:
        run.pending[tool_use_id] = Pending(tool_use_id, name, args, money, d)
        audit.log("approval_requested", "policy", run.id, tool=name, args=args, decision=d.verdict, rules=d.rules,
                  amount=m.amount, currency=m.currency, payee=m.payee, rationale=args.get("rationale"),
                  detail={"reasons": d.summary(), "agent_recommendation": args.get("recommendation")},
                  policy_version=d.policy_version, action_id=tool_use_id)


def _execute(run: Run, tool_use_id: str, name: str, args: dict, money: Money | None, approved_by: str | None = None,
             note: str = ""):
    t = REGISTRY[name]
    ctx = Ctx(run_id=run.id, idempotency_key=f"{run.id}:{tool_use_id}", approved_by=approved_by)
    m = money or Money(None, None, None)
    try:
        out = t.fn(args, ctx)
    except stripe.StripeError as e:
        audit.log("tool_failed", "system", run.id, tool=name, args=args, detail=str(e), action_id=tool_use_id)
        run.note("tool", f"{name} failed: {e.user_message or e}", tool=name, error=True)
        return _result(run, tool_use_id, f"Stripe error: {e.user_message or e}", error=True)
    except Exception as e:
        audit.log("tool_failed", "system", run.id, tool=name, args=args, detail=f"{type(e).__name__}: {e}",
                  action_id=tool_use_id)
        return _result(run, tool_use_id, f"Tool error: {type(e).__name__}: {e}", error=True)

    stripe_id = out.get("id") if isinstance(out, dict) else None
    if t.category != "read":
        audit.log("tool_executed", "agent", run.id, tool=name, args=args, rationale=args.get("rationale"),
                  amount=m.amount if t.money else None, currency=m.currency if t.money else None,
                  payee=m.payee if t.money else None, stripe_id=stripe_id,
                  detail={"result": out, "approved_by": approved_by or ("policy:auto" if t.money else None)},
                  action_id=tool_use_id)
        if name == "escalate_to_human":
            run.note("escalation", f"[{args.get('severity')}] {args.get('summary')}", tool=name)
        elif name == "record_finding":
            run.note("finding", f"{args.get('title')}: {args.get('detail')}", tool=name)
        else:
            run.note("tool", f"{name} executed" + (f" -> {stripe_id}" if stripe_id else ""), tool=name)
    else:
        run.note("read", f"{name}({_short(args)})", tool=name)
    _result(run, tool_use_id, note + json.dumps(out, default=str))


def _result(run: Run, tool_use_id: str, content: str, error: bool = False):
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if error:
        block["is_error"] = True
    run.results[tool_use_id] = block


def _flush_results(run: Run):
    """All tool results of one assistant turn go back in a single user message, in call order."""
    run.messages.append({"role": "user", "content": [run.results[i] for i in run.order]})
    run.results, run.order = {}, []


def _short(args: dict) -> str:
    s = ", ".join(f"{k}={v}" for k, v in args.items() if k != "rationale")
    return s if len(s) < 120 else s[:117] + "..."
