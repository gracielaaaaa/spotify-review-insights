# Golden-50 evaluation

Predictions: `runs/final_100k/records.jsonl`. 50 cases is a small diagnostic sample. Golden labels were AI-drafted (claude-opus-5-5) and human-reviewed; the reviewer accepted all drafts unchanged, so agreement partly measures model-vs-model consistency.

| Metric | Value |
|---|---|
| valid_predictions | 50 |
| ambiguous_cases | 20 |
| topic_agreement_exact | 0.9 |
| topic_agreement_accepted | 0.98 |
| intent_agreement_exact | 0.96 |
| intent_agreement_accepted | 0.98 |
| severity_exact | 0.88 |
| severity_mae | 0.12 |
| severity_mae_vs_accepted | 0.02 |
| sentiment_mae | 0.128 |
| sentiment_within_tolerance_0.3 | 0.94 |
| quote_substring_rate | 1.0 |
| topic_macro_f1_exact | 0.9214 |
| needs_review as predictor of ambiguity | precision 0.6364, recall 0.35 (7/20) |

## Topic confusion (rows = golden, columns = predicted)

| gold \ pred | access | usability | playback | downloads | catalog | billing | support | other |
|---|---|---|---|---|---|---|---|---|
| usability |  | 8 |  |  |  |  |  | 2 |
| playback |  |  | 4 |  |  |  |  |  |
| downloads |  |  |  | 1 |  |  |  |  |
| catalog |  |  |  |  | 4 |  |  | 3 |
| billing |  |  |  |  |  | 2 |  |  |
| other |  |  |  |  |  |  |  | 26 |

## Disagreements (topic, intent or severity differ from the main golden label)

| review_id | text | gold t/i/s | pred t/i/s | accepted? |
|---|---|---|---|---|
| 292ce26a | Staff must be full of mentally ill kool-aid heads. Banning conservativ | catalog/complaint/2 | other/unclear/1 | yes |
| 991b6b3a | Ads were okay, but limited functionality..it's just too much | usability/complaint/3 | usability/complaint/2 | yes |
| b5ee7834 | Spotify is awesome and and very diverse :) | catalog/praise/1 | other/praise/1 | yes |
| 1fc8b08f | Duo Premium .... No ads at all! | usability/praise/1 | other/praise/1 | no |
| 723f07de | Update: I reached out to Spotify Support and with some help from Sarah | playback/complaint/4 | playback/complaint/3 | yes |
| ac6cd66d | Lhat ng song nasa spotify | catalog/praise/1 | other/unclear/1 | no |
| 47f1406d | Very poor updates as we cannot playback the songs and most bad updates | playback/complaint/4 | playback/complaint/3 | yes |
| 46c0b49f | Are you out of your mind? This is dangerous! Listening to music in a n | usability/complaint/2 | usability/complaint/3 | yes |
| 2aa566c6 | Nice app, very less advertising | usability/praise/1 | other/praise/1 | yes |
| 9034d675 | Worst app ever too much ad | usability/complaint/2 | usability/complaint/3 | no |
