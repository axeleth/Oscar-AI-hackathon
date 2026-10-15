"""Numbered clauses from POLICY.md, the human-readable policy that policy.yaml enforces."""
import re
from functools import lru_cache

from . import config

POLICY_DOC = config.ROOT / "POLICY.md"
_CLAUSE = re.compile(r"^- \*\*(\d+\.\d+)\*\* (.+)$")


@lru_cache(maxsize=4)
def _parse(mtime: float) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for line in POLICY_DOC.read_text().splitlines()
            if (m := _CLAUSE.match(line.strip()))}


def all_clauses() -> dict[str, str]:
    return _parse(POLICY_DOC.stat().st_mtime) if POLICY_DOC.exists() else {}


def text(clause_id: str) -> str:
    return all_clauses().get(clause_id, "")


def cite(clause_ids: list[str]) -> str:
    """'§2.2 Any money movement above ... ; §3.2 ...'"""
    return "; ".join(f"§{c} {text(c)}" for c in clause_ids if c)
