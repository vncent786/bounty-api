# Bounty x402: Path B Scraping Strategy
## Sustainable auth-walled data infrastructure

---

## THE CORE INSIGHT

Our edge is NOT the scraper code. It's the **access infrastructure** — account pools, proxies, and the discipline to maintain them. Like investing: the alpha isn't in reading the same 10-K everyone reads, it's in the infrastructure that gets you access first.

GitHub repos give us the scraping logic for free. What we build and own is the layer above: accounts that survive, proxies that don't burn, and an API that agents can't replicate with a single curl.

---

## SUSTAINABILITY FRAMEWORK: The 4-Layer Model

### Layer 1: Access Infrastructure (OUR MOAT)
- **Account pools** — aged accounts with real history, warmed up gradually
- **Residential proxies** — rotating IPs that look human
- **Device fingerprinting** — unique browser/app fingerprints per account
- **Session management** — cookie/token rotation, re-auth on expiry
- This is what dies if neglected. This is what competitors can't easily copy.

### Layer 2: Scraping Logic (LEVERAGE OPEN SOURCE)
- Don't reinvent. Wrap the best maintained repos.
- Each platform gets a thin adapter that calls the upstream library.
- When upstream breaks, swap to next-best repo. Adapter stays stable.
- Maintain a health dashboard: last successful scrape per platform, error rate.

### Layer 3: API Normalization (OUR PRODUCT)
- Each platform has different data shapes. We normalize into clean JSON.
- Source provenance preserved. Missing data stays missing.
- x402 payment gating. Agents pay per call.
- This is where the x402 discovery, pricing, and payment happen.

### Layer 4: Monitoring & Self-Healing (OUR OPERATIONAL EDGE)
- Health check each scraper every N minutes.
- Alert when a platform goes down.
- Auto-failover to backup method if primary breaks.
- Weekly audit: are repos still maintained? Are accounts still alive?

---

## PLATFORM LANDSCAPE (from GitHub research, July 2026)

### Tier 1: Proven revenue, high demand, active tooling

| Platform | Best Repo | Stars | Last Active | Status | x402 Revenue Proof |
|---|---|---|---|---|---|
| X/Twitter | d60/twikit | 4.5K | Mar 2026 | Active | twit.sh $149K |
| X/Twitter | vladkens/twscrape | 2.5K | Jun 2026 | VERY active | (multi-account rotation built in) |

twscrape is the gold standard: multi-account pooling, proxy support, rate-limit handling. Updated 16 days ago. MIT license.

### Tier 2: Underserved on x402, high barrier, real demand

| Platform | Best Repo | Stars | Last Active | Status | Why it matters |
|---|---|---|---|---|---|
| TikTok (global) | Evil0ctal/Douyin_TikTok_Download_API | 18.7K | Oct 2025 | Stale? 9mo no push | Trend detection, influencer monitoring |
| Douyin (CN TikTok) | Same repo | 18.7K | Oct 2025 | Stale? | Chinese market intelligence |
| Xiaohongshu (RedNote) | RedNote/Xiaohongshu-API | 37 | Jun 2026 | Active but small | xsec_token + shield algorithm reversed |
| Kuaishou (快手) | Evil0ctal covers it | 18.7K | Oct 2025 | Stale? | Chinese video platform |
| Bilibili (B站) | Evil0ctal covers it | 18.7K | Oct 2025 | Stale? | Chinese video platform |

The Evil0ctal repo is impressive (18.7K stars, covers 4 Chinese platforms) but hasn't pushed in 9 months. Risk of staleness. The Xiaohongshu-API repo is tiny (37 stars) but has the hardest part reversed: the signature algorithms.

### Tier 3: High barrier but dominated by commercial players

| Platform | Challenge | Incumbent | Notes |
|---|---|---|---|
| Google SERP | Captchas, IP bans, frequent layout changes | SerpAPI ($50-250/mo), Oxylabs | Commercial proxy required |
| LinkedIn | Aggressive anti-bot, captcha, IP detection | Apollo, Clearbit ($500+/mo) | High maintenance |
| Instagram | Checkpoint challenges, device fingerprinting | Various | Moderate barrier |

---

## THE RESOURCEFULNESS PLAYBOOK

### How to find sustainable scraping methods (ongoing process)

1. **GitHub search discipline**
   - Search: `{platform} scraper python stars:>50 sort:updated`
   - Check `pushed_at` — anything older than 3 months is risky
   - Check `open_issues` — high open issues with no response = dying repo
   - Check forks — active forks may have continued where original died

2. **Chinese developer community**
   - Many best scrapers are by Chinese developers (Evil0ctal, RedNote org)
   - Search in Chinese: `{平台} 爬虫` (platform + crawler)
   - These repos are gold for Douyin, Rednote, Bilibili, Weibo, Zhihu
   - Less Western competition = lower anti-bot pressure from our side

