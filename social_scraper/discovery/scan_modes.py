"""Explicit scan modes and capability policies for discovery collection.

One call must never silently chain Trends ingestion, root search, thread
hydration and LLM analysis. Each scan mode declares exactly which
capabilities it may use; callers consult the policy instead of guessing
from flags.

Hard invariants (approved plan, Phase 1 Task 1.1):
- ``TRENDS_SNAPSHOT``: zero source-broker calls and zero LLM calls.
- ``ROOT_SWEEP``: root search calls allowed; zero thread and LLM calls
  (``max_threads=0``).
- ``DEEP_READ``: thread calls allowed; zero LLM calls.
- ``HORIZONTAL_SYNTHESIS``: at most one LLM call per changed family
  evidence bundle.
- ``OPTIONAL_INTERPRETATION``: interpretation of already-collected
  evidence only; no new collection.

``DEEP_READ``, ``HORIZONTAL_SYNTHESIS`` and ``OPTIONAL_INTERPRETATION``
are research-run stages (StagedRunner/handlers). They must never be
reachable from the Explore Trend feed; ``RESEARCH_RUN_MODES`` exists so
feed endpoints can reject them explicitly.
"""

from enum import Enum
from functools import lru_cache


class ScanMode(str, Enum):
    TRENDS_SNAPSHOT = "trends_snapshot"       # Trends metadata only
    ROOT_SWEEP = "root_sweep"                 # root social evidence, no threads/LLM
    DEEP_READ = "deep_read"                   # selected families only
    HORIZONTAL_SYNTHESIS = "horizontal_synthesis"
    OPTIONAL_INTERPRETATION = "optional_interpretation"


class ScanModePolicy:
    """Immutable capability declaration for one scan mode.

    ``max_threads``: ``0`` forbids thread hydration outright; ``None``
    means the mode imposes no cap of its own (the run's budget governs).
    ``max_llm_calls_per_family``: ``0`` forbids LLM calls; positive
    values are a hard per-family cap, never a target.
    """

    __slots__ = (
        "mode",
        "allows_broker_search",
        "allows_thread_reads",
        "max_threads",
        "allows_llm",
        "max_llm_calls_per_family",
    )

    def __init__(
        self,
        mode: ScanMode,
        *,
        allows_broker_search: bool,
        allows_thread_reads: bool,
        max_threads: int | None,
        allows_llm: bool,
        max_llm_calls_per_family: int,
    ):
        if max_threads is not None and max_threads < 0:
            raise ValueError("max_threads cannot be negative")
        if max_llm_calls_per_family < 0:
            raise ValueError("max_llm_calls_per_family cannot be negative")
        if allows_llm != (max_llm_calls_per_family > 0):
            raise ValueError(
                "allows_llm must agree with max_llm_calls_per_family"
            )
        if not allows_thread_reads and max_threads != 0:
            raise ValueError("thread reads forbidden requires max_threads=0")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "allows_broker_search", allows_broker_search)
        object.__setattr__(self, "allows_thread_reads", allows_thread_reads)
        object.__setattr__(self, "max_threads", max_threads)
        object.__setattr__(self, "allows_llm", allows_llm)
        object.__setattr__(
            self, "max_llm_calls_per_family", max_llm_calls_per_family
        )

    def __setattr__(self, *_args):
        raise AttributeError("ScanModePolicy is immutable")

    def __repr__(self) -> str:
        return (
            f"ScanModePolicy(mode={self.mode.value!r}, "
            f"broker_search={self.allows_broker_search}, "
            f"thread_reads={self.allows_thread_reads}, "
            f"max_threads={self.max_threads}, llm={self.allows_llm}, "
            f"max_llm_per_family={self.max_llm_calls_per_family})"
        )


_SCAN_MODE_POLICIES: dict[ScanMode, ScanModePolicy] = {
    ScanMode.TRENDS_SNAPSHOT: ScanModePolicy(
        ScanMode.TRENDS_SNAPSHOT,
        allows_broker_search=False,
        allows_thread_reads=False,
        max_threads=0,
        allows_llm=False,
        max_llm_calls_per_family=0,
    ),
    ScanMode.ROOT_SWEEP: ScanModePolicy(
        ScanMode.ROOT_SWEEP,
        allows_broker_search=True,
        allows_thread_reads=False,
        max_threads=0,
        allows_llm=False,
        max_llm_calls_per_family=0,
    ),
    ScanMode.DEEP_READ: ScanModePolicy(
        ScanMode.DEEP_READ,
        allows_broker_search=True,
        allows_thread_reads=True,
        max_threads=None,  # threads_per_platform budget governs
        allows_llm=False,
        max_llm_calls_per_family=0,
    ),
    ScanMode.HORIZONTAL_SYNTHESIS: ScanModePolicy(
        ScanMode.HORIZONTAL_SYNTHESIS,
        allows_broker_search=True,
        allows_thread_reads=True,
        max_threads=None,  # threads_per_platform budget governs
        allows_llm=True,
        max_llm_calls_per_family=1,
    ),
    ScanMode.OPTIONAL_INTERPRETATION: ScanModePolicy(
        ScanMode.OPTIONAL_INTERPRETATION,
        allows_broker_search=False,
        allows_thread_reads=False,
        max_threads=0,
        allows_llm=True,
        max_llm_calls_per_family=1,
    ),
}

#: Modes the Explore Trend feed may execute directly.
FEED_MODES = frozenset({ScanMode.TRENDS_SNAPSHOT, ScanMode.ROOT_SWEEP})

#: Modes that only explicit research-run stages may execute.
RESEARCH_RUN_MODES = frozenset({
    ScanMode.DEEP_READ,
    ScanMode.HORIZONTAL_SYNTHESIS,
    ScanMode.OPTIONAL_INTERPRETATION,
})


def coerce_scan_mode(value) -> ScanMode:
    """Accept a ScanMode or a human-typed string; reject everything else."""
    if isinstance(value, ScanMode):
        return value
    if isinstance(value, str):
        key = value.strip().casefold().replace("-", "_")
        for mode in ScanMode:
            if mode.value == key:
                return mode
    valid = ", ".join(mode.value for mode in ScanMode)
    raise ValueError(f"unknown scan mode: {value!r} (valid modes: {valid})")


@lru_cache(maxsize=None)
def policy_for(mode) -> ScanModePolicy:
    """Return the capability policy for a scan mode."""
    return _SCAN_MODE_POLICIES[coerce_scan_mode(mode)]


def resolve_scan_mode(
    mode=None,
    *,
    apply_gate: bool | None = None,
) -> ScanMode:
    """Resolve an explicit mode, falling back to the legacy gate flag.

    Rules:
    - An explicit ``mode`` always wins.
    - Legacy ``apply_gate=True`` maps to ``ROOT_SWEEP``: root social
      evidence, never thread hydration and never LLM analysis. No legacy
      flag combination may resolve to an LLM-capable mode.
    - Everything else defaults to the cheap ``TRENDS_SNAPSHOT``.
    """
    if mode is not None:
        return coerce_scan_mode(mode)
    if apply_gate is True:
        return ScanMode.ROOT_SWEEP
    return ScanMode.TRENDS_SNAPSHOT
