# Bounty Social Data API — Build Plan

Date: 2026-07-20
Status: APPROVED DIRECTION — horizontal social data infrastructure

## What we're building
A horizontal, platform-agnostic social data API. Any developer, agent, marketer, investor, or researcher can call it. Neutral inputs (keyword, platform, time_filter, region, sort). Normalized outputs. Source health on every response.

Not a scraper. Not a Vincent tool. **Social data infrastructure.**

---

## Architecture

```
Client (agent/dev/marketer/investor)
  │
  ▼
Bounty API (FastAPI on Railway)
  │
  ▼
Source Broker (the brain)
  ├── Connector: TikTok     [tikhub bridge → owned OSS]
  ├── Connector: Douyin     [tikhub bridge → owned OSS]
  ├── Connector: XHS        [tikhub bridge → owned OSS]
  ├── Connector: Instagram  [free Playwright tag page]
  ├── Connector: YouTube    [yt-dlp, free]
  ├── Connector: Reddit     [PullPush, free]
  │
  ▼
Normalizer (unified schema)
  │
  ▼
Response: items[] + source_health[] + meta
```

Every connector implements the same interface:
```python
class Connector:
    platform: str
    def search(self, keyword, count, time_filter, sort, region) -> ConnectorResult
    def get_post(self, post_id) -> ConnectorResult
    def get_comments(self, post_id, count) -> ConnectorResult
    def get_trending(self, region) -> ConnectorResult
    def health_check(self) -> HealthStatus
```

---

## API Surface (horizontal, neutral)

### Core endpoints

| Endpoint | Method | Description | Price |
|----------|--------|-------------|-------|
| `/social/search` | POST | Cross-platform keyword search. Input: keyword, platforms[], count, time_filter, sort, region. Returns normalized items from all requested platforms + per-source health. | $0.05 |
| `/social/trending` | GET | Trending keywords/hashtags/topics by platform and region. | $0.02 |
| `/social/post/{platform}/{id}` | GET | Full post detail: caption, media URLs, engagement, author, timestamp. | $0.01 |
| `/social/post/{platform}/{id}/comments` | GET | Comments thread with pagination. Sort: top, latest. | $0.02 |
| `/social/creator/{platform}/{handle}` | GET | Creator profile: followers, bio, recent posts, engagement stats. | $0.02 |
| `/social/creator/{platform}/{handle}/posts` | GET | Creator's post history with pagination. | $0.02 |
| `/social/sources/health` | GET | Live health status of all connectors. Free. | FREE |

### Normalized item schema
```json
{
  "platform": "tiktok|douyin|xiaohongshu|instagram|youtube|reddit",
  "post_id": "string",
  "url": "canonical URL",
  "author": {
    "username": "string",
    "display_name": "string",
    "profile_url": "string",
    "follower_count": null
  },
  "text": "caption/title/text content",
  "created_at": "ISO 8601 or null",
  "engagement": {
    "views": null,
    "likes": null,
    "comments": null,
    "shares": null,
    "collects": null
  },
  "media": {
    "type": "video|image|text|gallery",
    "thumbnail_url": null,
    "media_urls": []
  },
  "hashtags": [],
  "mentions": [],
  "language": null,
  "region": null,
  "raw": {}
}
```

### Source health schema (on every response)
```json
{
  "platform": "tiktok",
  "connector": "tikhub",
  "status": "ok|partial|error|skipped",
  "items_returned": 15,
  "items_requested": 20,
  "latency_ms": 845,
  "error": null,
  "fetched_at": "ISO 8601"
}
```

---

## Phased Build

### Phase 0: Foundation (today, no external deps)
**Goal:** Source broker skeleton + connector interface + normalizer.

Deliverables:
- [x] `social_scraper/broker.py` — source broker that routes requests to connectors by platform
- [x] `social_scraper/base.py` — abstract Connector interface + ConnectorResult + HealthStatus dataclasses
- [x] `social_scraper/connectors/douyin.py` — owned Douyin connector using Evil0ctal a_bogus signing
- [x] `social_scraper/connectors/tiktok.py` — owned TikTok connector using Evil0ctal X-Bogus signing
- [x] `apis/social_search_api.py` — `/social/search`, `/social/sources/health`, `/social/platforms`
- [x] Vendored Evil0ctal crawler engine under `crawlers/` (Apache 2.0)
- [ ] Refactor existing Reddit, YouTube, Instagram connectors to implement the new interface

Current live verification:
- Douyin signed search reaches platform: HTTP 200 JSON response from `/aweme/v1/web/general/search/single/`.
- TikTok signed search reaches platform: HTTP 200 from `/api/search/item/full/`.
- Both currently return no items from this machine because the vendored config has stale/missing session tokens/cookies. Douyin response explicitly says `search_nil_type=params_check`, `search_nil_item=invalid_app`. TikTok returns empty HTTP 200; Evil0ctal logs say current network cannot access TikTok token server and config needs proxy/cookie update.

Done = `POST /social/search {"keyword":"test","platforms":["tiktok","douyin"]}` returns normalized source health honestly. Next unlock is fresh cookies/proxy/token generation, not architecture.

