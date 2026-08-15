"""Deterministic, source-null-preserving engagement baselines.

A baseline observation is comparable only when it has the same platform and
content-age bucket as the root being scored.  When creator size is known for
the root, observations are additionally restricted to the same creator-size
bucket.  Every percentile has its own observed-only sample: an absent count is
never changed to zero.

Percentiles use an inclusive empirical CDF (the percentage of observed
comparable values less than or equal to the target).  The baseline status is
conservative: ``baseline_sample_size`` is the smallest sample behind any
emitted raw-metric percentile.  Thus every emitted raw-metric percentile has a
supported sample before the overall status can be ``supported``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class AgeBucket:
    """A half-open content-age range, expressed in hours."""

    name: str
    minimum_hours: float
    maximum_hours: float | None

    def contains(self, age_hours: float) -> bool:
        return age_hours >= self.minimum_hours and (
            self.maximum_hours is None or age_hours < self.maximum_hours
        )


@dataclass(frozen=True)
class CreatorSizeBucket:
    """A half-open creator follower-count range."""

    name: str
    minimum_followers: int
    maximum_followers: int | None

    def contains(self, followers: int) -> bool:
        return followers >= self.minimum_followers and (
            self.maximum_followers is None or followers < self.maximum_followers
        )


# These public tuples are intentionally explicit and inspectable.  Bucket
# boundaries are half-open: the lower bound belongs to the bucket and the
# upper bound belongs to the next bucket.
AGE_BUCKETS = (
    AgeBucket("under_6h", 0, 6),
    AgeBucket("6h_to_24h", 6, 24),
    AgeBucket("1d_to_3d", 24, 72),
    AgeBucket("3d_to_7d", 72, 168),
    AgeBucket("7d_to_30d", 168, 720),
    AgeBucket("30d_plus", 720, None),
)

CREATOR_SIZE_BUCKETS = (
    CreatorSizeBucket("under_1k", 0, 1_000),
    CreatorSizeBucket("1k_to_10k", 1_000, 10_000),
    CreatorSizeBucket("10k_to_100k", 10_000, 100_000),
    CreatorSizeBucket("100k_to_1m", 100_000, 1_000_000),
    CreatorSizeBucket("1m_plus", 1_000_000, None),
)

DEFAULT_TRAILING_PERIOD = timedelta(days=90)
DEFAULT_MIN_SUPPORTED_SAMPLE_SIZE = 20

# Source-native aliases are selected in order.  They are never added together
# for a raw metric, which avoids double-counting connectors that expose both a
# native field and a canonical alias.
METRIC_SOURCE_FIELDS = {
    "like": ("likes", "upvotes"),
    "comment": ("comments", "replies"),
    "repost": ("reposts", "shares"),
    "view": ("views",),
}
PERCENTILE_FIELDS = tuple(f"{metric}_percentile" for metric in METRIC_SOURCE_FIELDS)
RAW_COUNT_FIELDS = (
    "likes",
    "upvotes",
    "comments",
    "replies",
    "reposts",
    "shares",
    "views",
    "bookmarks",
    "collects",
    "creator_followers",
)


@dataclass(frozen=True)
class EngagementBaselineConfig:
    """Inspectable policy for trailing observations and support strength."""

    trailing_period: timedelta = DEFAULT_TRAILING_PERIOD
    min_supported_sample_size: int = DEFAULT_MIN_SUPPORTED_SAMPLE_SIZE
    age_buckets: tuple[AgeBucket, ...] = AGE_BUCKETS
    creator_size_buckets: tuple[CreatorSizeBucket, ...] = CREATOR_SIZE_BUCKETS

    def __post_init__(self) -> None:
        if self.trailing_period <= timedelta(0):
            raise ValueError("trailing_period must be positive")
        if self.min_supported_sample_size < 1:
            raise ValueError("min_supported_sample_size must be at least one")
        if not self.age_buckets:
            raise ValueError("at least one age bucket is required")
        if not self.creator_size_buckets:
            raise ValueError("at least one creator-size bucket is required")


DEFAULT_CONFIG = EngagementBaselineConfig()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    canonical_record = _as_mapping(record.get("record"))
    for key in keys:
        if record.get(key) is not None:
            return record[key]
        if canonical_record.get(key) is not None:
            return canonical_record[key]
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    # A timezone-less source value is ambiguous and therefore unavailable.
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _observed_at(record: Mapping[str, Any]) -> datetime | None:
    observation = _as_mapping(record.get("observation"))
    provenance = _as_mapping(record.get("provenance"))
    for value in (
        record.get("observed_at"),
        record.get("source_observed_at"),
        record.get("collected_at"),
        observation.get("source_observed_at"),
        observation.get("collected_at"),
        provenance.get("source_observed_at"),
        provenance.get("fetched_at"),
    ):
        parsed = _parse_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def _published_at(record: Mapping[str, Any]) -> datetime | None:
    return _parse_timestamp(_first(record, "published_at", "created_at"))


def _engagement(record: Mapping[str, Any]) -> Mapping[str, Any]:
    direct = _as_mapping(record.get("engagement"))
    if direct:
        return direct
    observation = _as_mapping(record.get("observation"))
    nested = _as_mapping(observation.get("engagement"))
    if nested:
        return nested
    return _as_mapping(record.get("raw_counts"))


def _count(value: Any) -> int | None:
    """Return only an actually observed non-negative integer count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def raw_counts(record: Mapping[str, Any]) -> dict[str, int | None]:
    """Retain source-native counts; unsupported and malformed fields stay null."""
    values = _engagement(record)
    result = {field: _count(values.get(field)) for field in RAW_COUNT_FIELDS}
    if result["creator_followers"] is None:
        direct = _count(record.get("creator_followers"))
        if direct is None:
            direct = _count(_as_mapping(record.get("author")).get("follower_count"))
        if direct is None:
            direct = _count(
                _as_mapping(_as_mapping(record.get("record")).get("author")).get(
                    "follower_count"
                )
            )
        result["creator_followers"] = direct
    return result


