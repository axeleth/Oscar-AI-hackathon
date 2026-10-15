"""Deterministic policy gate. The LLM proposes, this code decides.

evaluate() returns ALLOW / DENY / NEEDS_APPROVAL plus every rule that fired, so the audit log can
say exactly why. Add a rule = add a few lines in evaluate() with a stable rule id.
"""
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import yaml

from . import audit, config
from .tools import Money, Tool

ALLOW, DENY, NEEDS_APPROVAL = "ALLOW", "DENY", "NEEDS_APPROVAL"


@dataclass
class Decision:
    verdict: str
    rules: list[str] = field(default_factory=list)      # rule ids that fired
    reasons: list[str] = field(default_factory=list)    # human-readable explanations
    policy_version: str = ""
    clauses: list[str] = field(default_factory=list)    # POLICY.md clause ids behind the rules that fired

    def summary(self) -> str:
        return "; ".join(self.reasons) or "within policy"


def load_policy() -> tuple[dict, str]:
    raw = config.POLICY_PATH.read_bytes()
    return yaml.safe_load(raw) or {}, hashlib.sha256(raw).hexdigest()[:12]


def fmt_money(amount: int | None, currency: str | None) -> str:
    if amount is None:
        return "unknown amount"
    sym = {"gbp": "£", "usd": "$", "eur": "€"}.get((currency or "").lower(), "")
    return f"{sym}{amount / 100:,.2f}" + ("" if sym else f" {(currency or '').upper()}")


def evaluate(t: Tool, args: dict, money: Money | None) -> Decision:
    p, version = load_policy()
    deny: list[tuple[str, str]] = []
    approve: list[tuple[str, str]] = []

    if t.name in (p.get("blocked_tools") or []):
        deny.append(("tool_blocked", f"tool '{t.name}' is blocked by policy"))
    enabled = p.get("enabled_tools") or []
    if enabled and t.name not in enabled:
        deny.append(("tool_not_enabled", f"tool '{t.name}' is not enabled"))
    if t.name in ((p.get("approval") or {}).get("required_tools") or []):
        approve.append(("tool_requires_approval", f"'{t.name}' always requires human approval"))

    if t.money:
        currency = (p.get("currency") or "gbp").lower()
        limits = p.get("limits") or {}
        autonomy = p.get("autonomy", "threshold")
        rationale = (args.get("rationale") or "").strip()

        if p.get("require_rationale", True) and len(rationale) < 15:
            deny.append(("missing_rationale", "money-moving actions must include a specific rationale"))

        if money is None or money.amount is None:
            approve.append(("amount_unknown", "could not verify the amount from Stripe"))
        else:
            amt = money.amount
            if money.currency and money.currency.lower() != currency:
                deny.append(("currency_mismatch", f"currency {money.currency} is not the policy currency {currency}"))
            if amt <= 0:
                deny.append(("non_positive_amount", "amount must be positive"))
            max_txn = limits.get("max_per_transaction")
            if max_txn is not None and amt > max_txn:
                deny.append(("over_hard_limit", f"{fmt_money(amt, currency)} exceeds hard limit "
                                                f"{fmt_money(max_txn, currency)} per transaction"))
            daily = limits.get("daily_total")
            if daily is not None:
                midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                spent = audit.executed_total_since(midnight.isoformat(), currency)
                if spent + amt > daily:
                    deny.append(("daily_limit", f"would bring today's total to {fmt_money(spent + amt, currency)} "
                                                f"(limit {fmt_money(daily, currency)})"))
            threshold = (p.get("approval") or {}).get("threshold")
            if autonomy == "threshold" and threshold is not None and amt > threshold:
                approve.append(("over_approval_threshold", f"{fmt_money(amt, currency)} is above the "
                                                           f"auto-approve threshold {fmt_money(threshold, currency)}"))
            window = (p.get("duplicates") or {}).get("window_minutes")
            if window:
                since = (datetime.now(timezone.utc) - timedelta(minutes=window)).isoformat()
                if audit.recent_executions(t.name, money.payee, amt, since):
                    approve.append(("possible_duplicate", f"same {t.name} of {fmt_money(amt, currency)} to "
                                                          f"{money.payee} already executed in the last {window} min"))

        payees = p.get("payees") or {}
        allowlist = [a.lower() for a in (payees.get("allowlist") or [])]
        payee = (money.payee or "").lower() if money else ""
        if allowlist and payee not in allowlist:
            rule = ("unknown_payee", f"payee '{payee or 'unknown'}' is not on the allowlist")
            (deny if payees.get("unknown_payee") == "deny" else approve).append(rule)

        if autonomy == "suggest":
            approve.append(("autonomy_suggest_only", "autonomy is 'suggest': every money movement needs approval"))

    fired = deny + approve
    verdict = DENY if deny else NEEDS_APPROVAL if approve else ALLOW
    rule_clause = p.get("clauses") or {}
    tool_clause = (p.get("tool_clauses") or {}).get(t.name)
    reasons, cited = [], []
    for r, m in fired:
        c = tool_clause if r == "tool_requires_approval" and tool_clause else rule_clause.get(r)
        reasons.append(f"§{c} {m}" if c else m)
        if c and c not in cited:
            cited.append(c)
    if verdict == ALLOW and t.money:
        cited.append("2.1")  # auto-execution authority
    return Decision(verdict, [r for r, _ in fired], reasons, version, cited)
