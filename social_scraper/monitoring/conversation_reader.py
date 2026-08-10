"""
Conversation reader — LLM-powered analysis of social posts for a trending keyword.

Reads all social posts collected by the conversation gate and produces a
structured summary of what people are actually saying. This is the buzzabout
"read the conversation" step.

Output per keyword:
- summary: 2-3 sentences describing what the conversation is about
- sentiment: positive/negative/mixed + rough breakdown
- brands: companies/products mentioned in the discussion
- trend_type: Product/Consumer | Event | Cultural/Behavioral | Personality
- type_reason: one sentence explaining WHY this classification, citing posts
- key_quotes: 1-2 representative quotes from the posts

LLM provider is fully switchable via environment variables:
- BOUNTY_LLM_BASE_URL: OpenAI-compatible endpoint (e.g. z.ai, openrouter, local)
- BOUNTY_LLM_API_KEY: API key for the provider
- BOUNTY_LLM_MODEL: model name (e.g. glm-4.7, gpt-4o-mini)

If the primary provider fails (rate limit, timeout, etc.), the caller can
switch providers by changing env vars — no code changes needed.

Architecture:
    social posts (from gate, ~10-30 items)
        + keyword metadata (name, category, volume, growth)
        ↓
    ONE LLM call (balanced: max 5 posts per platform)
        ↓
    JSON response → ConversationSummary
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

# Max posts per platform in the LLM prompt (balance representation)
_MAX_POSTS_PER_PLATFORM = 5

# Truncate post text for the prompt (save tokens)
_MAX_TEXT_CHARS = 200


@dataclass
class ConversationSummary:
    """LLM-generated analysis of social discussion around a keyword."""
    keyword: str = ""
    summary: str = ""
    sentiment: str = ""           # positive/negative/mixed/neutral
    sentiment_breakdown: str = ""  # e.g. "40% excitement, 30% frustration"
    brands: list[str] = field(default_factory=list)
    trend_type: str = ""          # Product/Consumer | Event | Cultural/Behavioral | Personality
    type_reason: str = ""         # one sentence explaining WHY
    key_quotes: list[str] = field(default_factory=list)
    posts_analyzed: int = 0
    llm_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_SYSTEM_PROMPT = """You are a social media intelligence analyst. You analyze what people are actually saying about a trending topic across multiple social platforms.

You will receive:
1. A trending keyword with its metadata (search volume, growth %, category)
2. A set of social media posts about this keyword from YouTube, Reddit, and TikTok

Analyze the conversation and return ONLY a JSON object with these fields:

{
  "summary": "2-3 sentences describing what the conversation is actually about. What are people saying? What is the argument or discussion?",
  "sentiment": "one of: positive, negative, mixed, neutral",
  "sentiment_breakdown": "rough breakdown, e.g. '40% excitement, 30% frustration, 30% curiosity'",
  "brands": ["company or product names mentioned in the posts, lowercase, deduplicated"],
  "trend_type": "one of: Product/Consumer, Event, Cultural/Behavioral, Personality",
  "type_reason": "one sentence explaining WHY this classification, citing what you read in the posts",
  "key_quotes": ["1-2 short representative quotes from the posts that capture the conversation"]
}

Trend type definitions:
- Product/Consumer: people buying, reviewing, comparing, or asking about a product or service
- Event: discussion driven by a specific news event, policy change, disaster, sports match, or earnings
- Cultural/Behavioral: a shift in how people think, talk, or behave (language change, identity, lifestyle)
- Personality: discussion centered on a specific person (celebrity, politician, athlete)

