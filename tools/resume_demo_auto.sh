#!/bin/zsh
# Non-interactive interruption/resume demo: starts the 1,000-review resume-demo slice, sends SIGINT
# (same as Ctrl-C) after >=200 texts are saved, shows saved state, then resumes with the SAME command.
cd "${0:A:h}/.."
D="Final Assignment - Spotify Reviews Dataset"
CMD=(.venv/bin/python -m pipeline run --input "$D/spotify_reviews_18months.csv" --subset evals/resume/resume_demo_1000.csv
     --db state/pipeline.db --out runs/resume_demo --run-id resume-demo --stages ingest,enrich)
q() { sqlite3 state/pipeline.db "$1"; }
echo "### $(date '+%F %T')  STEP 1: start run (label_config $(.venv/bin/python -c 'from pipeline import config;print(config.label_config())'))"
echo "### saved texts before start: $(q "select count(*) from results")"
"${CMD[@]}" > runs/resume_demo_phase1.log 2>&1 &
PID=$!
while kill -0 $PID 2>/dev/null && [ "$(q "select count(*) from results")" -lt 800 ]; do sleep 1; done
echo "### $(date '+%F %T')  $(q "select count(*) from results") texts saved -> sending SIGINT (Ctrl-C) to PID $PID"
kill -INT $PID; wait $PID; echo "### exit code: $?"
grep -E 'request [0-9]+/|interrupted' runs/resume_demo_phase1.log | cut -c60-230
echo "### $(date '+%F %T')  STATE AFTER INTERRUPTION"
echo "    saved distinct texts: $(q "select count(*) from results")"
echo "    enrich calls (initial): $(q "select count(*) from calls where run_id='resume-demo' and role='enrich' and phase='initial'")"
.venv/bin/python -c "import json;print('    checkpoint_before.json completed_ids:',len(json.load(open('runs/resume_demo/checkpoint_before.json'))['completed_ids']))"
cp runs/resume_demo/checkpoint_before.json evals/resume/checkpoint_before.json
sleep 2
echo "### $(date '+%F %T')  STEP 2: RESUME with the identical command"
"${CMD[@]}" 2>&1 | grep -E 'request [0-9]+/|pending_texts|"status": "(done|checkpoint_after)"' | cut -c60-260
cp runs/resume_demo/checkpoint_after.json evals/resume/checkpoint_after.json
echo "### $(date '+%F %T')  STATE AFTER RESUME"
.venv/bin/python - <<'PY'
import json
b=set(json.load(open('evals/resume/checkpoint_before.json'))['completed_ids']); a=set(json.load(open('evals/resume/checkpoint_after.json'))['completed_ids'])
calls=[json.loads(l) for l in open('runs/resume_demo/calls.jsonl')]
en=[c for c in calls if c['role']=='enrich']
re=[c for c in en if c['phase']=='resume']
print(f"    completed before: {len(b)}  after: {len(a)}  new: {len(a-b)}  before ⊂ after: {b < a}")
print(f"    enrich calls initial: {sum(c['phase']=='initial' for c in en)}  resume: {len(re)}")
print(f"    already-completed IDs re-sent during resume: {sum(len(b & set(c['review_ids'])) for c in re)}")
PY
