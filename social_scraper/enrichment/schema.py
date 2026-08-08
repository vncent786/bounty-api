"""
Enrichment schema — structured tags extracted from social posts.

Every tag is domain-agnostic. The same tag serves different verticals:
- Marketer sees content opportunities and audience pain
- Investor sees brand sentiment and behavior shifts
- Journalist sees story angles and emerging narratives
- Entrepreneur sees product gaps and unmet needs
"""

from dataclasses import dataclass, field, asdict
from typing import Optional
from enum import Enum


class Emotion(str, Enum):
    FRUSTRATION = "frustration"
    EXCITEMENT = "excitement"
    FEAR = "fear"
    CURIOSITY = "curiosity"
    DISGUST = "disgust"
    JOY = "joy"
    ANGER = "anger"
    SURPRISE = "surprise"
    PRIDE = "pride"
    SHAME = "shame"
    NEUTRAL = "neutral"


class Intent(str, Enum):
    COMPLAINT = "complaint"
    RECOMMENDATION = "recommendation"
    QUESTION = "question"
    COMPARISON = "comparison"
    PURCHASE_INTENT = "purchase_intent"
    SWITCHING = "switching"          # moving from one brand/product to another
    QUITTING = "quitting"            # abandoning a product/habit
    DISCOVERY = "discovery"          # found something new
    ANNOUNCEMENT = "announcement"
    OPINION = "opinion"
    TUTORIAL = "tutorial"            # teaching how to do something
    WARNING = "warning"              # cautioning others
    RELAPSE = "relapse"              # went back to old behavior
    VALIDATION = "validation"        # seeking or providing confirmation


class HookType(str, Enum):
    HOW_TO = "how_to"
    BEFORE_AFTER = "before_after"
    STORY = "story"
    DATA_RESEARCH = "data_research"
    CONTROVERSY = "controversy"
    COMPARISON = "comparison"
    TUTORIAL = "tutorial"
    RANT = "rant"
    REVIEW = "review"
    TESTIMONIAL = "testimonial"
    LIST = "list"
    TREND_ALERT = "trend_alert"
    BEHIND_SCENES = "behind_scenes"
    MYTH_BUSTING = "myth_busting"


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class PurchaseSignal(str, Enum):
    NONE = "none"
    LOW = "low"          # researching, browsing, curious
    MEDIUM = "medium"    # comparing, asking for recommendations
    HIGH = "high"        # ready to buy, asking where to purchase, "take my money"


@dataclass
class EnrichedItem:
    """A social post enriched with structured metadata."""
    # Identity (from original post)
    platform: str = ""
    post_id: str = ""
    url: str = ""
    author: str = ""

    # Original content (truncated)
    text: str = ""

    # Enrichment tags
    emotion: str = Emotion.NEUTRAL.value
    intent: str = Intent.OPINION.value
    sentiment: str = Sentiment.NEUTRAL.value
    hook_type: str = HookType.STORY.value
    purchase_signal: str = PurchaseSignal.NONE.value

    # Free-form extractions
    pain_point: str = ""        # "battery dies in 2 hours"
    unmet_need: str = ""        # "wish there was a budget option with long battery"
    narrative: str = ""         # "user switched from iPhone to Android and regrets it"
    topic_tags: list[str] = field(default_factory=list)  # ["smartphones", "battery", "iPhone"]
    brand_mentions: list[str] = field(default_factory=list)  # ["Apple", "iPhone 15"]

    # Engagement (from original post)
    likes: Optional[int] = None
    views: Optional[int] = None
    comments: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EnrichmentBatch:
    """Result of enriching a batch of posts."""
    items: list[EnrichedItem] = field(default_factory=list)
    total_processed: int = 0
    success_count: int = 0
    error: str = ""

    # Aggregate insights across the batch
    emotion_distribution: dict = field(default_factory=dict)
    intent_distribution: dict = field(default_factory=dict)
    top_pain_points: list[str] = field(default_factory=list)
    top_brands: list[str] = field(default_factory=list)
    top_topics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "items": [item.to_dict() for item in self.items],
            "total_processed": self.total_processed,
            "success_count": self.success_count,
            "error": self.error,
            "emotion_distribution": self.emotion_distribution,
            "intent_distribution": self.intent_distribution,
            "top_pain_points": self.top_pain_points,
            "top_brands": self.top_brands,
            "top_topics": self.top_topics,
        }
