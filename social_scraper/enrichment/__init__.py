"""
Enrichment layer — the buzzabout competitor.

Extracts structured metadata from every social post using LLM batch processing.
Domain-agnostic tags that serve any vertical (marketing, investing, journalism, entrepreneurship).

Tag schema per post:
- emotion: primary emotional tone
- intent: what the author is trying to do
- sentiment: positive/negative/neutral/mixed
- pain_point: explicit problem described (if any)
- unmet_need: what they wish existed (if any)
- hook_type: content angle/format used
- topic_tags: 2-3 key topics
- brand_mentions: companies/products mentioned
- purchase_signal: low/medium/high
- narrative: one-line summary of the "story" this post tells
"""

from .schema import EnrichedItem, EnrichmentBatch
from .engine import EnrichmentEngine

__all__ = ["EnrichedItem", "EnrichmentBatch", "EnrichmentEngine"]
