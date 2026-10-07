"""Central configuration: models, prompt versions, limits. Changing anything that feeds
LABEL_CONFIG invalidates the exact-text result cache (by design)."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "Final Assignment - Spotify Reviews Dataset"
FULL_CSV = DATA_DIR / "spotify_reviews_18months.csv"
STATE_DIR = ROOT / "state"
DB_PATH = STATE_DIR / "pipeline.db"
PROMPTS = ROOT / "prompts"

FULL_SHA256 = "1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6"
SAMPLE_SEED = "berkeley-fall-2026-assignment-5-v1"  # from manifest.json

TOPICS = ("access", "usability", "playback", "downloads", "catalog", "billing", "support", "other")
INTENTS = ("cancellation", "complaint", "request", "praise", "unclear")  # precedence order

# Fixed subtopics per topic. Grouping uses topic.subtopic as the stable issue key.
SUBTOPICS = {
    "access": ["login_failure", "account_hacked_or_locked", "signup", "password_reset", "access_other"],
    "usability": ["ads", "ui_navigation", "playlist_queue", "shuffle_controls", "redesign_layout", "usability_other"],
    "playback": ["crash_wont_open", "stops_skips", "lag_slow", "connectivity", "audio_quality",
                 "battery_resources", "device_integration", "playback_other"],
    "downloads": ["downloads_disappear", "offline_not_working", "download_limits", "downloads_other"],
    "catalog": ["missing_content", "search", "recommendations", "lyrics", "podcasts", "catalog_other"],
    "billing": ["price", "unwanted_charges", "premium_paywall", "subscription_activation", "refunds", "billing_other"],
    "support": ["no_response", "unhelpful_support", "support_other"],
    "other": ["general_criticism", "general_praise", "unrelated_or_unclear", "other"],
}
ALL_SUBTOPICS = sorted({s for v in SUBTOPICS.values() for s in v})

# Model allowlist (exact IDs). Only these may be called.
MODELS = {
    "enrich": "claude-haiku-4-5",
    "verify": "claude-haiku-4-5",
    "group": "claude-haiku-4-5",
    "memo": "claude-sonnet-5-5",
    "fallback": "claude-sonnet-5-5",
}
ALLOWLIST = set(MODELS.values())

ENRICH_PROMPT_VERSION = "enrich_v1"
VERIFY_PROMPT_VERSION = "verify_v1"
GROUP_PROMPT_VERSION = "group_v1"
MEMO_PROMPT_VERSION = "memo_v1"
SCHEMA_VERSION = "schema_v1"

# Limits / controls
MAX_REVIEWS_PER_REQUEST = 50          # contract maximum
ENRICH_BATCH_SIZE = 50
ENRICH_MAX_TOKENS = 3000              # output cap per enrich request (50 items)
VERIFY_MAX_TOKENS = 2000
SHORT_TEXT_CHARS = 160                # <= this: evidence_quote = full stripped text (code, not model)
SPEND_CAP_USD = 9.00                  # hard cap across the whole project ledger
MAX_WORKERS = 1
FALLBACK_MAX_FRACTION = 0.02          # max share of records that may go to the stronger model
INVALID_RETRIES = 1                   # contract: retry invalid output once
TRANSIENT_RETRIES = 4                 # 429/5xx/connection, exponential backoff + jitter
VERIFY_SAMPLE_FRACTION = 0.01


def prompt_text(version: str) -> str:
    return (PROMPTS / f"{version}.md").read_text(encoding="utf-8")


def prompt_hash(version: str) -> str:
    return hashlib.sha256(prompt_text(version).encode("utf-8")).hexdigest()[:8]


def label_config() -> str:
    """model + prompt(version@hash) + schema. Stored on every record and enrich call."""
    return f"{MODELS['enrich']}+{ENRICH_PROMPT_VERSION}@{prompt_hash(ENRICH_PROMPT_VERSION)}+{SCHEMA_VERSION}"
