# Private Day-One Investment Radar Release Contract

**Date:** 2026-08-26

## Primary loop

Vincent opens `/dashboard/investing-preview`, starts one owned-worker scan, watches persisted progress, and receives either:

- a small list of qualified, cited retrospective information-arbitrage leads; or
- `No qualified leads this cycle`.

No raw post titles or generic trend rows are shown as leads.

## Included

1. Sixteen versioned Camillo consumer panels covering automobiles, travel, food, beauty, apparel, luxury, retail, technology, subscriptions, telecom, payments, fitness, pets, and household.
2. Four bounded owned-X web-GraphQL behavior-query slices per panel (64 fixed X scopes total).
3. One bounded TikTok, Instagram, Reddit, and YouTube discovery search per panel (64 non-X scopes total) before candidate generation, followed by deeper comments/replies for only the four strongest shortlisted anchors.
4. Four comparable exact-anchor X historical windows only for candidates that survive cheap multi-source evidence checks.
5. Stable evidence IDs, immutable source records, source-health receipts, and persisted scan state.
6. Fail-closed qualification:
   - specific topic and behavior;
   - supported retrospective anomaly from comparable uncapped windows;
   - independent-author/root breadth after deduplication;
   - citations;
   - financial/mainstream information-parity check;
   - economic mechanism, diligence question, contradiction, and invalidation.
6. Private manual scan API and progress polling.
7. Private Radar UI renders qualified leads only.

## Explicitly excluded

- Automatic trades or recommendations.
- Options-only discovery.
- Filing-backed materiality and verified ticker mapping.
- Public Radar reactivation.
- Multi-tenant billing or remote worker ingestion.
- Claims of exhaustive social or sell-side coverage.
- Claims that ranked X web search is a full firehose or "almost unlimited"; every cycle remains a bounded, query-defined sample with caps and source receipts.

## Acceptance checks

- Owned connectors run only with `BOUNTY_OWNED_SOCIAL_WORKER=1`.
- API/read path makes zero upstream calls.
- Missing, capped, failed, or incomparable history cannot pass anomaly qualification.
- A failed model call produces no visible candidate.
- Every visible claim resolves to stored public evidence.
- Scan survives reload and can be revisited.
- Empty and failure states are distinct.
- Focused and full tests pass.
- Real owned-source scan runs once end-to-end.
- Private desktop and mobile journey pass with no console/network errors or overflow.

## Release boundary

Commit locally after verification. Do not push or expose publicly until Vincent approves the real private output.
