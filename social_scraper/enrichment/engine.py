"""
Enrichment engine — LLM batch processing for social post tagging.

Design:
- Batches 20-30 posts per LLM call (truncated text, ~30 tokens each)
- Strict JSON output schema for reliable parsing
- Domain-agnostic system prompt
- Fallback to heuristics if LLM fails
- Aggregate insights computed from the batch

Cost estimate per batch of 25 posts:
- Input: ~25 posts × 40 tokens + system prompt ~300 tokens = ~1,300 tokens
- Output: 25 items × ~60 tokens = ~1,500 tokens
- Total: ~2,800 tokens per batch
- At GLM pricing: negligible
"""

import asyncio
import json
import logging
import os
import re
from collections import Counter
from typing import Optional
from datetime import datetime, timezone

from .schema import (
    EnrichedItem, EnrichmentBatch,
    Emotion, Intent, HookType, Sentiment, PurchaseSignal,
)

logger = logging.getLogger(__name__)

# Truncate post text for enrichment
_MAX_TEXT = 200

# Batch size for LLM calls
_BATCH_SIZE = 25

_SYSTEM_PROMPT = """You are a social media intelligence analyst. For each post below, extract structured tags.

Return a JSON array with one object per post, in the same order as the input. Each object MUST have exactly these fields:

{
  "emotion": one of [frustration, excitement, fear, curiosity, disgust, joy, anger, surprise, pride, shame, neutral],
  "intent": one of [complaint, recommendation, question, comparison, purchase_intent, switching, quitting, discovery, announcement, opinion, tutorial, warning, relapse, validation],
  "sentiment": one of [positive, negative, neutral, mixed],
  "hook_type": one of [how_to, before_after, story, data_research, controversy, comparison, tutorial, rant, review, testimonial, list, trend_alert, behind_scenes, myth_busting],
  "purchase_signal": one of [none, low, medium, high],
  "pain_point": short phrase or empty string if none,
  "unmet_need": short phrase or empty string if none,
  "narrative": one-sentence summary of what this post is really about,
  "topic_tags": array of 2-3 short topic keywords,
  "brand_mentions": array of brand/company/product names mentioned, empty if none
}

Rules:
- pain_point and unmet_need should be specific, not generic. "battery life" not "product issues"
- narrative should capture the human story, not just the topic
- topic_tags should be lowercase, no spaces (use underscores)
- brand_mentions should include any company, product, app, or service mentioned
- Be concise. Every field should be as short as possible while being accurate.
- Return ONLY the JSON array. No markdown, no commentary."""


