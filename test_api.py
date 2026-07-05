"""
Test suite for SG Stamp Duty API.
Verifies calculations against IRAS published examples.
"""
import httpx
import math
import sys

API_BASE = "http://localhost:8000"

def test_bsd_residential():
    """Test BSD calculation against IRAS example: $4,500,100 → $209,606"""
    resp = httpx.get(f"{API_BASE}/bsd", params={"price": 4500100, "property_type": "residential"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data["bsd"] == 209606, f"Expected 209606, got {data['bsd']}"
    print(f"  ✅ BSD residential $4.5M → ${data['bsd']:,} (matches IRAS)")
    return True

def test_bsd_non_residential():
    """Test non-residential BSD"""
    resp = httpx.get(f"{API_BASE}/bsd", params={"price": 1000000, "property_type": "non-residential"})
    data = resp.json()
    # First 180k@1% + next 180k@2% + next 640k@3% + remaining at 4%
    # 1800 + 3600 + 19200 + 0 (1000k - 1000k = 0)
    # Wait: 180k + 180k + 640k = 1000k, so remaining = 0... but price is 1000k
    # Actually 180k + 180k + 640k = 1000k exactly
    # 1800 + 3600 + 19200 = 24600
    assert data["bsd"] == 24600, f"Expected 24600, got {data['bsd']}"
    print(f"  ✅ BSD non-residential $1M → ${data['bsd']:,}")
    return True

def test_absd_sc_first():
    """SC buying first property → 0% ABSD"""
    resp = httpx.get(f"{API_BASE}/absd", params={"price": 2000000, "buyer_profile": "SC", "property_count": 1})
    data = resp.json()
    assert data["absd"] == 0, f"Expected 0, got {data['absd']}"
    assert data["applicable"] == False
    print(f"  ✅ ABSD SC first property → $0 (0%)")
    return True

def test_absd_sc_second():
    """SC buying second property → 20% ABSD"""
    resp = httpx.get(f"{API_BASE}/absd", params={"price": 2000000, "buyer_profile": "SC", "property_count": 2})
    data = resp.json()
    assert data["absd"] == 400000, f"Expected 400000, got {data['absd']}"
    assert data["absd_rate_percent"] == 20.0
    print(f"  ✅ ABSD SC second property → ${data['absd']:,} (20%)")
    return True

def test_absd_foreigner():
    """Foreigner buying any property → 60% ABSD"""
    resp = httpx.get(f"{API_BASE}/absd", params={"price": 2000000, "buyer_profile": "FR", "property_count": 1})
    data = resp.json()
    assert data["absd"] == 1200000, f"Expected 1200000, got {data['absd']}"
    assert data["absd_rate_percent"] == 60.0
    print(f"  ✅ ABSD Foreigner → ${data['absd']:,} (60%)")
    return True

def test_full_calculation():
    """Test the full /stamp-duty endpoint"""
    resp = httpx.post(f"{API_BASE}/stamp-duty", json={
        "price": 2000000,
        "property_type": "residential",
        "buyer_profile": "FR",
        "property_count": 1
    })
    data = resp.json()
    # BSD for $2M residential: 1800 + 3600 + 19200 + 20000 + 25000 = 69600
    assert data["bsd"] == 69600, f"Expected BSD 69600, got {data['bsd']}"
    assert data["absd"] == 1200000, f"Expected ABSD 1200000, got {data['absd']}"
    assert data["total_stamp_duty"] == 1269600, f"Expected total 1269600, got {data['total_stamp_duty']}"
    print(f"  ✅ Full calc: Foreigner $2M → BSD ${data['bsd']:,} + ABSD ${data['absd']:,} = ${data['total_stamp_duty']:,}")
    return True

def test_sc_first_property():
    """SC buying first $1.5M property"""
    resp = httpx.post(f"{API_BASE}/stamp-duty", json={
        "price": 1500000,
        "property_type": "residential",
        "buyer_profile": "SC",
        "property_count": 1
    })
    data = resp.json()
    # No ABSD for SC first property
    assert data["absd"] == 0
    # BSD: 1800 + 3600 + 19200 + 20000 + 18000 = 60600
    # Wait let me recalculate: 
    # First 180k@1% = 1800
    # Next 180k@2% = 3600
    # Next 640k@3% = 19200
    # Next 500k@4% = 20000
    # 180k+180k+640k+500k = 1500k → remaining = 0
    # Total: 1800+3600+19200+20000 = 44600
    assert data["bsd"] == 44600, f"Expected BSD 44600, got {data['bsd']}"
    print(f"  ✅ SC first $1.5M property → BSD ${data['bsd']:,}, ABSD $0, total ${data['total_stamp_duty']:,}")
    return True

def test_api_info():
    """Test the root endpoint returns API info"""
    resp = httpx.get(f"{API_BASE}/")
    data = resp.json()
    assert "stamp-duty" in data["name"].lower() or "stamp" in data["name"].lower()
    print(f"  ✅ API info endpoint works")
    return True


if __name__ == "__main__":
    print("="*80)
    print("RUNNING STAMP DUTY API TEST SUITE")
    print("="*80)

    tests = [
        test_api_info,
        test_bsd_residential,
        test_bsd_non_residential,
        test_absd_sc_first,
        test_absd_sc_second,
        test_absd_foreigner,
        test_full_calculation,
        test_sc_first_property,
    ]

    passed = 0
    failed = 0

    for test in tests:
        print(f"\n[{test.__name__}]")
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ❌ FAILED: {e}")
            failed += 1

    print(f"\n{'='*80}")
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("="*80)

    sys.exit(0 if failed == 0 else 1)
