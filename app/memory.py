"""Policy + decision memory. Backed by Cognee when available, with a plain keyword fallback so the
agent always works. Interface used by the rest of the app:
  consult(question)        -> {"answer", "clauses", "source"}
  remember(text, kind)     -> store a decision/precedent
  recall(query)            -> {"answer", "source"}
"""
import re
import threading
from datetime import datetime, timezone

from . import clauses

_lock = threading.Lock()
_decisions: list[dict] = []   # fallback store (in-memory, per server run)


def _keyword_clauses(question: str, k: int = 5) -> list[str]:
    words = {w for w in re.findall(r"[a-z0-9£.]+", question.lower()) if len(w) > 2}
    scored = []
    for cid, txt in clauses.all_clauses().items():
        t = txt.lower()
        score = sum(1 for w in words if w in t)
        if score:
            scored.append((score, cid))
    return [cid for _, cid in sorted(scored, reverse=True)[:k]]


def consult(question: str) -> dict:
    ids = _keyword_clauses(question)
    return {"answer": clauses.cite(ids) or "No clause matched; read POLICY.md sections 2-4.",
            "clauses": ids, "source": "keyword"}


def remember(text: str, kind: str = "decision", **meta) -> None:
    with _lock:
        _decisions.append({"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, "text": text, **meta})


def recall(query: str, k: int = 5) -> dict:
    words = {w for w in re.findall(r"[a-z0-9@.£-]+", query.lower()) if len(w) > 2}
    with _lock:
        hits = sorted(((sum(w in d["text"].lower() for w in words), i, d) for i, d in enumerate(_decisions)),
                      reverse=True)
    found = [d["text"] for score, _, d in hits[:k] if score]
    return {"answer": "\n".join(found) or "No precedents found.", "source": "keyword"}
