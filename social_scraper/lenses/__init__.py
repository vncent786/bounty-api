"""Research lens framework for Bounty Discovery."""

from .core import (
    CriterionResult,
    LensCriterion,
    LensEvaluation,
    ResearchLensSpec,
    evaluate_lens,
)

from .compiler import FEATURE_SOURCE_MAP, LensCompileError, compile_lens
from .investing import InvestingInterpretation, analyze_investing_lens
from .presets import LensPreset, get_lens_preset, list_lens_presets
from .storage import (
    ConflictError,
    LensStore,
    NotFoundError,
    ValidationError,
)

__all__ = [
    "ConflictError",
    "CriterionResult",
    "FEATURE_SOURCE_MAP",
    "InvestingInterpretation",
    "LensCompileError",
    "LensCriterion",
    "LensEvaluation",
    "LensPreset",
    "LensStore",
    "NotFoundError",
    "ResearchLensSpec",
    "ValidationError",
    "analyze_investing_lens",
    "compile_lens",
    "evaluate_lens",
    "get_lens_preset",
    "list_lens_presets",
]
