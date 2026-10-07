"""Stage 5 - Rank (code only, never a model). Recomputes the baseline ranking from saved
records + membership: priority_score = severity_sum = complaint_count x mean_severity.
Order: score desc, then issue_id asc. Means: 6 decimals, half-up."""

import csv
import json
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


def mean_string(total, n):
    return str((Decimal(total) / Decimal(n)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def compute(membership_rows, severity_by_id):
    sev = defaultdict(list)
    seen = set()
    for iid, rid in membership_rows:
        if (iid, rid) in seen:
            raise ValueError(f"duplicate membership {iid},{rid}")
        seen.add((iid, rid))
        sev[iid].append(severity_by_id[rid])
    rows = [{"issue_id": iid, "complaint_count": len(v), "severity_sum": sum(v),
             "mean_severity": mean_string(sum(v), len(v)), "priority_score": sum(v)} for iid, v in sev.items()]
    rows.sort(key=lambda r: (-r["priority_score"], r["issue_id"]))
    for k, r in enumerate(rows, 1):
        r["rank"] = k
    return rows


FIELDS = ["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"]


def write(rows, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in FIELDS})


def from_saved(grading_dir: Path):
    """Offline re-rank from grading/records.jsonl(.gz) + membership.csv. No API key needed."""
    import gzip
    p = grading_dir / "records.jsonl"
    stream = open(p, encoding="utf-8") if p.exists() else gzip.open(str(p) + ".gz", "rt", encoding="utf-8")
    sev = {}
    with stream as f:
        for line in f:
            r = json.loads(line)
            if r.get("status") == "completed" and r["intent"] in ("complaint", "cancellation"):
                sev[r["review_id"]] = r["severity"]
    with open(grading_dir / "membership.csv", newline="", encoding="utf-8") as f:
        mem = [(r["issue_id"], r["review_id"]) for r in csv.DictReader(f)]
    return compute(mem, sev)


def topic_rollup(rows):
    """Supplementary (not the baseline): sum issues to the 8 primary topics."""
    out = defaultdict(lambda: {"complaint_count": 0, "severity_sum": 0, "issues": 0})
    for r in rows:
        t = r["issue_id"].split(".")[0]
        out[t]["complaint_count"] += r["complaint_count"]
        out[t]["severity_sum"] += r["severity_sum"]
        out[t]["issues"] += 1
    res = []
    for t, v in out.items():
        res.append({"topic": t, **v, "mean_severity": mean_string(v["severity_sum"], v["complaint_count"])})
    res.sort(key=lambda r: (-r["severity_sum"], r["topic"]))
    return res
