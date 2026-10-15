"""'Why did you pay this?' - answers from the audit trail, the cited policy clauses and decision memory."""
import json

from . import audit, audit_view, clauses, config, memory

PROMPT = """You are Oscar's explanation service. Answer the finance team's question about ONE action using ONLY the
evidence below. Say plainly what happened (paid / refused / blocked / held / overridden), WHY, citing the policy
clause numbers (e.g. §4.1) and the evidence (Stripe ids, amounts, who approved and their stated reason). If a human
overrode Oscar's recommendation, say so and give both reasons. Max 120 words, no preamble, no markdown headings.
Money direction: a refund, payout or supplier payment is money LEAVING the company; a charge or a settled invoice is
money COMING IN. 'tool_executed' with a stripe_id means it really happened in Stripe."""


def _evidence(group: dict) -> str:
    steps, cited = [], []
    for e in group["events"]:
        d = {}
        try:
            d = json.loads(e["detail"]) if (e.get("detail") or "").startswith("{") else {"note": e.get("detail")}
        except ValueError:
            d = {"note": e.get("detail")}
        cited += [c for c in (d.get("clauses") or []) if c not in cited]
        d.pop("result", None)
        steps.append({"time": e["ts"][:19], "actor": e["actor"], "step": e["event"], "decision": e.get("decision"),
                      "rules": e.get("rules_list") or None, "reason_or_comment": e.get("rationale"),
                      "stripe_id": e.get("stripe_id"), "detail": d})
    payee = next((e.get("payee") for e in group["events"] if e.get("payee")), None)
    precedents = memory.recall(f"{payee or ''} {group.get('tool', '')}")["answer"] if payee else "n/a"
    return json.dumps({
        "action": group["title"], "outcome": group["outcome"], "decided_by": group.get("who"),
        "oscar_recommendation": group.get("recommendation"), "oscar_rationale": group.get("rationale"),
        "policy_clauses": {f"§{c}": clauses.text(c) for c in cited},
        "steps": steps, "precedents_in_memory": precedents}, indent=1, default=str)


def find_group(key: str) -> dict | None:
    run_id = key.split("-", 1)[0]
    rows = audit.query(run_id=None if run_id == "event" else run_id, limit=5000)
    return next((g for g in audit_view.headline_groups(rows, show_reads=True, limit=10000) if g["key"] == key), None)


def why(key: str, question: str = "") -> str:
    group = find_group(key)
    if not group:
        return "I can't find that action in the audit log."
    from .agent import claude
    msg = claude().messages.create(
        model=config.MODEL, max_tokens=2000, system=PROMPT, output_config={"effort": "low"},
        messages=[{"role": "user", "content": f"Question: {question or 'Why did Oscar do this?'}\n\nEvidence:\n"
                                              f"{_evidence(group)}"}])
    if msg.stop_reason == "refusal":
        return "The explanation service declined to answer."
    return "".join(b.text for b in msg.content if b.type == "text").strip()
