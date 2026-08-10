"""Research lens framework for Bounty Discovery."""

from .core import (
    CriterionResult,
    LensCriterion,
    LensEvaluation,
    ResearchLensSpec,
    evaluate_lens,
)

from .investing import InvestingInterpretation, analyze_investing_lens
from .presets import LensPreset, get_lens_preset, list_lens_presets

__all__ = [
    "CriterionResult",
    "InvestingInterpretation",
    "LensCriterion",
    "LensEvaluation",
    "LensPreset",
    "ResearchLensSpec",
    "analyze_investing_lens",
    "evaluate_lens",
    "get_lens_preset",
    "list_lens_presets",
]
