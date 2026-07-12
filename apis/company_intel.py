"""
Company Intelligence API — Scrape any company website for tech stack,
contact info, security posture, and business metadata.

Zero upstream cost: just fetches HTML and parses it.
Replaces BuiltWith ($295/mo) + Hunter ($34/mo) + SSL Labs in one call.
"""

import re
import ssl
import socket
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter(prefix="/company", tags=["Company Intelligence"])

# ============================================================
# Tech detection patterns (Wappalyzer-inspired, hand-curated)
# ============================================================

TECH_PATTERNS: dict[str, dict[str, list[str]]] = {
    "CMS / Platform": {
        "WordPress": ["wp-content/", "wp-includes/", "wp-json/", "wpengine"],
        "Shopify": ["cdn.shopify.com", "shopify.theme", "Shopify.theme"],
        "Webflow": ["w-nav", "wf-animation", "webflow.com"],
        "Squarespace": ["static1.squarespace.com", "squarespace.com/static/"],
        "Drupal": ["drupal.js", "Drupal.settings"],
        "Ghost": ["ghost-url", "__ghost"],
        "Wix": ["wixstatic.com", "wixsite.com"],
        "Contentful": ["ctfassets.net", "contentful.com/spaces"],
        "Sanity": ["sanity.io", "sanity-cdn"],
        "Storyblok": ["storyblok.com", "a.storyblok.com"],
    },
    "JS Framework": {
        "React": ["react", "react-dom", "data-reactroot"],
        "Next.js": ["_next/", "__NEXT_DATA__", "next/dist", "nextjs"],
        "Vue.js": ["vue.js", "v-app", "__vue__", "data-v-"],
        "Nuxt": ["_nuxt/", "__nuxt", "nuxt-link"],
        "Angular": ["ng-app", "ng-version", "angular"],
        "Svelte": ["svelte", "__svelte"],
        "SvelteKit": ["sveltekit", "__sveltekit"],
        "Gatsby": ["___gatsby", "gatsby-", "gatsbyjs"],
        "Astro": ["astro-island", "astro:"],
        "Remix": ["__remix", "remix"],
    },
    "Analytics & Tracking": {
        "Google Analytics": ["google-analytics.com", "googletagmanager.com/gtag/js", "GA4", "gtag("],
        "Google Tag Manager": ["googletagmanager.com/gtm.js", "GTM-"],
        "Mixpanel": ["mixpanel.com", "mixpanel"],
        "Segment": ["analytics.js", "segment.com", "segment.io"],
        "Hotjar": ["hotjar.com", "_hjSettings", "hj-"],
        "PostHog": ["posthog.com", "posthog"],
        "Amplitude": ["amplitude.com", "amplitude"],
        "Plausible": ["plausible.io"],
        "Fathom": ["fathom", "usefathom"],
        "Matomo": ["matomo", "_paq"],
        "Clarity": ["clarity.ms", "clarity"],
        "FullStory": ["fullstory.com", "fs.fs"],
        "Mouseflow": ["mouseflow.com"],
    },
    "CDN & Hosting": {
        "Cloudflare": ["cloudflare", "cf-ray", "__cf_bm", "cdn.cloudflare.net"],
        "Vercel": ["vercel", "x-vercel", "now.sh", "vercel.app"],
        "Netlify": ["netlify", "netlify.app"],
        "AWS CloudFront": ["cloudfront.net", "amazonaws.com"],
        "Fastly": ["fastly.net", "fastly"],
        "Akamai": ["akamai", "akamaized"],
        "GitHub Pages": ["github.io"],
        "Azure": ["azureedge.net", "azurewebsites.net"],
        "Google Cloud": ["googleusercontent.com", "cloud.google.com"],
        "Bunny.net": ["bunny.net", "bunnycdn"],
    },
    "Customer Support & Chat": {
        "Intercom": ["intercom", "intercomcdn", "widget.intercom"],
        "Zendesk": ["zendesk", "zdassets", "zopim"],
        "Drift": ["drift.com", "driftt"],
        "Crisp": ["crisp.chat", "client.crisp"],
        "Tawk.to": ["tawk.to", "tawk"],
        "HubSpot Chat": ["hs-scripts", "hubspotchat"],
        "LiveChat": ["livechat", "lc.nr-data"],
        "HelpScout": ["helpscout", "beacon-v2"],
        "Userlike": ["userlike"],
        "Tidio": ["tidio", "tidio.co"],
    },
    "Marketing & Email": {
        "Mailchimp": ["mailchimp", "mcjs", "list-manage.com"],
        "HubSpot": ["hs-scripts", "hubspot", "hsforms", "hs-analytics"],
        "Klaviyo": ["klaviyo", "kl_id", "klaviyo.com"],
        "ConvertKit": ["convertkit", "ck-form"],
        "Marketo": ["marketo", "mkto", "munchkin"],
        "Pardot": ["pardot", "piTracker", "piAId"],
        "Mailgun": ["mailgun"],
        "SendGrid": ["sendgrid", "sendgrid.net"],
        "Brevo": ["brevo", "sibforms", "sendinblue"],
    },
    "Payment Processing": {
        "Stripe": ["stripe.com", "stripe-js", "js.stripe.com", "pk_live_", "pk_test_"],
        "PayPal": ["paypal.com/sdk", "paypalobjects", "paypal-checkout"],
        "Square": ["squareup.com/v2", "sq-card", "sq-payment"],
        "Adyen": ["adyen.com", "checkoutshopper"],
        "Razorpay": ["razorpay.com", "checkout.razorpay"],
        "Mollie": ["mollie.com", "mollie.nl"],
        "Klarna": ["klarnacdn", "klarna-payments", "klarna_checkout"],
        "Apple Pay": ["apple-pay-session", "applePay"],
        "Google Pay": ["google-pay-button", "payments-google-pay"],
    },
    "Maps & Location": {
        "Google Maps": ["maps.googleapis.com", "maps.google", "gmaps"],
        "Mapbox": ["mapbox", "mapbox.com", ".mapbox-gl"],
        "Leaflet": ["leaflet", "leafletjs"],
    },
    "Authentication & Identity": {
        "Auth0": ["auth0.com", "cdn.auth0.com"],
        "Firebase Auth": ["firebase.googleapis.com", "firebaseapp.com"],
        "Clerk": ["clerk.com", "clerk.dev"],
        "Supabase": ["supabase.co", "supabase_auth"],
        "Okta": ["oktacdn.com", "okta.com"],
        "Stytch": ["stytch.com", "js.stytch"],
        "AWS Cognito": ["cognito-idp", "amazoncognito"],
        "Magic.link": ["magic.link", "magic-js"],
    },
    "A/B Testing & Feature Flags": {
        "Optimizely": ["optimizely.com", "optimizely"],
        "LaunchDarkly": ["launchdarkly", "ldclient"],
        "Statsig": ["statsig", "featuregates"],
        "GrowthBook": ["growthbook", "growthbook.io"],
    },
    "Monitoring & Error Tracking": {
        "Sentry": ["sentry", "sentry-cdn", "sentry.io"],
        "Datadog": ["datadog", "datadoghq"],
        "LogRocket": ["logrocket", "logrocket.com"],
        "Rollbar": ["rollbar", "rollbarjs"],
        "Bugsnag": ["bugsnag", "bugsnag.com"],
        "Raygun": ["raygun", "raygun.io"],
        "New Relic": ["newrelic", "nr-data.net"],
    },
    "SEO & Schema": {
        "Yoast SEO": ["yoast", "wpseo"],
        "RankMath": ["rank-math", "rankmath"],
        "Schema.org Markup": ["schema.org", "application/ld+json"],
    },
}


