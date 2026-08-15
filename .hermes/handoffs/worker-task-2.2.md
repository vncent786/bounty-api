# Worker handoff — Phase 2 Task 2.2

## Status

GREEN. Implemented deterministic root-observation deduplication and separate repost/copy propagation accounting as a narrow increment. No staging, commit, push, checkout, restore, or runtime artifact changes were performed.

## Files changed

- Created `social_scraper/conversations/deduplication.py`
- Created `social_scraper/conversations/propagation.py`
- Modified `social_scraper/discovery/handlers.py` only at root collection/result transfer boundaries
- Created `tests/conversations/test_deduplication.py`
- Created `tests/conversations/test_propagation.py`
- Created this handoff: `.hermes/handoffs/worker-task-2.2.md`

## Implemented behavior

### Root deduplication

- Identity precedence is platform + external/post ID, canonical URL, then exact NFKC/case/whitespace-normalized title/text hash.
- Stable IDs and canonical URLs bridge repeated connector/query observations.
- Exact content hash is a fallback when a record lacks both stronger identities. Separately addressable exact copies remain unique roots so propagation evidence is not erased.
- No fuzzy or near-duplicate threshold/grouping was added.
- Every raw input observation is retained in the immutable deduplication group manifest; handler roots also carry `_collection_provenance` for all matched observations.
- Unidentifiable/empty roots are retained independently rather than dropped or assigned fabricated identities.
- Input order deterministically selects the representative and inputs are not mutated.

### Propagation

- Native `is_repost` / `repost_of_external_id` relationships, including chains and orphan references, create propagation clusters.
- Exact-content copies with distinct stable source identities create `exact_content` propagation clusters; near-duplicates do not.
- A source-reported positive `reposts` count is retained as reach and can identify a propagation cluster without inventing individual roots/authors.
- Repost/copy authors are excluded from independent-author corroboration. Author identity is platform-scoped; unknown authors are not invented.
- Required output fields are separate: `unique_root_count`, `independent_author_count`, `repost_cluster_count`, `largest_repost_cluster_size`, and nullable-coverage-aware `propagation_reach`.
- Reach totals include only observed nonnegative integer metrics and report `observed_root_count`; unsupported metrics remain `None`, never zero-filled.

### Handler integration

- Root probe deduplicates before returning/stashing roots, so `records_returned`, deep-read selection, and horizontal evidence use unique roots.
- `_root_deduplication` retains the full observation/provenance manifest.
- `_root_summary` retains the required count/reach distinction.
- `build_handlers()` transfers both manifests into shared collection keys `:root_deduplication` and `:root_summary`.

## Exact verification

Test-first RED was confirmed before implementation: both new modules failed import during collection (`2 errors`).

Final focused plus directly affected existing handler command:

```text
python -m pytest tests/conversations/test_deduplication.py tests/conversations/test_propagation.py tests/discovery/test_execution.py tests/discovery/test_explore_cost_path.py::test_explicit_research_run_still_hydrates_threads_and_analyzes -q
........................                                                 [100%]
24 passed in 8.98s
```

Additional checks:

- `python -m compileall -q social_scraper/conversations/deduplication.py social_scraper/conversations/propagation.py social_scraper/discovery/handlers.py tests/conversations/test_deduplication.py tests/conversations/test_propagation.py` — passed with no output.
- `git diff --check -- social_scraper/discovery/handlers.py` — passed; Git emitted only the existing Windows LF/CRLF conversion warning. New files passed syntax checks above.

## Integration notes / blockers

- No blockers for Task 2.2.
- The pure `deduplicate_roots()` API accepts an aggregate of arbitrary member-query results. The current root handler applies it to each broker result. Phase 3 Task 3.5 should aggregate topic-family member-query roots first, then call this primitive once; family collection does not exist yet.
- Distinct stable IDs with exact copied content intentionally survive deduplication and are grouped by propagation. This prevents repost/copy evidence from disappearing while still using exact content hash as the identity fallback for otherwise unidentifiable observations.
- Shared-worktree Task 2.1 and Task 2.3 files, runtime DB/tmp/image/log artifacts, and unrelated changes were not modified by this worker.
