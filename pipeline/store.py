"""SQLite state store. Every enrichment batch is saved in ONE transaction (atomic):
results + call log row commit together or not at all, so a crash never half-saves."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS source (
  review_id TEXT PRIMARY KEY, row_idx INTEGER, source_sha256 TEXT, review_text TEXT,
  review_rating TEXT, review_likes TEXT, app_version TEXT, review_timestamp TEXT,
  text_sha TEXT, empty INTEGER, sample_key TEXT);
CREATE INDEX IF NOT EXISTS source_text ON source(text_sha);
CREATE INDEX IF NOT EXISTS source_key ON source(sample_key);

-- one validated result per (exact text, label_config); origin_review_id = the ID actually sent
CREATE TABLE IF NOT EXISTS results (
  text_sha TEXT, label_config TEXT, origin_review_id TEXT, topic TEXT, subtopic TEXT, intent TEXT,
  sentiment REAL, severity INTEGER, entities TEXT, evidence_quote TEXT, needs_review INTEGER,
  review_reason TEXT, request_id TEXT, phase TEXT, run_id TEXT, created_at REAL,
  PRIMARY KEY (text_sha, label_config));

-- unresolved texts after the allowed attempts
CREATE TABLE IF NOT EXISTS quarantine (
  text_sha TEXT, label_config TEXT, origin_review_id TEXT, reason TEXT, attempts INTEGER,
  last_error TEXT, created_at REAL, PRIMARY KEY (text_sha, label_config));

CREATE TABLE IF NOT EXISTS calls (
  request_id TEXT PRIMARY KEY, run_id TEXT, stage TEXT, role TEXT, model TEXT, effort TEXT,
  phase TEXT, outcome TEXT, label_config TEXT, prompt_version TEXT, review_ids TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
  tier TEXT, attempt INTEGER, started REAL, ended REAL, error TEXT, cost_usd REAL);

-- provider Batch API jobs, so a restart reconciles in-flight work instead of resubmitting
CREATE TABLE IF NOT EXISTS batch_jobs (
  batch_id TEXT PRIMARY KEY, run_id TEXT, phase TEXT, created REAL, status TEXT, requests TEXT);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def connect(path: Path = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=60, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


@contextmanager
def tx(con):
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def log_call(con, **c):
    c.setdefault("ended", time.time())
    c["review_ids"] = json.dumps(c.get("review_ids", []))
    cols = ",".join(c)
    con.execute(f"INSERT INTO calls ({cols}) VALUES ({','.join('?' * len(c))})", list(c.values()))


def spent_usd(con) -> float:
    return con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls").fetchone()[0]


def kv_get(con, k, default=None):
    r = con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def kv_set(con, k, v):
    con.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, json.dumps(v)))