Rules:
- summary should capture the ARGUMENT, not just the topic. What are people debating, asking, or experiencing?
- brands should include any company, product, app, or service mentioned (even competitors)
- type_reason must be specific, e.g. "Posts show people sharing temperature data and heat warnings from southern Europe this week" not "Posts discuss a current event"
- key_quotes should be verbatim or near-verbatim from the actual posts, not paraphrased
- Be concise. Return ONLY the JSON object. No markdown, no commentary."""


def _balance_posts(posts: list[dict], max_per_platform: int = _MAX_POSTS_PER_PLATFORM) -> list[dict]:
    """Balance post representation across platforms.

    Caps each platform at max_per_platform so YouTube's 10 results
    don't drown out TikTok's 2.
    """
    by_platform: dict[str, list[dict]] = {}
    for post in posts:
        platform = post.get("platform", "unknown")
        by_platform.setdefault(platform, []).append(post)

    balanced = []
    for platform, platform_posts in by_platform.items():
        balanced.extend(platform_posts[:max_per_platform])

    return balanced


def _build_user_prompt(keyword: str, metadata: dict, posts: list[dict]) -> str:
    """Build the LLM prompt with keyword metadata + social posts."""
    lines = []
    lines.append(f"TRENDING KEYWORD: {keyword}")
    lines.append(f"Category: {metadata.get('category', 'Unknown')}")
    lines.append(f"Search volume: {metadata.get('volume', 'Unknown'):,}")
    lines.append(f"Growth: +{metadata.get('growth', 0)}%")
    lines.append(f"Posts found on: {metadata.get('platforms', 'Unknown')}")
    lines.append("")
    lines.append("SOCIAL POSTS:")
    lines.append("")

    for i, post in enumerate(posts, 1):
        platform = post.get("platform", "?")
        text = _extract_text(post)
        eng = post.get("engagement", {})
        likes = eng.get("likes") or 0
        views = eng.get("views") or 0

        meta = f"[{platform}"
        if likes:
            meta += f", {likes} likes"
        if views:
            meta += f", {views:,} views"
        meta += "]"

        lines.append(f"{i}. {meta} {text}")
        lines.append("")

    return "\n".join(lines)


def _extract_text(post: dict) -> str:
    """Extract and truncate text from a social post."""
    text = post.get("text") or ""
    title = post.get("title") or ""
    full = f"{title} {text}".strip()
    full = re.sub(r"http\S+", "", full)
    full = re.sub(r"\s+", " ", full).strip()
    return full[:_MAX_TEXT_CHARS]


async def _call_llm(system_prompt: str, user_prompt: str) -> str:
    """Call the shared, provider-switchable Bounty LLM client."""
    from social_scraper.llm_client import call_llm

    return await call_llm(system_prompt, user_prompt, max_tokens=1000)


def _parse_response(raw: str, keyword: str) -> ConversationSummary:
    """Parse LLM JSON response into ConversationSummary."""
    clean = raw.strip()
    # Strip markdown fences
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)

    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        # Try to extract JSON object from text
        match = re.search(r'\{.*\}', clean, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse LLM response for '{keyword}'")
                return ConversationSummary(keyword=keyword, llm_error="parse_failed")
        else:
            return ConversationSummary(keyword=keyword, llm_error="parse_failed")

    return ConversationSummary(
        keyword=keyword,
        summary=parsed.get("summary", ""),
        sentiment=parsed.get("sentiment", ""),
        sentiment_breakdown=parsed.get("sentiment_breakdown", ""),
        brands=parsed.get("brands", []),
        trend_type=parsed.get("trend_type", ""),
        type_reason=parsed.get("type_reason", ""),
        key_quotes=parsed.get("key_quotes", []),
    )


async def read_conversation(
    keyword: str,
    metadata: dict,
    posts: list[dict],
    llm_call_fn=None,
) -> ConversationSummary:
    """Read the conversation around a keyword via LLM.

    Args:
        keyword: The trending keyword.
        metadata: Dict with category, volume, growth, platforms keys.
        posts: Social posts from the conversation gate.
        llm_call_fn: Optional async function(system, user) -> str.
                     If None, uses the default env-configured LLM.

    Returns:
        ConversationSummary with LLM analysis, or error state if LLM fails.
    """
    if not posts:
        return ConversationSummary(keyword=keyword, llm_error="no_posts")

    # Balance platform representation
    balanced = _balance_posts(posts)

    # Build prompt
    user_prompt = _build_user_prompt(keyword, metadata, balanced)

    # Call LLM
    try:
        if llm_call_fn:
            raw = await llm_call_fn(_SYSTEM_PROMPT, user_prompt)
        else:
            raw = await _call_llm(_SYSTEM_PROMPT, user_prompt)

        summary = _parse_response(raw, keyword)
        summary.posts_analyzed = len(balanced)
        logger.info(
            f"Conversation reader: '{keyword}' → type={summary.trend_type}, "
            f"sentiment={summary.sentiment}, brands={summary.brands[:3]}"
        )
        return summary

    except Exception as e:
        error_msg = str(e)[:200]
        logger.warning(f"Conversation reader failed for '{keyword}': {error_msg}")
        return ConversationSummary(
            keyword=keyword,
            posts_analyzed=len(balanced),
            llm_error=error_msg,
        )