### Phase 1: TikHub bridge (needs Vincent's signup — 50 free requests)
**Goal:** Validate TikTok/Douyin/XHS data shapes against real API responses.

Deliverables:
- [ ] Vincent signs up at tikhub.io (50 free requests, no card)
- [ ] `social_scraper/connectors/tikhub_tiktok.py`
- [ ] `social_scraper/connectors/tikhub_douyin.py`
- [ ] `social_scraper/connectors/tikhub_xhs.py`
- [ ] Map TikHub response fields → normalized schema
- [ ] Burn 50 free requests on diverse test queries across verticals
- [ ] Document which TikHub endpoints are worth keeping vs replacing with owned connectors

Done = `/social/search` returns results from 6 platforms (3 free + 3 TikHub).

**Decision gate after Phase 1:** Based on TikHub quality + cost, decide:
- (a) Keep TikHub as paid upstream (if cost math works at scale)
- (b) Replace with owned OSS connectors (if quality is sufficient)
- (c) Hybrid: TikHub for some platforms, owned for others

### Phase 2: Owned connectors (reduce dependency)
**Goal:** Replace TikHub bridge with owned access where viable.

Deliverables:
- [ ] Evaluate `Evil0ctal/Douyin_TikTok_Download_API` as self-hosted Douyin/TikTok connector
- [ ] Evaluate `JoeanAmier/XHS-Downloader` as self-hosted XHS connector
- [ ] Evaluate `xpzouying/xiaohongshu-mcp` for agent-native XHS access
- [ ] Add owned connectors behind the same interface
- [ ] A/B test: owned vs TikHub on same queries, compare item count + field coverage + latency + failure rate
- [ ] Configure broker to prefer owned, fall back to TikHub

Done = at least one platform served by owned connector with parity or better vs TikHub.

### Phase 3: Deep endpoints (post, comments, creator, trending)
**Goal:** Beyond search — full read API per platform.

Deliverables:
- [ ] `/social/post/{platform}/{id}` — post detail
- [ ] `/social/post/{platform}/{id}/comments` — comments
- [ ] `/social/creator/{platform}/{handle}` — creator profile
- [ ] `/social/creator/{platform}/{handle}/posts` — creator posts
- [ ] `/social/trending` — trending keywords/hashtags by platform
- [ ] Each endpoint works across all connected platforms

Done = full CRUD-style read API across TikTok, Douyin, XHS, Instagram, YouTube, Reddit.

### Phase 4: Production hardening
**Goal:** Reliable enough to charge money.

Deliverables:
- [ ] Caching layer (Redis or SQLite) — same query within TTL returns cached, saves upstream cost
- [ ] Rate limiting per source
- [ ] Circuit breaker per connector (auto-disable failing source, serve others)
- [ ] Cost tracking per request (upstream spend logged)
- [ ] x402 payment integration on paid endpoints
- [ ] `/social/sources/health` public dashboard
- [ ] llms.txt + MCP tool definitions updated
- [ ] Load test: 100 concurrent requests, measure p50/p95 latency

Done = bountyapi.com/social/search is live, paid, and returns reliable results.

### Phase 5: Scale and coverage
**Goal:** More platforms, more depth.

Future candidates:
- Bilibili, Kuaishou, Weibo, Zhihu (China ecosystem)
- Twitter/X, Threads, Bluesky (Western)
- Lemon8, Pinterest (lifestyle)
- Product review surfaces (Amazon, Shopee, Taobao)

---

## What needs Vincent's input

| Decision | Why I need you | Default if you say "go" |
|----------|---------------|------------------------|
| TikHub signup | External account + potential spend | I'll ask you to create the account; I build the connector against it |
| Phase 0 start | Green light to build | I start immediately |

Everything else I can execute autonomously and report back.

---

## Pricing model (horizontal)

| Tier | Endpoints | Price | Rationale |
|------|-----------|-------|-----------|
| Free | `/social/sources/health`, computed helpers | $0 | Discovery + trust |
| Search | `/social/search` | $0.05/call | Core value, multi-platform fanout |
| Deep data | post, comments, creator, trending | $0.01-$0.02/call | Cheaper per-call, higher volume |

Upstream cost estimate (TikHub bridge): ~$0.001-0.005 per platform per search. At $0.05/search with 3-6 platforms, gross margin is 70-90%.

Once owned connectors replace TikHub, upstream cost drops to near-zero (just hosting + proxy if needed). Margin approaches 95%+.

---

## Risk register

| Risk | Mitigation |
|------|-----------|
| TikHub bans/changes pricing | Phase 2 owned connectors reduce dependency; broker abstracts the upstream |
| Platform legal/ToS changes | Stay on public/observable data; don't store PII; provide source URLs not copyrighted media |
| OSS scrapers break | Connector interface + circuit breaker + health reporting; swap broken connector without downtime |
| Rate limiting | Cache + queue + multi-source fallback |
| Geo-blocks (China platforms) | Evaluate residential proxy need in Phase 2; document as known limitation if unsolved |
