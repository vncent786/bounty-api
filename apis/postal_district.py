"""
Singapore Postal Code → Postal District Mapper API.

Maps a Singapore 6-digit postal code to its postal district (1–28) and the
associated area names. Singapore is divided into 28 postal districts which
underpin property-market reporting (e.g. URA/SLA transaction data) and the
CCR/RCR/OCR regional classification.

How the mapping works
---------------------
A Singapore postal code's first two digits are the *postal sector*. Each
sector falls within exactly one of the 28 postal districts:

    01-06: D1   07-08: D2   14-16: D3   17-19: D4   20-23: D5
    24-27: D6   28-30: D7   31-33: D8   34-37: D9   38-41: D10
    42-45: D11  46-48: D12  49-55: D13  56-57: D14  58-63: D15
    64-65: D16  66-67: D17  68-76: D18  77-78: D19  79-80: D20
    81-82: D21  83-84: D22  85-86: D23  87-88: D24  89-90: D25
    91-92: D26  93-94: D27  95-99: D28

Sectors 09–13 are not assigned to any postal district under this scheme and
will return a clear "no district" error.

Designed for x402 micropayments ($0.002/call).

Data source: Singapore postal geography (static public reference). Area names
follow common real-estate usage.
"""

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(
    prefix="/postal",
    tags=["Singapore Postal Districts"],
)


# ============================================================
# Static reference data — Singapore's 28 postal districts
# ============================================================
# Each entry: number → (district_name, general area/region, [areas])
# `district_name` is a concise label; `areas` is the full list of localities.

