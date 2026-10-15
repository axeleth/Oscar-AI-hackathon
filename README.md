# Oscar – autonomous finance agent starter kit

Hackathon core for "let an AI agent move money safely": **autonomy, policy, auditability** on Stripe **test mode**.

```
 task ──► Claude (tool-use loop) ──► tool call ──► resolve REAL amount/payee from Stripe
                                                        │
                                       policy.yaml ──►  POLICY GATE (deterministic code)
                                                        ├─ ALLOW ─────────► execute on Stripe ─┐
                                                        ├─ DENY ──────────► error back to agent │
                                                        └─ NEEDS_APPROVAL ► pause → human UI ───┤
                                                                                                ▼
                                                    hash-chained SQLite audit log (every step, policy version)
```

## Quick start (5 min)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY and STRIPE_SECRET_KEY (sk_test_...)
.venv/bin/python -m pytest -q                 # offline tests (no keys needed)
.venv/bin/python scripts/seed_stripe.py       # demo customers, payments, invoices, a dispute
.venv/bin/python scripts/smoke_test.py --agent  # live check: ALLOW / DENY / APPROVAL + one Claude run
.venv/bin/uvicorn app.main:app --reload       # http://localhost:8000
```

Try in the UI: *"Check recent payments for duplicate charges and refund them. Then review open invoices and
flag anything suspicious."* – this should show an auto-refund, a held approval and a prompt-injection
escalation (the Initech invoice tells the AI to refund Shady Holdings).

Copy-paste demo prompts: [DEMO_PROMPTS.md](DEMO_PROMPTS.md).

Debug without the UI: `.venv/bin/python scripts/cli.py "your task"`. Fresh demo: `scripts/reset_stripe.py --audit`.

## Files

| File | What it is | Change on the night? |
|---|---|---|
| `app/prompts.py` | System prompt (ROLE / TASK_GUIDANCE / GUARDRAILS) | **Yes** – role + task |
| `app/tools.py` | Tool registry + Stripe tools. `@tool(..., money=True, resolve=...)` | **Yes** – add/remove tools |
| `policy.yaml` | Limits, approval threshold, allowlist, autonomy level. Hot-reloaded | **Yes** |
| `scripts/seed_stripe.py` | `SCENARIO` dict of test data | **Yes** |
| `app/policy.py` | Deterministic rules → ALLOW / DENY / NEEDS_APPROVAL + rule ids | Add a rule if needed |
| `app/agent.py` | Claude loop, gate, pause/resume for approvals | Rarely |
| `app/audit.py` | Append-only hash-chained log, `verify_chain()` | No |
| `app/main.py`, `app/templates/` | FastAPI + htmx UI, CSV export, JSON API | Branding/labels |

## Night-of checklist (premise → working demo)

1. **Rewrite `ROLE`/`TASK_GUIDANCE`** in `app/prompts.py` for the premise. Keep `GUARDRAILS`.
2. **Tools**: add premise-specific tools in `app/tools.py`. A money tool needs a `resolve(args) -> Money` that
   reads the real amount/payee from Stripe (never trust the LLM's numbers). Use `enabled_tools` in
   `policy.yaml` to hide irrelevant ones.
3. **Policy**: set threshold, limits, allowlist, `required_tools`. Add a premise rule in `policy.evaluate()` if
   it makes the story (e.g. "refunds > 30 days after purchase need approval").
4. **Seed data**: edit `SCENARIO` so the demo has a happy path, a blocked action, a held action and a trap.
5. **Rehearse the 3-minute demo**: task → agent works → auto-executed action → DENY → approval card → approve →
   audit log + `hash chain intact` + CSV export. Then edit `policy.yaml` live (e.g. `autonomy: suggest`) and rerun.

### Mapping likely premises onto the core

| Premise | Main tools | Policy story |
|---|---|---|
| Refunds/disputes agent | `list_charges`, `create_refund`, `list_disputes`, `submit_dispute_evidence` | auto-refund small/known, approve large, never refund unknown payee |
| Accounts receivable / collections | `list_invoices`, `send_invoice`, `create_invoice`, `void_invoice` | voids need approval, dunning cadence rules |
| Reconciliation / month-end | `list_balance_transactions`, `list_payouts`, `pay_invoice(out_of_band)`, `record_finding` | marking paid needs a matching bank line; mismatches escalate |
| Supplier payments (AP) | add `pay_vendor` (simulate, or Stripe Connect transfers / Issuing) | allowlisted vendors, new bank details → approval, duplicate detection |
| Agent spend / procurement | Stripe Issuing virtual cards with spending controls | per-merchant/category limits |

## Stripe in 60 seconds

- **Test mode** (toggle top-right in the Dashboard): fake money, keys start `sk_test_`. The app refuses live keys.
- **PaymentIntent** = one attempt to charge a customer. **Charge** = the resulting card charge. **Refund** sends
  money back. **Dispute** = customer's bank reversed a charge (chargeback). **Invoice** = bill (draft → open →
  paid/void). **Balance transactions** = the ledger (charges, fees, refunds, payouts). **Payout** = Stripe balance
  → your bank.
- Amounts are **minor units**: `12000` = £120.00.
- Magic test payment methods: `pm_card_visa` (works), `pm_card_bypassPending` (funds available immediately),
  `pm_card_createDispute` (gets disputed), `pm_card_chargeDeclined` (declines).
- **Restricted key** (good judge talking point – least privilege): Dashboard → Developers → API keys → Create
  restricted key, write on Customers, PaymentIntents, Refunds, Invoices, Disputes, Payouts; read on Charges,
  Balance, Balance transactions. Put the `rk_test_...` in `.env`.
- **Stripe Agent Toolkit** (`stripe-agent-toolkit`) and the Stripe MCP server expose the same APIs as LLM tools.
  This kit uses its own thin `stripe` SDK wrappers so every call is forced through the policy gate. That is
  the point to make to judges: the toolkit gives an agent *capabilities*; Oscar adds *governance*.

## Config (`.env`)

`OSCAR_MODEL` (default `claude-opus-5-5`; `claude-sonnet-5-5` is faster), `OSCAR_EFFORT` (default `medium`),
`OSCAR_FALLBACKS=1` (server-side refusal fallback, beta – set `0` if the API rejects it), `OSCAR_MAX_TURNS`.

## Endpoints

`/` UI · `/audit.csv[?run_id=]` export · `/api/audit/verify` chain check · `/api/runs/{id}` run JSON · `/api/tools`
