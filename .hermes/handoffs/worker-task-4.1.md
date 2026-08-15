# Worker handoff — Phase 4 Task 4.1

## Scope completed

Configurable, versioned universal eligibility and nine explicit route
evaluations with inspectable reasons/components/config, implemented test-first
at commit 318e544. No LLM, no storage/API edits, no scheduler/dashboard
changes (those are Tasks 4.2/4.3).

## Boolean model (exactly as plan lines 472-478)

- `eligible = all(universal gates)` — five gates from the plan's default YAML:
  minimum_unique_roots=3, minimum_independent_authors=2, require_usable_text,
  require_source_health, reject_duplicate_only_support. All defaults are
  explicit calibration candidates.
- `automatically_promoted = eligible and any(automatic route passed)`.
  Automatic routes: daily_search_persistence, search_trajectory_expansion,
  cross_platform_breadth, age_adjusted_engagement_breakout,
  creator_breadth_expansion, conversation_depth_trigger,
  personal_radar_recurrence.
- Passing components from different routes never combine; each route passes
  only on its own whole conditions (tested in
  `test_no_mixing_of_components_across_routes`).

## Route semantics implemented

1. `daily_search_persistence` — present in >=2 of last 3 comparable daily
   snapshots; gaps stay gaps (counted as neither present nor absent, recorded
   in components and limitations); minimal root-social evidence after the
   weekly sweep (>=1 unique root and >=1 independent author, configurable).
2. `search_trajectory_expansion` — persistence component PLUS at least one
   strictly observed increase in own-series volume/growth, related-query
   breadth, or regional breadth. Missing previous/current values yield
   observed=None and can never pass that component.
3. `cross_platform_breadth` — >=2 healthy platforms with hits, >=3 independent
   authors, >=3 distinct roots. Uses the Task 2.2 dedup-adjusted root summary;
   components record repost_cluster_count and raw_counts_excluded=true.
4. `age_adjusted_engagement_breakout` — two independent supported roots >=
   percentile_threshold (95.0), OR one extreme (>=99.0) plus an independent
   corroborating root. Roots with weak/unavailable baselines or
   repost_cluster_id never count. Thresholds in config.
5. `creator_breadth_expansion` — >=3 current independent creators AND material
   increase vs the family's own previous comparable observation
   (current > previous AND current >= previous * 1.5; multiple configurable).
   Missing previous observation = explicit unknown, cannot pass.
6. `conversation_depth_trigger` — comment/reply percentile >=90 on a supported
   platform/age baseline, OR >=2 independent roots with active discussions.
   Weak/unavailable baselines cannot pass (Task 2.3 rule).
7. `personal_radar_recurrence` — saved-radar match; applies configured
   radar_floor_overrides (only the two minimum floors, may only lower them,
   default 1/1) to eligibility while source limitations stay visible in
   limitations[].
8. `manual_promotion` — explicit_request AND within_explicit_budget. Mode
   "manual"; a manual pass claims the promotion and is NEVER attributed to
   automation (automatically_promoted=false; passing automatic routes stay
   listed for audit).
9. `exploration_allocation` — not a per-candidate pass. Per-candidate
   evaluation returns passed=false with status pending_cohort_selection.
   `select_exploration_sample(evaluations, policy)` samples only eligible,
   non-promoted (not automatic, not manual) candidates under a fixed
   per_run_cap (default 3) with category/region stratification:
   deterministic round-robin over sorted (category, region) strata,
   candidate_id order within a stratum; inputs never mutated; selected
   copies get exploration route passed=true + promotion_mode "exploration".

## Gaps stay gaps

- Any missing evidence produces status "unknown" (or "unverified" for the
  duplicate-only rejection gate) with observed=None — never zero-filled,
  never fabricated. Minimum gates fail closed on unknown; unknowns are
  surfaced in limitations[].

## Output shape (inspectable)

`evaluate_promotion(evidence, policy=None)` returns: candidate_id,
policy_version, config (full policy snapshot incl. version), eligible,
eligibility {radar_matched, floor_overrides_applied, gates[]},
routes[] (9 entries: route, mode, passed, components[{name, passed, observed,
required, status, ...}]), automatic_routes_passed, automatically_promoted,
promotion_mode (automatic|manual|exploration|none), reasons[], limitations[],
stratum {category, region}.

`PromotionPolicy(config=None, version=POLICY_VERSION)` — immutable; partial
config dicts merge over defaults; rejects unknown sections/keys, bad types,
percentiles outside 0-100, extreme<regular thresholds, required_snapshots>
comparable_window, material multiple <=1.0, radar overrides that raise floors
or touch non-floor gates. POLICY_VERSION = "2026-08-15.1" (Task 4.4 rule:
policy changes create a new immutable version — bump this constant).

Expected evidence fields are documented in the module docstring; engagement
roots consume Task 2.3 shapes (engagement_percentile, baseline_status
supported|weak|unavailable), root summaries consume Task 2.2 shapes.

## Files changed

- Created `social_scraper/discovery/promotion.py`
- Modified `social_scraper/discovery/prioritization.py` (minimal, backward
  compatible): `priority_components()` now prefers an attached
  `candidate["promotion"]` mapping ({eligible, promotion_mode}) for the
  eligible/manual_promoted flags via `_promotion_overrides()`; without the
  key, behavior is byte-identical to before.
- Created `tests/discovery/test_promotion.py`
- Created this handoff: `.hermes/handoffs/worker-task-4.1.md`

## Exact verification

Test-first RED was confirmed before implementation:

```text
python -m pytest tests/discovery/test_promotion.py -q
E   ModuleNotFoundError: No module named 'social_scraper.discovery.promotion'
1 error in 0.43s
```

Focused promotion + existing prioritization tests (staged scheduler is the
only existing prioritization consumer):

```text
python -m pytest tests/discovery/test_promotion.py tests/discovery/test_staged_scheduler.py -q
....................                                                       [100%]
20 passed in 1.91s
```

(test_promotion.py alone: 16 passed in 0.33s.)

Full discovery suite:

```text
python -m pytest tests/discovery/ -q
209 passed, 11 warnings in 35.82s
```

Additional checks: `python -m compileall -q` on all three files — clean;
`git diff --check -- social_scraper/discovery/prioritization.py` — clean
(only the pre-existing LF/CRLF warning).

## Blockers / cautions

- No blockers for Task 4.1.
- Shared-worktree note: while I worked, other workers' in-flight topic-family
  tests transiently failed (`DiscoveryStore.create_topic_family` missing)
  because their storage edits landed between my runs; by my final run the
  whole `tests/discovery/` suite was green (209 passed). My files do not
  import storage/topic-family code, so Task 4.1 is independent of that merge.
- `tests/discovery/test_radar_scheduler.py::test_radar_failure_does_not_stop_zone_loop`
  flaked once (asyncio timing) in an intermediate run and references no
  prioritization/promotion code; it passed in the final run. Pre-existing,
  not introduced by this task.
- Task 4.2 (persist route evaluations) should serialize the
  `evaluate_promotion` result as-is: it already carries policy version,
  config snapshot, gates, per-route components/outcomes, mode and
  limitations. Only input observation IDs are missing and belong to storage.
- Design decisions flagged for calibration review (all in config, none
  hard-coded): corroborating root for the extreme-outlier path requires a
  supported baseline but any observed percentile; "material" creator increase
  = 1.5x multiple; "several" active discussions = 2; radar floor overrides
  limited to the two minimum floors.
- No staging, commit, push, checkout, restore, or runtime DB/tmp artifact
  changes were performed; other workers' files were not touched.