# ============================================================
# Models
# ============================================================

class CompanyIntelResult(BaseModel):
    domain: str = Field(..., description="The queried domain")
    fetched_at: str = Field(..., description="ISO timestamp")
    url: str = Field(..., description="Full URL fetched")
    redirected_to: Optional[str] = Field(None, description="Final URL after redirects")
    title: Optional[str] = None
    description: Optional[str] = None
    og_title: Optional[str] = None
    og_description: Optional[str] = None
    og_image: Optional[str] = None
    favicon: Optional[str] = None
    language: Optional[str] = None
    technologies: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Detected technologies grouped by category",
    )
    emails: list[str] = Field(default_factory=list, description="Contact emails found")
    phone_numbers: list[str] = Field(default_factory=list, description="Phone numbers found")
    social_links: dict[str, str] = Field(
        default_factory=dict,
        description="Social media profile URLs",
    )
    ssl: Optional[dict] = Field(None, description="SSL certificate info")
    security_headers: dict[str, str | bool] = Field(
        default_factory=dict,
        description="Key security response headers",
    )
    meta_robots: Optional[str] = None
    generator: Optional[str] = None
    charset: Optional[str] = None
    page_size_kb: Optional[float] = None
    load_time_ms: Optional[float] = None


# ============================================================
# Helpers
# ============================================================