def _metric_values(
    counts: Mapping[str, int | None],
) -> tuple[dict[str, int | None], dict[str, str | None]]:
    values: dict[str, int | None] = {}
    sources: dict[str, str | None] = {}
    for metric, fields in METRIC_SOURCE_FIELDS.items():
        values[metric] = None
        sources[metric] = None
        for field in fields:
            value = counts.get(field)
            if value is not None:
                values[metric] = value
                sources[metric] = field
                break
    return values, sources


def age_bucket_for(
    published_at: datetime | str | None,
    observed_at: datetime | str | None,
    *,
    buckets: tuple[AgeBucket, ...] = AGE_BUCKETS,
) -> str | None:
    """Return the explicit content-age bucket, or null when age is unavailable."""
    published = _parse_timestamp(published_at)
    observed = _parse_timestamp(observed_at)
    if published is None or observed is None or observed < published:
        return None
    age_hours = (observed - published).total_seconds() / 3_600
    return next((bucket.name for bucket in buckets if bucket.contains(age_hours)), None)


def creator_size_bucket_for(
    followers: int | None,
    *,
    buckets: tuple[CreatorSizeBucket, ...] = CREATOR_SIZE_BUCKETS,
) -> str | None:
    """Return the explicit creator-size bucket when follower count is observed."""
    followers = _count(followers)
    if followers is None:
        return None
    return next((bucket.name for bucket in buckets if bucket.contains(followers)), None)


def _is_root(record: Mapping[str, Any]) -> bool:
    record_type = _first(record, "record_type")
    if record_type is not None and str(record_type).strip().lower() not in {"post", "root"}:
        return False
    depth = _first(record, "depth")
    return depth in (None, 0)


