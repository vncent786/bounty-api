# Worker Task 2.1 handoff

## Status
GREEN. Added canonical engagement fields and field-level provenance while preserving legacy `collects`, nullable missing values, timestamps, and conversation parent/depth behavior.

## Production files changed
- `social_scraper/base.py`
- `social_scraper/conversations/models.py`
- `social_scraper/conversations/normalize.py`
- `social_scraper/broker.py`
- `social_scraper/connectors/douyin.py`
- `social_scraper/connectors/douyin_playwright.py`
- `social_scraper/connectors/tiktok.py`
- `social_scraper/connectors/tiktok_playwright.py`
- `social_scraper/connectors/xhs_playwright.py`

## Focused test result
Command:
`python -m pytest tests/conversations/test_models_and_normalize.py tests/test_social_item_engagement.py tests/connectors/test_collect_count_bookmarks.py tests/test_source_broker_failover.py -q`

Exact result: `58 passed in 1.49s`

`git diff --check` passed for all production files changed (Git emitted only Windows LF/CRLF conversion warnings).

## Blockers
None.
