from social_scraper.discovery.market_awareness import assess_market_awareness


def test_mainstream_headline_is_visible_but_not_called_price_causation():
    result = assess_market_awareness(
        earliest_observed_at="2026-08-10T04:00:00+00:00",
        retrieved_at="2026-08-10T05:00:00+00:00",
        headlines=[{
            "title": "Company comments on demand",
            "url": "https://example.com/story",
            "published_at": "2026-08-10T03:00:00+00:00",
            "source_tier": "mainstream",
        }],
        price_context=[{
            "ticker": "ACME",
            "window": "1d",
            "return_pct": 4.2,
            "as_of": "2026-08-10T04:30:00+00:00",
        }],
        source_health=[{"source": "news", "status": "complete"}],
    )
    assert result["classification"] == "mainstream_coverage"
    assert result["headlines"][0]["url"] == "https://example.com/story"
    assert result["price_context"][0]["interpretation"] == "context_not_causation"


def test_healthy_no_headlines_can_be_social_only_but_source_failure_is_unknown():
    healthy = assess_market_awareness(
        earliest_observed_at="2026-08-10T04:00:00+00:00",
        retrieved_at="2026-08-10T05:00:00+00:00",
        headlines=[],
        source_health=[{"source": "news", "status": "complete"}],
    )
    failed = assess_market_awareness(
        earliest_observed_at="2026-08-10T04:00:00+00:00",
        retrieved_at="2026-08-10T05:00:00+00:00",
        headlines=[],
        source_health=[{"source": "news", "status": "error"}],
    )
    assert healthy["classification"] == "social_only"
    assert failed["classification"] == "unknown"
