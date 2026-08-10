"""Explicit stages and terminal/planning outcomes for progressive discovery."""

from enum import Enum


class CandidateStage(str, Enum):
    OBSERVED = "observed"
    SCREENING = "screening"
    ROOT_PROBE = "root_probe"
    DEEP_READ = "deep_read"
    HORIZONTAL_EXTRACTION = "horizontal_extraction"
    LENS_EVALUATION = "lens_evaluation"
    OPTIONAL_ENRICHMENT = "optional_enrichment"


class StageOutcome(str, Enum):
    OBSERVED = "observed"
    PLANNED = "planned"
    COMPLETE = "complete"
    CACHE_HIT = "cache_hit"
    SCREENED_OUT = "screened_out"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SKIPPED = "skipped"
    FAILED = "failed"
    MANUAL_PROMOTED = "manual_promoted"


COLLECTION_STAGES = (
    CandidateStage.ROOT_PROBE.value,
    CandidateStage.DEEP_READ.value,
    CandidateStage.HORIZONTAL_EXTRACTION.value,
    CandidateStage.OPTIONAL_ENRICHMENT.value,
)

DEPTH_STAGES = {
    "candidate": (),
    "root_probe": ("root_probe",),
    "deep_read": ("root_probe", "deep_read"),
    "horizontal_analysis": ("root_probe", "deep_read", "horizontal_extraction"),
    "horizontal_extraction": ("root_probe", "deep_read", "horizontal_extraction"),
    "custom_extraction": ("root_probe", "deep_read", "horizontal_extraction"),
    "optional_enrichment": (
        "root_probe", "deep_read", "horizontal_extraction", "optional_enrichment"
    ),
}


def stages_for_depth(required_depth: str) -> tuple[str, ...]:
    key = str(required_depth).strip().casefold().replace("-", "_")
    if key not in DEPTH_STAGES:
        raise ValueError(f"unknown required depth: {required_depth}")
    return DEPTH_STAGES[key]
