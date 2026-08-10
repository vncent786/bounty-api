"""Execution of an already-capped stage plan through injected async handlers."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from .budgets import StageUsage
from .stages import COLLECTION_STAGES


@dataclass(frozen=True)
class StageHandlerResult:
    records_returned: int = 0
    external_calls: int = 0
    llm_calls: int = 0
    cache_hit: bool = False
    status: str = "complete"
    error_category: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tokens_estimated: bool = False
    # Discovery from a handler is evidence, not permission to expand this fixed plan.
    candidates: list[Mapping[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class StagedRunResult:
    run_id: str
    usages: list[StageUsage]
    handler_results: dict[str, dict[str, StageHandlerResult]]


StageHandler = Callable[..., Awaitable[StageHandlerResult | Mapping[str, Any] | None]]


class StagedRunner:
    def __init__(
        self,
        handlers: Mapping[str, StageHandler],
        usage_recorder: Callable[[StageUsage], Any] | None = None,
    ):
        self.handlers = dict(handlers)
        self.usage_recorder = usage_recorder

    @staticmethod
    def _work(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
        if "shared_work" in plan:
            return [dict(item) for item in plan["shared_work"]]
        work = []
        for candidate in plan.get("candidates", []):
            for stage, outcome in candidate.get("stages", {}).items():
                if outcome == "planned":
                    work.append({
                        "candidate_id": candidate["candidate_id"],
                        "candidate": candidate.get("candidate", {}),
                        "stage": stage,
                        "workspace_ids": [candidate.get("workspace_id")]
                        if candidate.get("workspace_id") else [],
                    })
        return work

    @staticmethod
    def _caps(plan: Mapping[str, Any]) -> dict[str, int]:
        if "effective_budget" in plan:
            budget = plan["effective_budget"]
            return {
                "root_probe": int(budget.get("root_probe_candidates", 0)),
                "deep_read": int(budget.get("deep_read_candidates", 0)),
                "horizontal_extraction": int(budget.get("horizontal_llm_candidates", 0)),
                "optional_enrichment": int(budget.get("optional_enrichments", 0)),
            }
        # A union consists of independently capped plans. Deduped union caps are the
        # count of explicitly planned shared work, never an inferred larger allowance.
        work = StagedRunner._work(plan)
        return {stage: sum(item["stage"] == stage for item in work) for stage in COLLECTION_STAGES}

    async def run(self, run_id: str, plan: Mapping[str, Any]) -> StagedRunResult:
        work = self._work(plan)
        caps = self._caps(plan)
        results: dict[str, dict[str, StageHandlerResult]] = {}
        usages: list[StageUsage] = []
        for stage in COLLECTION_STAGES:
            stage_work = [item for item in work if item["stage"] == stage][:caps[stage]]
            if not stage_work:
                continue
            if stage not in self.handlers:
                raise ValueError(f"missing handler for planned stage: {stage}")
            started = datetime.now(timezone.utc)
            considered = len(stage_work)
            records = external = llm = cache_hits = 0
            input_tokens = output_tokens = 0
            tokens_known = True
            estimated = False
            status = "complete"
            error_category = None
            stage_results: dict[str, StageHandlerResult] = {}
            for item in stage_work:
                context = {
                    "run_id": run_id,
                    "stage": stage,
                    "workspace_ids": item.get("workspace_ids", []),
                    "remaining_candidates": caps[stage] - len(stage_results),
                }
                handler = self.handlers[stage]
                handler_candidate = dict(item.get("candidate", {}))
                handler_candidate.setdefault("candidate_id", item["candidate_id"])
                value = (
                    await handler(handler_candidate, context)
                    if len(inspect.signature(handler).parameters) >= 2
                    else await handler(handler_candidate)
                )
                if value is None:
                    result = StageHandlerResult()
                elif isinstance(value, StageHandlerResult):
                    result = value
                else:
                    result = StageHandlerResult(**dict(value))
                if result.llm_calls < 0 or result.external_calls < 0 or result.records_returned < 0:
                    raise ValueError("handler usage must be nonnegative")
                if stage == "horizontal_extraction" and result.llm_calls > 1:
                    raise RuntimeError("handler exceeded per-candidate horizontal LLM budget")
                if stage == "horizontal_extraction" and llm + result.llm_calls > caps[stage]:
                    raise RuntimeError("handler exceeded horizontal LLM budget")
                stage_results[item["candidate_id"]] = result
                records += result.records_returned
                external += result.external_calls
                llm += result.llm_calls
                cache_hits += int(result.cache_hit)
                if result.input_tokens is None:
                    tokens_known = False
                else:
                    input_tokens += result.input_tokens
                if result.output_tokens is None:
                    tokens_known = False
                else:
                    output_tokens += result.output_tokens
                estimated = estimated or result.tokens_estimated
                if result.status not in {"complete", "empty"}:
                    status = result.status
                    error_category = error_category or result.error_category
            completed = datetime.now(timezone.utc)
            usage = StageUsage(
                discovery_run_id=run_id, stage=stage, started_at=started,
                completed_at=completed, candidates_considered=considered,
                candidates_processed=len(stage_results), records_returned=records,
                external_calls=external, llm_calls=llm, cache_hits=cache_hits,
                status=status, error_category=error_category,
                input_tokens=input_tokens if tokens_known and llm else None,
                output_tokens=output_tokens if tokens_known and llm else None,
                tokens_estimated=estimated,
            )
            usages.append(usage)
            results[stage] = stage_results
            if self.usage_recorder:
                recorded = self.usage_recorder(usage)
                if inspect.isawaitable(recorded):
                    await recorded
        return StagedRunResult(run_id, usages, results)