SOCIAL_DOMAINS = {
    "linkedin.com": "linkedin",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "github.com": "github",
    "youtube.com": "youtube",
    "tiktok.com": "tiktok",
    "discord.com": "discord",
    "discord.gg": "discord",
    "t.me": "telegram",
    "pinterest.com": "pinterest",
    "threads.net": "threads",
}

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_REGEX = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}")


def normalize_domain(domain: str) -> str:
    """Strip protocol, path, www prefix."""
    domain = domain.strip().lower()
    if domain.startswith(("http://", "https://")):
        parsed = urlparse(domain)
        domain = parsed.netloc
    domain = domain.removeprefix("www.")
    # Remove any path
    domain = domain.split("/")[0]
    return domain


async def fetch_page(client: httpx.AsyncClient, url: str) -> tuple[str, httpx.Response]:
    """Fetch a page with browser-like headers, follow redirects."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = await client.get(url, headers=headers, follow_redirects=True, timeout=12.0)
    return resp.text, resp


def detect_technologies(html: str, resp: httpx.Response) -> dict[str, list[str]]:
    """Match HTML content + response headers against tech patterns."""
    # Combine HTML + headers for matching
    html_lower = html.lower()
    headers_str = " ".join(f"{k}: {v}" for k, v in resp.headers.items()).lower()
    combined = html_lower + " " + headers_str

    found: dict[str, list[str]] = {}
    for category, techs in TECH_PATTERNS.items():
        detected = []
        for tech_name, patterns in techs.items():
            if any(p.lower() in combined for p in patterns):
                detected.append(tech_name)
        if detected:
            found[category] = detected
    return found


def extract_emails(html: str, domain: str) -> list[str]:
    """Find email addresses, filtering out junk."""
    raw = EMAIL_REGEX.findall(html)
    # Filter: no image/png/@sentry type false positives
    cleaned = []
    seen = set()
    for email in raw:
        email = email.lower().strip(".")
        if email in seen:
            continue
        # Skip common false positives
        if any(skip in email for skip in [".png", ".jpg", ".gif", ".webp", ".svg", "sentry", "example.com", ".wixpress"]):
            continue
        if len(email) < 6 or len(email) > 60:
            continue
        seen.add(email)
        cleaned.append(email)
    return cleaned[:10]  # Cap at 10


def extract_social_links(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
    """Find social media profile links."""
    socials: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Handle relative URLs
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        netloc = parsed.netloc.removeprefix("www.").lower()

        for social_domain, social_name in SOCIAL_DOMAINS.items():
            if social_domain in netloc:
                # Don't overwrite with share links
                if social_name not in socials:
                    # Skip share/intent links
                    if any(skip in full_url for skip in ["/intent/", "/share", "/sharer", "/plugin"]):
                        continue
                    socials[social_name] = full_url
                break
    return socials


def extract_phones(html: str) -> list[str]:
    """Find phone numbers in tel: links and text."""
    phones: list[str] = []
    seen = set()
    # First check tel: links
    tel_matches = re.findall(r'tel:([+0-9\s()-]+)', html, re.IGNORECASE)
    for phone in tel_matches:
        phone = phone.strip()
        if phone and phone not in seen:
            seen.add(phone)
            phones.append(phone)
    return phones[:5]


def check_ssl(domain: str) -> dict | None:
    """Check SSL certificate for the domain."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
        issuer = dict(x[0] for x in cert.get("issuer", []))
        subject = dict(x[0] for x in cert.get("subject", []))
        not_after = cert.get("notAfter", "")
        not_before = cert.get("notBefore", "")
        # Parse dates
        try:
            expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
            days_until_expiry = (expiry - datetime.now()).days
        except (ValueError, TypeError):
            days_until_expiry = None
        return {
            "issuer": issuer.get("organizationName", "Unknown"),
            "subject": subject.get("commonName", domain),
            "valid_from": not_before,
            "valid_to": not_after,
            "days_until_expiry": days_until_expiry,
            "is_valid": days_until_expiry is not None and days_until_expiry > 0 if days_until_expiry is not None else None,
        }
    except Exception:
        return None