DISTRICTS: Dict[int, Dict] = {
    1: {
        "name": "Raffles Place / Cecil / Marina",
        "general_area": "CBD / Core City (CCR)",
        "areas": ["Raffles Place", "Cecil", "Marina", "People's Park"],
    },
    2: {
        "name": "Anson / Tanjong Pagar",
        "general_area": "CBD / Core City (CCR)",
        "areas": ["Anson", "Tanjong Pagar", "Shenton Way"],
    },
    3: {
        "name": "Queenstown / Tiong Bahru",
        "general_area": "City Fringe (RCR)",
        "areas": ["Queenstown", "Tiong Bahru", "Alexandra", "Commonwealth"],
    },
    4: {
        "name": "Harbourfront / Sentosa",
        "general_area": "Southern (CCR)",
        "areas": ["Telok Blangah", "Harbourfront", "Mount Faber", "Sentosa", "Keppel"],
    },
    5: {
        "name": "Buona Vista / West Coast / Clementi",
        "general_area": "West (RCR/OCR)",
        "areas": ["Buona Vista", "Dover", "Pasir Panjang", "West Coast", "Clementi New Town"],
    },
    6: {
        "name": "City Hall / High Street / Beach Road",
        "general_area": "CBD / Core City (CCR)",
        "areas": ["High Street", "Beach Road", "City Hall", "North Bridge Road"],
    },
    7: {
        "name": "Middle Road / Golden Mile / Bugis",
        "general_area": "City Fringe (RCR)",
        "areas": ["Middle Road", "Golden Mile", "Bugis", "Rochor", "Beach Road"],
    },
    8: {
        "name": "Little India / Farrer Park",
        "general_area": "City Fringe (RCR)",
        "areas": ["Little India", "Farrer Park", "Serangoon Road", "Lavender"],
    },
    9: {
        "name": "Orchard / Cairnhill / River Valley",
        "general_area": "Core Central (CCR)",
        "areas": ["Orchard", "Cairnhill", "River Valley", "Leonie Hill"],
    },
    10: {
        "name": "Ardmore / Bukit Timah / Holland / Tanglin",
        "general_area": "Core Central (CCR)",
        "areas": ["Ardmore", "Bukit Timah", "Holland", "Tanglin", "Balmoral", "Watten Estate"],
    },
    11: {
        "name": "Newton / Novena / Thomson / Watten",
        "general_area": "Core Central (CCR)",
        "areas": ["Watten Estate", "Dunearn", "Newton", "Novena", "Thomson", "Chancery"],
    },
    12: {
        "name": "Balestier / Toa Payoh / Serangoon",
        "general_area": "City Fringe (RCR)",
        "areas": ["Balestier", "Toa Payoh", "Serangoon", "Mackenzie"],
    },
    13: {
        "name": "Macpherson / Braddell / Potong Pasir",
        "general_area": "Central (RCR/OCR)",
        "areas": ["Macpherson", "Braddell", "Potong Pasir", "Aljunied"],
    },
    14: {
        "name": "Geylang / Eunos / Paya Lebar",
        "general_area": "East (RCR/OCR)",
        "areas": ["Geylang", "Eunos", "Paya Lebar", "Sims", "Pulau Ubin", "Pulau Tekong"],
    },
    15: {
        "name": "Katong / Marine Parade / Joo Chiat",
        "general_area": "East Coast (RCR)",
        "areas": ["Katong", "Joo Chiat", "Amber Road", "Marine Parade", "Tanjong Rhu", "Siglap"],
    },
    16: {
        "name": "Bedok / Upper East Coast",
        "general_area": "East Coast (OCR)",
        "areas": ["Bedok", "Upper East Coast", "Eastwood", "Kew Drive", "Chai Chee", "Bayshore"],
    },
    17: {
        "name": "Loyang / Changi",
        "general_area": "Far East (OCR)",
        "areas": ["Flora Drive", "Loyang", "Changi", "Changi Bay"],
    },
    18: {
        "name": "Tampines / Pasir Ris / Simei",
        "general_area": "East (OCR)",
        "areas": ["Tampines", "Pasir Ris", "Simei", "Tampines East"],
    },
    19: {
        "name": "Serangoon Gardens / Hougang / Punggol / Sengkang",
        "general_area": "Northeast (OCR)",
        "areas": ["Serangoon Garden", "Hougang", "Punggol", "Sengkang", "Buangkok"],
    },
    20: {
        "name": "Bishan / Ang Mo Kio / Thomson",
        "general_area": "Central (RCR/OCR)",
        "areas": ["Bishan", "Ang Mo Kio", "Thomson", "Sin Ming", "Marymount"],
    },
    21: {
        "name": "Upper Bukit Timah / Clementi Park / Ulu Pandan",
        "general_area": "West / Central (OCR)",
        "areas": ["Upper Bukit Timah", "Ulu Pandan", "Clementi Park", "Pine Grove", "Chestnut Drive"],
    },
    22: {
        "name": "Jurong / Boon Lay / Tuas",
        "general_area": "West (OCR)",
        "areas": ["Jurong", "Boon Lay", "Tuas", "Pioneer", "Lakeside", "Clementi West", "Jurong East"],
    },
    23: {
        "name": "Hillview / Bukit Panjang / Choa Chu Kang",
        "general_area": "West / Northwest (OCR)",
        "areas": ["Hillview", "Dairy Farm", "Bukit Panjang", "Choa Chu Kang", "Bukit Batok", "Tengah"],
    },
    24: {
        "name": "Lim Chu Kang / Tengah / Kranji",
        "general_area": "Far West / Northwest (OCR)",
        "areas": ["Lim Chu Kang", "Tengah", "Kranji"],
    },
    25: {
        "name": "Admiralty / Woodlands",
        "general_area": "North (OCR)",
        "areas": ["Admiralty", "Woodlands", "Marsiling", "Sembawang", "Springleaf"],
    },
    26: {
        "name": "Mandai / Upper Thomson",
        "general_area": "North (OCR)",
        "areas": ["Mandai", "Sungei Kadut", "Upper Thomson", "Simpang"],
    },
    27: {
        "name": "Yishun / Sembawang",
        "general_area": "North (OCR)",
        "areas": ["Yishun", "Sembawang", "Admiralty"],
    },
    28: {
        "name": "Seletar / Yio Chu Kang",
        "general_area": "Northeast (OCR)",
        "areas": ["Seletar", "Yio Chu Kang"],
    },
}


# ============================================================
# Postal sector (first 2 digits) → postal district lookup table
# ============================================================

_SECTOR_RANGES = [
    # (low, high, district_number)
    (1, 6, 1),
    (7, 8, 2),
    (14, 16, 3),
    (17, 19, 4),
    (20, 23, 5),
    (24, 27, 6),
    (28, 30, 7),
    (31, 33, 8),
    (34, 37, 9),
    (38, 41, 10),
    (42, 45, 11),
    (46, 48, 12),
    (49, 55, 13),
    (56, 57, 14),
    (58, 63, 15),
    (64, 65, 16),
    (66, 67, 17),
    (68, 76, 18),
    (77, 78, 19),
    (79, 80, 20),
    (81, 82, 21),
    (83, 84, 22),
    (85, 86, 23),
    (87, 88, 24),
    (89, 90, 25),
    (91, 92, 26),
    (93, 94, 27),
    (95, 99, 28),
]

# Flat lookup: sector int → district int (1..99)
POSTAL_SECTOR_TO_DISTRICT: Dict[int, int] = {}
for _low, _high, _district in _SECTOR_RANGES:
    for _sector in range(_low, _high + 1):
        POSTAL_SECTOR_TO_DISTRICT[_sector] = _district
del _low, _high, _district, _sector


# ============================================================
# Pydantic response schemas
# ============================================================

