"""Append-only, hash-chained audit log (SQLite).

Every row stores the hash of the previous row, so editing or deleting any past row breaks
`verify_chain()`. This is the "auditability" story: who/what/when/why + which policy version.
"""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id TEXT,
    actor TEXT NOT NULL,          -- agent | policy | human | system
    event TEXT NOT NULL,          -- run_started, tool_proposed, policy_decision, approval_*, tool_executed, ...
    tool TEXT,
    args TEXT,                    -- JSON
    decision TEXT,                -- ALLOW | DENY | NEEDS_APPROVAL | APPROVED | REJECTED
    rules TEXT,                   -- JSON list of rule ids that fired
    rationale TEXT,               -- agent's stated reason, or human comment
    amount INTEGER,               -- minor units (pence/cents)
    currency TEXT,
    payee TEXT,
    stripe_id TEXT,
    detail TEXT,                  -- JSON / free text (tool result, error, message)
    policy_version TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
"""

FIELDS = ["ts", "run_id", "actor", "event", "tool", "args", "decision", "rules", "rationale",
          "amount", "currency", "payee", "stripe_id", "detail", "policy_version"]
# Added later; hashed only when set, so rows written before it existed still verify.
OPTIONAL_FIELDS = ["action_id"]       # groups every event of one tool call (= its tool_use_id)
ALL_FIELDS = FIELDS + OPTIONAL_FIELDS


def _connect() -> sqlite3.Connection:
    path = Path(config.AUDIT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    for f in OPTIONAL_FIELDS:
        if f not in cols:
            conn.execute(f"ALTER TABLE events ADD COLUMN {f} TEXT")
    return conn


def _row_hash(prev_hash: str, row: dict) -> str:
    data = {k: row.get(k) for k in FIELDS}
    data.update({k: row[k] for k in OPTIONAL_FIELDS if row.get(k) is not None})
    payload = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256((prev_hash + payload).encode()).hexdigest()


def _dump(v):
    if v is None or isinstance(v, str):
        return v
    return json.dumps(v, default=str)


def log(event: str, actor: str, run_id: str | None = None, **kw) -> dict:
    """Append one event. kw may contain any of ALL_FIELDS; dict/list values are JSON-encoded."""
    row = {k: None for k in ALL_FIELDS}
    row.update({k: (_dump(v) if k in ("args", "rules", "detail") else v) for k, v in kw.items() if k in ALL_FIELDS})
    row.update(event=event, actor=actor, run_id=run_id, ts=datetime.now(timezone.utc).isoformat())
    with _lock:
        conn = _connect()
        try:
            last = conn.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else "GENESIS"
            row["prev_hash"] = prev
            row["hash"] = _row_hash(prev, row)
            cols = ALL_FIELDS + ["prev_hash", "hash"]
            conn.execute(f"INSERT INTO events ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         [row[c] for c in cols])
            conn.commit()
        finally:
            conn.close()
    return row


def query(run_id: str | None = None, limit: int = 200) -> list[dict]:
    conn = _connect()
    try:
        if run_id:
            rows = conn.execute("SELECT * FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id, limit))
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
    finally:
        conn.close()


def executed_total_since(since_iso: str, currency: str) -> int:
    """Sum of money moved by executed money tools since a timestamp (used for daily limits)."""
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM events WHERE event='tool_executed' AND amount IS NOT NULL "
            "AND ts >= ? AND lower(currency)=lower(?)", (since_iso, currency)).fetchone()
        return int(r["s"])
    finally:
        conn.close()


def recent_executions(tool: str, payee: str | None, amount: int | None, since_iso: str) -> list[dict]:
    """Executed calls of the same tool/payee/amount since a timestamp (duplicate detection)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE event='tool_executed' AND tool=? AND payee IS ? AND amount IS ? AND ts >= ?",
            (tool, payee, amount, since_iso))
        return [dict(r) for r in rows]
    finally:
        conn.close()


def verify_chain() -> tuple[bool, int, int | None]:
    """Returns (ok, rows_checked, first_bad_id)."""
    conn = _connect()
    try:
        prev = "GENESIS"
        n = 0
        for r in conn.execute("SELECT * FROM events ORDER BY id ASC"):
            r = dict(r)
            if r["prev_hash"] != prev or _row_hash(prev, r) != r["hash"]:
                return False, n, r["id"]
            prev = r["hash"]
            n += 1
        return True, n, None
    finally:
        conn.close()
