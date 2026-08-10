"""Pure deterministic planning for progressively deeper Discovery collection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .budgets import ScanBudget
from .prioritization import DEFAULT_METRIC_ORDER, prioritize_candidates
from .stages import StageOutcome, stages_for_depth


_STAGE_BUDGET = {
    "root_probe": "root_probe_candidates",
    "deep_read": "deep_read_candidates",
    "horizontal_extraction": "horizontal_llm_candidates",
    "optional_enrichment": "optional_enrichments",
}


@dataclass(frozen=True)
class WorkspacePlanRequest:
    workspace_id: str
    candidates: Iterable[Mapping[str, Any]]
    budget: ScanBudget
    required_depth: str
    metric_order: Sequence[str] = DEFAULT_METRIC_ORDER


class DiscoveryScheduler:
    """Plans bounded work only. It never invokes a source or an LLM."""

    @staticmethod
    def effective_budget(requested: ScanBudget, required_depth: str) -> ScanBudget:
        required = set(stages_for_depth(required_depth))
        root = requested.root_probe_candidates if "root_probe" in required else 0
        deep = min(requested.deep_read_candidates, root) if "deep_read" in required else 0
        horizontal = (
            min(requested.horizontal_llm_candidates, deep)
            if "horizontal_extraction" in required else 0
        )
        enrichment = (
            min(requested.optional_enrichments, horizontal)
            if "optional_enrichment" in required else 0
        )
        return ScanBudget(
            root_probe_candidates=root,
            deep_read_candidates=deep,
            horizontal_llm_candidates=horizontal,
            threads_per_platform=requested.threads_per_platform,
            comments_per_thread=requested.comments_per_thread,
            max_thread_depth=requested.max_thread_depth,
            optional_enrichments=enrichment,
        )

    def plan(
        self,
        candidates: Iterable[Mapping[str, Any]],
        budget: ScanBudget,
        required_depth: str,
        *,
        workspace_id: str | None = None,
        metric_order: Sequence[str] = DEFAULT_METRIC_ORDER,
    ) -> dict[str, Any]:
        required = stages_for_depth(required_depth)
        effective = self.effective_budget(budget, required_depth)
        ordered = prioritize_candidates(candidates, metric_order)
        remaining = {
            stage: getattr(effective, field) for stage, field in _STAGE_BUDGET.items()
        }
        rows = []
        for item in ordered:
            components = item["priority_components"]
            eligible = components["eligible"]
            stages = {
                "observed": StageOutcome.OBSERVED.value,
                "screening": (
                    StageOutcome.COMPLETE.value if eligible else StageOutcome.SCREENED_OUT.value
                ),
            }
            outcome = StageOutcome.COMPLETE.value if eligible else StageOutcome.SCREENED_OUT.value
            blocked = not eligible
            for stage in required:
                if blocked:
                    stages[stage] = (
                        StageOutcome.SKIPPED.value if not eligible
                        else StageOutcome.BUDGET_EXHAUSTED.value
                    )
                    continue
                if remaining[stage] <= 0:
                    stages[stage] = StageOutcome.BUDGET_EXHAUSTED.value
                    outcome = StageOutcome.BUDGET_EXHAUSTED.value
                    blocked = True
                else:
                    stages[stage] = StageOutcome.PLANNED.value
                    remaining[stage] -= 1
            rows.append({
                "candidate_id": item["candidate_id"],
                "workspace_id": workspace_id,
                "candidate": {
                    key: value for key, value in item.items()
                    if key not in {"priority_components", "candidate_id"}
                },
                "priority_components": components,
                "stages": stages,
                "outcome": outcome,
            })
        return {
            "workspace_id": workspace_id,
            "required_depth": required_depth,
            "required_stages": list(required),
            "requested_budget": budget.to_dict(),
            "effective_budget": effective.to_dict(),
            "candidates": rows,
        }

    def plan_workspaces(self, requests: Iterable[WorkspacePlanRequest]) -> dict[str, Any]:
        plans = []
        shared: dict[tuple[str, str], dict[str, Any]] = {}
        for request in sorted(requests, key=lambda value: value.workspace_id):
            plan = self.plan(
                request.candidates, request.budget, request.required_depth,
                workspace_id=request.workspace_id, metric_order=request.metric_order,
            )
            plans.append(plan)
            for candidate in plan["candidates"]:
                for stage, outcome in candidate["stages"].items():
                    if outcome != StageOutcome.PLANNED.value:
                        continue
                    key = (candidate["candidate_id"], stage)
                    work = shared.setdefault(key, {
                        "candidate_id": candidate["candidate_id"],
                        "candidate": candidate["candidate"],
                        "stage": stage,
                        "workspace_ids": [],
                    })
                    if request.workspace_id not in work["workspace_ids"]:
                        work["workspace_ids"].append(request.workspace_id)
        return {
            "workspace_plans": plans,
            "shared_work": sorted(
                shared.values(), key=lambda item: (item["candidate_id"], item["stage"])
            ),
        }
