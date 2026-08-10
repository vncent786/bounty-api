"""Application service for workspace workflows and deterministic durable actions."""

from __future__ import annotations

from typing import Any, Mapping

from social_scraper.discovery import DiscoveryScheduler, DiscoveryStore, ScanBudget
from social_scraper.lenses.storage import LensStore, NotFoundError as LensNotFoundError

from .storage import NotFoundError, ValidationError, WorkspaceStore


class WorkspaceService:
    """Coordinates stores while keeping all live source/LLM work out of API requests."""

    def __init__(self, store: WorkspaceStore, lens_store: LensStore,
                 discovery_store: DiscoveryStore,
                 scheduler: DiscoveryScheduler | None = None):
        self.store = store
        self.lens_store = lens_store
        self.discovery_store = discovery_store
        self.scheduler = scheduler or DiscoveryScheduler()

    def _validate_lens(self, workspace_id: str, lens_id: str | None,
                       lens_version: int | None) -> None:
        if (lens_id is None) != (lens_version is None):
            raise ValidationError("lens_id and lens_version must be supplied together")
        if lens_id is not None:
            try:
                self.lens_store.get_lens_version(workspace_id, lens_id, lens_version)  # type: ignore[arg-type]
            except LensNotFoundError as exc:
                raise ValidationError("lens version does not exist in this workspace") from exc

    def create_project(self, workspace_id: str, *, name: str, description: str = "",
                       default_geo: str = "", first_subject: Mapping[str, Any] | None = None
                       ) -> dict[str, Any]:
        subject_data = dict(first_subject) if first_subject is not None else None
        if subject_data is not None:
            self._validate_lens(workspace_id, subject_data.get("lens_id"),
                                subject_data.get("lens_version"))
        with self.store.transaction() as connection:
            project = self.store.create_project(
                workspace_id, name, description=description, default_geo=default_geo,
                connection=connection,
            )
            subject = None
            if subject_data is not None:
                if not subject_data.get("geo"):
                    subject_data["geo"] = default_geo
                subject = self.store.create_subject(
                    workspace_id, project["id"], connection=connection, **subject_data,
                )
        return {"project": project, "first_subject": subject}

    def create_subject(self, workspace_id: str, project_id: str, **values: Any) -> dict[str, Any]:
        self._validate_lens(workspace_id, values.get("lens_id"), values.get("lens_version"))
        return self.store.create_subject(workspace_id, project_id, **values)

    def update_subject(self, workspace_id: str, project_id: str, subject_id: str,
                       **changes: Any) -> dict[str, Any]:
        current = self.store.get_subject(workspace_id, project_id, subject_id)
        lens_id = changes.get("lens_id", current["lens_id"])
        lens_version = changes.get("lens_version", current["lens_version"])
        self._validate_lens(workspace_id, lens_id, lens_version)
        return self.store.update_subject(workspace_id, project_id, subject_id, **changes)

    def create_action(self, workspace_id: str, project_id: str, action_type: str, **values: Any
                      ) -> tuple[dict[str, Any], bool]:
        """Persist first, then perform only deterministic local workflow operations."""
        action, created = self.store.create_action(
            workspace_id, project_id, action_type, **values,
        )
        if not created:
            return action, False
        if action_type not in {"run_discovery", "promote_candidate",
                               "start_monitoring", "pause_monitoring"}:
            return action, True
        claimed = self.store.claim_action(workspace_id, project_id, action["id"])
        if claimed is None:  # defensive; a freshly inserted action is claimable
            return self.store.get_action(workspace_id, project_id, action["id"]), True
        token = claimed["lease_token"]
        try:
            result = self._perform(claimed)
            action = self.store.complete_action(
                workspace_id, project_id, action["id"], result=result, lease_token=token,
            )
        except Exception as exc:
            self.store.fail_action(
                workspace_id, project_id, action["id"], error_category=type(exc).__name__,
                result={"detail": str(exc)}, lease_token=token,
            )
            raise
        return action, True

    def _perform(self, action: Mapping[str, Any]) -> dict[str, Any]:
        action_type = action["action_type"]
        workspace_id, project_id = action["workspace_id"], action["project_id"]
        payload, requested_budget = action["payload"], action["requested_budget"]
        if action_type == "run_discovery":
            subject = (self.store.get_subject(workspace_id, project_id, action["subject_id"])
                       if action.get("subject_id") else None)
            budget_data = requested_budget or (subject or {}).get("budget") or {}
            budget = ScanBudget.from_dict(budget_data)
            required_depth = str(payload.get("required_depth") or "candidate")
            plan = self.scheduler.plan(
                payload.get("candidates", []), budget, required_depth,
                workspace_id=workspace_id, metric_order=payload.get("priority_metrics", (
                    "recency", "volume", "growth", "category_match", "already_processed")),
            )
            run = self.discovery_store.create_research_run(
                workspace_id=workspace_id,
                source_discovery_run_id=payload.get("source_discovery_run_id"),
                requested_budget=budget.to_dict(), effective_budget=plan["effective_budget"],
                plan=plan, status="planned",
            )
            return {"phase": "planned", "collection_status": "not_started",
                    "run_id": run["id"], "effective_budget": run["effective_budget"]}
        if action_type == "promote_candidate":
            run_id = payload.get("run_id") or (action.get("target_id")
                                                if action.get("target_type") == "research_run" else None)
            candidate_id = payload.get("candidate_id") or (action.get("target_id")
                                                           if action.get("target_type") == "candidate" else None)
            if not run_id or not candidate_id:
                raise ValidationError("promote_candidate requires run_id and candidate_id")
            run = self.discovery_store.get_research_run(str(run_id))
            if run is None or run["workspace_id"] != workspace_id:
                raise NotFoundError("research run not found")
            promoted = self.discovery_store.promote_research_candidate(str(run_id), str(candidate_id))
            return {"run_id": run_id, "candidate": promoted}
        if action_type in {"start_monitoring", "pause_monitoring"}:
            if not action.get("subject_id"):
                raise ValidationError(f"{action_type} requires subject_id")
            subject = self.update_subject(
                workspace_id, project_id, action["subject_id"],
                active=action_type == "start_monitoring",
            )
            return {"subject": subject}
        # Other action types remain durable queue items for a dedicated worker.
        return {"phase": "accepted", "collection_status": "not_started"}