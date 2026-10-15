"""End-to-end check against the real Stripe TEST account. Run seed_stripe.py first.

Drives the policy gate directly (no LLM) and checks: ALLOW executes, DENY blocks, NEEDS_APPROVAL holds and
executes after approval, missing rationale is denied, and the audit hash chain is intact.
  .venv/bin/python scripts/smoke_test.py            # gate only (no Claude call)
  .venv/bin/python scripts/smoke_test.py --agent    # also one read-only Claude run
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agent, audit, config  # noqa: E402
from app.tools import sc  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}")
    failures += 0 if cond else 1


def find_pi(email: str):
    cust = sc().v1.customers.list(params={"email": email, "limit": 1}).data
    if not cust:
        sys.exit(f"No customer {email}; run scripts/seed_stripe.py first")
    pis = [p for p in sc().v1.payment_intents.list(params={"customer": cust[0].id, "limit": 20}).data
           if p.status == "succeeded"]
    if not pis:
        sys.exit(f"No succeeded payments for {email}; run scripts/seed_stripe.py")
    return cust[0], pis[0]


def main():
    config.assert_test_mode()
    acme, acme_pi = find_pi("billing@acme.test")
    _, shady_pi = find_pi("payments@shady-holdings.test")
    run = agent.manual_run("smoke test")
    print(f"run {run.id}")

    # 1. small refund to an allowlisted customer -> ALLOW + executed
    small = random.randint(101, 999)  # random so re-runs don't trip the duplicate rule
    tid = agent.propose(run, "create_refund", {"payment_intent": acme_pi.id, "amount": small,
                                               "rationale": "Smoke test: small goodwill refund to allowlisted customer",
                                               "recommendation": "approve"})
    res = run.results.get(tid, {})
    check("ALLOW executes small refund", not res.get("is_error") and '"re_' in res.get("content", ""),
          res.get("content", "")[:80])

    # 2. huge charge -> DENY over_hard_limit (nothing hits Stripe)
    tid = agent.propose(run, "charge_customer", {"customer": acme.id, "amount": 9_000_000, "currency": "gbp",
                                                 "rationale": "Smoke test: should exceed the hard limit",
                                                 "recommendation": "reject"})
    res = run.results.get(tid, {})
    check("DENY over hard limit", res.get("is_error") and "over_hard_limit" in res.get("content", ""))

    # 3. missing rationale -> DENY
    tid = agent.propose(run, "create_refund", {"payment_intent": acme_pi.id, "amount": 100, "rationale": "ok",
                                               "recommendation": "approve"})
    res = run.results.get(tid, {})
    check("DENY missing rationale", res.get("is_error") and "missing_rationale" in res.get("content", ""))

    # 4. refund to an unknown payee -> NEEDS_APPROVAL, then approve -> executed
    tid = agent.propose(run, "create_refund", {"payment_intent": shady_pi.id, "amount": 500,
                                               "rationale": "Smoke test: refund to a payee not on the allowlist",
                                               "recommendation": "unsure"})
    check("NEEDS_APPROVAL for unknown payee", tid in run.pending,
          str(run.pending[tid].decision.rules) if tid in run.pending else "")
    agent.resolve_approval(run.id, tid, True, "smoke-test", "approved by script", delay=0,
                           background=False, resume=False)
    res = run.results.get(tid, {})
    check("approved action executes", not res.get("is_error") and '"re_' in res.get("content", ""))

    ok, n, bad = audit.verify_chain()
    check("audit hash chain intact", ok, f"({n} rows)")

    if "--agent" in sys.argv:
        print("\nClaude run (read-only task)...")
        r = agent.start_run("Read-only check: list the 5 most recent payments and give a two-line summary. "
                            "Do not move any money.", background=False)
        for t in r.transcript:
            print(f"  [{t['kind']}] {t['text'][:200]}")
        check("agent run completes", r.status == "done", r.status)

    print(f"\n{'ALL PASSED' if failures == 0 else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
