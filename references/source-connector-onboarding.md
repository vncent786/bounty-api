# Adding a new Bounty data source once

A monitor should never own login code, retry rules or data formatting. It asks the shared source catalogue for a type of evidence such as airline ticket sales or procurement awards.

## One-time source setup

1. **Name the question the source answers.**
   - Good: “How many tickets were settled?”
   - Bad: “Airline demand.”

2. **Register what the source can do.**
   Add its `SourceCapability` with:
   - evidence types;
   - countries and history available;
   - whether it is free, paid or mixed;
   - rate limits and safe concurrency;
   - known limitations;
   - one known-positive health check;
   - a credential reference name.

   Planned airline, procurement and issuer sources are listed in `references/source-connector-registry-v1.json`.

3. **Reference credentials; never copy them.**
   Add one `CredentialDefinition` to the central `CredentialResolver`. It points to environment variables or a registered authenticated session. Secret values stay in the local environment, Railway variables or the owned browser session.

4. **Write the smallest source adapter.**
   It only needs to:
   - run the known-positive health check;
   - search or collect a bounded request;
   - distinguish a valid no-result from a broken source;
   - convert each record to `NormalizedEvidence`.

5. **Use the shared executor.**
   `SourceExecutor` already owns:
   - credential readiness;
   - production-shaped health checks;
   - bounded retries;
   - completed-request reuse;
   - evidence and source hashes;
   - safe error categories;
   - credential redaction;
   - atomic JSON receipts.

6. **Test four outcomes.**
   - healthy source with records;
   - healthy source with a valid no-result;
   - missing or expired credentials;
   - timeout, challenge, parser failure or rate limit.

7. **Attach the evidence type to monitors.**
   A monitor selects connectors through `CapabilityCatalogue` by evidence type, country, history and whether paid sources are allowed. It never knows the API key.

## Example

```python
from social_scraper.source_connectors import (
    CapabilityCatalogue,
    CapabilityQuery,
    ConnectorOperation,
    CredentialResolver,
    JsonReceiptStore,
    SourceExecutor,
    build_bounty_source_catalogue,
)

catalogue = build_bounty_source_catalogue(
    broker=existing_social_broker,
    google_discovery=existing_google_discovery,
)

sources = catalogue.select(CapabilityQuery(
    operation=ConnectorOperation.SEARCH,
    data_kinds=frozenset({"social_conversation"}),
    geography="SG",
    lookback_days=30,
    allow_paid=False,
))

executor = SourceExecutor(
    catalogue,
    CredentialResolver.with_bounty_defaults(),
    JsonReceiptStore("artifacts/source-receipts"),
)
```

Existing Google and social collectors are already wrapped without changing their collection code. ARC, EUROCONTROL and UK CAA now use the same catalogue. GeBIZ, TED, SAM.gov, CanadaBuys and U.S. BTS remain follow-up adapters.

## Rules that prevent another rebuild

- One source ID has one meaning and one owner.
- Authentication belongs to the credential reference, not the monitor.
- The health check uses the same route and request shape as production.
- A failed health check means source unavailable, not zero demand.
- A valid no-result is recorded as empty, not failed.
- Completed requests resume from a verified receipt instead of recollecting.
- A monitor uses one stable `resume_key` while finishing a paginated refresh, then a new key for the next scheduled refresh.
- Paid sources stay disabled until explicitly approved and budgeted.
- Source limitations travel with every result.
- Reports never contain credential values, cookies or authenticated browser files.
- Adding one source must not require editing every monitor.

## First shared catalogue

The current plan covers:

- Google Trends;
- X, TikTok, Instagram, Reddit and YouTube;
- ARC airline sales;
- EUROCONTROL;
- UK CAA;
- U.S. BTS;
- Singapore GeBIZ;
- EU TED Open Data;
- SAM.gov;
- CanadaBuys;
- issuer filings and investor-relations sources;
- disabled paid placeholders for OAG and ForwardKeys.

Official-source adapters can now be added one by one without changing the monitor or credential architecture.
