"""Central configuration, loaded from .env. Fails fast on anything that could touch real money."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")

MODEL = os.getenv("OSCAR_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("OSCAR_EFFORT", "medium")  # low | medium | high | xhigh | max
# Server-side refusal fallback (beta). Set OSCAR_FALLBACKS=0 if the API rejects it.
USE_FALLBACKS = os.getenv("OSCAR_FALLBACKS", "1") == "1"
MAX_AGENT_TURNS = int(os.getenv("OSCAR_MAX_TURNS", "25"))

POLICY_PATH = Path(os.getenv("OSCAR_POLICY", ROOT / "policy.yaml"))
AUDIT_DB = Path(os.getenv("OSCAR_AUDIT_DB", ROOT / "data" / "audit.db"))


def assert_test_mode() -> None:
    """Refuse to run with a live Stripe key. Test (sk_test_) or restricted test (rk_test_) only."""
    if not STRIPE_SECRET_KEY:
        raise RuntimeError("STRIPE_SECRET_KEY is not set (see .env.example)")
    if not STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_")):
        raise RuntimeError("Refusing to start: STRIPE_SECRET_KEY is not a TEST key (sk_test_/rk_test_)")
