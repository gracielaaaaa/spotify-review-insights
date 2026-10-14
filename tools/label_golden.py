"""Hand-label the golden 50 (human labels only; never shown to any model).

  .venv/bin/python tools/label_golden.py

  .venv/bin/python tools/label_golden.py --quick   (one line per review: "topic intent severity sentiment")

Saves after every review to evals/golden_50_human.csv; rerun to resume. Press Enter to
accept a [default]. 'b' goes back one review, 'q' quits (progress is kept)."""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Final Assignment - Spotify Reviews Dataset" / "golden_50_to_label.csv"
# Independent human labels (blind: this tool never shows the assistant drafts or any model output).
OUT = ROOT / "evals" / "golden_50_human.csv"
TOPICS = ["access", "usability", "playback", "downloads", "catalog", "billing", "support", "other"]
INTENTS = ["cancellation", "complaint", "request", "praise", "unclear"]
EXTRA = ["accepted_topics", "accepted_intents", "accepted_severities", "ambiguous", "notes", "labeler", "label_source"]

HELP = """
TOPIC  1 access(login/account)  2 usability(controls/nav/layout/queue/ads)  3 playback(crash/lag/stops/audio)
       4 downloads(offline/saved)  5 catalog(missing songs/search/recs/lyrics)  6 billing(price/charges/paywall/premium-only)
       7 support(contacting support)  8 other(general praise/criticism, unrelated)
INTENT (first match wins) 1 cancellation  2 complaint  3 request  4 praise  5 unclear
SEVERITY 1 no problem/praise/unclear/pure request  2 annoyance/generic criticism  3 degraded, some use left
         4 core task blocked  5 explicit serious financial/privacy/data harm
"""


def ask(prompt, default="", choices=None):
    while True:
        v = input(f"  {prompt}" + (f" [{default}]" if default != "" else "") + ": ").strip()
        if v in ("q", "b"):
            return v
        v = v or default
        if choices is None:
            return v
        if v.isdigit() and 1 <= int(v) <= len(choices):
            return choices[int(v) - 1]
        if v in choices:
            return v
        print("   choose a number or one of:", ", ".join(choices))


QUICK = "--quick" in sys.argv


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8", newline="")))
    fields = list(rows[0].keys()) + EXTRA
    done = {}
    if OUT.exists():
        done = {r["review_id"]: r for r in csv.DictReader(open(OUT, encoding="utf-8", newline=""))}
    labeler = next((r["labeler"] for r in done.values() if r.get("labeler")), "") or input("Your name/initials: ").strip()
    print(HELP)
    i = next((k for k, r in enumerate(rows) if r["review_id"] not in done or not done[r["review_id"]].get("topic")), len(rows))
    while i < len(rows):
        r = {**rows[i], **done.get(rows[i]["review_id"], {})}
        print(f"\n[{i + 1}/50] ({r['review_rating']}★ ignore stars for labels)\n  TEXT: {r['review_text']}\n")
        steps = [("topic", "topic 1-8", TOPICS), ("intent", "intent 1-5", INTENTS), ("severity", "severity 1-5", list("12345")),
                 ("sentiment", "sentiment -1..1", None), ("entities", "entities (; separated)", None),
                 ("evidence_quote", "evidence quote (exact substring; Enter = full text)", None),
                 ("needs_review", "needs_review y/n", ["y", "n"]),
                 ("ambiguous", "ambiguous y/n", ["y", "n"]),
                 ("accepted_topics", "other acceptable topics (; separated, optional)", None),
                 ("accepted_intents", "other acceptable intents (optional)", None),
                 ("accepted_severities", "other acceptable severities (optional)", None),
                 ("notes", "notes (optional)", None)]
        back = False
        if QUICK:
            while True:
                v = input("  topic intent severity sentiment [+a if ambiguous]  e.g. '8 2 2 -0.8': ").strip()
                if v in ("q", "b"):
                    break
                parts = v.split()
                amb = parts[-1] == "a" if parts else False
                parts = parts[:-1] if amb else parts
                try:
                    t, n, sv, se = int(parts[0]), int(parts[1]), int(parts[2]), float(parts[3])
                    assert 1 <= t <= 8 and 1 <= n <= 5 and 1 <= sv <= 5 and -1 <= se <= 1
                    break
                except (AssertionError, ValueError, IndexError):
                    print("   need 4 values: topic 1-8, intent 1-5, severity 1-5, sentiment -1..1")
            if v == "q":
                return
            if v == "b":
                i = max(0, i - 1)
                continue
            r.update(topic=TOPICS[t - 1], intent=INTENTS[n - 1], severity=str(sv), sentiment=str(se),
                     entities=r.get("entities", ""), evidence_quote=r["review_text"].strip(),
                     needs_review="true" if amb else "false", ambiguous="y" if amb else "n")
            if amb:
                r["accepted_topics"] = input("  other acceptable topics (names, ; separated): ").strip()
                r["accepted_intents"] = input("  other acceptable intents: ").strip()
                r["accepted_severities"] = input("  other acceptable severities: ").strip()
            r["notes"] = input("  note (optional, Enter to skip): ").strip()
            steps = []
        for key, prompt, choices in steps:
            d = r.get(key, "")
            if key == "needs_review" and d in ("true", "false"):
                d = "y" if d == "true" else "n"
            v = ask(prompt, d, choices)
            if v == "q":
                return
            if v == "b":
                back = True
                break
            if key == "evidence_quote":
                v = v or r["review_text"].strip()
                if v not in r["review_text"]:
                    print("   ! not an exact substring; using the full text")
                    v = r["review_text"].strip()
            if key == "sentiment":
                try:
                    v = str(max(-1.0, min(1.0, float(v))))
                except ValueError:
                    v = "0.0"
            if key == "needs_review":
                v = "true" if v == "y" else "false"
            r[key] = v
        if back:
            i = max(0, i - 1)
            continue
        r["labeler"] = labeler
        r["label_source"] = "human_blind"
        done[r["review_id"]] = r
        OUT.parent.mkdir(exist_ok=True)
        with open(OUT, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                if row["review_id"] in done:
                    w.writerow(done[row["review_id"]])
        i += 1
    print(f"\nAll 50 labeled -> {OUT}")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nSaved; rerun to resume.")
        sys.exit(0)
