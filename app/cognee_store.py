"""Cognee-backed memory: the spending policy as a knowledge graph, plus every decision Oscar makes.

All Cognee calls run on ONE background asyncio loop thread (Cognee is async and its embedded graph/vector
stores are file-locked), so the sync agent threads can call in safely. Every call has a timeout; callers fall
back to keyword memory if Cognee is slow or unavailable.
"""
import asyncio
import hashlib
import os
import threading

from . import config

POLICY_DATASET = "oscar_policy"
DECISIONS_DATASET = "oscar_decisions"
STORE_DIR = config.ROOT / "data" / "cognee"
POLICY_MARK = STORE_DIR / "policy.sha256"

# Cognee reads its configuration at import time: set it before importing.
os.environ.setdefault("LLM_PROVIDER", "anthropic")
os.environ.setdefault("LLM_MODEL", f"anthropic/{config.MODEL}")
os.environ.setdefault("LLM_API_KEY", config.ANTHROPIC_API_KEY)
os.environ.setdefault("EMBEDDING_PROVIDER", "fastembed")          # local embeddings (no OpenAI key needed)
os.environ.setdefault("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
os.environ.setdefault("EMBEDDING_DIMENSIONS", "384")
os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")   # one shared local store for all agents
os.environ.setdefault("DATA_ROOT_DIRECTORY", str(STORE_DIR / "data"))
os.environ.setdefault("SYSTEM_ROOT_DIRECTORY", str(STORE_DIR / "system"))
os.environ.setdefault("TELEMETRY_DISABLED", "1")
os.environ.setdefault("LOG_LEVEL", "ERROR")
os.environ.setdefault("LITELLM_LOG", "ERROR")

STATUS = {"state": "off", "detail": "not started", "decisions_stored": 0}
_loop: asyncio.AbstractEventLoop | None = None
_cognee = None


def _start_loop():
    global _loop
    _loop = asyncio.new_event_loop()
    threading.Thread(target=_loop.run_forever, daemon=True, name="cognee-loop").start()


def _run(coro, timeout: float):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout)


def _submit(coro):
    """Fire-and-forget on the Cognee loop (decision writes must never block the money path)."""
    return asyncio.run_coroutine_threadsafe(coro, _loop)


def ready() -> bool:
    return STATUS["state"] == "ready"


# --------------------------------------------------------------------------- policy graph model

def _policy_graph_model():
    from cognee.low_level import DataPoint

    class ActionType(DataPoint):
        name: str
        metadata: dict = {"index_fields": ["name"], "identity_fields": ["name"]}

    class Counterparty(DataPoint):
        name: str
        metadata: dict = {"index_fields": ["name"], "identity_fields": ["name"]}

    class Approver(DataPoint):
        role: str
        metadata: dict = {"index_fields": ["role"], "identity_fields": ["role"]}

    class Limit(DataPoint):
        description: str              # e.g. "£200.00 per money movement without approval" (indexed: must be text)
        amount: float
        currency: str = "GBP"
        period: str | None = None
        metadata: dict = {"index_fields": ["description"]}

    class Clause(DataPoint):
        clause_id: str
        text: str
        governs: list[ActionType] = []
        has_limit: list[Limit] = []
        requires_approval_from: list[Approver] = []
        counterparties: list[Counterparty] = []
        metadata: dict = {"index_fields": ["clause_id", "text"], "identity_fields": ["clause_id"]}

    class PolicyGraph(DataPoint):
        clauses: list[Clause]

    return PolicyGraph


EXTRACT_PROMPT = """Extract EVERY numbered clause of this spending policy (e.g. 2.1, 4.2) as a Clause: clause_id
exactly as written (like "4.2"), text verbatim, the action types it governs (refund, charge, payout, invoice,
dispute evidence, supplier payment, any money movement), monetary limits in GBP (each with a short text description, the amount and the period), required
approvers, and named counterparties. Do not invent clauses."""

CONSULT_PROMPT = """You answer questions about the company spending policy for Oscar, a finance agent. Using only
the retrieved policy, list the clauses that govern the situation, each as '§<id> <verbatim text>', then one sentence
on what Oscar may do on its own and what needs a human. Never invent a clause."""

