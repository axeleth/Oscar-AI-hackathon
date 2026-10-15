"""Build (or rebuild) the Cognee policy knowledge graph from POLICY.md before a demo, and try a question.
  .venv/bin/python scripts/cognee_ingest.py [--force]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import cognee_store  # noqa: E402

t = time.time()
if "--force" in sys.argv and cognee_store.POLICY_MARK.exists():
    cognee_store.POLICY_MARK.unlink()
cognee_store.start(background=False)
print(f"{cognee_store.STATUS} in {time.time() - t:.0f}s")
if cognee_store.ready():
    q = "Can Oscar refund £3,000 to Shady Holdings for a licence they no longer want?"
    t = time.time()
    print(f"\nQ: {q}\n{cognee_store.consult(q)}\n({time.time() - t:.0f}s)")
