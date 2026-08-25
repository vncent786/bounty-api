"""
Scheduler for periodic zone collection and the discovery radar.

Runs as a background asyncio task inside the FastAPI app.
Zone collection checks every 30 minutes for zones that are due, then
collects + clusters + enriches.

The radar (Task 1.3b) runs one tick per loop interval: reconcile durable
geography/subject schedules, claim due work through the replica-safe
lease API, and execute only the two feed modes (``trends_snapshot`` /
``root_sweep``). Deep reads, horizontal synthesis, optional
interpretation and any LLM call are structurally out of scope here.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 1800  # 30 minutes
_SCHEDULER_TASK = None

# --- Radar configuration (environment/workspace controlled) ----------------
# No hardcoded default regions: BOUNTY_RADAR_GEOS is the single source of
# geography truth. Empty/missing means "no geography schedules".

RADAR_ENV_GEOS = "BOUNTY_RADAR_GEOS"
RADAR_ENV_ENABLED = "BOUNTY_RADAR_ENABLED"
RADAR_ENV_TRENDS_INTERVAL = "BOUNTY_RADAR_TRENDS_INTERVAL_MINUTES"
RADAR_ENV_ROOT_INTERVAL = "BOUNTY_RADAR_ROOT_INTERVAL_MINUTES"
RADAR_ENV_ROOT_MAX_CANDIDATES = "BOUNTY_RADAR_ROOT_MAX_CANDIDATES"
RADAR_ENV_LEASE_MINUTES = "BOUNTY_RADAR_LEASE_MINUTES"
RADAR_ENV_CLAIM_LIMIT = "BOUNTY_RADAR_CLAIM_LIMIT"
INVESTING_RADAR_ENV_ENABLED = "BOUNTY_INVESTING_RADAR_ENABLED"
INVESTING_RADAR_ENV_INTERVAL_MINUTES = "BOUNTY_INVESTING_RADAR_INTERVAL_MINUTES"

_INVESTING_RADAR_INTERVAL_MINUTES = 360
_INVESTING_RADAR_FAILED_RETRY_MINUTES = 30
_INVESTING_RADAR_STALE_RUNNING_MINUTES = 120
SOCIAL_PULSE_ENV_ENABLED = "BOUNTY_SOCIAL_PULSE_ENABLED"
SOCIAL_PULSE_ENV_INTERVAL_MINUTES = "BOUNTY_SOCIAL_PULSE_INTERVAL_MINUTES"
_SOCIAL_PULSE_INTERVAL_MINUTES = 720
_SOCIAL_PULSE_FAILED_RETRY_MINUTES = 0
_SOCIAL_PULSE_STALE_RUNNING_MINUTES = 180

_RADAR_TRENDS_INTERVAL_MINUTES = 1440   # daily trends snapshot
_RADAR_ROOT_INTERVAL_MINUTES = 10080    # weekly root sweep
_RADAR_ROOT_MAX_CANDIDATES = 100
_RADAR_LEASE_MINUTES = 10
_RADAR_CLAIM_LIMIT = 100

_TRENDS_SNAPSHOT = "trends_snapshot"
_ROOT_SWEEP = "root_sweep"
_FEED_SCAN_MODES = frozenset({_TRENDS_SNAPSHOT, _ROOT_SWEEP})
_SUBJECT_HEALTHY_GATE_STATUS = frozenset({"complete", "empty"})


def _parse_positive_int_env(environ: Mapping[str, str], name: str, default: int) -> int:
    """Read a positive integer setting; invalid values fail closed."""
    raw = environ.get(name)
    if raw is None:
        return default
    value = str(raw).strip()
    if not value:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a positive integer, got {value!r}"
        ) from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return parsed


def _parse_radar_geos(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Comma-separated geographies, uppercased and deduplicated in order."""
    raw = environ.get(RADAR_ENV_GEOS, "") or ""
    geos: list[str] = []
    for item in str(raw).split(","):
        geo = item.strip().upper()
        if geo and geo not in geos:
            geos.append(geo)
    return tuple(geos)


