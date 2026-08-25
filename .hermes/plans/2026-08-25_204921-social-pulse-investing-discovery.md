# Social Pulse Investing Discovery Release

> **For Hermes:** Implement the smallest verified social-first discovery loop. Do not broaden into every platform feature or long-horizon analytics during this release.

**Goal:** Let an investor begin with emerging social conversations, see a short citation-backed list of subjects worth considering, and hand any subject to Classic Bounty for deeper research.

**Architecture:** A central scheduler runs versioned behavioural probes and broad community feeds through existing connectors, persists immutable source evidence and source outcomes, then runs one bounded citation-gated candidate extraction. Customer reads are database-only. Social Pulse remains a peer lane beside Google search attention; it does not mutate the canonical evidence corpus or hide source gaps.

**Primary loop:** Open `/dashboard` → scan `Social conversations` → read why a subject surfaced and inspect source links → choose Investigate → Classic Bounty opens with the subject prefilled.

## Frozen user-visible outcomes

1. A new **Social conversations** lane appears before Google search attention.
2. It shows at most 12 extracted subjects, not a firehose of posts.
3. Every subject states observed behaviour, independent voice count, platform coverage, why it may merit attention, and openable evidence links.
4. Source coverage is honest. Reddit/YouTube/TikTok/Instagram/X may each be complete, empty, unavailable, or failed. Missing platforms do not erase supported leads.
5. Investigate opens `/dashboard/classic?topic=...` with the subject prefilled.

## Collection scope

- Reddit broad feeds from a versioned set of five consumer/technology/problem communities, using the owned current connector with a blank keyword.
- YouTube bounded behavioural searches, sorted with source-native views/recency.
- TikTok and Instagram bounded behavioural/product-discovery searches through the registered broker routes.
- X bounded behavioural search through the registered route.
- Versioned probes describe purchase/adoption, switching, shortages, rejection, and pain points. They are discovery scopes, not factual claims.
- All collection is centrally scheduled. No customer API triggers upstream collection.

## Candidate extraction

- Input is capped and balanced per platform.
- Every evidence record receives an immutable local ID and must have a public URL.
- The LLM may propose labels/summaries only from supplied records and must return evidence IDs.
- Unknown IDs, uncitable records, unsupported behaviour labels, and empty candidate labels are rejected.
- A lead may be single-platform but must be labeled as such. Cross-platform and repeated-voice support are displayed as evidence, not converted into an opaque score.
- LLM/provider/parser failure is `analysis_unavailable`, not `no social signal`.

## Explicitly excluded

- Company/ticker mapping
- Materiality estimates
- Sell-side coverage
- Building Quietly history
- Automated investment recommendations
- Claims that all five platforms are reliable
- A synthetic cross-platform engagement score

## Files

- Create `social_scraper/investing/social_pulse.py`
- Create `tests/investing/test_social_pulse.py`
- Modify `apis/dashboard_api.py` for persisted GET only
- Modify `apis/scheduler.py` for central scheduled collection
- Modify `public/investing-dashboard.js`, `public/investing-dashboard.css`, and `apis/investing_dashboard_page.py`
- Modify product tests and state documentation

## Release gates

1. Controlled connector fixtures preserve all five source outcomes and cited evidence.
2. Invalid citation IDs and unsupported fields are rejected.
3. Failed latest attempt does not hide or freshen prior successful Social Pulse data.
4. Real bounded collection returns actual source evidence from at least one platform; unavailable platforms remain explicit.
5. Customer reads trigger zero upstream calls.
6. Desktop and mobile have zero horizontal overflow and no uncaught browser errors.
7. Classic handoff prefill works.
8. Full `tests/` suite passes and independent release review returns PASS.
