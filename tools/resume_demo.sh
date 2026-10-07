#!/bin/zsh
# Interruption / resume demonstration on checkpoint_500.csv (records saved after every 50-review request).
# Screen-record this whole script. Real paid calls (~$0.07 total).
cd "${0:A:h}/.."
trap 'echo "  (Ctrl-C received; run stopped after its last saved batch)"' INT   # handler (not ignore) so Python still gets Ctrl-C
D="Final Assignment - Spotify Reviews Dataset"
CMD=(.venv/bin/python -m pipeline run --input "$D/spotify_reviews_18months.csv" --subset "$D/checkpoint_500.csv"
     --db state/pipeline.db --out runs/checkpoint_500 --run-id checkpoint500 --verify-min 50)
count() { .venv/bin/python -c "
import sqlite3; c=sqlite3.connect('state/pipeline.db')
print('  enrich calls so far (this run):', c.execute(\"select count(*), coalesce(group_concat(distinct phase),'') from calls where run_id='checkpoint500' and role='enrich'\").fetchone())
print('  validated distinct texts saved :', c.execute('select count(*) from results').fetchone()[0])"; }

echo "=== STEP 1: start the 500-review run. Press Ctrl-C after 3-4 'request N/10 saved' lines. ==="
"${CMD[@]}" 2>&1 | grep --line-buffered -E 'request [0-9]+/|interrupted|label_config=' | cut -c1-200
echo; echo "=== Interrupted. Saved state: ==="; count
.venv/bin/python -c "import json;d=json.load(open('runs/checkpoint_500/checkpoint_before.json'));print('  checkpoint_before.json completed_ids:',len(d['completed_ids']))"
echo; read "?=== STEP 2: press Enter to RESUME with the same command (only pending reviews are sent) ==="
"${CMD[@]}" 2>&1 | grep --line-buffered -E 'request [0-9]+/|label_config=|"status": "done"' | cut -c1-200
echo; echo "=== Resumed. Final state: ==="; count
.venv/bin/python -c "
import json
b=set(json.load(open('runs/checkpoint_500/checkpoint_before.json'))['completed_ids']); a=set(json.load(open('runs/checkpoint_500/checkpoint_after.json'))['completed_ids'])
calls=[json.loads(l) for l in open('runs/checkpoint_500/calls.jsonl')]
re=[c for c in calls if c['role']=='enrich' and c['phase']=='resume']
print('  before:',len(b),' after:',len(a),' new completed:',len(a-b))
print('  resume enrich calls:',len(re),' re-sent already-completed IDs:',sum(len(b & set(c['review_ids'])) for c in re))"
