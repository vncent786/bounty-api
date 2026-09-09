# Bounty social source-health debugging

Use this when X, TikTok, Instagram, Reddit or YouTube is reported as rate-limited, unhealthy, empty, partial, or timed out.

## Rule zero

`Unhealthy` is an outcome, not a diagnosis. Do not retry or change providers until the failed layer is identified.

Keep these states separate:

1. `profile_concurrency`: another process owns the browser/account lock.
2. `local_budget`: Bounty's account/page/day cap is exhausted.
3. `upstream_rate_limit`: the provider returned a verified HTTP 429 or source-native rate-limit code.
4. `credential_or_session`: missing/expired cookie, token, checkpoint, or session-origin mismatch.
5. `operation_timeout`: the bounded connector or subprocess exceeded its real deadline.
6. `parser_or_response_shape`: the response arrived but was empty, changed, challenged, or unparsable.
7. `candidate_no_match`: a healthy route completed and the exact subject did not appear.
8. `thread_specific_gap`: search succeeded but one post's comments/replies could not be read.
9. `not_registered_on_this_worker`: the API/cloud plane intentionally lacks an owned residential route.

Never translate 400, timeout, busy lock, parser failure, candidate no-match, or unreadable thread into `rate limited`.

## First five checks

1. **Exact tree and runtime**
   - Verify branch/HEAD/worktree and that the active interpreter has connector dependencies.
   - Check `GIT_INDEX_FILE` before Git operations.
2. **Process ownership**
   - List active `run_rising_social`, `run_ghost_social`, TikTok, Instagram and Scweet processes.
   - Do not start another stateful collector while one owns the same profile/account.
3. **Local budget/cooldown**
   - Inspect X/other persisted account summaries without printing cookies or tokens.
   - `daily_budget_exhausted` is local budget; `429` is upstream rate limiting. They are not interchangeable.
4. **Production-shaped canary**
   - X: `nike`, Latest, half-year, bounded search plus a readable conversation.
   - TikTok: `nike`, Latest, half-year, authenticated search plus comments from a bounded ranked set of comment-bearing posts.
   - Instagram: `#nike` through the authenticated keyword/hashtag route plus depth checked separately.
   - Reddit: `running shoes` through the owned mobile route with explicit discovered/named communities.
   - YouTube: `iphone` through yt-dlp plus bounded comments.
5. **Candidate unit**
   - Only after the canary passes, rerun the failed candidate/platform/query/thread unit.
   - A healthy canary plus explicit candidate empty is completed coverage, not source failure.

## Platform diagnosis

### X

- Read `eligible_accounts`, blocked state, daily requests and waited/cooldown fields without revealing credentials.
- `x_daily_budget_exhausted` means Bounty's cap; `x_rate_limited` means upstream rate limiting.
- Preserve `conversation_id` and parent ID through broker serialization.
- Use the stored conversation ID for thread search.
- If `conversation_id:` search returns only the original while the post reports replies, classify that post as a thread-specific gap. Use the owned direct-post fallback for a bounded linked sample; zero returned replies remains unresolved when the post reports a positive reply count.

### TikTok

- A transient empty API response is not a valid empty result unless the page/API explicitly reports no results and the production canary is healthy.
- Profile acquisition must be cancellation-safe. A cancelled waiter must never acquire the lock later.
- Profile contention is `tiktok_profile_busy`, not `connector_timeout` or rate limiting.
- Try up to five ranked comment-bearing canary posts before calling depth unavailable. One unreadable post is not a platform outage.

### Instagram

- Cookie presence alone does not prove source health.
- Validate authentication and the actual `#nike` search route on the session-origin egress.
- Distinguish `ig_credentials_missing`, `ig_session_expired`, `ig_profile_busy`, `ig_rate_limited`, `ig_canary_no_results`, and other HTTP errors.
- A completed exact no-match is not connector failure.

### Reddit

- The owned mobile route is scoped to discovered or named communities, not platform-wide.
- If every requested community succeeds with no transport/auth error, the route is healthy even when zero or fewer-than-requested posts match.
- Preserve requested/successful/failed community lists and an explicit bounded-scope note.
- A failing PullPush/Arctic/Brave fallback does not make Reddit unavailable when the selected owned route completes.

### YouTube

- Use `iphone` as the known-positive search canary, not the vague word `test`.
- Preserve `youtube_timeout` and `youtube_process_error`; never swallow subprocess failure into an empty/partial result.
- The connector owns a real subprocess timeout so broker cancellation does not leave yt-dlp running unseen.
- Disabled comments, explicit empty comments, bounded partial comments and source failure are separate.

## Qualification gate

Before declaring the repair durable:

```bash
python scripts/qualify_social_source_health.py \
  --cycles 2 \
  --output artifacts/source-health/social-source-health-qualification-YYYY-MM-DD.json
```

Required result:

- two complete cycles;
- all five production-shaped search+depth canaries healthy;
- one additional bounded known-positive search per platform complete in each cycle;
- no profile-busy, auth/session, local-budget, rate-limit, timeout or parser failures;
- `monitor_or_dashboard_written=false` during qualification.

Run focused tests:

```bash
python -m pytest \
  tests/test_owned_worker_lock.py \
  tests/test_source_broker_failover.py \
  tests/test_reddit_arctic.py \
  tests/test_reddit_mobile.py \
  tests/connectors/test_x_official.py \
  tests/connectors/test_youtube_threads.py \
  tests/test_social_collection_api.py \
  tests/test_social_source_health_qualification.py -q
```

Then run the full repository suite on the exact release tree and obtain independent review.

## Release behavior

- Publish only completed source observations.
- Failed attempts remain audit-only and cannot overwrite the last success.
- Source health must list all five required platforms, including explicit `not_registered_on_this_worker` rows.
- Do not publish connector logs, raw dictionaries, hashes, paths, cookies, tokens, or internal retry details to the investor message.
- User-facing wording: what was observed, what changed, what it means, coverage limit, action.

## Incident lessons from 4–8 September 2026

- Social local budgets were not exhausted.
- TikTok failures were transient empty/depth/timeout events plus a duplicate-worker hazard; no persisted evidence proved upstream rate limiting.
- X search remained healthy while specific threads were unreadable.
- Instagram had real session/search-route failures and later recovered.
- Reddit scoped success was mislabeled partial because yield was below the requested count.
- YouTube had one timeout; process errors were previously swallowed.
- Google Trends was a separate request-shape defect (`gprop="web"` caused HTTP 400), not proof that social platforms were rate-limited.
