# Private Radar filter policy

Version: `opportunity-first/1`

This document separates filters that protect evidence integrity from thresholds that apply only when promoting an investigation to trade research. No hidden filter may delete a valid cited behavior merely because it has not yet passed a trade gate.

## Discovery and storage

- Preserve valid roots from the last **90 days** in current discovery.
- Request the next wider source-native window when a platform lacks an exact 90-day option, then enforce 90 days locally.
- Persist `age_days` and `recency_bucket` (`last_30_days`, `last_90_days`, `historical`, or `timestamp_missing`).
- Older records may be used only in explicitly comparable historical windows. They cannot prove current acceleration.
- Missing timestamps remain explicit and cannot count toward comparable history.

## Integrity filters

These may reject a record or proposed opportunity:

- missing text or non-public/invalid source URL;
- comment/reply presented as an independent root;
- native repost or known copy-cluster presented as independent support;
- no valid citation supporting the specific subject;
- generic or semantically drifted anchor;
- reporting/promotional copy presented as firsthand behavior;
- unsupported financial claim presented as fact.

## Opportunity Investigation eligibility

A subject remains visible when it has:

- at least one valid citation;
- a specific object/product/service/problem;
- at least one concrete behavior attached to that object;
- a reason to investigate, missing evidence, next action, and rejection condition.

The following are **not** Opportunity rejection filters. They remain visible as missing evidence or caveats:

- fewer than three firsthand voices;
- one-platform or one-community concentration;
- less than two weeks of persistence;
- incomplete/truncated comments or replies;
- unavailable or non-anomalous comparable history;
- sampled financial/news coverage;
- company exposure or materiality not yet verified.

## Trade-ready promotion

Trade-ready remains strict and may honestly be empty. It requires all of:

- behavior in at least two independent cited voices;
- at least three independent firsthand records with usable engagement/cross-platform support;
- persistence across at least two weeks and seven calendar days;
- at least two independent roots/authors without copy inflation;
- complete, uncapped comparable historical windows and a supported anomaly;
- complete required conversation-depth coverage;
- information parity below repeated financial consensus;
- explicit mechanism, contradiction, invalidation, and verified company/instrument exposure.

## Adaptive cost allocation

- Broad discovery remains panel-wide.
- Deep follow-up covers one primary anchor per panel first, then a four-anchor low-engagement exploration reserve, capped at 20 anchors globally.
- At most one root per platform per panel receives bounded comment/reply depth in the first pass.
- Trend-candidate social fan-out is capped at four candidates.
- The shared budget is sized to leave capacity for up to eight candidates times four comparable historical windows.

## Google Trends

- Initial scans collect Worldwide 3M/1Y/5Y movement only.
- Country histories are progressive enrichment, not a discovery prerequisite.
- Snapshot publication packages persisted movement and performs zero Google/model calls.
- Failed series remain visible holes and may retry incrementally later. They never invalidate already collected series.
