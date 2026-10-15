"""Policy + decision memory used by the agent tools and the explanations.

Backed by Cognee (app/cognee_store.py) when it is ready; a plain keyword store answers instantly as a
fallback, so the agent always works:
  consult(question)        -> {"answer", "clauses", "source"}
  remember(text, kind)     -> store a decision/precedent (Cognee write happens in the background)
  recall(query)            -> {"answer", "source"}
"""
import re
import threading
from datetime import datetime, timezone

from . import clauses, cognee_store

_lock = threading.Lock()
_decisions: list[dict] = []   # keyword fallback store (in-memory, per server run)
FEED: list[dict] = []         # recent memory writes, shown live in the dashboard's policy box


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
    note = ""
    if cognee_store.ready():
        try:
            answer = cognee_store.consult(question)
            if answer:
                ids = list(dict.fromkeys(re.findall(r"§\s?(\d+\.\d+)", answer)))
                return {"answer": answer, "clauses": ids, "source": "cognee policy graph"}
        except Exception as e:
            note = f" (cognee unavailable: {type(e).__name__})"
    ids = _keyword_clauses(question)
    return {"answer": clauses.cite(ids) or "No clause matched; read POLICY.md sections 2-4.",
            "clauses": ids, "source": "keyword" + note}


def remember(text: str, kind: str = "decision", summary: str = "", **meta) -> None:
    entry = {"id": len(FEED) + 1, "ts": datetime.now(timezone.utc).isoformat(), "summary": summary or text[:120],
             "state": "local"}
    with _lock:
        _decisions.append({"ts": entry["ts"], "kind": kind, "text": text, **meta})
        FEED.append(entry)
    if cognee_store.ready():
        try:
            entry["state"] = "saving"
            cognee_store.remember_decision(text, on_done=lambda ok: entry.update(state="stored" if ok else "local"))
        except Exception:
            entry["state"] = "local"  # keyword copy above is kept either way


def feed(n: int = 4) -> list[dict]:
    with _lock:
        return list(reversed(FEED[-n:]))


def _keyword_recall(query: str, k: int = 5) -> list[str]:
    words = {w for w in re.findall(r"[a-z0-9@.£-]+", query.lower()) if len(w) > 2}
    with _lock:
        hits = sorted(((sum(w in d["text"].lower() for w in words), i, d) for i, d in enumerate(_decisions)),
                      reverse=True)
    return [d["text"] for score, _, d in hits[:k] if score]


def recall(query: str, k: int = 5) -> dict:
    recent = _keyword_recall(query, k)
    if cognee_store.ready():
        try:
            graph = cognee_store.recall(query)
            if graph and "no relevant precedents" not in graph.lower():
                extra = ("\nMost recent (may not be in the graph yet):\n" + "\n".join(recent[:3])) if recent else ""
                return {"answer": graph + extra, "source": "cognee decision memory"}
        except Exception:
            pass
    return {"answer": "\n".join(recent) or "No precedents found.", "source": "keyword"}


def status() -> dict:
    return dict(cognee_store.STATUS)
