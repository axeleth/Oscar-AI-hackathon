"""Agent runtime: Claude tool-use loop where every tool call goes through the policy gate.

Flow per tool call:  propose -> resolve real amount/payee -> policy.evaluate -> ALLOW: execute
                                                                              -> DENY: error result to agent
                                                                              -> NEEDS_APPROVAL: pause run
A paused run resumes (in a new thread) once every pending call in that turn has a human decision.
Runs live in memory; the audit log in SQLite is the durable record.
"""
import json
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


def resolve_approval(run_id: str, tool_use_id: str, approve: bool, approver: str, comment: str = "",
                     background: bool = True, resume: bool = True) -> None:
    run = RUNS[run_id]
    with run.lock:
        p = run.pending.pop(tool_use_id, None)
        if p is None:
            return
        approver = approver.strip() or "human"
        m = p.money or Money(None, None, None)
        audit.log("approval_granted" if approve else "approval_rejected", "human", run.id, tool=p.tool,
                  args=p.args, decision="APPROVED" if approve else "REJECTED", rationale=comment,
                  amount=m.amount, currency=m.currency, payee=m.payee, detail={"approver": approver},
                  policy_version=p.decision.policy_version)
        if approve:
            run.note("human", f"{approver} APPROVED {p.tool}" + (f": {comment}" if comment else ""))
            _execute(run, tool_use_id, p.tool, p.args, p.money, approved_by=approver)
        else:
            run.note("human", f"{approver} REJECTED {p.tool}" + (f": {comment}" if comment else ""))
            _result(run, tool_use_id, f"REJECTED by human approver {approver}. "
                                      f"Comment: {comment or '(none)'}. Do not retry this action.", error=True)
        if not run.pending and resume and run.status != "manual":
            _flush_results(run)
            run.status = "running"
            _spawn(run, background)


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
                    elif b.type == "text" and b.text.strip():
                        run.note("agent", b.text)

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

    audit.log("tool_proposed", "agent", run.id, tool=name, args=args, rationale=args.get("rationale"))

    money = None
    if t.money:
        try:
            money = t.resolve(args)
        except stripe.StripeError as e:
            audit.log("tool_failed", "system", run.id, tool=name, args=args, detail=f"resolve failed: {e}")
            return _result(run, tool_use_id, f"Could not look up the object: {e.user_message or e}", error=True)

    d = policy.evaluate(t, args, money)
    m = money or Money(None, None, None)
    audit.log("policy_decision", "policy", run.id, tool=name, args=args, decision=d.verdict, rules=d.rules,
              rationale=args.get("rationale"), amount=m.amount, currency=m.currency, payee=m.payee,
              detail=d.summary(), policy_version=d.policy_version)
    amount_txt = f" {policy.fmt_money(m.amount, m.currency)} to {m.payee_label or m.payee}" if money else ""
    run.note("policy", f"{d.verdict}: {name}{amount_txt} - {d.summary()}", verdict=d.verdict, tool=name)

    if d.verdict == policy.ALLOW:
        _execute(run, tool_use_id, name, args, money)
    elif d.verdict == policy.DENY:
        _result(run, tool_use_id, f"BLOCKED BY POLICY ({', '.join(d.rules)}): {d.summary()}", error=True)
    else:
        run.pending[tool_use_id] = Pending(tool_use_id, name, args, money, d)
        audit.log("approval_requested", "policy", run.id, tool=name, args=args, decision=d.verdict, rules=d.rules,
                  amount=m.amount, currency=m.currency, payee=m.payee, detail=d.summary(),
                  policy_version=d.policy_version)


def _execute(run: Run, tool_use_id: str, name: str, args: dict, money: Money | None, approved_by: str | None = None):
    t = REGISTRY[name]
    ctx = Ctx(run_id=run.id, idempotency_key=f"{run.id}:{tool_use_id}", approved_by=approved_by)
    m = money or Money(None, None, None)
    try:
        out = t.fn(args, ctx)
    except stripe.StripeError as e:
        audit.log("tool_failed", "system", run.id, tool=name, args=args, detail=str(e))
        run.note("tool", f"{name} failed: {e.user_message or e}", tool=name, error=True)
        return _result(run, tool_use_id, f"Stripe error: {e.user_message or e}", error=True)
    except Exception as e:
        audit.log("tool_failed", "system", run.id, tool=name, args=args, detail=f"{type(e).__name__}: {e}")
        return _result(run, tool_use_id, f"Tool error: {type(e).__name__}: {e}", error=True)

    stripe_id = out.get("id") if isinstance(out, dict) else None
    if t.category != "read":
        audit.log("tool_executed", "agent", run.id, tool=name, args=args, rationale=args.get("rationale"),
                  amount=m.amount if t.money else None, currency=m.currency if t.money else None,
                  payee=m.payee if t.money else None, stripe_id=stripe_id,
                  detail={"result": out, "approved_by": approved_by or ("policy:auto" if t.money else None)})
        if name == "escalate_to_human":
            run.note("escalation", f"[{args.get('severity')}] {args.get('summary')}", tool=name)
        elif name == "record_finding":
            run.note("finding", f"{args.get('title')}: {args.get('detail')}", tool=name)
        else:
            run.note("tool", f"{name} executed" + (f" -> {stripe_id}" if stripe_id else ""), tool=name)
    else:
        run.note("read", f"{name}({_short(args)})", tool=name)
    _result(run, tool_use_id, json.dumps(out, default=str))


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