class EnrichmentEngine:
    """LLM-powered batch enrichment of social posts."""

    def __init__(self, llm_call_fn=None):
        """
        Args:
            llm_call_fn: async function(system_prompt: str, user_prompt: str) -> str
                         that calls an LLM and returns the text response.
                         If None, uses Hermes execute_code fallback or heuristic mode.
        """
        self._llm_call = llm_call_fn

    async def enrich_posts(self, items: list[dict]) -> EnrichmentBatch:
        """Enrich a list of social posts with structured tags.

        Args:
            items: list of post dicts (from broker search results)

        Returns:
            EnrichmentBatch with tagged items and aggregate insights
        """
        if not items:
            return EnrichmentBatch()

        # Process in batches
        batches = [items[i:i + _BATCH_SIZE] for i in range(0, len(items), _BATCH_SIZE)]
        all_enriched = []

        for batch_idx, batch in enumerate(batches):
            logger.info(f"Enriching batch {batch_idx + 1}/{len(batches)} ({len(batch)} posts)")
            try:
                enriched = await self._enrich_batch(batch)
                all_enriched.extend(enriched)
            except Exception as e:
                logger.warning(f"Batch {batch_idx + 1} failed: {e}, using heuristics")
                enriched = [self._heuristic_enrich(item) for item in batch]
                all_enriched.extend(enriched)

        # Compute aggregate insights
        batch_result = self._compute_aggregates(all_enriched)
        return batch_result

    async def _enrich_batch(self, items: list[dict]) -> list[EnrichedItem]:
        """Enrich a single batch via LLM."""
        # Build user prompt: numbered list of post texts
        post_lines = []
        for i, item in enumerate(items):
            text = self._extract_text(item)
            platform = item.get("platform", "?")
            author = item.get("author_username") or item.get("author_display_name") or ""
            eng = item.get("engagement", {})
            likes = eng.get("likes")
            views = eng.get("views")
            meta = f"[{platform}"
            if likes:
                meta += f", {likes} likes"
            if views:
                meta += f", {views} views"
            meta += "]"
            post_lines.append(f"{i + 1}. {meta} @{author}: {text}")

        user_prompt = "\n".join(post_lines)

        # Call LLM
        if self._llm_call:
            raw_response = await self._llm_call(_SYSTEM_PROMPT, user_prompt)
        else:
            raw_response = await self._default_llm_call(_SYSTEM_PROMPT, user_prompt)

        # Parse JSON response
        tags_list = self._parse_llm_response(raw_response, len(items))

        # Map tags back to original items
        enriched = []
        for i, item in enumerate(items):
            tags = tags_list[i] if i < len(tags_list) else {}
            enriched.append(self._build_enriched_item(item, tags))

        return enriched

    async def _default_llm_call(self, system_prompt: str, user_prompt: str) -> str:
        """Default LLM call using the built-in model.

        Uses openai-compatible call to the configured provider.
        Override this or pass llm_call_fn to use a different model.
        """
        try:
            import httpx
        except ImportError:
            raise RuntimeError("httpx not available for LLM call")

        # Read LLM config from environment
        base_url = os.getenv("BOUNTY_LLM_BASE_URL", "").strip()
        api_key = os.getenv("BOUNTY_LLM_API_KEY", "").strip()
        model = os.getenv("BOUNTY_LLM_MODEL", "gpt-4o-mini").strip()

        if not base_url or not api_key:
            # Fallback to heuristic enrichment
            logger.warning("No LLM configured, using heuristic enrichment")
            raise RuntimeError("llm_not_configured")

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 4000,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_llm_response(raw: str, expected_count: int) -> list[dict]:
        """Parse LLM JSON response, handling markdown wrappers and partial failures."""
        # Strip markdown code fences if present
        clean = raw.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean)
            clean = re.sub(r"\s*```$", "", clean)

        try:
            parsed = json.loads(clean)
            if isinstance(parsed, list):
                return parsed
            elif isinstance(parsed, dict) and "items" in parsed:
                return parsed["items"]
        except json.JSONDecodeError:
            # Try to extract JSON array from text
            match = re.search(r'\[.*\]', clean, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass

        logger.warning(f"Failed to parse LLM response, returning {expected_count} empty dicts")
        return [{} for _ in range(expected_count)]

    @staticmethod
    def _extract_text(item: dict) -> str:
        """Extract and truncate text from a social item."""
        text = item.get("text") or ""
        title = item.get("title") or ""
        full = f"{title} {text}".strip()
        full = re.sub(r"http\S+", "", full)
        full = re.sub(r"\s+", " ", full).strip()
        return full[:_MAX_TEXT]

    @staticmethod
    def _build_enriched_item(item: dict, tags: dict) -> EnrichedItem:
        """Merge original post data with LLM-extracted tags."""
        eng = item.get("engagement", {})
        return EnrichedItem(
            platform=item.get("platform", ""),
            post_id=str(item.get("post_id", item.get("id", ""))),
            url=item.get("url", ""),
            author=item.get("author_username") or item.get("author_display_name") or "",
            text=EnrichmentEngine._extract_text(item),
            emotion=tags.get("emotion", Emotion.NEUTRAL.value),
            intent=tags.get("intent", Intent.OPINION.value),
            sentiment=tags.get("sentiment", Sentiment.NEUTRAL.value),
            hook_type=tags.get("hook_type", HookType.STORY.value),
            purchase_signal=tags.get("purchase_signal", PurchaseSignal.NONE.value),
            pain_point=tags.get("pain_point", ""),
            unmet_need=tags.get("unmet_need", ""),
            narrative=tags.get("narrative", ""),
            topic_tags=tags.get("topic_tags", []),
            brand_mentions=tags.get("brand_mentions", []),
            likes=eng.get("likes"),
            views=eng.get("views"),
            comments=eng.get("comments"),
        )

    @staticmethod
    def _heuristic_enrich(item: dict) -> EnrichedItem:
        """Fallback enrichment without LLM — basic keyword detection."""
        text = EnrichmentEngine._extract_text(item).lower()
        eng = item.get("engagement", {})

        # Emotion detection
        emotion = Emotion.NEUTRAL.value
        if any(w in text for w in ["hate", "terrible", "awful", "worst", "frustrated"]):
            emotion = Emotion.FRUSTRATION.value
        elif any(w in text for w in ["love", "amazing", "incredible", "best", "excited"]):
            emotion = Emotion.EXCITEMENT.value
        elif any(w in text for w in ["scared", "afraid", "worried", "concerned"]):
            emotion = Emotion.FEAR.value
        elif any(w in text for w in ["wow", "did not expect", "surprised", "shocking"]):
            emotion = Emotion.SURPRISE.value
        elif any(w in text for w in ["disgusting", "gross", "nasty"]):
            emotion = Emotion.DISGUST.value

        # Intent detection
        intent = Intent.OPINION.value
        if any(w in text for w in ["how to", "how do", "guide", "tutorial"]):
            intent = Intent.TUTORIAL.value
        elif any(w in text for w in ["recommend", "suggest", "best", "should i"]):
            intent = Intent.RECOMMENDATION.value
        elif any(w in text for w in ["bought", "buying", "ordered", "purchased", "take my money"]):
            intent = Intent.PURCHASE_INTENT.value
        elif any(w in text for w in ["switched", "switching", "moved to", "changed to"]):
            intent = Intent.SWITCHING.value
        elif any(w in text for w in ["quit", "stopped", "cancelled", "deleted", "uninstalled"]):
            intent = Intent.QUITTING.value
        elif "?" in text:
            intent = Intent.QUESTION.value
        elif any(w in text for w in ["don't buy", "avoid", "warning", "stay away"]):
            intent = Intent.WARNING.value
        elif any(w in text for w in ["found", "discovered", "new to me", "never seen"]):
            intent = Intent.DISCOVERY.value

        # Sentiment
        sentiment = Sentiment.NEUTRAL.value
        if emotion in [Emotion.FRUSTRATION.value, Emotion.ANGER.value, Emotion.DISGUST.value, Emotion.FEAR.value]:
            sentiment = Sentiment.NEGATIVE.value
        elif emotion in [Emotion.EXCITEMENT.value, Emotion.JOY.value, Emotion.PRIDE.value]:
            sentiment = Sentiment.POSITIVE.value

        # Brand mentions (capitalized words that look like brands)
        brand_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b'
        raw_text = item.get("text", "") or ""
        potential_brands = re.findall(brand_pattern, raw_text)
        # Filter common words
        common = {"The", "This", "That", "They", "When", "What", "How", "Why", "Who",
                   "Just", "But", "And", "For", "With", "From", "About", "Into"}
        brand_mentions = [b for b in potential_brands if b not in common][:3]

        return EnrichedItem(
            platform=item.get("platform", ""),
            post_id=str(item.get("post_id", item.get("id", ""))),
            url=item.get("url", ""),
            author=item.get("author_username") or item.get("author_display_name") or "",
            text=EnrichmentEngine._extract_text(item),
            emotion=emotion,
            intent=intent,
            sentiment=sentiment,
            hook_type=HookType.STORY.value,
            purchase_signal=PurchaseSignal.NONE.value,
            pain_point="",
            unmet_need="",
            narrative="",
            topic_tags=[],
            brand_mentions=brand_mentions,
            likes=eng.get("likes"),
            views=eng.get("views"),
            comments=eng.get("comments"),
        )

    @staticmethod
    def _compute_aggregates(items: list[EnrichedItem]) -> EnrichmentBatch:
        """Compute aggregate insights across enriched items."""
        emotion_dist = Counter(item.emotion for item in items)
        intent_dist = Counter(item.intent for item in items)
        pain_points = Counter()
        brands = Counter()
        topics = Counter()

        for item in items:
            if item.pain_point:
                pain_points[item.pain_point.lower()] += 1
            for b in item.brand_mentions:
                brands[b.lower()] += 1
            for t in item.topic_tags:
                topics[t.lower()] += 1

        return EnrichmentBatch(
            items=items,
            total_processed=len(items),
            success_count=len(items),
            emotion_distribution=dict(emotion_dist.most_common()),
            intent_distribution=dict(intent_dist.most_common()),
            top_pain_points=[p for p, _ in pain_points.most_common(10)],
            top_brands=[b for b, _ in brands.most_common(10)],
            top_topics=[t for t, _ in topics.most_common(10)],
        )
