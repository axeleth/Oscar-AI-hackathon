"""'Why did you pay this?' - answers from the audit trail, the cited policy clauses and decision memory."""
import json
import re
from datetime import datetime
from html import escape

from . import audit, audit_view, clauses, config, memory

PROMPT = """You are Oscar, explaining one of your own decisions to a finance manager who is not technical.
Use ONLY the evidence given. Write plain, friendly sentences (max 90 words): what happened (paid / refused /
blocked / held for approval / overridden) and why, in everyday words. If a human overrode your recommendation,
say so kindly and give both reasons.

Keep ALL technical specifics OUT of the sentences: no Stripe ids, rule names, clause numbers, JSON keys, timestamps
or code. Instead, highlight the everyday word they belong to with [[word|tN]] and put the specifics in `terms`
under that id. Examples: "the [[refund|t1]]" -> t1 details: Stripe refund id, amount, date; "our [[refund
policy|t2]]" -> t2 details: clause numbers with their text; "[[Axel|t3]] approved it" -> t3: approver, comment,
time. Use 2-5 terms; every [[...|tN]] needs a matching term and vice versa.

Money direction: a refund, payout or supplier payment is money LEAVING the company; a charge or a settled invoice
is money COMING IN. 'tool_executed' with a stripe_id means it really happened in Stripe."""

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "terms": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "details": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
                    "required": ["label", "value"], "additionalProperties": False}},
            },
            "required": ["id", "title", "details"], "additionalProperties": False}},
    },
    "required": ["answer", "terms"], "additionalProperties": False,
}


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
        steps.append({"time": datetime.fromisoformat(e["ts"]).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"), "actor": e["actor"], "step": e["event"], "decision": e.get("decision"),
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


def why(key: str, question: str = "") -> dict:
    """Returns {"answer": "plain text with [[word|tN]] markers", "terms": [{id, title, details:[{label, value}]}]}."""
    group = find_group(key)
    if not group:
        return {"answer": "I can't find that action in the audit log.", "terms": []}
    from .agent import claude
    msg = claude().messages.create(
        model=config.MODEL, max_tokens=4000, system=PROMPT,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": f"Question: {question or 'Why did you do this?'}\n\nEvidence:\n"
                                              f"{_evidence(group)}"}])
    if msg.stop_reason == "refusal":
        return {"answer": "I'm not able to answer that one.", "terms": []}
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        return {"answer": text, "terms": []}


_MARK = re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]")


def render_html(result: dict) -> str:
    """Plain answer with highlighted words; hovering shows the details, right-click locks the popup open."""
    terms = {t["id"]: t for t in result.get("terms", [])}

    def term(m):
        word, tid = m.group(1), m.group(2).strip()
        t = terms.get(tid)
        if not t:
            return escape(word)
        rows = "".join(f"<tr><th>{escape(d['label'])}</th><td>{escape(d['value'])}</td></tr>" for d in t["details"])
        return (f'<span class="term" tabindex="0">{escape(word)}<span class="pop"><b>{escape(t["title"])}</b>'
                f'<table>{rows}</table><span class="hint">right-click to pin</span></span></span>')

    out, last = [], 0
    answer = result.get("answer", "")
    for m in _MARK.finditer(answer):
        out.append(escape(answer[last:m.start()]))
        out.append(term(m))
        last = m.end()
    out.append(escape(answer[last:]))
    return f'<div class="why-answer">{"".join(out)}</div>'
