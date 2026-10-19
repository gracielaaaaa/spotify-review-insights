# Golden-50 evaluation

Predictions: `runs/final_100k/records.jsonl`. 50 cases is a small diagnostic sample. Golden labels were assigned by the student on the printed/annotated worksheet evals/golden_labeling_sheet.pdf (review text only; no drafts or model output shown) and transcribed by code from evals/golden_labeling_sheet_completed.pdf. The student had seen an earlier AI-drafted set (evals/golden_50_draft.csv) during a previous review pass; agreement with those drafts is reported in human_vs_assistant_draft.

| Metric | Value |
|---|---|
| valid_predictions | 50 |
| ambiguous_cases | 24 |
| topic_agreement_exact | 0.86 |
| topic_agreement_accepted | 0.96 |
| intent_agreement_exact | 0.94 |
| intent_agreement_accepted | 0.98 |
| severity_exact | 0.86 |
| severity_mae | 0.14 |
| severity_mae_vs_accepted | 0.08 |
| sentiment_mae | 0.15 |
| sentiment_within_tolerance_0.3 | 0.88 |
| quote_substring_rate | 1.0 |
| topic_macro_f1_exact | 0.8427 |
| needs_review as predictor of ambiguity | precision 0.8182, recall 0.375 (9/24) |

## Topic confusion (rows = golden, columns = predicted)

| gold \ pred | access | usability | playback | downloads | catalog | billing | support | other |
|---|---|---|---|---|---|---|---|---|
| usability |  | 6 |  |  |  |  |  | 2 |
| playback |  |  | 4 |  |  |  |  |  |
| downloads |  |  |  | 1 |  |  |  |  |
| catalog |  |  |  |  | 4 |  |  | 3 |
| billing |  | 2 |  |  |  | 2 |  |  |
| other |  |  |  |  |  |  |  | 26 |

## Disagreements (topic, intent or severity differ from the main golden label)

| review_id | text | gold t/i/s | pred t/i/s | accepted? |
|---|---|---|---|---|
| 292ce26a | Staff must be full of mentally ill kool-aid heads. Banning conservativ | catalog/complaint/2 | other/unclear/1 | no |
| 991b6b3a | Ads were okay, but limited functionality..it's just too much | billing/complaint/3 | usability/complaint/2 | yes |
| b5ee7834 | Spotify is awesome and and very diverse :) | catalog/praise/1 | other/praise/1 | yes |
| 1fc8b08f | Duo Premium .... No ads at all! | usability/praise/1 | other/praise/1 | no |
| 723f07de | Update: I reached out to Spotify Support and with some help from Sarah | playback/complaint/4 | playback/complaint/3 | no |
| ac6cd66d | Lhat ng song nasa spotify | catalog/praise/1 | other/unclear/1 | no |
| 47f1406d | Very poor updates as we cannot playback the songs and most bad updates | playback/complaint/4 | playback/complaint/3 | yes |
| f4c8146a | Unconditional love, Stoli Canales | other/praise/1 | other/unclear/1 | yes |
| 46c0b49f | Are you out of your mind? This is dangerous! Listening to music in a n | usability/complaint/2 | usability/complaint/3 | no |
| 2aa566c6 | Nice app, very less advertising | usability/praise/1 | other/praise/1 | yes |
| 9034d675 | Worst app ever too much ad | usability/complaint/2 | usability/complaint/3 | no |
| d2f3874f | Too expensive and the free version is pretty much unusable with consta | billing/complaint/2 | usability/complaint/3 | no |
