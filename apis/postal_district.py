"""
Singapore Postal Code → Postal District Mapper API.

Maps a Singapore 6-digit postal code to its postal district (1–28) and the
associated area names. Singapore is divided into 28 postal districts which
underpin property-market reporting (e.g. URA/SLA transaction data) and the
CCR/RCR/OCR regional classification.

How the mapping works
---------------------
A Singapore postal code's first two digits are the *postal sector*. The
sector-to-district mapping is NOT sequential — it follows the historical
URA postal district scheme. See _SECTOR_RANGES below for the authoritative
table (source: URA).

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
# Source: URA "List of Postal Districts" (archived Mar 2023)
# https://web.archive.org/web/20230309010306/https://www.ura.gov.sg/realEstateIIWeb/resources/misc/list_of_postal_districts.htm
# Confirmed by Wikipedia: Postal codes in Singapore
#
# Each entry: number → (district_name, general area/region, [areas])

DISTRICTS: Dict[int, Dict] = {
    1: {
        "name": "Raffles Place, Cecil, Marina, People's Park",
        "general_area": "CBD / Core City (CCR)",
        "areas": ["Raffles Place", "Cecil", "Marina", "People's Park"],
    },
    2: {
        "name": "Anson, Tanjong Pagar",
        "general_area": "CBD / Core City (CCR)",
        "areas": ["Anson", "Tanjong Pagar", "Shenton Way"],
    },
    3: {
        "name": "Queenstown, Tiong Bahru",
        "general_area": "City Fringe (RCR)",
        "areas": ["Queenstown", "Tiong Bahru", "Alexandra", "Commonwealth"],
    },
    4: {
        "name": "Telok Blangah, Harbourfront",
        "general_area": "Southern (CCR)",
        "areas": ["Telok Blangah", "Harbourfront", "Mount Faber", "Sentosa", "Keppel"],
    },
    5: {
        "name": "Pasir Panjang, Hong Leong Garden, Clementi New Town",
        "general_area": "West (RCR/OCR)",
        "areas": ["Pasir Panjang", "Hong Leong Garden", "Clementi New Town", "West Coast"],
    },
    6: {
        "name": "High Street, Beach Road (part)",
        "general_area": "CBD / Core City (CCR)",
        "areas": ["High Street", "Beach Road", "City Hall", "North Bridge Road"],
    },
    7: {
        "name": "Middle Road, Golden Mile",
        "general_area": "City Fringe (RCR)",
        "areas": ["Middle Road", "Golden Mile", "Bugis", "Rochor"],
    },
    8: {
        "name": "Little India",
        "general_area": "City Fringe (RCR)",
        "areas": ["Little India", "Farrer Park", "Serangoon Road", "Lavender"],
    },
    9: {
        "name": "Orchard, Cairnhill, River Valley",
        "general_area": "Core Central (CCR)",
        "areas": ["Orchard", "Cairnhill", "River Valley", "Leonie Hill"],
    },
    10: {
        "name": "Ardmore, Bukit Timah, Holland Road, Tanglin",
        "general_area": "Core Central (CCR)",
        "areas": ["Ardmore", "Bukit Timah", "Holland Road", "Tanglin", "Balmoral", "Watten Estate"],
    },
    11: {
        "name": "Watten Estate, Novena, Thomson",
        "general_area": "Core Central (CCR)",
        "areas": ["Watten Estate", "Dunearn", "Newton", "Novena", "Thomson", "Chancery"],
    },
    12: {
        "name": "Balestier, Toa Payoh, Serangoon",
        "general_area": "City Fringe (RCR)",
        "areas": ["Balestier", "Toa Payoh", "Serangoon", "Mackenzie"],
    },
    13: {
        "name": "Macpherson, Braddell",
        "general_area": "Central (RCR/OCR)",
        "areas": ["Macpherson", "Braddell", "Potong Pasir", "Aljunied"],
    },
    14: {
        "name": "Geylang, Eunos",
        "general_area": "East (RCR/OCR)",
        "areas": ["Geylang", "Eunos", "Paya Lebar", "Sims"],
    },
    15: {
        "name": "Katong, Joo Chiat, Amber Road",
        "general_area": "East Coast (RCR)",
        "areas": ["Katong", "Joo Chiat", "Amber Road", "Marine Parade", "Tanjong Rhu", "Siglap"],
    },
    16: {
        "name": "Bedok, Upper East Coast, Eastwood, Kew Drive",
        "general_area": "East Coast (OCR)",
        "areas": ["Bedok", "Upper East Coast", "Eastwood", "Kew Drive", "Chai Chee", "Bayshore"],
    },
    17: {
        "name": "Loyang, Changi",
        "general_area": "Far East (OCR)",
        "areas": ["Loyang", "Changi", "Changi Bay", "Flora Drive"],
    },
    18: {
        "name": "Tampines, Pasir Ris",
        "general_area": "East (OCR)",
        "areas": ["Tampines", "Pasir Ris", "Simei", "Tampines East"],
    },
    19: {
        "name": "Serangoon Garden, Hougang, Punggol",
        "general_area": "Northeast (OCR)",
        "areas": ["Serangoon Garden", "Hougang", "Punggol", "Sengkang", "Buangkok"],
    },
    20: {
        "name": "Bishan, Ang Mo Kio",
        "general_area": "Central (RCR/OCR)",
        "areas": ["Bishan", "Ang Mo Kio", "Thomson", "Sin Ming", "Marymount"],
    },
    21: {
        "name": "Upper Bukit Timah, Clementi Park, Ulu Pandan",
        "general_area": "West / Central (OCR)",
        "areas": ["Upper Bukit Timah", "Ulu Pandan", "Clementi Park", "Pine Grove", "Chestnut Drive"],
    },
    22: {
        "name": "Jurong",
        "general_area": "West (OCR)",
        "areas": ["Jurong", "Boon Lay", "Tuas", "Pioneer", "Lakeside", "Clementi West", "Jurong East"],
    },
    23: {
        "name": "Hillview, Dairy Farm, Bukit Panjang, Choa Chu Kang",
        "general_area": "West / Northwest (OCR)",
        "areas": ["Hillview", "Dairy Farm", "Bukit Panjang", "Choa Chu Kang", "Bukit Batok", "Tengah"],
    },
    24: {
        "name": "Lim Chu Kang, Tengah",
        "general_area": "Far West / Northwest (OCR)",
        "areas": ["Lim Chu Kang", "Tengah"],
    },
    25: {
        "name": "Kranji, Woodgrove",
        "general_area": "North (OCR)",
        "areas": ["Kranji", "Woodgrove", "Admiralty"],
    },
    26: {
        "name": "Upper Thomson, Springleaf",
        "general_area": "North (OCR)",
        "areas": ["Upper Thomson", "Springleaf", "Simpang"],
    },
    27: {
        "name": "Yishun, Sembawang",
        "general_area": "North (OCR)",
        "areas": ["Yishun", "Sembawang", "Admiralty"],
    },
    28: {
        "name": "Seletar",
        "general_area": "Northeast (OCR)",
        "areas": ["Seletar", "Yio Chu Kang"],
    },
}


# ============================================================
# Postal sector (first 2 digits) → postal district lookup table
# ============================================================
# Source: URA "List of Postal Districts" (archived Mar 2023)
# This is the authoritative mapping from Singapore's Urban Redevelopment Authority.
# The sector→district mapping is NOT sequential — districts 4-5 fill gaps that
# a naive sequential table would get wrong. Every entry below matches the URA source exactly.

_SECTOR_RANGES = [
    # (low, high, district_number) — verified against URA reference
    (1, 6, 1),        # 01-06 → D1 Raffles Place
    (7, 8, 2),        # 07-08 → D2 Anson
    (14, 16, 3),      # 14-16 → D3 Queenstown
    (9, 10, 4),       # 09-10 → D4 Telok Blangah
    (11, 13, 5),      # 11-13 → D5 Pasir Panjang/Clementi
    (17, 17, 6),      # 17    → D6 High Street/Beach Road
    (18, 19, 7),      # 18-19 → D7 Middle Road
    (20, 21, 8),      # 20-21 → D8 Little India
    (22, 23, 9),      # 22-23 → D9 Orchard
    (24, 27, 10),     # 24-27 → D10 Bukit Timah/Tanglin
    (28, 30, 11),     # 28-30 → D11 Novena/Thomson
    (31, 33, 12),     # 31-33 → D12 Toa Payoh
    (34, 37, 13),     # 34-37 → D13 Macpherson
    (38, 41, 14),     # 38-41 → D14 Geylang
    (42, 45, 15),     # 42-45 → D15 Katong
    (46, 48, 16),     # 46-48 → D16 Bedok
    (49, 50, 17),     # 49-50 → D17 Loyang/Changi
    (81, 81, 17),     # 81    → D17 (Loyang/Changi extension)
    (51, 52, 18),     # 51-52 → D18 Tampines
    (53, 55, 19),     # 53-55 → D19 Serangoon Garden/Hougang
    (82, 82, 19),     # 82    → D19 (extension)
    (56, 57, 20),     # 56-57 → D20 Bishan/Ang Mo Kio
    (58, 59, 21),     # 58-59 → D21 Upper Bukit Timah
    (60, 64, 22),     # 60-64 → D22 Jurong
    (65, 68, 23),     # 65-68 → D23 Hillview/Bukit Panjang
    (69, 71, 24),     # 69-71 → D24 Lim Chu Kang
    (72, 73, 25),     # 72-73 → D25 Kranji/Woodgrove
    (77, 78, 26),     # 77-78 → D26 Upper Thomson
    (75, 76, 27),     # 75-76 → D27 Yishun/Sembawang
    (79, 80, 28),     # 79-80 → D28 Seletar
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

    Example: `238801` → sector `23`, **District 9** (Orchard, Cairnhill, River Valley).

    Returns the district number, district name, general area, and the list of
    areas within that district.
    """
    return resolve_postal_code(postal_code)
