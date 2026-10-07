"""Model access for every role: allowlist, rates, spend ledger with reservations, bounded
retries with backoff + jitter, and per-attempt call logging. Importing this module never
makes a network call; a client is created only when a paid command runs."""

import csv
import os
import random
import threading
import time
import uuid
from dataclasses import dataclass

from . import config

RATES_CSV = config.ROOT / "cost" / "rates.csv"


class BudgetExceeded(RuntimeError):
    pass


def load_rates(path=RATES_CSV) -> dict:
    """{(model, tier, item): usd_per_unit}. Prices in the CSV are per 1M tokens."""
    rates = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rates[(r["model"], r["tier"], r["item"])] = float(r["usd_per_unit"]) / float(r["unit_size"])
    return rates


def cost_of(usage: dict, model: str, tier: str, rates: dict) -> float:
    """Mutually exclusive billing items: uncached input, cache write, cache read, output.
    Anthropic's input_tokens already excludes cache read/write tokens."""
    total = 0.0
    for item, field in (("input", "input_tokens"), ("cache_write", "cache_write_tokens"),
                        ("cache_read", "cache_read_tokens"), ("output", "output_tokens")):
        total += (usage.get(field) or 0) * rates[(model, tier, item)]
    return total


@dataclass
class Ledger:
    """One shared spend ledger. Workers reserve worst-case cost before dispatch; the
    reservation is replaced by actual cost when the call returns."""
    spent: float
    cap: float
    reserved: float = 0.0

    def __post_init__(self):
        self.lock = threading.Lock()

    def reserve(self, amount: float):
        with self.lock:
            if self.spent + self.reserved + amount > self.cap:
                raise BudgetExceeded(f"spent {self.spent:.4f} + reserved {self.reserved:.4f} + next {amount:.4f} "
                                     f"> cap {self.cap:.2f}")
            self.reserved += amount

    def settle(self, reserved: float, actual: float):
        with self.lock:
            self.reserved -= reserved
            self.spent += actual


_client = None


def client():
    global _client
    if _client is None:
        from dotenv import load_dotenv
        import anthropic
        load_dotenv(config.ROOT / ".env")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY missing: copy .env.example to .env (paid commands only).")
        _client = anthropic.Anthropic(max_retries=0, timeout=120)  # retries handled below, so each is logged
    return _client


def estimate_tokens(text: str) -> int:
    return len(text) // 3 + 1  # conservative (real ratio ~4 chars/token for English)


def call(*, role, model, system, user, max_tokens, ledger, rates, schema=None, tier="standard",
         on_attempt=None, extra=None):
    """Synchronous call with bounded transient retries. Returns (message, usage, attempts).
    on_attempt(dict) is invoked for every attempt (failed ones too) so the caller logs it."""
    import anthropic
    if model not in config.ALLOWLIST:
        raise ValueError(f"model {model} not in allowlist")
    worst = (estimate_tokens(system + user) * rates[(model, tier, "input")]
             + max_tokens * rates[(model, tier, "output")])
    params = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": user}])
    if schema:
        params["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    if extra:
        params.update(extra)
    for attempt in range(1, config.TRANSIENT_RETRIES + 2):
        ledger.reserve(worst)
        started = time.time()
        try:
            msg = client().messages.create(**params)
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError,
                anthropic.APITimeoutError) as e:
            ledger.settle(worst, 0.0)
            unknown = isinstance(e, (anthropic.APITimeoutError, anthropic.APIConnectionError))
            if on_attempt:
                on_attempt(dict(request_id=f"local-{uuid.uuid4().hex[:12]}", outcome="failed", attempt=attempt,
                                started=started, input_tokens=0, output_tokens=0, cache_read_tokens=0,
                                cache_write_tokens=0, cost_usd=0.0,
                                error=f"{type(e).__name__}: {str(e)[:200]}"
                                      + (" [charge unknown: timeout/connection]" if unknown else "")))
            if attempt > config.TRANSIENT_RETRIES:
                raise
            delay = min(60, 2 ** attempt) + random.uniform(0, 1)
            if isinstance(e, anthropic.RateLimitError):
                try:
                    delay = max(delay, float(e.response.headers.get("retry-after", 0)))
                except (TypeError, ValueError):
                    pass
            time.sleep(delay)
            continue
        except anthropic.APIStatusError as e:
            ledger.settle(worst, 0.0)
            if on_attempt:
                on_attempt(dict(request_id=f"local-{uuid.uuid4().hex[:12]}", outcome="failed", attempt=attempt,
                                started=started, input_tokens=0, output_tokens=0, cache_read_tokens=0,
                                cache_write_tokens=0, cost_usd=0.0, error=f"HTTP {e.status_code}: {str(e)[:200]}"))
            raise
        u = msg.usage
        usage = dict(input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                     cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                     cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0)
        actual = cost_of(usage, model, tier, rates)
        ledger.settle(worst, actual)
        usage.update(cost_usd=actual, request_id=msg.id, attempt=attempt, started=started,
                     stop_reason=msg.stop_reason)
        return msg, usage
    raise RuntimeError("unreachable")


def text_of(msg) -> str:
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
