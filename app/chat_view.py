"""Shapes a run's transcript into a chat: user / Oscar bubbles, collapsed activity, and artifact cards."""
from html import escape

import markdown

from . import audit, audit_view

ACTIVITY = {"thinking", "policy", "read", "tool", "memory", "system", "inbox", "finding"}
LABEL = {"thinking": "thinking", "policy": "policy", "read": "looked up", "tool": "action", "memory": "memory",
         "system": "system", "inbox": "inbox", "finding": "finding"}
HIDDEN_TOOLS = {"consult_policy", "recall_precedents"}


def md(text: str) -> str:
    """Markdown -> HTML, with raw HTML in the text escaped first."""
    return markdown.markdown(escape(text or ""), extensions=["tables", "sane_lists"])


def chat_items(run) -> list[dict]:
    items: list[dict] = []
    for i, t in enumerate(run.transcript):
        kind = t["kind"]
        if kind in ACTIVITY:
            if not items or items[-1]["type"] != "activity":
                items.append({"type": "activity", "key": f"act-{run.id}-{i}", "steps": []})
            items[-1]["steps"].append({"label": LABEL.get(kind, kind), "kind": kind, "text": t["text"],
                                       "verdict": t.get("verdict")})
        elif kind == "user":
            items.append({"type": "user", "text": t["text"]})
        elif kind == "agent":
            items.append({"type": "oscar", "html": md(t["text"])})
        elif kind == "final":
            items.append({"type": "final", "html": md(t["text"])})
        elif kind == "escalation":
            items.append({"type": "alert", "text": t["text"]})
        elif kind == "human":
            items.append({"type": "human", "text": t["text"]})
        elif kind == "error":
            items.append({"type": "error", "text": t["text"]})
    finals = [it for it in items if it["type"] == "final"]
    if finals:
        finals[-1]["last_final"] = True  # artifact cards go under the latest report only
    return items


def artifacts(run_id: str) -> list[dict]:
    """What the run actually did, as cards: money movements, inbox outcomes, escalations, findings."""
    groups = audit_view.headline_groups(audit.query(run_id=run_id, limit=5000), limit=500)
    return list(reversed([g for g in groups if g.get("tool") and g["tool"] not in HIDDEN_TOOLS]))
