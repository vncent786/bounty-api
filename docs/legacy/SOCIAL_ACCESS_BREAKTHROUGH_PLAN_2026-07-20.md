# Social Access Breakthrough Plan — TikTok / Douyin / XHS / RedNote

Date: 2026-07-20 SGT
Owner: Bond

## Ground rule
Do not waste time on brittle account-farm or anti-bot evasion as the core product. The durable objective is **licensed/paid/observable social signal access**, normalized into Bounty APIs. If a source is blocked, report it as blocked. No fake empty arrays.

## Current verified baseline
From existing probes in `SOCIAL_SCRAPING_STRATEGY.md`:
- TikTok free web search is blocked without `X-Bogus` / `A_Bogus` signatures.
- XHS/Douyin direct scraping from SG/US-style environments is weak because of geo/captcha/risk controls.
- Reddit, YouTube and Instagram-tag top-of-page are already working as free sources.

New checks run 2026-07-20:
- GitHub repos still active:
  - `Evil0ctal/Douyin_TikTok_Download_API`: 18,866 stars, updated 2026-07-20.
  - `JoeanAmier/XHS-Downloader`: 11,999 stars, updated 2026-07-20.
  - `xpzouying/xiaohongshu-mcp`: 14,744 stars, updated 2026-07-20.
- Apify store has real supply/demand:
  - TikTok Scraper: 100,651,616 total runs, 221,065 total users, 9,515,715 succeeded public runs in last 30 days.
  - TikTok Comments Scraper: 10,652,330 total runs, 36,286 users, 2,002,052 succeeded public runs in last 30 days.
  - TikTok Hashtag Scraper: 582,204 total runs, 15,315 users, 62,665 succeeded public runs in last 30 days.
  - XHS/RedNote Apify actors exist and show tens of thousands of 30-day successful public runs across top actors.
  - Douyin Apify actors exist, but quality is mixed. One major actor has low rating (~2.7), newer search-specific actors show better recent success but less history.

## Breakthrough routes, ranked

### Route 1 — Provider API aggregator: TikHub-first
Use TikHub as the first paid connector for TikTok, Douyin and XHS/RedNote.

Why:
- Existing strategy notes say approx $0.001/request, 50 free requests on signup, broad platform coverage.
- If Bounty sells `/social/trend-search` at $0.05/call, even a 5-provider fanout at $0.005 raw cost leaves plenty of gross margin.
- Operationally superior to reverse-engineering TikTok signatures.

What to build:
- `social_scraper/providers/tikhub.py` with normalized output shape.
- Health reporting: provider, endpoint, HTTP status, item count, failure reason.
- Feature flags by source: `tiktok`, `douyin`, `xhs`.
- Cache by `(source, query, endpoint, window)` to control spend.

Decision needed later: API key/payment funding. Do not block architecture on it.

### Route 2 — Build a TikHub-like owned access layer
Do not use Apify as a production dependency. It is a scraping marketplace competitor, and depending on it validates their platform instead of Bounty.

Instead, build our own narrow version of TikHub:
- One unified API surface for TikTok, Douyin, XHS/RedNote.
- Multiple backend access methods per platform: public web endpoints, mobile/app-style endpoints, official/creator/ad surfaces, cookies where explicitly provided, and paid upstream APIs only as temporary benchmarks.
- Normalized schema + source health + freshness + cost tracking.

Verified 2026-07-20 from TikHub OpenAPI:
- TikHub exposes 1,021 paths, version V5.3.2.
- TikTok: 165 paths, including `fetch_general_search`, `fetch_search_video`, `fetch_trending_searchwords`, app search, comments, creator-search insights.
- Douyin: 309 paths, including app hot search, hashtag videos, comments, billboard, Xingtu/creator-ranking surfaces.
- Xiaohongshu: 36 paths, including `search_notes`, `search_products`, hot list, note comments, user notes.

This proves the model works: not one magic scraper, but a large connector portfolio with normalized API packaging. Bounty should copy the architecture, not rent from marketplace competitors.

Implementation:
- Build `social_scraper/providers/owned_access.py` as the broker interface.
- Add first owned connectors from active OSS projects: XHS-Downloader, xiaohongshu-mcp, Douyin_TikTok_Download_API.
- Add TikHub connector as a **temporary bridge/benchmark**, not the final dependency.
- Cache aggressively by `(platform, query, page, time_filter)` to reduce upstream calls.
- Track method quality: item count, timestamp coverage, engagement coverage, comments availability, freshness, error rate.

### Route 3 — China SaaS data vendors / dashboard exports
For XHS/Douyin investment and marketing signals, do not limit ourselves to scrapers. China already has vertical data vendors.

Candidate vendor categories to evaluate:
- XHS analytics: 千瓜数据, 新红数据, 灰豚数据.
- Douyin/TikTok commerce analytics: 蝉妈妈, 飞瓜数据, FastMoss, Shoplus.
- Creator/influencer platforms: 巨量星图 / Ocean Engine ecosystem, TikTok Creator Marketplace equivalents.

Use case:
- If vendor dashboards allow CSV export/API/alerts, they may beat scraping for investment-grade trend velocity.
- Bounty can productize **normalized signal extraction**, not raw platform scraping.

