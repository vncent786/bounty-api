"""
Singapore Address Intelligence API.

Upgrades the basic postal district lookup with:
- Planning area identification
- CCR/RCR/OCR market segmentation
- HDB town mapping
- Nearest MRT stations (with walking distance estimate)
- Address normalization

MRT station coordinates sourced from LTA DataMall / OneMap public data.
Planning area mapping sourced from URA Master Plan 2019.

No external API calls required — all data is static/local.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import math
import re

router = APIRouter(tags=["Address Intelligence"])

# ============================================================
# Haversine distance
# ============================================================

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two lat/lng points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _walking_time_minutes(distance_km: float) -> int:
    """Estimate walking time at 5 km/h, rounded to nearest minute."""
    return max(1, round(distance_km / 5.0 * 60))


# ============================================================
# MRT Station Database
# Coordinates approximate from public LTA/OneMap data
# ============================================================

MRT_STATIONS = [
    # North-South Line (NS)
    ("Jurong East", "NS1", 1.3329, 103.7436),
    ("Bukit Batok", "NS2", 1.3491, 103.7493),
    ("Bukit Gombak", "NS3", 1.3587, 103.7518),
    ("Choa Chu Kang", "NS4", 1.3854, 103.7445),
    ("Yew Tee", "NS5", 1.3975, 103.7540),
    ("Kranji", "NS7", 1.4250, 103.7621),
    ("Marsiling", "NS8", 1.4438, 103.7748),
    ("Woodlands", "NS9", 1.4361, 103.7861),
    ("Admiralty", "NS10", 1.4420, 103.7996),
    ("Sembawang", "NS11", 1.4492, 103.8206),
    ("Canberra", "NS12", 1.4538, 103.8316),
    ("Yishun", "NS13", 1.4292, 103.8356),
    ("Khatib", "NS14", 1.4174, 103.8328),
    ("Yio Chu Kang", "NS15", 1.3819, 103.8448),
    ("Ang Mo Kio", "NS16", 1.3699, 103.8495),
    ("Bishan", "NS17", 1.3508, 103.8482),
    ("Braddell", "NS18", 1.3403, 103.8473),
    ("Toa Payoh", "NS19", 1.3326, 103.8475),
    ("Novena", "NS20", 1.3202, 103.8440),
    ("Newton", "NS21", 1.3128, 103.8366),
    ("Orchard", "NS22", 1.3041, 103.8316),
    ("Somerset", "NS23", 1.3006, 103.8387),
    ("Dhoby Ghaut", "NS24", 1.2992, 103.8456),
    ("City Hall", "NS25", 1.2933, 103.8546),
    ("Raffles Place", "NS26", 1.2841, 103.8513),
    ("Marina Bay", "NS27", 1.2776, 103.8535),
    ("Marina South Pier", "NS28", 1.2705, 103.8610),

    # East-West Line (EW)
    ("Pasir Ris", "EW1", 1.3721, 103.9487),
    ("Tampines", "EW2", 1.3531, 103.9443),
    ("Simei", "EW3", 1.3426, 103.9537),
    ("Tanah Merah", "EW4", 1.3265, 103.9467),
    ("Bedok", "EW5", 1.3236, 103.9321),
    ("Kembangan", "EW6", 1.3211, 103.9122),
    ("Eunos", "EW7", 1.3198, 103.9011),
    ("Paya Lebar", "EW8", 1.3179, 103.8917),
    ("Aljunied", "EW9", 1.3133, 103.8817),
    ("Kallang", "EW10", 1.3105, 103.8715),
    ("Lavender", "EW11", 1.3070, 103.8645),
    ("Bugis", "EW12", 1.3006, 103.8561),
    ("City Hall", "EW13", 1.2933, 103.8546),
    ("Raffles Place", "EW14", 1.2841, 103.8513),
    ("Tanjong Pagar", "EW15", 1.2764, 103.8460),
    ("Outram Park", "EW16", 1.2807, 103.8378),
    ("Tiong Bahru", "EW17", 1.2863, 103.8328),
    ("Redhill", "EW18", 1.2894, 103.8211),
    ("Queenstown", "EW19", 1.2944, 103.8061),
    ("Commonwealth", "EW20", 1.2968, 103.7986),
    ("Buona Vista", "EW21", 1.2936, 103.7905),
    ("Dover", "EW22", 1.2928, 103.7830),
    ("Clementi", "EW23", 1.3153, 103.7645),
    ("Jurong East", "EW24", 1.3329, 103.7436),
    ("Chinese Garden", "EW25", 1.3415, 103.7360),
    ("Lakeside", "EW26", 1.3452, 103.7226),
    ("Boon Lay", "EW27", 1.3385, 103.7040),
    ("Pioneer", "EW28", 1.3341, 103.6954),
    ("Joo Koon", "EW29", 1.3276, 103.6817),
    ("Gul Circle", "EW30", 1.3162, 103.6751),
    ("Tuas Crescent", "EW31", 1.3072, 103.6657),
    ("Tuas West Road", "EW32", 1.2988, 103.6548),
    ("Tuas Link", "EW33", 1.2937, 103.6473),

    # North-East Line (NE)
    ("HarbourFront", "NE1", 1.2658, 103.8215),
    ("Outram Park", "NE3", 1.2807, 103.8378),
    ("Chinatown", "NE4", 1.2841, 103.8438),
    ("Clarke Quay", "NE5", 1.2870, 103.8460),
    ("Dhoby Ghaut", "NE6", 1.2992, 103.8456),
    ("Little India", "NE7", 1.3064, 103.8495),
    ("Farrer Park", "NE8", 1.3124, 103.8540),
    ("Boon Keng", "NE9", 1.3176, 103.8628),
    ("Potong Pasir", "NE10", 1.3256, 103.8686),
    ("Woodleigh", "NE11", 1.3303, 103.8736),
    ("Serangoon", "NE12", 1.3496, 103.8753),
    ("Kovan", "NE13", 1.3562, 103.8833),
    ("Hougang", "NE14", 1.3713, 103.8918),
    ("Buangkok", "NE15", 1.3835, 103.8971),
    ("Sengkang", "NE16", 1.3915, 103.8946),
    ("Punggol", "NE17", 1.4041, 103.9019),

    # Circle Line (CC)
    ("Dhoby Ghaut", "CC1", 1.2992, 103.8456),
    ("Bras Basah", "CC2", 1.2966, 103.8503),
    ("Esplanade", "CC3", 1.2935, 103.8561),
    ("Promenade", "CC4", 1.2920, 103.8617),
    ("Nicoll Highway", "CC5", 1.2975, 103.8641),
    ("Stadium", "CC6", 1.3028, 103.8742),
    ("Mountbatten", "CC7", 1.3069, 103.8819),
    ("Dakota", "CC8", 1.3079, 103.8888),
    ("Paya Lebar", "CC9", 1.3179, 103.8917),
    ("MacPherson", "CC10", 1.3297, 103.8924),
    ("Tai Seng", "CC11", 1.3329, 103.8998),
    ("Bartley", "CC12", 1.3426, 103.8985),
    ("Serangoon", "CC13", 1.3496, 103.8753),
    ("Lorong Chuan", "CC14", 1.3546, 103.8653),
    ("Bishan", "CC15", 1.3508, 103.8482),
    ("Marymount", "CC16", 1.3456, 103.8398),
    ("Caldecott", "CC17", 1.3355, 103.8323),
    ("Botanic Gardens", "CC19", 1.3226, 103.8156),
    ("Farrer Road", "CC20", 1.3140, 103.8063),
    ("Holland Village", "CC21", 1.3113, 103.7956),
    ("Buona Vista", "CC22", 1.2936, 103.7905),
    ("one-north", "CC23", 1.2900, 103.7837),
    ("Kent Ridge", "CC24", 1.2937, 103.7723),
    ("Haw Par Villa", "CC25", 1.2817, 103.7712),
    ("Pasir Panjang", "CC26", 1.2792, 103.7604),
    ("Labrador Park", "CC27", 1.2724, 103.7535),
    ("Telok Blangah", "CC28", 1.2675, 103.8180),
    ("HarbourFront", "CC29", 1.2658, 103.8215),

    # Downtown Line (DT)
    ("Bukit Panjang", "DT1", 1.3782, 103.7626),
    ("Cashew", "DT2", 1.3704, 103.7676),
    ("Hillview", "DT3", 1.3616, 103.7685),
    ("Beauty World", "DT5", 1.3427, 103.7747),
    ("King Albert Park", "DT6", 1.3360, 103.7820),
    ("Sixth Avenue", "DT7", 1.3296, 103.7905),
    ("Tan Kah Kee", "DT8", 1.3253, 103.7975),
    ("Botanic Gardens", "DT9", 1.3226, 103.8156),
    ("Stevens", "DT10", 1.3224, 103.8232),
    ("Newton", "DT11", 1.3128, 103.8366),
    ("Little India", "DT12", 1.3064, 103.8495),
    ("Rochor", "DT13", 1.3028, 103.8538),
    ("Bugis", "DT14", 1.3006, 103.8561),
    ("Promenade", "DT15", 1.2920, 103.8617),
    ("Bayfront", "DT16", 1.2830, 103.8592),
    ("Downtown", "DT17", 1.2790, 103.8532),
    ("Telok Ayer", "DT18", 1.2803, 103.8467),
    ("Chinatown", "DT19", 1.2841, 103.8438),
    ("Fort Canning", "DT20", 1.2925, 103.8443),
    ("Bencoolen", "DT21", 1.2975, 103.8494),
    ("Jalan Besar", "DT22", 1.3035, 103.8548),
    ("Bendemeer", "DT23", 1.3103, 103.8608),
    ("Geylang Bahru", "DT24", 1.3140, 103.8690),
    ("Mattar", "DT25", 1.3164, 103.8779),
    ("MacPherson", "DT26", 1.3297, 103.8924),
    ("Ubi", "DT27", 1.3303, 103.8985),
    ("Kaki Bukit", "DT28", 1.3355, 103.9046),
    ("Bedok North", "DT29", 1.3322, 103.9170),
    ("Bedok Reservoir", "DT30", 1.3306, 103.9245),
    ("Tampines West", "DT31", 1.3454, 103.9365),
    ("Tampines", "DT32", 1.3531, 103.9443),
    ("Tampines East", "DT33", 1.3531, 103.9564),
    ("Upper Changi", "DT34", 1.3611, 103.9700),
    ("Expo", "DT35", 1.3572, 103.9915),

    # Thomson-East Coast Line (TE)
    ("Woodlands North", "TE1", 1.4639, 103.7976),
    ("Woodlands", "TE2", 1.4361, 103.7861),
    ("Woodlands South", "TE3", 1.4280, 103.7763),
    ("Springleaf", "TE4", 1.4045, 103.8196),
    ("Lentor", "TE5", 1.3885, 103.8386),
    ("Mayflower", "TE6", 1.3801, 103.8424),
    ("Bright Hill", "TE7", 1.3694, 103.8370),
    ("Upper Thomson", "TE8", 1.3569, 103.8313),
    ("Caldecott", "TE9", 1.3355, 103.8323),
    ("Stevens", "TE11", 1.3224, 103.8232),
    ("Napier", "TE12", 1.3153, 103.8213),
    ("Orchard Boulevard", "TE13", 1.3073, 103.8218),
    ("Orchard", "TE14", 1.3041, 103.8316),
    ("Great World", "TE15", 1.2945, 103.8313),
    ("Havelock", "TE16", 1.2906, 103.8397),
    ("Outram Park", "TE17", 1.2807, 103.8378),
    ("Maxwell", "TE18", 1.2790, 103.8457),
    ("Shenton Way", "TE19", 1.2793, 103.8522),
    ("Marina Bay", "TE20", 1.2776, 103.8535),
    ("Gardens by the Bay", "TE22", 1.2820, 103.8640),
    ("Tanjong Rhu", "TE23", 1.2975, 103.8733),
    ("Katong Park", "TE24", 1.3025, 103.8800),
    ("Tanjong Katong", "TE25", 1.3065, 103.8880),
    ("Marine Parade", "TE26", 1.3025, 103.9060),
    ("Marine Terrace", "TE27", 1.3055, 103.9245),
    ("Siglap", "TE28", 1.3185, 103.9240),
    ("Bayshore", "TE29", 1.3155, 103.9400),
    ("Bedok South", "TE30", 1.3205, 103.9460),
    ("Sungei Bedok", "TE31", 1.3260, 103.9480),
]

# Build deduplicated station list (some stations appear on multiple lines)
_station_map = {}
for name, code, lat, lng in MRT_STATIONS:
    if name not in _station_map:
        _station_map[name] = {"name": name, "lines": [], "lat": lat, "lng": lng}
    _station_map[name]["lines"].append(code)

STATIONS = list(_station_map.values())


def find_nearest_mrt(lat: float, lng: float, limit: int = 5) -> List[dict]:
    """Find nearest MRT stations by Haversine distance."""
    scored = []
    for st in STATIONS:
        dist = _haversine_km(lat, lng, st["lat"], st["lng"])
        scored.append({
            "station": st["name"],
            "lines": st["lines"],
            "distance_km": round(dist, 2),
            "walking_time_minutes": _walking_time_minutes(dist),
        })
    scored.sort(key=lambda x: x["distance_km"])
    return scored[:limit]


# ============================================================
# Postal sector → planning area + region mapping
# Source: URA Master Plan 2019, SLA postal sector reference
# ============================================================

# Region classification: CCR (Core Central), RCR (Rest of Central), OCR (Outside Central)
# Based on URA market segmentation

SECTOR_TO_PLANNING_AREA = {
    "01": ("Chinatown", "CCR"), "02": ("Chinatown", "CCR"), "03": ("Queenstown", "CCR"),
    "04": ("Telok Blangah", "CCR"), "05": ("Telok Blangah", "CCR"), "06": ("Chinatown", "CCR"),
    "07": ("Chinatown", "CCR"), "08": ("Chinatown", "CCR"),
    "14": ("Queenstown", "CCR"), "15": ("Queenstown", "CCR"),
    "16": ("Queenstown", "CCR"),
    "22": ("Orchard", "CCR"), "23": ("Orchard", "CCR"),
    "09": ("Bukit Timah", "CCR"), "10": ("Bukit Timah", "CCR"), "11": ("Bukit Timah", "CCR"),
    "12": ("Bukit Timah", "CCR"), "13": ("Bukit Timah", "CCR"),
    "17": ("Downtown Core", "CCR"), "18": ("Downtown Core", "CCR"), "19": ("Downtown Core", "CCR"),
    "20": ("Downtown Core", "CCR"), "21": ("Downtown Core", "CCR"),
    "24": ("Bukit Timah", "CCR"), "25": ("Bukit Panjang", "OCR"),
    "26": ("Bukit Timah", "CCR"), "27": ("Bukit Timah", "CCR"), "28": ("Bukit Timah", "CCR"),
    "29": ("Bukit Timah", "CCR"),
    "30": ("Bishan", "RCR"), "31": ("Bishan", "RCR"),
    "32": ("Bishan", "RCR"), "33": ("Bishan", "RCR"),
    "34": ("Geylang", "RCR"), "35": ("Geylang", "RCR"), "36": ("Geylang", "RCR"),
    "37": ("Geylang", "RCR"), "38": ("Geylang", "RCR"),
    "39": ("Geylang", "RCR"), "40": ("Geylang", "RCR"), "41": ("Geylang", "RCR"),
    "42": ("Geylang", "RCR"),
    "43": ("Marine Parade", "RCR"), "44": ("Marine Parade", "RCR"), "45": ("Marine Parade", "RCR"),
    "46": ("Marine Parade", "RCR"),
    "47": ("Marine Parade", "RCR"), "48": ("Marine Parade", "RCR"),
    "49": ("Bukit Merah", "RCR"), "50": ("Bukit Merah", "RCR"),
    "51": ("Bukit Merah", "RCR"), "52": ("Bukit Merah", "RCR"),
    "53": ("Bukit Merah", "RCR"), "54": ("Bukit Merah", "RCR"), "55": ("Bukit Merah", "RCR"),
    "56": ("Bukit Merah", "RCR"), "57": ("Bukit Merah", "RCR"),
    "58": ("Queenstown", "RCR"), "59": ("Queenstown", "RCR"),
    "60": ("Kallang", "RCR"), "61": ("Kallang", "RCR"), "62": ("Kallang", "RCR"),
    "63": ("Kallang", "RCR"), "64": ("Kallang", "RCR"), "65": ("Kallang", "RCR"),
    "66": ("Kallang", "RCR"), "67": ("Kallang", "RCR"),
    "68": ("Toa Payoh", "RCR"), "69": ("Toa Payoh", "RCR"), "70": ("Toa Payoh", "RCR"),
    "71": ("Toa Payoh", "RCR"), "72": ("Toa Payoh", "RCR"), "73": ("Toa Payoh", "RCR"),
    "76": ("Toa Payoh", "RCR"), "77": ("Toa Payoh", "RCR"),
    "78": ("Toa Payoh", "RCR"),
    "74": ("Novena", "RCR"), "75": ("Novena", "RCR"),
    "79": ("Novena", "RCR"),
    "80": ("Ang Mo Kio", "OCR"), "81": ("Ang Mo Kio", "OCR"),
    "82": ("Ang Mo Kio", "OCR"), "83": ("Ang Mo Kio", "OCR"),
    "56": ("Bukit Merah", "RCR"),
    "73": ("Toa Payoh", "RCR"),
    "79": ("Novena", "RCR"),
    "54": ("Bukit Merah", "RCR"),
    "55": ("Bukit Merah", "RCR"),
}

# HDB town mapping by postal sector (approximate)
SECTOR_TO_HDB_TOWN = {
    "10": "Bukit Timah", "11": "Bukit Timah", "12": "Bukit Timah",
    "13": "Bukit Timah",
    "18": "Downtown", "19": "Downtown", "20": "Downtown", "21": "Downtown",
    "23": "Orchard", "22": "Orchard",
    "30": "Bishan", "31": "Bishan", "32": "Bishan", "33": "Bishan",
    "53": "Bukit Merah", "54": "Bukit Merah", "55": "Bukit Merah",
    "56": "Bukit Merah", "57": "Bukit Merah",
    "60": "Kallang/Whampoa", "61": "Kallang/Whampoa",
    "62": "Kallang/Whampoa", "63": "Kallang/Whampoa",
    "68": "Toa Payoh", "69": "Toa Payoh", "70": "Toa Payoh",
    "71": "Toa Payoh", "72": "Toa Payoh", "73": "Toa Payoh",
    "74": "Novena", "75": "Novena", "76": "Toa Payoh", "77": "Toa Payoh",
    "78": "Toa Payoh",
    "80": "Ang Mo Kio", "81": "Ang Mo Kio", "82": "Ang Mo Kio",
    "83": "Ang Mo Kio",
    "56": "Bukit Merah",
    "58": "Queenstown", "59": "Queenstown",
    "14": "Queenstown", "15": "Queenstown", "16": "Queenstown",
    "03": "Queenstown", "04": "Queenstown", "05": "Queenstown",
    "49": "Bukit Merah", "50": "Bukit Merah", "51": "Bukit Merah",
    "52": "Bukit Merah",
    "64": "Kallang/Whampoa", "65": "Kallang/Whampoa",
    "66": "Kallang/Whampoa", "67": "Kallang/Whampoa",
    "79": "Novena",
}

# Approximate coordinates for postal sectors (sector center)
# Used for MRT proximity when we don't have exact postal code geocoding
# Source: approximate centroid of each postal sector
SECTOR_COORDS = {
    "01": (1.2820, 103.8440), "02": (1.2810, 103.8440), "03": (1.2890, 103.8160),
    "04": (1.2750, 103.8100), "05": (1.2750, 103.8050), "06": (1.2800, 103.8460),
    "07": (1.2840, 103.8440), "08": (1.2850, 103.8460),
    "09": (1.3160, 103.8230), "10": (1.3150, 103.8200), "11": (1.3250, 103.8250),
    "12": (1.3320, 103.8300), "13": (1.3350, 103.8350),
    "14": (1.2960, 103.8060), "15": (1.2950, 103.8030), "16": (1.2930, 103.8000),
    "17": (1.2890, 103.8510), "18": (1.2870, 103.8530), "19": (1.2870, 103.8550),
    "20": (1.2860, 103.8570), "21": (1.2850, 103.8590),
    "22": (1.3040, 103.8320), "23": (1.3050, 103.8350),
    "24": (1.3230, 103.8240), "25": (1.3770, 103.7600),
    "26": (1.3260, 103.8000), "27": (1.3250, 103.8000), "28": (1.3300, 103.8000),
    "29": (1.3320, 103.7900),
    "30": (1.3510, 103.8480), "31": (1.3520, 103.8500), "32": (1.3530, 103.8520),
    "33": (1.3540, 103.8550),
    "34": (1.3180, 103.8900), "35": (1.3200, 103.8900), "36": (1.3220, 103.8900),
    "37": (1.3240, 103.8900), "38": (1.3260, 103.8900),
    "39": (1.3280, 103.8900), "40": (1.3300, 103.8900), "41": (1.3320, 103.8900),
    "42": (1.3340, 103.8900),
    "43": (1.3050, 103.9050), "44": (1.3060, 103.9100), "45": (1.3070, 103.9150),
    "46": (1.3080, 103.9200),
    "47": (1.3020, 103.9250), "48": (1.3030, 103.9300),
    "49": (1.2750, 103.8300), "50": (1.2780, 103.8300),
    "51": (1.2810, 103.8300), "52": (1.2840, 103.8300),
    "53": (1.2870, 103.8300), "54": (1.2900, 103.8250), "55": (1.2930, 103.8250),
    "56": (1.2820, 103.8200), "57": (1.2780, 103.8200),
    "58": (1.2940, 103.8080), "59": (1.2950, 103.8050),
    "60": (1.3110, 103.8720), "61": (1.3120, 103.8700), "62": (1.3130, 103.8680),
    "63": (1.3140, 103.8660), "64": (1.3150, 103.8640), "65": (1.3160, 103.8620),
    "66": (1.3170, 103.8600), "67": (1.3180, 103.8580),
    "68": (1.3330, 103.8470), "69": (1.3340, 103.8460), "70": (1.3350, 103.8450),
    "71": (1.3360, 103.8440), "72": (1.3370, 103.8430), "73": (1.3380, 103.8420),
    "74": (1.3200, 103.8440), "75": (1.3210, 103.8420),
    "76": (1.3390, 103.8410), "77": (1.3400, 103.8400), "78": (1.3410, 103.8390),
    "79": (1.3220, 103.8400),
    "80": (1.3700, 103.8500), "81": (1.3710, 103.8520), "82": (1.3720, 103.8540),
    "83": (1.3730, 103.8560),
}


def _normalize_postal_code(code: str) -> str:
    """Extract 6 digits from a postal code string."""
    digits = re.sub(r'\D', '', code)
    if len(digits) == 6:
        return digits
    return code.strip()


@router.get("/address/{postal_code}")
async def address_intelligence(postal_code: str):
    """
    Full address intelligence for a Singapore postal code.

    Returns:
    - Postal district number and name
    - Planning area (URA Master Plan)
    - Market region (CCR/RCR/OCR)
    - HDB town (if applicable)
    - Nearest MRT stations with distance and walking time
    - Approximate coordinates

    All data from static reference tables. No external API calls.
    """
    code = _normalize_postal_code(postal_code)
    sector = code[:2] if len(code) >= 2 else ""

    # Import district mapping from existing postal_district module
    from apis.postal_district import POSTAL_SECTOR_TO_DISTRICT

    # Find district
    sector_int = int(sector) if sector.isdigit() else 0
    district_num = POSTAL_SECTOR_TO_DISTRICT.get(sector_int)

    # Get planning area and region
    planning_area, region = SECTOR_TO_PLANNING_AREA.get(sector, ("Unknown", "Unknown"))

    # Get HDB town
    hdb_town = SECTOR_TO_HDB_TOWN.get(sector, None)

    # Get coordinates
    coords = SECTOR_COORDS.get(sector, None)
    lat, lng = coords if coords else (None, None)

    # Find nearest MRT
    nearest_mrt = []
    if lat and lng:
        nearest_mrt = find_nearest_mrt(lat, lng, limit=5)

    return {
        "postal_code": code,
        "sector": sector,
        "district_number": district_num,
        "planning_area": planning_area,
        "market_region": region,
        "region_description": {
            "CCR": "Core Central Region (prime districts 9-11, downtown core)",
            "RCR": "Rest of Central Region (city fringe, intermediate zone)",
            "OCR": "Outside Central Region (suburban, mass-market)",
        }.get(region, "Unknown"),
        "hdb_town": hdb_town,
        "approximate_coordinates": {"lat": lat, "lng": lng} if lat else None,
        "nearest_mrt_stations": nearest_mrt,
        "total_mrt_stations_in_db": len(STATIONS),
        "source": "URA Master Plan 2019 (planning areas), LTA/OneMap (MRT coordinates), SLA (postal sectors)",
        "fetched_at": datetime.now().strftime("%Y-%m-%d"),
    }


@router.get("/mrt/search")
async def search_mrt(
    q: str = Query(..., description="Station name to search (partial match)"),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Search MRT stations by name. Returns station details with line codes."""
    q_lower = q.lower()
    results = []
    for st in STATIONS:
        if q_lower in st["name"].lower():
            results.append({
                "station": st["name"],
                "lines": st["lines"],
                "latitude": st["lat"],
                "longitude": st["lng"],
            })
    return {
        "query": q,
        "results": results[:limit],
        "total_found": len(results),
        "source": "LTA DataMall / OneMap public data",
    }


@router.get("/mrt/near/{postal_code}")
async def mrt_near_postal(
    postal_code: str,
    limit: int = Query(default=5, ge=1, le=20),
):
    """Find nearest MRT stations to a Singapore postal code."""
    code = _normalize_postal_code(postal_code)
    sector = code[:2] if len(code) >= 2 else ""
    coords = SECTOR_COORDS.get(sector, None)

    if not coords:
        return {
            "error": f"Postal sector {sector} not found in coordinate database",
            "postal_code": code,
            "sector": sector,
        }

    lat, lng = coords
    stations = find_nearest_mrt(lat, lng, limit=limit)

    return {
        "postal_code": code,
        "sector": sector,
        "approximate_coordinates": {"lat": lat, "lng": lng},
        "nearest_stations": stations,
        "source": "Approximate sector centroid → Haversine distance. Not exact address geocoding.",
    }


@router.get("/mrt/stations")
async def list_all_stations():
    """List all MRT stations in the database."""
    return {
        "total_stations": len(STATIONS),
        "stations": [
            {
                "name": st["name"],
                "lines": st["lines"],
                "latitude": st["lat"],
                "longitude": st["lng"],
            }
            for st in STATIONS
        ],
        "source": "LTA DataMall / OneMap public data",
    }
