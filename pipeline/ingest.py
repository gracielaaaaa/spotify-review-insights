"""Stage 1 - Prepare (code only, no model). Reads every row with a multiline-safe CSV parser,
preserves source strings exactly, hashes rows with the course helper, profiles quality,
finds exact duplicate texts and builds the pending-work scope."""

import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

from . import config, store

sys.path.insert(0, str(config.ROOT))
import check_submission as cs  # noqa: E402  (course-provided helper: row_sha, profile, csv_rows)


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sample_key(review_id: str) -> str:
    return hashlib.sha256(f"{config.SAMPLE_SEED}:{review_id}".encode("utf-8")).hexdigest()


def ingest(csv_path: Path, con, out_dir: Path) -> dict:
    t0 = time.time()
    csv_path = Path(csv_path)
    rows, ids = [], set()
    dup_ids = 0
    for i, row in enumerate(cs.csv_rows(csv_path)):
        rid = row["review_id"]
        dup_ids += rid in ids
        ids.add(rid)
        txt = row["review_text"]
        rows.append((rid, i, cs.row_sha(row), txt, row["review_rating"], row["review_likes"],
                     row["app_version"], row["review_timestamp"], text_sha(txt),
                     int(not txt.strip()), sample_key(rid)))
    with store.tx(con):
        con.execute("DELETE FROM source")
        con.executemany("INSERT INTO source VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)

    texts = Counter(r[8] for r in rows if not r[9])
    ratings = Counter(r[4] for r in rows)
    likes = sorted(int(r[5]) for r in rows if r[5].strip().lstrip("-").isdigit())
    lengths = sorted(len(r[3]) for r in rows if not r[9])
    report = {
        "input_path": csv_path.name,
        "file_sha256": cs.sha(csv_path),
        "matches_course_checksum": cs.sha(csv_path) == config.FULL_SHA256,
        "file_bytes": csv_path.stat().st_size,
        "records": len(rows),
        "duplicate_review_ids": dup_ids,
        "empty_review_text": sum(r[9] for r in rows),
        "empty_review_ids": [r[0] for r in rows if r[9]],
        "nonempty_review_text": sum(1 for r in rows if not r[9]),
        "distinct_nonempty_texts": len(texts),
        "exact_duplicate_rows_reusable": sum(texts.values()) - len(texts),
        "texts_with_duplicates": sum(1 for c in texts.values() if c > 1),
        "top_duplicate_texts": [
            {"text": next(r[3] for r in rows if r[8] == h), "count": c} for h, c in texts.most_common(10)],
        "missing_app_version": sum(1 for r in rows if not r[6].strip()),
        "ratings": dict(sorted(ratings.items())),
        "invalid_ratings": sum(c for k, c in ratings.items() if k not in {"1", "2", "3", "4", "5"}),
        "likes": {"min": likes[0], "median": likes[len(likes) // 2], "max": likes[-1],
                  "nonnumeric": len(rows) - len(likes)},
        "text_length_chars": {"mean": round(sum(lengths) / len(lengths), 2), "median": lengths[len(lengths) // 2],
                              "p95": lengths[int(len(lengths) * .95)], "max": lengths[-1]},
        "first_review": min(r[7] for r in rows),
        "last_review": max(r[7] for r in rows),
        "reviews_by_month": dict(sorted(Counter(r[7][:7] for r in rows).items())),
        "multiline_texts": sum(1 for r in rows if "\n" in r[3]),
        "seconds": round(time.time() - t0, 2),
        "notes": ["Empty texts are quarantined with reason empty_review_text; never sent to a model.",
                  "Missing app_version does not block classification.",
                  "Ratings/likes are metadata only; classification input is review_text only."],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ingestion_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    manifest = {"source_file": csv_path.name, "sha256": report["file_sha256"], "bytes": report["file_bytes"],
                "records": report["records"], "course_manifest_sha256": config.FULL_SHA256,
                "row_hash": "check_submission.row_sha: sha256(canonical JSON of 6 source fields)",
                "sample_key": f"sha256('{config.SAMPLE_SEED}:' + review_id)",
                "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (out_dir / "data_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return report


def scope_ids(con, scope: int | None, subset_csv: Path | None = None) -> list[str]:
    """Nonempty review IDs to classify. subset_csv -> exactly those IDs (dev checkpoints);
    scope -> first N nonempty rows by the manifest's deterministic SHA-256 sample order;
    neither -> every nonempty row."""
    if subset_csv:
        wanted = [r["review_id"] for r in cs.csv_rows(subset_csv)]
        have = {r[0] for r in con.execute(
            f"SELECT review_id FROM source WHERE empty=0 AND review_id IN ({','.join('?' * len(wanted))})", wanted)}
        return [w for w in wanted if w in have]
    q = "SELECT review_id FROM source WHERE empty=0 ORDER BY sample_key"
    if scope:
        q += f" LIMIT {int(scope)}"
    return [r[0] for r in con.execute(q)]
