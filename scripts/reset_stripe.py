"""Clean up seeded test data for a fresh demo.

Voids open invoices and deletes customers tagged metadata.oscar_seed=1. Payments/refunds can't be deleted
in Stripe; for a total wipe use Dashboard > Developers > "Delete all test data" (test mode only).
  .venv/bin/python scripts/reset_stripe.py           # Stripe objects
  .venv/bin/python scripts/reset_stripe.py --audit   # also delete the local audit log
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.tools import sc  # noqa: E402


def main():
    config.assert_test_mode()
    s = sc()
    customers = s.v1.customers.search(params={"query": "metadata['oscar_seed']:'1'", "limit": 100}).data
    for c in customers:
        for inv in s.v1.invoices.list(params={"customer": c.id, "limit": 100}).data:
            if inv.status == "open":
                s.v1.invoices.void_invoice(inv.id)
                print(f"voided   {inv.id}")
            elif inv.status == "draft":
                s.v1.invoices.delete(inv.id)
                print(f"deleted  {inv.id}")
        s.v1.customers.delete(c.id)
        print(f"deleted  {c.id} {c.email}")
    print(f"Removed {len(customers)} seeded customers (search index can lag ~1 min after seeding).")

    if "--audit" in sys.argv:
        if config.AUDIT_DB.exists():
            config.AUDIT_DB.unlink()
            print(f"deleted  {config.AUDIT_DB}")
        from app import cognee_store
        cognee_store.start(background=False)
        if cognee_store.ready():
            cognee_store.reset_all()
            print("rebuilt  Cognee memory from scratch (all decisions wiped, policy graph rebuilt from POLICY.md)")


if __name__ == "__main__":
    main()