class PostalCodeResult(BaseModel):
    postal_code: str = Field(..., description="The 6-digit Singapore postal code queried")
    sector: str = Field(..., description="Postal sector — first two digits of the postal code")
    district_number: int = Field(..., ge=1, le=28, description="Singapore postal district (1–28)")
    district_name: str = Field(..., description="Concise name/label for the postal district")
    general_area: str = Field(..., description="Broad region the district belongs to (e.g. Core Central (CCR))")
    areas: List[str] = Field(..., description="Localities / area names within the postal district")


class DistrictInfo(BaseModel):
    district_number: int = Field(..., ge=1, le=28, description="Singapore postal district number (1–28)")
    district_name: str = Field(..., description="Concise name/label for the postal district")
    general_area: str = Field(..., description="Broad region the district belongs to")
    areas: List[str] = Field(..., description="Localities / area names within the postal district")
    postal_sectors: List[str] = Field(..., description="Postal sectors (2-digit) that map to this district")


class DistrictListResponse(BaseModel):
    total: int = Field(..., description="Total number of postal districts (always 28)")
    districts: List[DistrictInfo]


class PostalSectorInfo(BaseModel):
    """Helper view: every postal sector and the district it maps to."""
    total_sectors: int
    sectors: Dict[str, int] = Field(..., description="Mapping of 2-digit postal sector → district number")


# ============================================================
# Internal helpers
# ============================================================

def _district_sectors(district_number: int) -> List[str]:
    """Return the sorted 2-digit postal sectors that map to a district."""
    return [
        f"{sector:02d}"
        for sector in sorted(POSTAL_SECTOR_TO_DISTRICT)
        if POSTAL_SECTOR_TO_DISTRICT[sector] == district_number
    ]


def resolve_postal_code(postal_code: str) -> PostalCodeResult:
    """
    Resolve a Singapore postal code to its postal district.

    Raises:
        HTTPException 422 — malformed postal code (not 6 digits).
        HTTPException 404 — valid format but sector has no mapped district.
    """
    if postal_code is None:
        raise HTTPException(status_code=422, detail="Postal code is required.")

    cleaned = postal_code.strip()

    if not cleaned.isdigit():
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid postal code '{postal_code}': must contain digits only "
                f"(no letters or spaces)."
            ),
        )

    if len(cleaned) != 6:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid postal code '{postal_code}': Singapore postal codes are "
                f"exactly 6 digits long (received {len(cleaned)})."
            ),
        )

    sector_int = int(cleaned[:2])
    district_number = POSTAL_SECTOR_TO_DISTRICT.get(sector_int)

    if district_number is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Sector '{cleaned[:2]}' (from postal code {cleaned}) is not "
                f"mapped to any of Singapore's 28 postal districts."
            ),
        )

    info = DISTRICTS[district_number]
    return PostalCodeResult(
        postal_code=cleaned,
        sector=f"{sector_int:02d}",
        district_number=district_number,
        district_name=info["name"],
        general_area=info["general_area"],
        areas=list(info["areas"]),
    )


# ============================================================
# API Endpoints
# ============================================================

@router.get(
    "/districts",
    response_model=DistrictListResponse,
    summary="List all 28 Singapore postal districts",
)
async def list_districts() -> DistrictListResponse:
    """
    Return all 28 Singapore postal districts with their area names and the
    postal sectors (first two digits of a postal code) that map to each.
    """
    districts = [
        DistrictInfo(
            district_number=num,
            district_name=info["name"],
            general_area=info["general_area"],
            areas=list(info["areas"]),
            postal_sectors=_district_sectors(num),
        )
        for num, info in sorted(DISTRICTS.items())
    ]
    return DistrictListResponse(total=len(districts), districts=districts)


@router.get(
    "/sectors",
    response_model=PostalSectorInfo,
    summary="Map every postal sector to its district",
)
async def list_sectors() -> PostalSectorInfo:
    """
    Return a flat lookup of every postal sector (01–99, where assigned) to the
    postal district number it belongs to. Useful for building local caches.
    """
    sectors = {f"{s:02d}": d for s, d in sorted(POSTAL_SECTOR_TO_DISTRICT.items())}
    return PostalSectorInfo(total_sectors=len(sectors), sectors=sectors)


# IMPORTANT: the `/{postal_code}` route is declared *after* the fixed-path
# routes (`/districts`, `/sectors`) so FastAPI matches those first instead of
# treating "districts" as a postal code path parameter.
@router.get(
    "/{postal_code}",
    response_model=PostalCodeResult,
    summary="Resolve a postal code to its postal district",
)
async def get_postal_code(postal_code: str) -> PostalCodeResult:
    """
    Resolve a 6-digit Singapore postal code to its postal district.

    Example: `238801` → sector `23`, **District 5** (Buona Vista / West Coast / Clementi).

    Returns the district number, district name, general area, and the list of
    areas within that district.
    """
    return resolve_postal_code(postal_code)