PRECEDENT_PROMPT = """You recall Oscar's past finance decisions. Using only the retrieved decision records, list the
relevant precedents (date, outcome, who decided, amount, counterparty, reasons) most relevant first, in at most
6 short lines. If none are relevant, say 'No relevant precedents.'"""


# --------------------------------------------------------------------------- lifecycle

async def _ingest_policy(force: bool = False):
    text = (config.ROOT / "POLICY.md").read_text()
    sha = hashlib.sha256(text.encode()).hexdigest()
    if not force and POLICY_MARK.exists() and POLICY_MARK.read_text() == sha:
        return "policy graph up to date"
    try:
        await _cognee.forget(dataset=POLICY_DATASET)
    except Exception:
        pass  # first build: nothing to forget
    await _cognee.remember(text, dataset_name=POLICY_DATASET, graph_model=_policy_graph_model(),
                           custom_prompt=EXTRACT_PROMPT, self_improvement=False)
    POLICY_MARK.write_text(sha)
    return "policy graph built"


def start(background: bool = True):
    """Import Cognee and make sure the policy graph matches POLICY.md (rebuilt when the file changes)."""
    global _cognee
    if os.getenv("OSCAR_MEMORY", "cognee") != "cognee":
        STATUS.update(state="off", detail="OSCAR_MEMORY is not 'cognee'; using keyword memory")
        return
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import cognee
        _cognee = cognee
    except Exception as e:
        STATUS.update(state="off", detail=f"cognee not importable: {e}")
        return
    if _loop is None:
        _start_loop()
    STATUS.update(state="building", detail="building policy knowledge graph")

    def build():
        try:
            STATUS.update(state="ready", detail=_run(_ingest_policy(), timeout=600))
        except Exception as e:
            STATUS.update(state="error", detail=f"{type(e).__name__}: {e}"[:300])

    threading.Thread(target=build, daemon=True).start() if background else build()


# --------------------------------------------------------------------------- queries / writes

def _texts(results) -> str:
    out = []
    for r in results or []:
        t = getattr(r, "text", None) or (r.get("text") if isinstance(r, dict) else None) or str(r)
        out.append(str(t))
    return "\n".join(out).strip()


def consult(question: str, timeout: float = 60) -> str:
    from cognee import SearchType
    return _texts(_run(_cognee.recall(question, query_type=SearchType.GRAPH_COMPLETION,
                                      datasets=[POLICY_DATASET], system_prompt=CONSULT_PROMPT), timeout))


def recall(query: str, timeout: float = 60) -> str:
    from cognee import SearchType
    return _texts(_run(_cognee.recall(query, query_type=SearchType.GRAPH_COMPLETION,
                                      datasets=[DECISIONS_DATASET], system_prompt=PRECEDENT_PROMPT), timeout))


def remember_decision(text: str, on_done=None):
    async def write():
        try:
            await _cognee.remember(text, dataset_name=DECISIONS_DATASET, node_set=["decisions"],
                                   self_improvement=False)
            STATUS["decisions_stored"] += 1
            ok = True
        except Exception:
            ok = False
        if on_done:
            on_done(ok)
    _submit(write())


def reset_decisions(timeout: float = 120):
    """Forget all remembered decisions (keeps the policy graph). Used by scripts/reset_stripe.py --audit."""
    async def wipe():
        try:
            await _cognee.forget(dataset=DECISIONS_DATASET)
        except Exception:
            pass  # nothing stored yet
    _run(wipe(), timeout)
    STATUS["decisions_stored"] = 0


def render_graph(which: str, timeout: float = 120) -> str | None:
    """Render a Cognee dataset ('policy' or 'decisions') to a self-contained interactive HTML file; returns the path."""
    if not ready():
        return None
    dataset = POLICY_DATASET if which == "policy" else DECISIONS_DATASET
    path = STORE_DIR / f"graph_{which}.html"

    async def render():
        await _cognee.visualize_graph(destination_file_path=str(path), dataset=dataset, full=True,
                                      include_session_events=False)
    _run(render(), timeout)
    return str(path) if path.exists() else None
