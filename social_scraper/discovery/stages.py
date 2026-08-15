"""Explicit stages and terminal/planning outcomes for progressive discovery."""

from enum import Enum

from .scan_modes import ScanMode, coerce_scan_mode


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


# Explicit scan modes (social_scraper.discovery.scan_modes) mapped to the
# candidate collection stages they are allowed to execute. Feed modes stop
# at root evidence; the deep modes are research-run territory.
SCAN_MODE_STAGES = {
    ScanMode.TRENDS_SNAPSHOT: (),
    ScanMode.ROOT_SWEEP: (CandidateStage.ROOT_PROBE.value,),
    ScanMode.DEEP_READ: (
        CandidateStage.ROOT_PROBE.value,
        CandidateStage.DEEP_READ.value,
    ),
    ScanMode.HORIZONTAL_SYNTHESIS: (
        CandidateStage.ROOT_PROBE.value,
        CandidateStage.DEEP_READ.value,
        CandidateStage.HORIZONTAL_EXTRACTION.value,
    ),
    ScanMode.OPTIONAL_INTERPRETATION: (
        CandidateStage.ROOT_PROBE.value,
        CandidateStage.DEEP_READ.value,
        CandidateStage.HORIZONTAL_EXTRACTION.value,
        CandidateStage.OPTIONAL_ENRICHMENT.value,
    ),
}


def stages_for_scan_mode(mode) -> tuple[str, ...]:
    """Return the collection stages a scan mode may execute."""
    return SCAN_MODE_STAGES[coerce_scan_mode(mode)]
