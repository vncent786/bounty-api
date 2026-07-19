# Social Scraping Strategy for Bounty / Cairn / Social Arb

Date: 2026-07-12

## Executive Summary

We do **not** need to create many accounts as the default path for social scraping.

Account creation is only one access strategy, and it is usually the most fragile one because every platform treats mass account creation as hostile. For a sustainable social data product, use a layered access model:

1. **Public/unauthenticated sources first** — no account risk, cheap, robust enough for trend discovery.
2. **Open-source protocol scrapers second** — GitHub libraries often reverse-engineer mobile/web endpoints better than we can alone.
3. **Paid scraping APIs/actors third** — Apify, TikHub, Bright Data, etc. when reliability matters more than cost.
4. **Owned accounts only for authenticated gaps** — comments behind login, deep pagination, creator dashboards, account-specific views.
5. **Account creation last** — only if buying/using accounts is cheaper than paid APIs and legally/operationally acceptable.

For Vincent's use cases:
- **Cairn marketing:** trend discovery, hooks, creators, comments, competing app narratives.
- **Social arb:** behavior shifts before news/analysts price them in.
- **Bounty API:** paid endpoints agents genuinely cannot assemble cheaply themselves.

The product should be a **social signal engine**, not a brittle account farm.

---

## Why Multiple Accounts Are Not the Core Requirement

Multiple accounts help when:
- A platform hard-requires login for data.
- You need very deep pagination at high volume.
- You need private-ish logged-in surfaces like following feeds, saved collections, or creator dashboards.
- Rate limits are account-scoped and not easily solved by provider APIs.

But most trend intelligence does **not** require that. For early social signal detection, we mostly need:
- Search results by keyword/hashtag
- Video/post metadata: title, caption, author, timestamp, URL
- Engagement stats: views, likes, comments, shares
- Top comments / comment sentiment
- Growth velocity across repeated snapshots
- Cross-platform confirmation

Those can usually be collected without owning many accounts, by combining public endpoints, reverse-engineered libraries, and selective paid APIs.

---

## Platform-by-Platform Access Plan

### TikTok

**STATUS (verified July 19, 2026 by running probes):**

- Evil0ctal demo API at `api.douyin.wtf`: **DEAD** (404 on docs, empty response on endpoints).
- Evil0ctal PyPI package: it's a **URL parser, not a keyword search**. You give it a video URL, it returns metadata. Useless for trend discovery.
- Playwright on `tiktok.com/search`: **BLOCKED**. TikTok serves an error page ("Something went wrong") to headless browsers. The XHR endpoint `/api/search/general/full/` is reachable but returns an empty body without the `X-Bogus` signature.
- TikTok's web API requires `X-Bogus` + `A_Bogus` algorithm signatures. Reverse-engineering them is fragile (TikTok changes the algorithm regularly) and high-effort.

**Working options for TikTok:**

1. **TikHub.io paid API** — $0.001/req, ~50 free requests on signup (no credit card), $2 with referral code. 1000+ endpoints across 16 platforms including TikTok, Douyin, Instagram, XHS, YouTube. This is the rational choice. Reselling at $0.05/call = 50x margin.
2. **Self-host Evil0ctal scraper** — requires manual browser cookie harvesting for Douyin, fragile against TikTok rate limits, breaks weekly. Bad ROI.
3. **Skip TikTok for v1** — 3 free sources (Reddit, YouTube, Instagram) cover most trend surfaces. Most fast-moving trends cross-post to Reels/Shorts within 24-48h anyway.

**Recommendation: TikHub.** Sign up, burn the 50 free requests to validate the full flow, then decide whether to fund it.

**Goal:** Discover fast-moving trends, hooks, creator videos, comments, and behavior shifts.

Recommended access stack:

1. **Open-source first:**
   - `Evil0ctal/Douyin_TikTok_Download_API` — 18.7K stars, updated 2026-07-12. Covers TikTok + Douyin + Kuaishou + Bilibili. Good candidate for self-hosted API layer.
   - `drawrowfly/tiktok-scraper` — 5.1K stars, updated 2026-07-11. Older but popular for user/hashtag/music/feed metadata.
   - `bellingcat/tiktok-hashtag-analysis` — 367 stars, useful analysis tooling for hashtag investigations.

