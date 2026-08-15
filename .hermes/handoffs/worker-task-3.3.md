# Worker handoff — Phase 3 Task 3.3 (local semantic-linkage spike)

## Scope completed

Bounded SPIKE only, per plan lines 365-381. Read AGENTS.md first; worked at commit
318e544 in the shared worktree. No production code written or modified; no dependency
added to the repo; nothing staged/committed/pushed; no runtime artifacts touched.

### Verdict

**PARTIAL** for the literal sentence-transformers objective — `sentence_transformers` is
not installed on this host and installing it would pull torch (>200MB wheel, over the
spike's hard budget), so that library was never exercised and no numbers are claimed for
it. **The underlying objective is VALIDATED via the measured alternative**: fastembed
0.8.0 (ONNX, no torch) running `BAAI/bge-small-en-v1.5` — the exact model the prior
roadmap contemplated. Lexical TF-IDF fallbacks were also measured and INVALIDATED as a
primary linkage signal.

### Headline measurements (fastembed BGE-small-en-v1.5, context mode)

- Precision 0.917 / Recall 1.000 at t=0.60; precision 1.000 / recall 0.909 at t=0.65.
  All 11 related pairs >= 0.639 vs max unrelated 0.619 — separation exists but the gap
  is 0.02 wide on 22 pairs; do not ship a hard-coded threshold from this alone.
- Canonical `x402` <-> `agentic payments`: context cosine 0.667 (label-only 0.482 —
  embed contexts, not bare labels).
- Warm model load 0.54s; encode ~23ms/text; cold load 7.79s incl. download.
- Peak Python working set 265MB absolute (Windows K32GetProcessMemoryInfo incl. native
  ONNX allocations); model cache 64.1MB; largest single download 13.8MB — all under the
  200MB cap; trivial on the 16GB host.
- Lexical baselines: word TF-IDF best F1 0.400 (canonical pair cos 0.021, missed);
  word+char3-5 best F1 0.286. Not viable as primary linkage.

## Files created (all spike/fixture scope, none imported by production)

- `tmp/spikes/topic_family_embeddings.py` — the spike script (probes
  sentence_transformers -> fastembed -> lexical TF-IDF; measures P/R sweep, load/encode
  time, peak working set, cache size; writes results JSON). Re-runnable:
  `tmp/spikes/.venv-embed/Scripts/python.exe tmp/spikes/topic_family_embeddings.py`
- `tests/fixtures/discovery/topic_relationship_labels.json` — 15 topics, 22 labeled
  pairs (11 related / 11 unrelated) with lexically-clean contexts, lexical traps
  ("shortage" in both ozempic and gpu_shortage contexts; "subagents" vs "agonists"),
  and the required canonical x402 <-> agentic_payments pair. Labeled by this worker;
  owner should spot-check before it becomes a regression fixture.
- `tmp/spikes/topic_family_embeddings_results.json` — machine-readable results
  (per-pair cosines, full sweep).
- `tmp/spikes/topic-family-embeddings-result.md` — the full result doc: VERDICT
  (PARTIAL sentence-transformer / VALIDATED objective via fastembed), actual command
  output (cold + warm runs), constraints, next actions.
- `tmp/spikes/.venv-embed/` — throwaway spike venv (106MB, fastembed + onnxruntime;
  NOT the repo venv) and `tmp/spikes/model_cache/` (65MB model). Safe to delete; delete
  only via `tmp/` paths, they are untracked.
- This handoff.

## Exact verification

Real runs (repo `.venv` python first — proved both embedding libraries absent and
exercised the lexical fallback — then the spike venv):

```text
> .venv/Scripts/python.exe tmp/spikes/topic_family_embeddings.py
[sentence_transformers] UNAVAILABLE: ImportError ... (repo venv)
[fastembed] UNAVAILABLE: ImportError: No module named 'fastembed' (repo venv)
[lexical_tfidf_word+char3-5_purepython] best-F1=0.286 ... canonical pair cos=0.026

> tmp/spikes/.venv-embed/Scripts/python.exe tmp/spikes/topic_family_embeddings.py
[fastembed] model=BAAI/bge-small-en-v1.5 load=0.54s encode=0.68s
  context-mode  best-F1=0.957 @ t=0.6  P=0.917 R=1.000
  canonical pair context cosine=0.6672 label cosine=0.4824
  peak working-set delta=247.8MB absolute=265.2MB  cache=64.1MB
```

`git status`: only the files above added under `tmp/` and
`tests/fixtures/discovery/`; `git diff --cached` empty (0 staged). Other workers'
in-flight files (topic_families/promotion tests, dashboard edits, explore_read_model)
were present in the worktree and untouched.

## Notes for the next worker / owner

1. Dependency decision now unblocked: fastembed + pinned bge-small-en-v1.5 is viable
   locally (all caps met). sentence-transformers is NOT viable on this host under a
   200MB cap (torch). Railway note: model needs a cache volume or baked-in weights;
   cold path costs ~8s + 64MB download per fresh deploy.
2. Threshold guidance from this spike: auto-link ~0.62-0.65; gray zone ~0.50-0.62 to
   Task 3.4 adjudication; <0.50 unrelated. Re-validate on a larger fixture (~100+
   real Explore pairs) before hardcoding — 22 pairs proves viability, not constants.
3. The fixture intentionally withholds paired-topic vocabulary from contexts; keep that
   invariant if extending it, or lexical shortcuts will inflate scores.
4. Per plan line 372, spike code must not ship into production paths; if Task 3.4/3.5
   adopt embeddings, implement fresh in `social_scraper/discovery/` with fastembed as
   an explicit pinned dependency.
