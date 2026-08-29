# Generic Candidate-to-Dossier Product Release Contract

**Primary loop:** From any persisted private Radar social candidate, Vincent can open Research, confirm the company/ticker, optionally add primary/transcript URLs and explicit low/base/high assumptions, start a background run, watch persisted progress, and revisit a cited source-grounded dossier.

## Included

- Candidate handoff from an exact persisted scan or the shipped verified private-Radar snapshot.
- Candidate evidence and original qualification/review status preserved immutably.
- Human-confirmed company/ticker fields. No ticker inferred from a brand string.
- Free GLEIF legal-entity and OpenFIGI instrument resolution.
- Automatic zero-key SEC ticker/CIK, latest 10-K/20-F and 10-Q discovery for SEC registrants.
- SEC CompanyFacts consolidated-revenue baseline with accession and as-of protection.
- User-supplied non-US primary-document URLs, retained as supplied source candidates and never promoted into verified numeric facts automatically.
- Optional user-supplied transcript URL, with bounded speaker-attributed findings and secondary-source caveat.
- Optional Finnhub transcript lookup only when a user supplies a free API key; failure remains explicit.
- Public-news implication checks labelled sampled, never complete sell-side parity.
- Low/base/high educated-assumption materiality model. No calculation if any required factor is missing.
- Append-only dossier and persisted research-run storage.
- Authenticated async API: create, resume, status, list, dossier retrieval.
- Private Radar Research UI: candidate handoff, form, progress, saved dossier list/detail, desktop/mobile.
- Common stock valid; options optional and not required.

## Excluded

- Automatic order execution or trade recommendation.
- Automatic product/brand-to-ticker inference without human confirmation.
- Claims of comprehensive worldwide filings, transcripts, analyst coverage or consensus.
- Paid data requirements.
- Numeric candidate materiality without reported numerator or explicit user assumptions.
- Mutation from the public read-only snapshot URL.

## Acceptance

- Costco and T-Mobile candidates can use the same generic runner without issuer-specific code.
- Candidate social counts never enter financial arithmetic.
- Exact consolidated revenue comes only from an accession-matched SEC CompanyFacts observation.
- Missing/failed filings, transcripts, news or instrument mapping remain explicit and cannot become absence or zero.
- A complete assumption set produces low/base/high revenue and contribution scenarios; an incomplete set produces none.
- Progress survives page navigation and runs can be resumed after a stale task.
- Saved dossiers hash-verify before serving.
- Read-only snapshot mode makes no research API calls.
- Focused/full tests, controlled browser QA, independent review and authenticated production smoke pass.
