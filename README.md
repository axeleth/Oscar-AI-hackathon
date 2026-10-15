# Oscar AIccountant – an autonomous refunds & payments agent that explains every pound

**Stripe × Briefcase AI Fintech Hackathon, London, 15 Oct 2026 · Track: Agents that spend · Side quest: Best use of Cognee memory**

Oscar works through a company's finance inbox on Stripe (test mode). It refunds and pays **on its own** when the
written policy allows, sends anything bigger to a human **with its own recommendation**, refuses fraud, and can
explain every decision in plain English, citing the policy clause and the evidence. **Cognee** holds the policy as a
knowledge graph and remembers every decision, so Oscar knows *which clause authorised each payment* and *what was
decided before*.

> Code enforces the rules. Cognee remembers them, and everything we've decided, so Oscar can explain every pound.

## What judges should look at

| Criterion | Where |
|---|---|
| **Money really moves** | Refunds/invoice settlement on Stripe test mode, each tagged with `run_id` + `approved_by` metadata |
| **Autonomy with control** | `POLICY.md` (27 numbered clauses) mirrored by `policy.yaml`, enforced deterministically in `app/policy.py` *before* any money moves: £200 own authority, approval above, typed amount above £1,000, hard caps, payee allowlist, duplicate detection |
| **Human-error safeguards** | Oscar must recommend approve/reject; overriding it needs a reason (both recorded); 5-second undo window; typed amount for large payments |
| **Explains every pound** | Hash-chained, append-only audit log grouped per action; **Go!** on any action gives a plain-English answer with hover popups for ids/clauses; CSV export |
| **Cognee memory** | `app/cognee_store.py`: POLICY.md → custom policy graph model (Clause → ActionType / Limit / Approver / Counterparty); `consult_policy` and `recall_precedents` query it; every decision is remembered and flashes in the dashboard; `/policy?tab=graph` shows the live graph |

## Built before vs during the event (commit history is unedited)

**Before 18:00 – starter template (commits 14:49–16:36):** generic agent loop (Claude tool use), Stripe test-mode
tools, deterministic policy gate (ALLOW / DENY / NEEDS_APPROVAL), approval queue, hash-chained audit log, htmx UI,
seed/reset/smoke scripts, approval safeguards (recommendation + override, undo, typed amount), grouped audit view.

**Built at the hackathon (18:00–21:00, commits from 18:22):** the Spend-track product – written policy with numbered
clauses and rule→clause mapping; refunds/payments inbox scenario; **Cognee policy knowledge graph + decision memory**
(consult/recall tools, live memory feed, graph page); plain-English explanations with detail popups (**Go!**);
chat-style run panel with artifact cards and follow-up messages; orphaned-approval handling; partial-authority
refunds; demo inbox; branding and UI.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # ANTHROPIC_API_KEY + STRIPE_SECRET_KEY (sk_test_...)
.venv/bin/python scripts/seed_stripe.py
.venv/bin/python scripts/cognee_ingest.py        # builds the Cognee policy graph (first run downloads a local embedding model)
OSCAR_INBOX=scenarios/inbox_demo.json .venv/bin/uvicorn app.main:app   # http://localhost:8000
```
Then send: *"Work through the finance inbox. Handle every message according to policy."* See `DEMO_PROMPTS.md`.
Reset for a fresh demo: `.venv/bin/python scripts/reset_stripe.py --audit && .venv/bin/python scripts/seed_stripe.py`.

---

# Starter kit notes

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