def extract_security_headers(resp: httpx.Response) -> dict[str, str | bool]:
    """Extract key security headers from response."""
    headers = {k.lower(): v for k, v in resp.headers.items()}
    result = {}
    security_checks = [
        "strict-transport-security",
        "content-security-policy",
        "x-frame-options",
        "x-content-type-options",
        "referrer-policy",
        "permissions-policy",
        "x-xss-protection",
    ]
    for header in security_checks:
        val = headers.get(header)
        if val:
            result[header] = val
    result["https_enforced"] = str(resp.url).startswith("https://")
    return result


# ============================================================
# Main endpoint
# ============================================================

@router.get("/{domain}", response_model=CompanyIntelResult)
async def company_intelligence(domain: str):
    """
    Analyze any company website: tech stack, contacts, security, metadata.

    Returns detected technologies (CMS, frameworks, analytics, CDN, payments,
    chat, auth), contact emails, social profiles, SSL certificate info,
    and security headers in one structured response.

    Replaces BuiltWith + Hunter + SSL Labs in a single call.
    """
    clean_domain = normalize_domain(domain)
    url = f"https://{clean_domain}"

    start = datetime.now(timezone.utc)

    async with httpx.AsyncClient() as client:
        try:
            html, resp = await fetch_page(client, url)
        except httpx.ConnectError:
            # Try http fallback
            try:
                html, resp = await fetch_page(client, f"http://{clean_domain}")
            except Exception:
                raise HTTPException(status_code=502, detail=f"Could not reach {clean_domain}")
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail=f"Timeout fetching {clean_domain}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error fetching {clean_domain}: {str(e)}")

    end = datetime.now(timezone.utc)
    load_time_ms = (end - start).total_seconds() * 1000

    soup = BeautifulSoup(html, "lxml")

    # Extract metadata
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    meta_desc = soup.find("meta", attrs={"name": "description"})
    description = meta_desc.get("content") if meta_desc else None

    og_title_tag = soup.find("meta", attrs={"property": "og:title"})
    og_title = og_title_tag.get("content") if og_title_tag else None

    og_desc_tag = soup.find("meta", attrs={"property": "og:description"})
    og_description = og_desc_tag.get("content") if og_desc_tag else None

    og_image_tag = soup.find("meta", attrs={"property": "og:image"})
    og_image = og_image_tag.get("content") if og_image_tag else None

    favicon_link = soup.find("link", rel="icon") or soup.find("link", rel="shortcut icon")
    favicon = None
    if favicon_link and favicon_link.get("href"):
        favicon = urljoin(url, favicon_link["href"])

    html_tag = soup.find("html")
    language = html_tag.get("lang") if html_tag else None

    meta_robots_tag = soup.find("meta", attrs={"name": "robots"})
    meta_robots = meta_robots_tag.get("content") if meta_robots_tag else None

    generator_tag = soup.find("meta", attrs={"name": "generator"})
    generator = generator_tag.get("content") if generator_tag else None

    # Detect technologies
    technologies = detect_technologies(html, resp)

    # Extract contacts
    emails = extract_emails(html, clean_domain)
    social_links = extract_social_links(soup, url)
    phone_numbers = extract_phones(html)

    # SSL check (run in thread to avoid blocking)
    loop = asyncio.get_event_loop()
    ssl_info = await loop.run_in_executor(None, check_ssl, clean_domain)

    # Security headers
    security_headers = extract_security_headers(resp)

    # Page size
    page_size_kb = round(len(html) / 1024, 1)

    redirected_to = str(resp.url) if str(resp.url) != url else None

    return CompanyIntelResult(
        domain=clean_domain,
        fetched_at=start.isoformat(),
        url=url,
        redirected_to=redirected_to,
        title=title,
        description=description,
        og_title=og_title,
        og_description=og_description,
        og_image=og_image,
        favicon=favicon,
        language=language,
        technologies=technologies,
        emails=emails,
        phone_numbers=phone_numbers,
        social_links=social_links,
        ssl=ssl_info,
        security_headers=security_headers,
        meta_robots=meta_robots,
        generator=generator,
        charset=resp.encoding,
        page_size_kb=page_size_kb,
        load_time_ms=round(load_time_ms, 1),
    )
