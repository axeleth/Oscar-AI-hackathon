"""Run a task in the terminal, approving held actions interactively. Handy for debugging without the UI.
  .venv/bin/python scripts/cli.py "Find duplicate charges this week and refund them"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agent, policy  # noqa: E402


def main():
    task = " ".join(sys.argv[1:]) or input("Task: ")
    run = agent.start_run(task, background=False)
    seen = 0
    while True:
        for t in run.transcript[seen:]:
            print(f"[{t['kind']}] {t['text']}\n")
        seen = len(run.transcript)
        if run.status != "awaiting_approval":
            break
        for p in list(run.pending.values()):
            m = p.money
            print(f">>> APPROVAL NEEDED: {p.tool} {policy.fmt_money(m.amount, m.currency) if m else ''} "
                  f"to {m.payee if m else '-'}\n    why held: {p.decision.summary()}\n"
                  f"    Oscar recommends: {(p.recommendation or 'no opinion').upper()} - {p.args.get('rationale')}")
            ok = input("    approve? [y/N] ").strip().lower() == "y"
            comment = input("    reason: ")
            typed, override = "", False
            while err := agent.check_decision(p, ok, comment, typed, override):
                print(f"    ! {err}")
                if p.needs_override and not override:
                    override = input("    override Oscar? [y/N] ").strip().lower() == "y"
                    if not override:
                        ok = not ok
                        continue
                    comment = input("    your reason for overriding: ")
                else:
                    typed = input("    type the amount: ")
            agent.resolve_approval(run.id, p.tool_use_id, ok, "cli", comment, typed_amount=typed,
                                   confirm_override=override, delay=0, background=False)
    print(f"status: {run.status}")


if __name__ == "__main__":
    main()