2. **Browser fallback:**
   - Playwright search scraping with persistent cookies and health reporting.
   - Use for low-volume exploratory scans, not as the main production engine.

3. **Paid fallback:**
   - Apify TikTok actors when reliability > cost.
   - TikHub API if we want TikTok + Douyin + XHS from one provider.

4. **Accounts:**
   - Not required at first.
   - Only add aged accounts if comment depth/login-gated surfaces become necessary.

Verdict: **Build TikTok scraping without account creation first.**

---

### Instagram

**STATUS (verified July 19, 2026 by running probes):**

- `instagram.com/explore/tags/{tag}/`: **WORKS FREE** for top-of-page data. Server-renders ~5-10 top creators with follower counts, the hashtag's total post count, and post captions BEFORE the login wall blocks scrolling.
- `/explore/` and `/explore/search/keyword/`: full login wall, no useful data.
- Deep pagination, comments, per-post engagement counts: **BLOCKED** behind login.

**What we extract free:** top creators (handle + follower count), hashtag saturation (total posts), top captions with hashtags. That's enough for trend intel — who's dominating a niche, what language they use, how saturated the hashtag is.

**Connector:** `social_scraper/instagram.py` (`scan_instagram_tag`), wired into the audit harness.

**Goal:** Reels/trend/creator monitoring for Cairn marketing, plus comment-language extraction.

Recommended access stack:

1. **Open-source first:**
   - `postaddictme/instagram-php-scraper` — 3.3K stars, updated 2026-07-10.
   - `drawrowfly/instagram-scraper` — 853 stars, updated 2026-07-09.
   - `GramAddict/bot` — 1.6K stars, Android UI automation. Useful if web endpoints are too hostile.

2. **Paid fallback:**
   - Apify Instagram scrapers for reliable post/profile/reel data.

3. **Accounts:**
   - More likely needed than TikTok for reliable IG scraping.
   - Prefer **one or a few manually-created/aged accounts** over automated account creation.
   - Use Android automation only if web routes fail.

Verdict: **Probe open-source scrapers and Apify before building account creation.**

---

### Reddit

**Goal:** High-signal text behavior: quitting, switching, relapse, product complaints, brand discovery.

Access stack:

1. PullPush.io for public search.
2. Reddit OAuth/PRAW for durable access.
3. No account farming needed.

Verdict: **Most reliable social source for behavior-language extraction.** Use heavily.

---

### YouTube

**Goal:** Creator narratives, comments, video velocity, educational/consumer shift videos.

Access stack:

1. `yt-dlp` for search/video metadata.
2. YouTube Data API if needed.
3. Comment extraction selectively.
4. No accounts needed.

Verdict: **Core source.** Good for social arb, less direct for fast TikTok-style trends.

---

### Douyin / Xiaohongshu / RedNote

**Goal:** China/APAC trend detection, beauty/consumer behavior, brands with China exposure.

Direct scraping is weak from SG/US IPs due to geo/captcha blocks.

Recommended access:

1. **TikHub.io** — preferred. Approx $0.001/request, covers Douyin + Xiaohongshu + TikTok + more.
2. Apify XHS actors if TikHub data is insufficient.
3. Open-source candidates:
   - `Evil0ctal/Douyin_TikTok_Download_API`
   - `JoeanAmier/XHS-Downloader` — 11.9K stars, updated 2026-07-12.
   - `xpzouying/xiaohongshu-mcp` — 14.6K stars, updated 2026-07-12.

Verdict: **Use provider API first.** Cheaper than fighting geo-blocks.

---

## Sustainable Architecture

### Layer 1: Source Connectors

Each connector returns a standard shape:

```json
{
  "source": "tiktok",
  "status": "ok|error|partial",
  "query": "brand or trend",
  "items": [],
  "count": 0,
  "fetched_at": "ISO timestamp",
  "error": null,
  "method": "oss_api|playwright|apify|tikhub|oauth"
}
```