### Route 4 — Official / semi-official surfaces
These are narrower but durable:
- TikTok Creative Center: trend discovery for hashtags/music/creatives. Good for marketing hooks, less complete for organic search.
- TikTok Research API / Commercial Content API: useful only if eligibility fits and scope matches. Likely not enough for Bounty's commercial trend-search alone.
- TikTok Ads / Business APIs: good for ad-level/commercial signals, not broad organic social listening.
- Douyin/Open Ocean Engine/巨量算数/星图 surfaces: worth checking for public trend/ad/creator intelligence.
- XHS business/蒲公英/creator-commerce surfaces: good for brand/creator/commerce signals.

Product implication: combine official surfaces with paid scrapers. Official = stable, scraper/API vendors = breadth.

### Route 5 — Open-source self-hosted connectors as cheap experiments
Use active GitHub projects only for low-volume, non-critical probes:
- `Evil0ctal/Douyin_TikTok_Download_API` for Douyin/TikTok URL/user parsing and possible self-hosted endpoints.
- `JoeanAmier/XHS-Downloader` for RedNote/XHS note/user/search extraction.
- `xpzouying/xiaohongshu-mcp` for agent-native XHS workflows.

Caveat:
- These often need cookies/manual sessions and can break. Treat as experimental sources with health flags, not production backbone.

### Route 6 — Human-in-the-loop capture for the hardest gaps
For high-value investment questions, let humans/operators feed URLs/screenshots/exports into Bounty rather than pretending everything can be automated.

Examples:
- A Telegram/Discord intake bot where Vincent or scouts drop TikTok/XHS/Douyin URLs.
- OCR/transcript pipeline from screenshots/screen recordings.
- Manual CSV export from vendor dashboards -> normalized Bounty signal pack.

This is ugly but robust. Markets pay for signal, not scraper purity.

### Route 7 — Cross-post and second-order proxy sources
Many TikTok/Douyin/XHS signals leak to easier platforms:
- YouTube Shorts, Instagram Reels, Reddit, Twitter/X, Bilibili, Kuaishou.
- E-commerce signals: TikTok Shop product rank, Shopee/Lazada sales proxies, Amazon review velocity.
- Search/news proxy: Google/Baidu indexed pages, creator link aggregators, brand forums.

Use these for confirmation and fallback, not as a replacement for direct platform data.

## Recommended product architecture
Build a source broker, not a platform scraper.

```text
/social/trend-search
  -> source broker
       -> free connectors: reddit, youtube, instagram-top
       -> owned connectors: xhs-downloader, xiaohongshu-mcp, douyin-tiktok-download-api, official/creator/ad surfaces
       -> temporary benchmark/bridge: tikhub
       -> vendor import connectors: csv/api/manual
  -> normalize
  -> dedupe
  -> classify: behavior_shift / pain_point / purchase_intent / creator_format / investment_signal
  -> return results + source health + freshness
```

Every response must include:
- `sources_requested`
- `sources_succeeded`
- `sources_failed`
- `paid_source_cost_estimate`
- `source_health[]`
- `items[]`
- `notes[]`

## Immediate 48-hour plan
1. Build owned source-broker interface and config-driven connectors. No Apify production dependency.
2. Add TikHub adapter as temporary bridge/benchmark, using OpenAPI-discovered endpoints once API key is available.
3. Add OSS connector experiments: XHS-Downloader, xiaohongshu-mcp, Douyin_TikTok_Download_API.
4. Dogfood-test with broad query families across multiple verticals (NOT scoped to one use case):
   - Health/habit: `dopamine detox`, `quit porn`, `screen time addiction`
   - Investing: brand/product keywords with long/short theses
   - China consumer: beauty, sportswear, travel, food/beverage in EN + CN
   - Marketing: hook formats, creator niches, objection language
   - Product research: complaint patterns, switch triggers, unmet needs
5. Compare item quality, not just count: URLs, timestamps, engagement, comments, source reliability, cost.

## Bottom line
The breakthrough is not defeating TikTok/XHS/Douyin directly. It is **owning the normalization + signal layer** while renting access from whoever has the best current pipe. Scraper war is a knife fight in a phone booth. Bounty should sell the cleaned intelligence, not the scars.

## Product positioning (important)
This is **general-purpose social data infrastructure**, not a Vincent-specific tool.

- The API surface must be neutral: any developer, marketer, investor, researcher, or agent can call it.
- Vincent's own use cases (investment, product building, marketing, social arb) are the **dogfood** that proves the product is grounded in real demand, not abstract vaporware.
- NEVER scope an endpoint to "investment-only" or "Cairn-only" or "Vincent-only". If a capability is useful for Vincent, it is almost certainly useful for others with the same problem, and many more he doesn't have.
- Build with Vincent's use case in mind (so the product stays real), but ship for everyone (so the product scales).

The thesis: social data access is broken for everyone, not just us. TikTok/XHS/Douyin lock up the most valuable consumer behavior signals on the planet. Whoever builds the cleanest, most reliable normalized access layer wins a category, not a tool.