3. **Telegram/Discord scraping communities**
   - Real-time intel on what's working, what just broke
   - Account sellers, proxy recommendations, algorithm updates

4. **Monitor the platforms themselves**
   - When a platform pushes an anti-bot update, repos will spike with issues
   - Watch the issue trackers of our repos for early warning

5. **Multi-method redundancy**
   - NEVER depend on a single scraping method per platform
   - Always have a primary + fallback method
   - Example for Twitter: twikit (internal API) + twscrape (web scraping) + nitter instances

---

## PLATFORM PRIORITY (recommended build order)

### Phase 1: X/Twitter (proven $149K revenue)
- Use twscrape as primary (multi-account rotation, proxy support)
- Build: `/x/search?q=...`, `/x/user/{username}`, `/x/tweets/{username}`
- Need: 5-10 aged Twitter accounts, residential proxy
- Price: $0.02-0.05/call
- Build time: 2-3 days (twscrape does the heavy lifting)

### Phase 2: TikTok + Douyin (underserved on x402, high demand)
- Evaluate Evil0ctal repo health first. If stale, find active fork.
- Build: `/tiktok/search?q=...`, `/tiktok/user/{username}`, `/tiktok/video/{id}`
- Build: `/douyin/search?q=...`, `/douyin/user/{username}`
- Need: Device ID generation, proxy
- Price: $0.05-0.10/call
- Build time: 3-5 days
- Social arb connection: trend detection for investment signals

### Phase 3: Xiaohongshu/RedNote (unique edge, growing platform)
- Use RedNote/Xiaohongshu-API for signature algorithms
- Build: `/rednote/search?q=...`, `/rednote/note/{id}`, `/rednote/user/{username}`
- Need: Cookie/token management, understanding of xsec_token flow
- Price: $0.05-0.10/call
- Build time: 3-5 days
- Social arb connection: Chinese consumer trend signals

### Phase 4: Google SERP (if proxy economics work)
- Self-hosted scraping with residential proxies
- Build: `/serp?q=...`
- Need: Reliable proxy pool, captcha handling
- Price: $0.01-0.02/call
- Build time: 3-5 days
- Risk: High maintenance, proxy costs may eat margins

### Phase 5 (FUTURE): Account creation service
- Vincent's idea: agents pay to have social media accounts created
- Genuinely hard: phone verification, device fingerprinting, behavioral warming
- High demand: every scraper needs accounts, nobody wants to make them
- Would need: SMS verification service, device farm or emulator, captcha solving
- Price: $0.50-5.00/account depending on platform
- Build time: 2-3 weeks minimum
- This is a product in itself, not just an API endpoint

---

## THE SOCIAL ARBITRAGE CONNECTION

Our existing social arb scanner already does social media monitoring for investment signals. This creates a natural synergy:

1. **Shared infrastructure** — account pools and proxies power both the scanner AND the x402 API
2. **Shared domain knowledge** — we know which data matters for trend detection
3. **Internal customer** — our own scanner is the first user of the APIs
4. **Unique data product** — combine social signals from X, TikTok, Douyin, Rednote into a single "cross-platform trend" API that no Western competitor offers

The Chinese platform angle is especially interesting:
- Western x402 operators focus on Western platforms
- Douyin + Rednote + Bilibili data is genuinely hard for Western agents to access
- Language barrier + anti-bot + different ecosystem = real moat
- We have SEA positioning (Singapore base, Indonesian family connections)

---

## UNIT ECONOMICS

| Platform | Price/call | Proxy cost/call | Account cost amortized | Net margin |
|---|---|---|---|---|
| X/Twitter | $0.03 | ~$0.001 | ~$0.002 | ~91% |
| TikTok | $0.05 | ~$0.002 | ~$0.003 | ~90% |
| Douyin | $0.05 | ~$0.002 | ~$0.003 | ~90% |
| RedNote | $0.05 | ~$0.002 | ~$0.003 | ~90% |
| Google SERP | $0.02 | ~$0.005 | ~$0.001 | ~70% |

Residential proxy costs are the main variable. At scale (10K+ calls/day), bulk proxy pricing drops significantly.

Account amortization: if an account lasts 30 days before ban and handles 1000 calls/day, that's 30K calls per account. At $1-3 per account (phone verified), that's ~$0.0001/call.

---

## OPERATIONAL CHECKLIST (before building each platform)

- [ ] Viability gate: Can agent do this with one free curl? (Must be NO)
- [ ] Best repo identified and health-checked (active within 3 months)
- [ ] Backup repo identified
- [ ] Account acquisition method determined
- [ ] Proxy strategy determined
- [ ] Anti-bot signature/algorithm understood (or delegated to repo)
- [ ] Local test: can we get real data?
- [ ] x402 payment gating verified
- [ ] AgentCash/OpenAPI/llms.txt discovery metadata added
- [ ] Health monitoring endpoint added
