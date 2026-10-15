"""Offline tests: policy engine, audit chain, and the agent gate with a fake money tool (no Stripe, no Claude)."""
import sqlite3

import pytest

from app import agent, audit, config, policy
from app.tools import REGISTRY, Money, tool

POLICY = """
currency: gbp
autonomy: threshold
limits: {max_per_transaction: 500000, daily_total: 1000000}
approval: {threshold: 20000, required_tools: [fake_sensitive]}
payees: {allowlist: [good@test], unknown_payee: needs_approval}
duplicates: {window_minutes: 60}
require_rationale: true
blocked_tools: [fake_blocked]
"""

EXECUTED = []


def _resolve(args):
    return Money(int(args["amount"]), args.get("currency", "gbp"), args["payee"], args["payee"])


@tool("fake_pay", "test money tool", {"amount": {"type": "integer"}, "payee": {"type": "string"},
                                      "currency": {"type": "string"}}, ["amount", "payee"],
      money=True, resolve=_resolve)
def fake_pay(args, ctx):
    EXECUTED.append(args)
    return {"id": f"fake_{len(EXECUTED)}", "ok": True}


@tool("fake_blocked", "blocked", {}, [], category="write")
def fake_blocked(args, ctx):
    return {"id": "x"}


@tool("fake_sensitive", "always needs approval", {}, [], category="write")
def fake_sensitive(args, ctx):
    return {"id": "s"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    p = tmp_path / "policy.yaml"
    p.write_text(POLICY)
    monkeypatch.setattr(config, "POLICY_PATH", p)
    monkeypatch.setattr(config, "AUDIT_DB", tmp_path / "audit.db")
    EXECUTED.clear()
    yield p


def pay(amount, payee="good@test", rationale="paying the agreed invoice INV-1 per contract", **kw):
    return {"amount": amount, "payee": payee, "rationale": rationale, **kw}


def ev(args, name="fake_pay"):
    t = REGISTRY[name]
    return policy.evaluate(t, args, t.resolve(args) if t.money else None)


def test_small_known_payee_allowed():
    assert ev(pay(5000)).verdict == policy.ALLOW


def test_over_threshold_needs_approval():
    d = ev(pay(25000))
    assert d.verdict == policy.NEEDS_APPROVAL and "over_approval_threshold" in d.rules


def test_hard_limit_denied():
    d = ev(pay(600000))
    assert d.verdict == policy.DENY and "over_hard_limit" in d.rules


def test_unknown_payee_needs_approval_or_deny(isolated):
    assert "unknown_payee" in ev(pay(100, payee="shady@test")).rules
    isolated.write_text(POLICY.replace("unknown_payee: needs_approval", "unknown_payee: deny"))
    assert ev(pay(100, payee="shady@test")).verdict == policy.DENY


def test_missing_rationale_and_currency():
    assert "missing_rationale" in ev(pay(100, rationale="ok")).rules
    assert "currency_mismatch" in ev(pay(100, currency="usd")).rules


def test_blocked_and_required_tools():
    assert ev({}, "fake_blocked").verdict == policy.DENY
    assert ev({}, "fake_sensitive").verdict == policy.NEEDS_APPROVAL


def test_suggest_mode(isolated):
    isolated.write_text(POLICY.replace("autonomy: threshold", "autonomy: suggest"))
    assert ev(pay(100)).verdict == policy.NEEDS_APPROVAL


def test_policy_version_changes_with_file(isolated):
    v1 = ev(pay(100)).policy_version
    isolated.write_text(POLICY + "\n# edited\n")
    assert ev(pay(100)).policy_version != v1


def test_gate_end_to_end_duplicate_and_daily_limit():
    run = agent.manual_run("t")
    t1 = agent.propose(run, "fake_pay", pay(15000))
    assert not run.results[t1].get("is_error") and len(EXECUTED) == 1
    # same payee + amount again -> held as possible duplicate
    t2 = agent.propose(run, "fake_pay", pay(15000))
    assert t2 in run.pending and "possible_duplicate" in run.pending[t2].decision.rules
    agent.resolve_approval(run.id, t2, False, "tester", "dupe", background=False, resume=False)
    assert run.results[t2]["is_error"] and len(EXECUTED) == 1
    # approve path executes and records approver
    t3 = agent.propose(run, "fake_pay", pay(30000))
    agent.resolve_approval(run.id, t3, True, "tester", "fine", background=False, resume=False)
    assert len(EXECUTED) == 2
    rows = audit.query(run.id)
    assert any(r["event"] == "approval_granted" and "tester" in r["detail"] for r in rows)
    # daily limit: 15000 + 30000 executed; a 960000 payment would exceed 1,000,000
    t4 = agent.propose(run, "fake_pay", pay(400000))
    assert t4 in run.pending  # over threshold, still under daily limit
    d = ev(pay(499999 + 500000))
    assert d.verdict == policy.DENY


def test_audit_chain_detects_tampering():
    for i in range(3):
        audit.log("test", "system", "r1", detail=f"e{i}")
    assert audit.verify_chain()[0]
    conn = sqlite3.connect(config.AUDIT_DB)
    conn.execute("UPDATE events SET detail='forged' WHERE id=2")
    conn.commit()
    conn.close()
    ok, _, bad = audit.verify_chain()
    assert not ok and bad == 2