def prepare_baseline_observation(
    record: Mapping[str, Any],
    *,
    observed_at: datetime | str | None = None,
    config: EngagementBaselineConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Normalize one root into the additive persistence representation.

    Identity and an aware observation timestamp are required.  Publication
    time, creator size, and engagement metrics remain nullable rather than
    being inferred.
    """
    if not isinstance(record, Mapping):
        raise ValueError("record must be a mapping")
    if not _is_root(record):
        raise ValueError("engagement baseline observations must be roots")
    platform = str(_first(record, "platform") or "").strip().lower()
    external_id = _first(record, "external_id", "post_id")
    external_id = "" if external_id is None else str(external_id).strip()
    observed = _parse_timestamp(observed_at) if observed_at is not None else _observed_at(record)
    if not platform:
        raise ValueError("platform is required")
    if not external_id:
        raise ValueError("root external_id/post_id is required")
    if observed is None:
        raise ValueError("an aware observed_at timestamp is required")

    published = _published_at(record)
    counts = raw_counts(record)
    age_seconds = None
    if published is not None and observed >= published:
        age_seconds = (observed - published).total_seconds()
    return {
        "platform": platform,
        "root_external_id": external_id,
        "observed_at": _iso(observed),
        "published_at": _iso(published) if published is not None else None,
        "content_age_seconds": age_seconds,
        "content_age_bucket": age_bucket_for(
            published, observed, buckets=config.age_buckets
        ),
        "creator_size_bucket": creator_size_bucket_for(
            counts["creator_followers"], buckets=config.creator_size_buckets
        ),
        "raw_counts": counts,
    }


def _percentile(target: int | float, observed: list[int | float]) -> float | None:
    if not observed:
        return None
    rank = sum(value <= target for value in observed)
    return round(100.0 * rank / len(observed), 6)


def calculate_engagement_percentiles(
    root: Mapping[str, Any],
    observed_records: Iterable[Mapping[str, Any]],
    *,
    as_of: datetime | str | None = None,
    config: EngagementBaselineConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Score a root against trailing, observed-only comparable records.

    ``as_of`` defaults to the root's own observation timestamp.  An explicit
    aware value is recommended for reproducible route evaluation.  Records
    outside the trailing period, non-roots, malformed timestamps, other
    platforms, other age buckets, and (when known) other creator-size buckets
    are excluded before metric-specific null filtering.
    """
    if not isinstance(root, Mapping):
        raise ValueError("root must be a mapping")
    root_counts = raw_counts(root)
    root_metrics, metric_sources = _metric_values(root_counts)
    platform = str(_first(root, "platform") or "").strip().lower()
    evaluation_time = _parse_timestamp(as_of) if as_of is not None else _observed_at(root)
    published = _published_at(root)
    age_bucket = age_bucket_for(
        published, evaluation_time, buckets=config.age_buckets
    )
    creator_bucket = creator_size_bucket_for(
        root_counts["creator_followers"], buckets=config.creator_size_buckets
    )

    if evaluation_time is not None:
        period_start = evaluation_time - config.trailing_period
    else:
        period_start = None

    comparable: list[tuple[dict[str, int | None], dict[str, int | None]]] = []
    if platform and evaluation_time is not None and age_bucket is not None and _is_root(root):
        for candidate in observed_records:
            if not isinstance(candidate, Mapping) or not _is_root(candidate):
                continue
            candidate_platform = str(_first(candidate, "platform") or "").strip().lower()
            if candidate_platform != platform:
                continue
            candidate_observed = _observed_at(candidate)
            candidate_published = _published_at(candidate)
            if (
                candidate_observed is None
                or candidate_observed < period_start
                or candidate_observed > evaluation_time
                or age_bucket_for(
                    candidate_published,
                    candidate_observed,
                    buckets=config.age_buckets,
                )
                != age_bucket
            ):
                continue
            candidate_counts = raw_counts(candidate)
            if creator_bucket is not None and creator_size_bucket_for(
                candidate_counts["creator_followers"],
                buckets=config.creator_size_buckets,
            ) != creator_bucket:
                continue
            candidate_metrics, _ = _metric_values(candidate_counts)
            comparable.append((candidate_counts, candidate_metrics))

    percentiles: dict[str, float | None] = {}
    sample_sizes: dict[str, int] = {}
    emitted_raw_samples: list[int] = []
    for metric in METRIC_SOURCE_FIELDS:
        target = root_metrics[metric]
        sample = [
            values[metric]
            for _, values in comparable
            if values[metric] is not None
        ]
        sample_sizes[metric] = len(sample)
        percentiles[f"{metric}_percentile"] = (
            _percentile(target, sample) if target is not None else None
        )
        if target is not None:
            emitted_raw_samples.append(len(sample))

    # The creator-adjusted feature compares total observed engagement per
    # follower only across records with the exact same metric-availability
    # signature.  Consequently no absent component is silently added as zero.
    target_followers = root_counts["creator_followers"]
    target_signature = frozenset(
        metric for metric, value in root_metrics.items() if value is not None
    )
    adjusted_sample: list[float] = []
    target_adjusted: float | None = None
    if target_followers is not None and target_followers > 0 and target_signature:
        target_adjusted = sum(root_metrics[metric] for metric in target_signature) / target_followers
        for candidate_counts, candidate_metrics in comparable:
            followers = candidate_counts["creator_followers"]
            signature = frozenset(
                metric for metric, value in candidate_metrics.items() if value is not None
            )
            if followers is None or followers <= 0 or signature != target_signature:
                continue
            adjusted_sample.append(
                sum(candidate_metrics[metric] for metric in signature) / followers
            )
    sample_sizes["creator_adjusted"] = len(adjusted_sample)
    creator_adjusted_percentile = (
        _percentile(target_adjusted, adjusted_sample)
        if target_adjusted is not None
        else None
    )

    baseline_sample_size = min(emitted_raw_samples) if emitted_raw_samples else 0
    if baseline_sample_size == 0:
        status = "unavailable"
    elif baseline_sample_size < config.min_supported_sample_size:
        status = "weak"
    else:
        status = "supported"

    age_seconds = None
    if published is not None and evaluation_time is not None and evaluation_time >= published:
        age_seconds = (evaluation_time - published).total_seconds()

    return {
        **percentiles,
        "creator_adjusted_percentile": creator_adjusted_percentile,
        "baseline_sample_size": baseline_sample_size,
        "baseline_status": status,
        "raw_counts": root_counts,
        "platform": platform or None,
        "content_age_seconds": age_seconds,
        "content_age_bucket": age_bucket,
        "creator_size_bucket": creator_bucket,
        "trailing_period_seconds": config.trailing_period.total_seconds(),
        "baseline_observed_from": _iso(period_start) if period_start is not None else None,
        "baseline_observed_through": (
            _iso(evaluation_time) if evaluation_time is not None else None
        ),
        "minimum_supported_sample_size": config.min_supported_sample_size,
        "metric_sample_sizes": sample_sizes,
        "metric_sources": metric_sources,
    }


def is_supported_outlier(
    baseline: Mapping[str, Any],
    *,
    threshold: float,
    metric: str | None = None,
) -> bool:
    """Return whether a supported baseline can satisfy an outlier component.

    Weak and unavailable baselines always return ``False``.  Creator-adjusted
    output is also required to have the configured supported sample count,
    because it can be sparser than the raw metric samples.
    """
    if not 0 <= threshold <= 100:
        raise ValueError("threshold must be between 0 and 100")
    if baseline.get("baseline_status") != "supported":
        return False

    fields = PERCENTILE_FIELDS + ("creator_adjusted_percentile",)
    if metric is not None:
        normalized = metric if metric.endswith("_percentile") else f"{metric}_percentile"
        if normalized not in fields:
            raise ValueError(f"unsupported percentile metric: {metric}")
        fields = (normalized,)

    sample_sizes = _as_mapping(baseline.get("metric_sample_sizes"))
    minimum = baseline.get("minimum_supported_sample_size")
    minimum = minimum if isinstance(minimum, int) and minimum > 0 else 1
    for field in fields:
        value = baseline.get(field)
        sample_key = field.removesuffix("_percentile")
        sample_size = sample_sizes.get(sample_key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= threshold
            and isinstance(sample_size, int)
            and sample_size >= minimum
        ):
            return True
    return False
