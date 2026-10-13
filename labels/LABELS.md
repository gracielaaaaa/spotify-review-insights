# Shared labels, subtopics, and how we applied them

The common grading labels are used **exactly** (`GRADING_CONTRACT.md`). Subtopics are our optional extension. Every subtopic belongs to exactly one topic, so the model emits only the subtopic key, and code derives the topic (`pipeline/config.py: SUBTOPICS`).

## Topics (primary) and our subtopics
| topic | contract definition | subtopics (issue keys) |
|---|---|---|
| access | login, signup, password or account access | login_failure, account_hacked_or_locked, signup, password_reset, access_other |
| usability | navigation, controls, layout, queue/playlist management, ad interruptions | ads, ui_navigation, playlist_queue, shuffle_controls, redesign_layout, usability_other |
| playback | playback failure, crashes, lag, connection failures, audio quality, resource use | crash_wont_open, stops_skips, lag_slow, connectivity, audio_quality, battery_resources, device_integration, playback_other |
| downloads | downloading, saved music, offline listening, disappearing downloads | downloads_disappear, offline_not_working, download_limits, downloads_other |
| catalog | missing songs/artists, search/discovery, recommendations, lyrics availability | missing_content, search, recommendations, lyrics, podcasts, catalog_other |
| billing | price, charges, subscriptions, paywalls, premium entitlement; explicitly premium-only controls | price, unwanted_charges, premium_paywall, subscription_activation, refunds, billing_other |
| support | contacting support and the support response | no_response, unhelpful_support, support_other |
| other | general praise/criticism, unrelated content, no supported specific topic | general_criticism, general_praise, unrelated_or_unclear, other |

**Rules applied.**
- Choose the highest-severity supported problem. On a tie, choose the first mentioned.
- For a positive review, choose the first specific praised feature. General praise is `other`.
- A paid-plan mention alone is not billing. A subscription that fails to activate is billing. Music crashing for a paying user is playback.
- "Bad update" with no feature named is `other/general_criticism`.

## Intent (precedence order)
`cancellation` (explicitly leaving, uninstalling, cancelling, switching, or threatening to) → `complaint` (any negative experience, including mixed) → `request` (wants a change, no failure reported) → `praise` → `unclear` (meaningless, unrelated, bare boycott or political slogans).

## Severity
| level | meaning | our examples |
|---|---|---|
| 1 | no reported problem: praise, unclear text, or a pure feature request | "Good"; "Please add a sleep timer" |
| 2 | dislike, generic criticism, minor annoyance, cosmetic | "Worst update ever"; "Ads every two songs, annoying"; "Price hike is too much" |
| 3 | degraded or restricted function, some use remains | "Need premium to repeat a song"; "Keeps crashing sometimes"; "Lyrics removed"; "More ads than music" |
| 4 | core task clearly blocked | "Can't log in even after reset"; "App won't open"; "Downloads won't play offline at all" |
| 5 | explicit serious financial, privacy or data harm | "Charged twice and no refund"; "Account hacked"; "All my playlists were deleted" |

Stars, angry language and cancellation intent alone never raise severity. When impact is unclear, choose the lower level and set `needs_review`.

## Record schema (export)
`review_id, source_sha256, status, topic, intent, sentiment (-1..1), severity (1..5 int), entities (list of str), evidence_quote (exact source substring), needs_review (bool), label_config` plus `subtopic`, an optional `review_reason`, and `cache_source_id` (exact-text reuse). Quarantined records have `status:"quarantined"`, a `reason`, and `attempts` where applicable.

- **Entities:** extracted by **code**. These are explicit feature terms found in the text (`config.ENTITY_TERMS`), returned as they appear in the source.
- **Evidence quote:** for reviews of 160 characters or fewer it is the whole stripped text, set by code. For longer reviews it is a model-chosen span that code verifies as an exact substring. A case- or whitespace-only mismatch is repaired back to the exact source span. Otherwise the item gets one retry, then a first-sentence fallback flagged `needs_review`.

Full worked examples: [`prompts/enrich_v3.md`](../prompts/enrich_v3.md). They are illustrative or paraphrased from development data, never from the golden 50.
