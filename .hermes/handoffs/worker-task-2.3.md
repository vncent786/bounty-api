# Worker handoff — Phase 2 Task 2.3

## Scope completed

Implemented a narrow deterministic engagement-baseline increment without editing Task 2.1 connector/model files or `social_scraper/discovery/handlers.py`.

### Baseline behavior

- Added platform- and content-age-matched empirical percentiles.
- Added creator-size matching when creator followers are observed.
- Uses an inclusive empirical CDF on observed comparable values only.
- Missing/invalid counts remain null and never enter a metric sample as zero; an observed integer zero remains zero.
- Retains raw source-native counts and reports source alias selection (`likes`/`upvotes`, `comments`/`replies`, `reposts`/`shares`).
- Returns nullable like/comment/repost/view/creator-adjusted percentiles, conservative `baseline_sample_size`, and `supported|weak|unavailable` status.
- `is_supported_outlier()` always rejects weak/unavailable baselines and also checks the selected feature's sample count.
- Explicit policy is inspectable in code/result metadata:
  - age buckets: under 6h, 6–24h, 1–3d, 3–7d, 7–30d, 30d+
  - creator buckets: under 1k, 1k–10k, 10k–100k, 100k–1m, 1m+
  - default trailing period: 90 days
  - default supported floor: 20 observations
- Creator-adjusted percentile compares observed engagement per follower only among records with the exact same metric-availability signature, so absent components are not treated as zero.

## Files changed

- Created `social_scraper/analysis/engagement.py`
- Modified `social_scraper/discovery/storage.py`
- Created `tests/analysis/test_engagement.py`
- Created this handoff: `.hermes/handoffs/worker-task-2.3.md`

## Persistence / migration notes

- Added only the new `engagement_baseline_observations` table and its dimensional index; no existing table is rebuilt or rewritten.
- Added migration marker `2026_08_15_engagement_baseline_observations`.
- Added `DiscoveryStore.record_engagement_baseline_observation()` and `list_engagement_baseline_observations()`.
- Exact duplicate observations are idempotent. A conflicting reuse of platform/root/observed-at identity is rejected rather than overwriting evidence.
- The migration test uses a realistic populated discovery DB (candidate history, completed gate check, source-health and stored root record), removes only the new Task 2.3 table/marker to represent the immediate predecessor, reopens it, and verifies all legacy rows survive while the additive schema is installed.

## Exact verification

Focused tests only, as requested:

```text
python -m pytest tests/analysis/test_engagement.py -q
............                                                             [100%]
12 passed in 0.63s
```

```text
python -m pytest tests/analysis/test_engagement.py tests/discovery/test_candidate_history.py -q
.................                                                        [100%]
17 passed in 1.22s
```

Additional syntax/whitespace checks:

- `python -m compileall -q social_scraper/analysis/engagement.py social_scraper/discovery/storage.py tests/analysis/test_engagement.py` — passed with no output.
- `git diff --check -- social_scraper/discovery/storage.py` — passed with no output.

## Blockers / cautions

- No blockers.
- No staging, commit, push, checkout, restore, or runtime DB/tmp/image/log changes were performed.
- The shared worktree contains unrelated concurrent Task 2.1/2.2 and pre-existing changes; they were not modified by this task.
