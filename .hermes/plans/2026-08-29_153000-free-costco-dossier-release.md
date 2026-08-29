# Free Costco Investment Dossier Release Contract

**Primary loop:** Run one command against free/public sources and receive a persisted, cited Costco Executive-membership dossier that separates reported facts, derived calculations, analyst assumptions, missing disclosure, transcript findings, information-parity coverage, and common-stock/optional-options implementation status.

## Included

- Zero-key entity resolution through GLEIF.
- Zero-key instrument mapping through OpenFIGI.
- Primary filing retrieval from SEC EDGAR.
- Exact filing passages and verified fact extraction.
- Materiality states: exact, source-supported bound, proxy, qualitative, not estimable.
- Explicit assumption bridge. No scenario total when a required term is missing.
- Official webcast discovery plus bounded findings from a clearly labeled public secondary transcript when official text is unavailable.
- Google News RSS implication checks, labeled sampled public coverage rather than complete sell-side parity.
- Append-only SQLite dossier persistence with content hashes.
- JSON and Markdown review artifacts.
- Common stock accepted; options are optional.

## Excluded

- Paid providers or mandatory API keys.
- Claims of one complete global filings/transcript database.
- Full worldwide jurisdiction adapters.
- Automatic trade recommendation or order execution.
- Dashboard UI and production deployment before Vincent reviews the first dossier.
- Full sell-side/consensus parity without licensed data.

## Acceptance

- The live build resolves Costco through GLEIF and COST through OpenFIGI.
- SEC filings are fetched from their official URLs and cited.
- The dossier shows Executive strategic relevance but does not infer standalone profitability.
- US/Canada consumer reward break-even may be mechanically derived from reported fee/reward terms, while company economics remain uncalculated when required terms are absent.
- Transcript findings preserve source type and URL; unavailable official text remains explicit.
- News results never become proof that the exact financial implication is fully covered.
- Focused and full tests pass.
- Output is persisted and hash-verified.
