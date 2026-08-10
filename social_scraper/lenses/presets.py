"""Neutral and use-case lens presets over one horizontal evidence corpus."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LensPreset:
    preset_id: str
    name: str
    purpose: str
    suggested_signal_kinds: tuple[str, ...]
    questions: tuple[str, ...]
    optional_enrichments: tuple[str, ...] = ()
    requires_monitored_subject: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


_PRESETS = (
    LensPreset(
        preset_id="horizontal-explorer",
        name="Horizontal Explorer",
        purpose="Inspect cited conversation evidence without imposing a use-case ranking.",
        suggested_signal_kinds=(),
        questions=(
            "What are people saying and doing?",
            "Which claims repeat across independent voices or threads?",
            "What is missing or unavailable in source coverage?",
        ),
    ),
    LensPreset(
        preset_id="investing-social-arbitrage",
        name="Investing / Social Arbitrage",
        purpose="Interpret behavior change as possible economic exposure without changing the corpus.",
        suggested_signal_kinds=(
            "adoption", "switching", "rejection", "behavior_change",
            "catalyst", "risk", "pain_point",
        ),
        questions=(
            "Who could be economically exposed, and through what mechanism?",
            "Could the observed behavior be material?",
            "Is the information already mainstream, company-acknowledged, or reflected in price?",
            "What evidence would invalidate the thesis?",
        ),
        optional_enrichments=("company_exposure", "market_awareness", "price_context"),
    ),
    LensPreset(
        preset_id="product-opportunity",
        name="Product Opportunities",
        purpose="Find recurring problems and desired outcomes that may justify a product or feature.",
        suggested_signal_kinds=(
            "pain_point", "unmet_need", "desired_outcome", "workaround",
            "request", "question", "switching", "rejection",
        ),
        questions=(
            "What outcome are people trying to achieve?",
            "What do they currently do instead?",
            "Which frustrations or requests recur across independent voices?",
            "What evidence suggests urgency or willingness to change behavior?",
        ),
    ),
    LensPreset(
        preset_id="marketing-intelligence",
        name="Marketing Intelligence",
        purpose="Track changing language, objections, triggers, and comparisons around a monitored subject.",
        suggested_signal_kinds=(
            "objection", "purchase_trigger", "comparison", "pain_point",
            "desired_outcome", "question", "adoption", "rejection",
        ),
        questions=(
            "What language do people use for the problem and desired result?",
            "What objections or triggers are appearing now?",
            "What alternatives are compared, adopted, or rejected?",
            "Which changes are evidenced versus merely narrated?",
        ),
        requires_monitored_subject=True,
    ),
)


def list_lens_presets() -> list[dict]:
    return [preset.to_dict() for preset in _PRESETS]


def get_lens_preset(preset_id: str) -> LensPreset:
    for preset in _PRESETS:
        if preset.preset_id == preset_id:
            return preset
    raise KeyError(preset_id)