def _parse_radar_enabled(environ: Mapping[str, str]) -> bool:
    """Parse an explicit boolean; configuration typos fail closed."""
    raw = environ.get(RADAR_ENV_ENABLED)
    if raw is None:
        return True
    value = str(raw).strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{RADAR_ENV_ENABLED} must be one of true/false, 1/0, yes/no, on/off; "
        f"got {raw!r}"
    )


def _parse_bool_env(
    environ: Mapping[str, str],
    name: str,
    *,
    default: bool,
) -> bool:
    raw = environ.get(name)
    if raw is None:
        return default
    value = str(raw).strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of true/false, 1/0, yes/no, on/off")


@dataclass(frozen=True)
class RadarConfig:
    """Immutable radar settings parsed from the environment (fail-closed)."""

    enabled: bool = True
    geos: tuple[str, ...] = field(default=())
    trends_interval_minutes: int = _RADAR_TRENDS_INTERVAL_MINUTES
    root_interval_minutes: int = _RADAR_ROOT_INTERVAL_MINUTES
    root_max_candidates: int = _RADAR_ROOT_MAX_CANDIDATES
    lease_minutes: int = _RADAR_LEASE_MINUTES
    claim_limit: int = _RADAR_CLAIM_LIMIT

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        if isinstance(self.geos, (str, bytes)):
            raise ValueError("geos must be a sequence of geography codes")
        normalized: list[str] = []
        for raw in self.geos:
            geo = str(raw or "").strip().upper()
            if not geo:
                raise ValueError("geos entries must be non-empty geography codes")
            if geo not in normalized:
                normalized.append(geo)
        object.__setattr__(self, "geos", tuple(normalized))
        for name in (
            "trends_interval_minutes", "root_interval_minutes",
            "root_max_candidates", "lease_minutes", "claim_limit",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "RadarConfig":
        env = os.environ if environ is None else environ
        return cls(
            enabled=_parse_radar_enabled(env),
            geos=_parse_radar_geos(env),
            trends_interval_minutes=_parse_positive_int_env(
                env, RADAR_ENV_TRENDS_INTERVAL, _RADAR_TRENDS_INTERVAL_MINUTES,
            ),
            root_interval_minutes=_parse_positive_int_env(
                env, RADAR_ENV_ROOT_INTERVAL, _RADAR_ROOT_INTERVAL_MINUTES,
            ),
            root_max_candidates=_parse_positive_int_env(
                env, RADAR_ENV_ROOT_MAX_CANDIDATES, _RADAR_ROOT_MAX_CANDIDATES,
            ),
            lease_minutes=_parse_positive_int_env(
                env, RADAR_ENV_LEASE_MINUTES, _RADAR_LEASE_MINUTES,
            ),
            claim_limit=_parse_positive_int_env(
                env, RADAR_ENV_CLAIM_LIMIT, _RADAR_CLAIM_LIMIT,
            ),
        )


class RadarScheduler:
    """One-tick radar executor: reconcile, claim, execute — never a loop.

    Every dependency is injectable (stores, discovery, broker, gate
    check, clock) so ticks are deterministic under test; production uses
    the dashboard singletons and a fresh wall clock. Only feed modes can
    be reconciled or executed; a discovery run plus gate outcomes decide
    complete/partial/error and comparability, with source health copied
    from observed records and never invented.
    """

    def __init__(
        self,
        *,
        config: RadarConfig,
        discovery_store: Any,
        workspace_store: Any,
        topdown: Any,
        broker: Any = None,
        clock: Callable[[], datetime] | None = None,
        gate_check: Callable | None = None,
    ):
        self.config = config
        self.discovery_store = discovery_store
        self.workspace_store = workspace_store
        self.topdown = topdown
        self.broker = broker
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._heartbeat_interval_seconds = max(
            1.0, float(self.config.lease_minutes * 60) / 3.0
        )
        if gate_check is None:
            from social_scraper.monitoring.conversation_gate import (
                gate_check_keyword as gate_check,
            )
        self._gate_check = gate_check

    # -- public one-tick entry point -----------------------------------

    async def tick(self) -> dict | None:
        """Run one reconcile + just-in-time claim/execute pass."""
        if not self.config.enabled:
            logger.debug("Radar scheduler disabled (BOUNTY_RADAR_ENABLED)")
            return None
        summary = self._reconcile(self._clock())
        outcomes: list[dict] = []
        claimed = 0
        for _ in range(self.config.claim_limit):
            claims = self.discovery_store.claim_due_schedules(
                now=self._clock(),
                lease_minutes=self.config.lease_minutes,
                limit=1,
            )
            if not claims:
                break
            claim = claims[0]
            claimed += 1
            try:
                outcomes.append(await self._execute_with_lease(claim))
            except asyncio.CancelledError:
                released = self.discovery_store.release_radar_schedule_claim(
                    claim["schedule_id"], claim["claim_token"], now=self._clock(),
                )
                if not released:
                    logger.warning(
                        "Radar: cancelled claim %s could not be released; "
                        "its token was already lost or completed",
                        claim.get("schedule_id"),
                    )
                raise
            except Exception as exc:
                logger.error(
                    f"Radar: claim {claim.get('schedule_id')} failed: {exc}",
                    exc_info=True,
                )
                outcomes.append(await self._fail_claim(claim, exc))
        summary["claimed"] = claimed
        summary["outcomes"] = outcomes
        logger.info(
            f"Radar tick: reconciled {summary['created']} created / "
            f"{summary['disabled']} disabled, {claimed} claimed, "
            f"{sum(1 for item in outcomes if item['status'] == 'complete')} complete"
        )
        return summary

    # -- reconciliation --------------------------------------------------

    def _reconcile(self, now: datetime) -> dict:
        """Refresh desired schedules; disable stale managed scopes.

        Upserts never reset an existing ``next_run_at`` (storage
        guarantee), so reconciliation is idempotent per tick and a
        disable/reactivate cycle keeps due time and history intact.
        """
        geos = self.config.geos
        created = 0
        for geo in geos:
            self.discovery_store.upsert_radar_schedule(
                scan_mode=_TRENDS_SNAPSHOT, scope_type="geography", geo=geo,
                interval_minutes=self.config.trends_interval_minutes,
                next_run_at=now, now=now,
            )
            self.discovery_store.upsert_radar_schedule(
                scan_mode=_ROOT_SWEEP, scope_type="geography", geo=geo,
                interval_minutes=self.config.root_interval_minutes,
                next_run_at=now, now=now,
            )
            created += 2

        active_subject_ids: set[str] = set()
        for subject in self.workspace_store.list_active_subjects():
            geo = str(
                subject.get("geo") or subject.get("project_default_geo") or ""
            ).strip().upper()
            if not geo:
                logger.info(
                    f"Radar: subject {subject.get('id')} "
                    f"({subject.get('name')!r}) has no effective geo; skipping"
                )
                continue
            self.discovery_store.upsert_radar_schedule(
                scan_mode=_ROOT_SWEEP, scope_type="subject", geo=geo,
                subject_id=subject["id"],
                interval_minutes=int(subject["cadence_minutes"]),
                next_run_at=now, now=now,
            )
            active_subject_ids.add(subject["id"])
            created += 1

        disabled = 0
        for row in self.discovery_store.list_radar_schedules(enabled=True):
            stale = False
            if row["scope_type"] == "geography":
                stale = row["geo"] not in geos
            elif row["scope_type"] == "subject":
                stale = row["subject_id"] not in active_subject_ids
            if stale:
                self.discovery_store.set_radar_schedule_enabled(
                    row["id"], enabled=False, now=now,
                )
                disabled += 1
                logger.info(
                    f"Radar: disabled stale {row['scope_type']} schedule "
                    f"{row['id']} (mode={row['scan_mode']}, geo={row['geo']})"
                )
        return {"created": created, "disabled": disabled}

    # -- claim execution ---------------------------------------------------

    async def _lease_heartbeat(self, claim: dict) -> None:
        """Renew a live claim until cancelled; fail if its token is lost."""
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            renewed = self.discovery_store.renew_radar_schedule_claim(
                claim["schedule_id"], claim["claim_token"],
                now=self._clock(), lease_minutes=self.config.lease_minutes,
            )
            if renewed is None:
                raise RuntimeError("radar schedule lease was lost during execution")

    async def _execute_with_lease(self, claim: dict) -> dict:
        """Execute one claim while renewing its token-checked lease."""
        execution = asyncio.create_task(self._execute_claim(claim))
        heartbeat = asyncio.create_task(self._lease_heartbeat(claim))
        try:
            done, _ = await asyncio.wait(
                {execution, heartbeat}, return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done:
                error = heartbeat.exception()
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                raise error or RuntimeError("radar lease heartbeat stopped unexpectedly")
            return await execution
        finally:
            heartbeat.cancel()
            if not execution.done():
                execution.cancel()
            await asyncio.gather(execution, heartbeat, return_exceptions=True)

    async def _execute_claim(self, claim: dict) -> dict:
        mode = str(claim.get("scan_mode") or "")
        scope = str(claim.get("scope_type") or "")
        if scope == "geography" and mode in _FEED_SCAN_MODES:
            return await self._run_geography(claim)
        if scope == "subject" and mode == _ROOT_SWEEP:
            return await self._run_subject(claim)
        # Anything else (including any research-run mode that somehow got
        # in) is refused loudly rather than executed.
        return self._complete(
            claim, status="error", comparable=False,
            error_category="unsupported_radar_schedule",
        )

    async def _fail_claim(self, claim: dict, exc: Exception) -> dict:
        """Resolve a claim whose execution raised; keep failures honest."""
        try:
            return self._complete(
                claim, status="error", comparable=False,
                error_category=f"executor:{type(exc).__name__}",
                source_health=None,
            )
        except Exception:
            logger.error(
                f"Radar: could not release claim {claim.get('schedule_id')}; "
                "the expired lease will be reclaimed",
                exc_info=True,
            )
            return {
                "schedule_id": claim.get("schedule_id"),
                "status": "error",
                "comparable": False,
                "released": False,
            }

    async def _run_geography(self, claim: dict) -> dict:
        geo = str(claim["geo"])
        mode = str(claim["scan_mode"])
        if mode == _ROOT_SWEEP:
            candidates = await self.topdown.scan_all(
                geo=geo, mode=_ROOT_SWEEP,
                gate_max=self.config.root_max_candidates,
            )
        else:
            candidates = await self.topdown.scan_all(geo=geo, mode=_TRENDS_SNAPSHOT)

        run_id = str(getattr(self.topdown, "last_run_id", "") or "").strip() or None
        run = self.discovery_store.get_discovery_run(run_id) if run_id else None
        if run is None:
            logger.error(f"Radar: geo {geo} scan produced no persisted run")
            return self._complete(
                claim, status="error", comparable=False,
                error_category="missing_discovery_run", source_health=None,
            )
        if run["status"] == "error":
            return self._complete(
                claim, status="error", comparable=False,
                discovery_run_id=run_id,
                source_health=run["source_health"],
                error_category=str(run.get("error_category") or "discovery_error"),
            )
        if run["status"] == "partial":
            if mode == _ROOT_SWEEP:
                health_entries = [
                    dict(entry) for entry in (run.get("source_health") or [])
                ]
                gate_statuses = [
                    str(getattr(item, "gate_status", "") or "not_checked")
                    for item in candidates
                ]
                for item in candidates:
                    observed = getattr(item, "gate_source_health", None)
                    if observed is not None:
                        health_entries.extend(dict(entry) for entry in observed)
                unchecked = sum(
                    1 for status in gate_statuses if status == "not_checked"
                )
                health_entries.append({
                    "source": "root_sweep_coverage",
                    "status": "partial",
                    "error_category": "discovery_partial",
                    "candidates_total": len(candidates),
                    "candidates_checked": len(candidates) - unchecked,
                    "candidates_unchecked": unchecked,
                })
                source_health = health_entries
            else:
                source_health = run["source_health"]
            return self._complete(
                claim, status="partial", comparable=False,
                discovery_run_id=run_id,
                source_health=source_health,
                error_category=str(run.get("error_category") or "discovery_partial"),
            )

        run_comparable = bool(run.get("comparable"))
        if mode != _ROOT_SWEEP:
            return self._complete(
                claim, status="complete", comparable=run_comparable,
                discovery_run_id=run_id, source_health=run["source_health"],
            )

        # Root-sweep health combines the persisted discovery health and every
        # observed gate health entry. Unknown (None) remains unknown unless an
        # explicit derived coverage record is added below.
        health_entries: list[dict] = []
        health_observed = run.get("source_health") is not None
        if health_observed:
            health_entries.extend(dict(entry) for entry in run["source_health"])
        gate_statuses: list[str] = []
        for item in candidates:
            gate_statuses.append(
                str(getattr(item, "gate_status", "") or "not_checked")
            )
            item_health = getattr(item, "gate_source_health", None)
            if item_health is not None:
                health_observed = True
                health_entries.extend(dict(entry) for entry in item_health)

        unchecked = sum(1 for status in gate_statuses if status == "not_checked")
        degraded = [
            status for status in gate_statuses
            if status not in {"complete", "empty", "not_checked"}
        ]
        if unchecked or degraded:
            if unchecked:
                error_category = (
                    f"gate_coverage_capped:{unchecked} unchecked of {len(candidates)}"
                )
                coverage_reason = "candidate_cap"
            elif any(status in {"failed", "error"} for status in degraded):
                error_category = "gate_source_failed"
                coverage_reason = "gate_source_failed"
            else:
                error_category = "gate_source_partial"
                coverage_reason = "gate_source_partial"
            health_entries.append({
                "source": "root_sweep_coverage",
                "status": "partial",
                "error_category": coverage_reason,
                "candidates_total": len(candidates),
                "candidates_checked": len(candidates) - unchecked,
                "candidates_unchecked": unchecked,
            })
            return self._complete(
                claim, status="partial", comparable=False,
                discovery_run_id=run_id, source_health=health_entries,
                error_category=error_category,
            )

        return self._complete(
            claim, status="complete", comparable=run_comparable,
            discovery_run_id=run_id,
            source_health=health_entries if health_observed else None,
        )

    async def _run_subject(self, claim: dict) -> dict:
        subject_id = str(claim["subject_id"])
        subject = next(
            (item for item in self.workspace_store.list_active_subjects()
             if item["id"] == subject_id),
            None,
        )
        if subject is None:
            logger.info(f"Radar: subject {subject_id} no longer active at execution")
            return self._complete(
                claim, status="error", comparable=False,
                error_category="subject_inactive", source_health=None,
            )

        queries = [subject["name"]]
        for alias in subject.get("aliases") or []:
            if alias.get("kind") == "include":
                queries.append(alias["alias"])
        queries = list(dict.fromkeys(queries))

        platforms = [
            str(item).strip() for item in subject.get("platforms") or []
            if str(item).strip()
        ] or None

        results = []
        for query in queries:
            # Root posts only: no thread hydration, no LLM, ever.
            results.append(await self._gate_check(
                self.broker, query,
                platforms=platforms, max_threads_per_platform=0,
            ))

        statuses = [str(result.status or "") for result in results]
        source_health: list[dict] = []
        source_health_observed = False
        for query, result in zip(queries, results):
            observed = getattr(result, "source_health", None)
            if observed is not None:
                source_health_observed = True
                for entry in observed:
                    annotated = dict(entry)
                    annotated.setdefault("query", query)
                    source_health.append(annotated)
        persisted_health = source_health if source_health_observed else None

        if statuses and all(s in _SUBJECT_HEALTHY_GATE_STATUS for s in statuses):
            # Healthy-and-empty is comparable: an explicit observation that
            # nobody is discussing the subject needs no discovery run.
            return self._complete(
                claim, status="complete", comparable=True,
                discovery_run_id=None, source_health=persisted_health,
            )
        if statuses and all(s == "failed" for s in statuses):
            return self._complete(
                claim, status="error", comparable=False,
                discovery_run_id=None, source_health=persisted_health,
                error_category="gate_all_failed",
            )
        return self._complete(
            claim, status="partial", comparable=False,
            discovery_run_id=None, source_health=persisted_health,
            error_category="gate_partial_source",
        )

    def _complete(
        self,
        claim: dict,
        *,
        status: str,
        comparable: bool,
        discovery_run_id: str | None = None,
        source_health: list[dict] | None = None,
        error_category: str | None = None,
    ) -> dict:
        receipt = self.discovery_store.complete_schedule_attempt(
            claim["schedule_id"], claim["claim_token"],
            status=status, comparable=comparable,
            discovery_run_id=discovery_run_id,
            source_health=source_health,
            error_category=error_category,
            started_at=claim.get("claimed_at"),
            now=self._clock(),
        )
        return {
            "schedule_id": claim["schedule_id"],
            "status": status,
            "comparable": comparable and status == "complete",
            "discovery_run_id": discovery_run_id,
            "released": True,
        }


# --- Background loop: zone collection + one radar tick ----------------------


async def _scheduler_loop():
    """Background loop that collects due zones and ticks the radar."""
    from apis.social_search_api import build_default_broker
    from apis.dashboard_api import _get_registry
    from social_scraper.monitoring import TrendMonitor
    from social_scraper.enrichment import EnrichmentEngine

    registry = _get_registry()
    broker = build_default_broker(route_timeout_seconds=240.0)
    monitor = TrendMonitor(registry, broker)
    engine = EnrichmentEngine(llm_call_fn=_llm_call)

    logger.info("Trend monitoring scheduler started")

    while True:
        try:
            due_zones = registry.list_due()
            if due_zones:
                logger.info(f"Scheduler: {len(due_zones)} zones due for collection")

            for zone in due_zones:
                try:
                    logger.info(f"Scheduler: collecting zone '{zone.name}'")
                    report = await monitor.run_zone(zone.name)

                    # Enrich sample posts
                    if engine and report.top_clusters:
                        all_posts = []
                        for cluster in report.top_clusters:
                            all_posts.extend(cluster.get("sample_posts", []))
                        if all_posts:
                            try:
                                enriched = await engine.enrich_posts(all_posts)
                                logger.info(f"Scheduler: enriched {enriched.success_count} posts for zone '{zone.name}'")
                            except Exception as e:
                                logger.warning(f"Scheduler: enrichment failed for '{zone.name}': {e}")

                    logger.info(
                        f"Scheduler: zone '{zone.name}' done — "
                        f"{report.total_items} items, {report.cluster_count} clusters, "
                        f"{len(report.alerts)} alerts"
                    )
                except Exception as e:
                    logger.error(f"Scheduler: zone '{zone.name}' failed: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Scheduler loop error: {e}", exc_info=True)

        # One radar tick per loop interval. Radar failures are caught and
        # logged separately so they can never take zone collection down.
        try:
            await radar_tick_once()
        except Exception as e:
            logger.error(f"Radar scheduler tick failed: {e}", exc_info=True)

        # Investing Radar collection is central and scheduled. Dashboard reads
        # persisted data only and never trigger upstream collection.
        try:
            await investing_radar_tick_once()
        except Exception as e:
            logger.error(f"Investing Radar tick failed: {e}", exc_info=True)

        try:
            await social_pulse_tick_once()
        except Exception as e:
            logger.error(f"Social Pulse tick failed: {e}", exc_info=True)

        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)


async def _llm_call(system_prompt: str, user_prompt: str) -> str:
    """Call the shared, provider-switchable Bounty LLM client."""
    from social_scraper.llm_client import call_llm

    return await call_llm(system_prompt, user_prompt, max_tokens=4000)


# --- Radar production wiring -------------------------------------------------

_RADAR_SCHEDULER = None


def _get_radar_scheduler() -> RadarScheduler | None:
    """Build (once) the production radar from env config and singletons.

    Raises ``ValueError`` with a clear message when a positive-integer
    setting is invalid — fail-closed, no silent defaults. Returns
    ``None`` when the radar is disabled, before touching any store.
    """
    global _RADAR_SCHEDULER
    if _RADAR_SCHEDULER is None:
        config = RadarConfig.from_env()
        if not config.enabled:
            return None
        from apis.dashboard_api import (
            _get_broker,
            _get_discovery_store,
            _get_workspace_store,
        )
        from social_scraper.monitoring.topdown import TopDownDiscovery

        broker = _get_broker()
        discovery_store = _get_discovery_store()
        # The radar owns its discovery instance. Sharing the dashboard
        # singleton would make mutable last_run_id vulnerable to concurrent
        # HTTP discovery requests and could attach the wrong run provenance.
        radar_discovery = TopDownDiscovery(
            broker=broker, discovery_store=discovery_store,
        )
        _RADAR_SCHEDULER = RadarScheduler(
            config=config,
            discovery_store=discovery_store,
            workspace_store=_get_workspace_store(),
            topdown=radar_discovery,
            broker=broker,
        )
    return _RADAR_SCHEDULER


async def radar_tick_once() -> dict | None:
    """Execute one radar tick; ``None`` when the radar is disabled."""
    scheduler = _get_radar_scheduler()
    if scheduler is None:
        logger.debug("Radar scheduler disabled (BOUNTY_RADAR_ENABLED)")
        return None
    return await scheduler.tick()


def _age_minutes(timestamp: str | None, now: datetime) -> float | None:
    if not timestamp:
        return None
    text = str(timestamp)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 60.0)


