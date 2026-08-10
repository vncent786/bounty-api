"""Per-lens feature extraction and deterministic ranking."""

from __future__ import annotations

from dataclasses import asdict

from social_scraper.lenses import ResearchLensSpec, evaluate_lens


_SIGNAL_FEATURES = {
    "pain_point", "unmet_need", "question", "desire", "desired_outcome",
    "workaround", "objection", "request", "purchase_trigger", "adoption",
    "switching", "rejection", "comparison", "behavior_change", "catalyst", "risk",
}


def features_from_analysis(analysis: dict) -> dict[str, float | None]:
    if analysis.get("status") not in {"supported", "insufficient_evidence"}:
        return {
            "behavior_evidence": None,
            "independent_voices": None,
            "durability": None,
            **{key: None for key in _SIGNAL_FEATURES},
        }
    behavior_values = {
        "observed_action": 1.0,
        "intended_action": 0.65,
        "informational_discussion": 0.35,
        "sentiment_only": 0.15,
        "unknown": None,
    }
    voice_count = analysis.get("independent_voice_count")
    if not isinstance(voice_count, (int, float)):
        voice_count = (analysis.get("coverage") or {}).get("independent_voices")
    voice_feature = min(float(voice_count) / 5.0, 1.0) if isinstance(voice_count, (int, float)) else None
    signals = analysis.get("signals") or []
    features: dict[str, float | None] = {
        "behavior_evidence": behavior_values.get(analysis.get("behavior_type")),
        "independent_voices": voice_feature,
        "durability": min(len(analysis.get("durability_evidence") or []) / 3.0, 1.0),
    }
    for key in _SIGNAL_FEATURES:
        matching = [item for item in signals if item.get("kind") == key]
        if not matching:
            features[key] = 0.0
            continue
        maximum_voices = max(
            int(item.get("independent_voices") or 0) for item in matching
        )
        features[key] = min(maximum_voices / 3.0, 1.0)
    return features


def rank_for_lens(candidates: list[dict], spec: ResearchLensSpec) -> list[dict]:
    """Rerank the full candidate set for one lens; never silently discard."""
    status_order = {
        "included": 0,
        "review": 1,
        "insufficient_evidence": 2,
        "excluded": 3,
        "error": 4,
    }
    rows = []
    for candidate in candidates:
        evaluation_input = {
            "candidate_id": candidate.get("candidate_id"),
            "features": features_from_analysis(
                candidate.get("conversation_analysis") or {}
            ),
        }
        evaluation = evaluate_lens(evaluation_input, spec)
        row = dict(candidate)
        row["lens_evaluation"] = asdict(evaluation)
        rows.append(row)
    return sorted(rows, key=lambda row: (
        status_order.get(row["lens_evaluation"]["status"], 99),
        -(row["lens_evaluation"]["score"]
          if row["lens_evaluation"]["score"] is not None else -1.0),
        -row["lens_evaluation"]["score_coverage"],
        str(row.get("candidate_id") or ""),
    ))
