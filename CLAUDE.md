# Oscar – notes for Claude

Hackathon starter kit: Claude agent operating Stripe TEST mode behind a deterministic policy gate, with a
hash-chained audit log and human approval queue. See README.md for the architecture and night-of checklist.

- Python 3.12, venv at `.venv`. Run tests: `.venv/bin/python -m pytest -q` (offline). Server:
  `.venv/bin/uvicorn app.main:app --reload`. Live check: `.venv/bin/python scripts/smoke_test.py --agent`.
- Every tool call must go through `agent._handle_tool_use` → `policy.evaluate`. Never call Stripe from the
  agent loop directly or bypass the gate.
- Money tools: `@tool(..., money=True, resolve=fn)`; `resolve` must read the real amount/payee from Stripe.
  Amounts are minor units. Write calls pass `options=_opts(ctx)` (idempotency key).
- Policy rules live in `policy.evaluate()` with stable rule ids; `policy.yaml` is hot-reloaded.
- Money tools and `opinion=True` tools require `rationale` + `recommendation` (approve/reject/unsure).
  Human decisions go through `agent.resolve_approval`, which enforces: override needs confirmation + reason,
  amounts above `approval.confirm_above` must be typed, approvals wait `approval.undo_seconds` (undo_approval).
- Audit events of one tool call share `action_id` (= tool_use_id); `audit_view.headline_groups` builds the UI.
- Audit log is append-only; never UPDATE/DELETE rows (breaks `verify_chain`). Add events via `audit.log()`.
- Claude calls: `claude().beta.messages.create` with adaptive thinking + `output_config.effort`; append
  `response.content` unchanged to history (thinking blocks must be preserved). No forced `tool_choice`
  on Opus 5.5.
- `config.assert_test_mode()` refuses live Stripe keys – keep it that way.