async def investing_radar_tick_once(
    *,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Centrally refresh the persisted global investing Radar when due."""
    env = os.environ if environ is None else environ
    if not _parse_bool_env(env, INVESTING_RADAR_ENV_ENABLED, default=True):
        return None
    interval = _parse_positive_int_env(
        env,
        INVESTING_RADAR_ENV_INTERVAL_MINUTES,
        _INVESTING_RADAR_INTERVAL_MINUTES,
    )
    current_time = now or datetime.now(timezone.utc)

    from apis.dashboard_api import _get_investing_store
    from social_scraper.investing import GlobalRadarSweep
    from social_scraper.monitoring.topdown import TRENDING_NOW_COUNTRIES

    store = _get_investing_store()
    latest = store.latest_sweep()
    if latest and latest["status"] == "running":
        age = _age_minutes(latest.get("started_at"), current_time)
        if age is None or age < _INVESTING_RADAR_STALE_RUNNING_MINUTES:
            return {"status": "running", "run_id": latest["id"], "started": False}
        store.finalize_sweep(latest["id"], completed_at=current_time)
        latest = None

    if latest:
        reference = latest.get("completed_at") or latest.get("started_at")
        age = _age_minutes(reference, current_time)
        retry_interval = (
            _INVESTING_RADAR_FAILED_RETRY_MINUTES
            if latest["status"] == "failed"
            else interval
        )
        if age is not None and age < retry_interval:
            return {"status": "not_due", "run_id": latest["id"], "started": False}

    sweep_id, created = store.create_sweep_if_idle(
        len(TRENDING_NOW_COUNTRIES),
        started_at=current_time,
    )
    if not created:
        return {"status": "running", "run_id": sweep_id, "started": False}
    result = await GlobalRadarSweep(store).run(sweep_id=sweep_id)
    return {"status": result["status"], "run_id": sweep_id, "started": True}


async def social_pulse_tick_once(
    *,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Run due social-first discovery centrally; dashboard reads stay cache-only."""
    env = os.environ if environ is None else environ
    if not _parse_bool_env(env, SOCIAL_PULSE_ENV_ENABLED, default=True):
        return None
    interval = _parse_positive_int_env(
        env,
        SOCIAL_PULSE_ENV_INTERVAL_MINUTES,
        _SOCIAL_PULSE_INTERVAL_MINUTES,
    )
    current_time = now or datetime.now(timezone.utc)

    from apis.dashboard_api import _get_social_pulse_store
    from social_scraper.investing.social_pulse import (
        SocialPulseCollector,
        build_default_social_fetchers,
    )

    store = _get_social_pulse_store()
    latest = store.latest_attempt()
    if latest and latest["status"] == "running":
        age = _age_minutes(latest.get("started_at"), current_time)
        if age is None or age < _SOCIAL_PULSE_STALE_RUNNING_MINUTES:
            return {"status": "running", "run_id": latest["id"], "started": False}
        store.fail_stale_run(latest["id"], "stale_collector_recovered")
        latest = None

    if latest:
        reference = latest.get("completed_at") or latest.get("started_at")
        age = _age_minutes(reference, current_time)
        retry_interval = (
            _SOCIAL_PULSE_FAILED_RETRY_MINUTES
            if latest["status"] in {"failed", "analysis_unavailable"}
            else interval
        )
        if age is not None and age < retry_interval:
            return {"status": "not_due", "run_id": latest["id"], "started": False}

    run_id, created = store.create_run_if_idle()
    if not created:
        return {"status": "running", "run_id": run_id, "started": False}
    try:
        fetchers = await build_default_social_fetchers()
        result = await SocialPulseCollector(store, fetchers).run(run_id=run_id)
    except asyncio.CancelledError:
        current = store.get_run(run_id)
        if current and current["status"] == "running":
            store.fail_stale_run(run_id, "collector_cancelled")
        raise
    except Exception as exc:
        current = store.get_run(run_id)
        if current and current["status"] == "running":
            store.fail_stale_run(run_id, f"collector:{type(exc).__name__}")
        logger.error("Social Pulse collection failed", exc_info=True)
        return {"status": "failed", "run_id": run_id, "started": True}
    return {"status": result["status"], "run_id": run_id, "started": True}


# --- Lifecycle --------------------------------------------------------------


def start_scheduler():
    """Start the scheduler as a background task. Call once on app startup."""
    global _SCHEDULER_TASK
    if _SCHEDULER_TASK is None or _SCHEDULER_TASK.done():
        _SCHEDULER_TASK = asyncio.create_task(_scheduler_loop())
        logger.info("Scheduler task created")
    return _SCHEDULER_TASK


def stop_scheduler():
    """Cancel the scheduler task and return it so callers can await it."""
    global _SCHEDULER_TASK
    task = _SCHEDULER_TASK
    if task is not None and not task.done():
        task.cancel()
        logger.info("Scheduler task cancelled")
    return task


async def shutdown_scheduler() -> None:
    """Cancel the scheduler and await its shutdown so no task is orphaned."""
    task = stop_scheduler()
    if task is None:
        return
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Scheduler task stopped cleanly")