Every source must report health. No silent `except: return []` failures.

### Layer 2: Normalized Social Item Schema

```json
{
  "platform": "tiktok|instagram|reddit|youtube|xhs|douyin",
  "url": "canonical URL",
  "id": "platform item id",
  "author": "creator/user",
  "author_url": "profile URL",
  "text": "caption/title/post/comment text",
  "created_at": "timestamp if available",
  "engagement": {
    "views": null,
    "likes": null,
    "comments": null,
    "shares": null,
    "score": null
  },
  "media": {
    "type": "video|image|text",
    "thumbnail": null
  },
  "query": "original search query",
  "raw": {}
}
```

### Layer 3: Signal Classifier

Classify items by intent, not sentiment alone:

- **Behavior shift:** stopped buying, switched, deleted app, relapsed, downloaded, subscribed, cancelled.
- **Trend emergence:** repeated new terms, memes, hooks, aesthetics, creator formats.
- **Pain point:** complaints, workarounds, unmet need.
- **Purchase intent:** where to buy, sold out, dupe requests, recommendations.
- **Cairn-specific:** urge control, relapse, porn addiction, dopamine detox, relationship shame, blocker apps, accountability.
- **Investment-specific:** brand switching, boycott with revenue materiality, quality decline, sold-out products.

### Layer 4: Velocity + Freshness

Trend signal needs time series, not one scrape.

Track:
- first seen
- last seen
- count by platform/day
- engagement velocity
- creator concentration
- cross-platform spread
- mainstream-news coverage status

For investing: edge dies when mainstream coverage appears. For marketing: mainstream confirmation can be useful because it validates demand.

---

## What Becomes a Bounty API Product

Good paid endpoints:

1. `/social/trend-search`
   - Query across TikTok, Instagram, Reddit, YouTube, XHS/Douyin.
   - Returns normalized posts + health report.

2. `/social/behavior-shifts`
   - Input: brand/category/problem.
   - Output: ranked behavior shifts, quotes, source URLs, velocity.

3. `/social/hook-miner`
   - Input: niche, product, audience.
   - Output: winning hooks, captions, objections, creator formats.
   - Useful for Cairn marketing.

4. `/social/arb-signal`
   - Input: ticker/brand/category.
   - Output: investment-grade signal package with freshness, materiality, bear case, source audit.

5. `/social/creator-map`
   - Input: niche/category.
   - Output: creators repeatedly appearing around a trend, with engagement and content examples.

Why agents would pay:
- Cross-platform normalization is annoying.
- Social scrapers break constantly.
- Health reporting + freshness detection + dedupe + ranking is real work.
- Marketers/investors want answers, not raw HTML.

---

## Immediate Build Plan

### Phase 1 — Source Audit Harness

Build a CLI that tests each data source independently:

```bash
python -m social_scraper.audit --query "dopamine detox"
```

Outputs:
- per-source status
- item count
- sample items
- error cause
- method used

Sources to test first:
1. Reddit PullPush
2. YouTube via yt-dlp
3. TikTok via `Evil0ctal/Douyin_TikTok_Download_API`
4. TikTok via Playwright fallback
5. Instagram OSS scraper candidate
6. TikHub if API key available

### Phase 2 — Cairn Trend Queries

Seed queries:
- porn addiction
- quit porn
- nofap relapse
- dopamine detox
- screen time addiction
- accountability app
- blocker app
- urge surfing
- relationship shame porn
- habit tracker men

Output: hooks, objections, pain language, creators, trending terms.

### Phase 3 — Social Arb Queries

Use existing scanner philosophy:
- long + short keywords
- materiality gate
- freshness gate
- mainstream coverage gate
- per-source health

### Phase 4 — Bounty API

Expose only after the scanner proves useful internally.

---

## Key Decision

Stop building account creation for now.

Build the **social scraping + signal layer** first. Use accounts only if a specific high-value source absolutely requires them.
