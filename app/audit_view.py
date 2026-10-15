"""Turns raw audit events into one headline per action (tool call), each expandable into its steps."""
import json

from .policy import fmt_money

LABELS = {"create_refund": "Refund", "charge_customer": "Charge", "pay_invoice": "Settle invoice",
          "create_payout": "Payout", "void_invoice": "Void invoice", "submit_dispute_evidence": "Dispute evidence",
          "create_invoice": "Create invoice", "finalize_invoice": "Finalize invoice", "send_invoice": "Send invoice",
          "create_customer": "Create customer"}

# outcome -> css class used by the pills
OUTCOME_CLASS = {"EXECUTED": "ALLOW", "RECORDED": "ALLOW", "BLOCKED": "DENY", "REJECTED": "DENY",
                 "FAILED": "DENY", "AWAITING APPROVAL": "NEEDS_APPROVAL", "SCHEDULED": "NEEDS_APPROVAL",
                 "READ": "muted-pill", "STARTED": "running", "DONE": "done", "ERROR": "error"}


def _j(v):
    try:
        return json.loads(v) if v else {}
    except (TypeError, ValueError):
        return {}


def _outcome(events: list[dict], is_read: bool, is_agent_tool: bool) -> str:
    kinds = [e["event"] for e in events]
    if "tool_executed" in kinds:
        return "RECORDED" if is_agent_tool else "EXECUTED"
    if "tool_failed" in kinds:
        return "FAILED"
    if "approval_rejected" in kinds:
        return "REJECTED"
    if any(e["event"] == "policy_decision" and e["decision"] == "DENY" for e in events):
        return "BLOCKED"
    last_approval = [k for k in kinds if k in ("approval_requested", "approval_granted", "approval_undone")]
    if last_approval:
        return "SCHEDULED" if last_approval[-1] == "approval_granted" else "AWAITING APPROVAL"
    return "READ" if is_read else "PROPOSED"


ACTION_EVENTS = {"tool_proposed", "policy_decision", "approval_requested", "approval_granted", "approval_rejected",
                 "approval_undone", "recommendation_override", "tool_executed", "tool_failed"}
CLOSING = {"tool_executed", "tool_failed", "approval_rejected"}


def _infer_legacy_action_ids(rows: list[dict]) -> None:
    """Rows logged before action_id existed: attach each event to the latest open proposal of the same
    run + tool. Display-only; the stored rows are untouched."""
    open_actions: dict[tuple, str] = {}
    for r in rows:
        if r.get("action_id") or r["event"] not in ACTION_EVENTS:
            continue
        k = (r["run_id"], r.get("tool"))
        if r["event"] == "tool_proposed" or k not in open_actions:
            open_actions[k] = f"legacy{r['id']}"
        r["action_id"] = open_actions[k]
        if r["event"] in CLOSING or (r["event"] == "policy_decision" and r["decision"] == "DENY"):
            open_actions.pop(k, None)


def headline_groups(rows: list[dict], show_reads: bool = False, limit: int = 80) -> list[dict]:
    """rows: audit rows (any order). Returns newest-first groups with a headline and their steps."""
    rows = sorted(rows, key=lambda r: r["id"])
    _infer_legacy_action_ids(rows)
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r["run_id"], r["action_id"]) if r.get("action_id") else ("event", r["id"])
        groups.setdefault(key, {"key": f"{key[0]}-{key[1]}", "events": []})["events"].append(r)

    out = []
    for g in groups.values():
        ev = g["events"]
        first = ev[0]
        g.update(ts=first["ts"], run_id=first["run_id"], first_id=first["id"])
        if not first.get("action_id"):  # run lifecycle and other standalone events
            if first["event"] == "run_started":
                g.update(title=f"Run started: {(first['detail'] or '')[:140]}", outcome="STARTED", who=first["actor"])
            elif first["event"] == "run_finished":
                g.update(title=f"Run finished: {(first['detail'] or '')[:140]}",
                         outcome=first["decision"] or "DONE", who=first["actor"])
            else:
                g.update(title=f"{first['event']} {first.get('tool') or ''}", outcome=first.get("decision") or "",
                         who=first["actor"])
            g["outcome_class"] = OUTCOME_CLASS.get(g["outcome"], "")
            out.append(g)
            continue

        tool = first.get("tool") or ""
        args = _j(first.get("args"))
        money_ev = next((e for e in ev if e.get("amount") is not None), None)
        policy_ev = next((e for e in ev if e["event"] == "policy_decision"), None)
        is_read = policy_ev is not None and policy_ev["decision"] == "ALLOW" and not any(
            e["event"] in ("tool_executed", "tool_failed") for e in ev) and money_ev is None
        if is_read and not show_reads:
            continue
        is_agent_tool = tool in ("escalate_to_human", "record_finding", "close_message", "consult_policy",
                                 "recall_precedents")

        if tool == "escalate_to_human":
            title = f"Escalation [{args.get('severity', '?')}]: {args.get('summary', '')}"
        elif tool == "record_finding":
            title = f"Finding: {args.get('title', '')}"
        elif tool == "close_message":
            title = f"Inbox {args.get('message_id')} → {args.get('outcome')}: {args.get('summary', '')}"
        elif tool == "consult_policy":
            title = f"Consulted policy: {args.get('question', '')}"
        elif tool == "recall_precedents":
            title = f"Recalled precedents: {args.get('query', '')}"
        elif money_ev:
            title = (f"{LABELS.get(tool, tool)} {fmt_money(money_ev['amount'], money_ev['currency'])}"
                     f" → {money_ev.get('payee') or '?'}")
        elif is_read:
            title = f"Read: {tool}"
        else:
            title = f"{LABELS.get(tool, tool)} {args.get('invoice_id') or args.get('dispute_id') or ''}".strip()

        outcome = _outcome(ev, is_read, is_agent_tool)
        executed = next((e for e in ev if e["event"] == "tool_executed"), None)
        decided = next((e for e in reversed(ev) if e["event"] in ("approval_granted", "approval_rejected")), None)
        if decided:
            who = _j(decided.get("detail")).get("approver") or "human"
        elif outcome == "BLOCKED":
            who = "policy: " + ", ".join(_j(policy_ev.get("rules")) or [])
        elif outcome == "EXECUTED" and executed:
            who = _j(executed.get("detail")).get("approved_by") or "policy:auto"
        else:
            who = "agent" if is_agent_tool else "policy" if policy_ev else ""

        g.update(title=title, tool=tool, outcome=outcome, outcome_class=OUTCOME_CLASS.get(outcome, ""), who=who,
                 recommendation=args.get("recommendation"), rationale=args.get("rationale"),
                 override=any(e["event"] == "recommendation_override" for e in ev),
                 undone=any(e["event"] == "approval_undone" for e in ev),
                 stripe_id=executed.get("stripe_id") if executed else None,
                 rules=_j(policy_ev.get("rules")) if policy_ev else [])
        for e in ev:
            e["rules_list"] = _j(e.get("rules")) if e.get("rules") else []
            d = _j(e.get("detail")) if (e.get("detail") or "").startswith("{") else None
            e["detail_text"] = (", ".join(f"{k}: {v}" for k, v in d.items() if k != "result" and v not in (None, ""))
                                if isinstance(d, dict) else (e.get("detail") or ""))[:240]
        out.append(g)

    out.sort(key=lambda g: g["first_id"], reverse=True)
    return out[:limit]
