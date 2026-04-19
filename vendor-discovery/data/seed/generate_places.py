#!/usr/bin/env python3
"""Generate places.json — Google Places-shaped vendor fixtures for each work order.

Reads requests.json, emits places.json keyed by work_order_id. Each entry mimics
what a real Google Places "Nearby Search" + "Place Details" call would return for
a 20-mile radius search centered on the work order's location, extended with
multi-source signals (BBB, license, insurance, years in business) so the scoring
layer has real differentiation.

Vendors per request are drawn from weighted archetypes (premium, solid-mid,
suspicious-five-star, mediocre, low-quality, out-of-radius, expired-license,
residential-only) to guarantee non-trivial scoring. All RNG goes through a
seeded random.Random so the output is reproducible.

Run:
    python generate_places.py
"""
from __future__ import annotations

import json
import math
import random
import string
from datetime import date, timedelta
from pathlib import Path


RNG_SEED = 20260419
RADIUS_MILES = 20
VENDORS_PER_REQUEST = (12, 20)
HERE = Path(__file__).resolve().parent
REQUESTS_PATH = HERE / "requests.json"
OUT_PATH = HERE / "places.json"


# ---------------------------------------------------------------------------
# Trade / state reference tables
# ---------------------------------------------------------------------------

TRADE_GOOGLE_TYPES = {
    "plumbing": ["plumber", "point_of_interest", "establishment"],
    "hvac": ["hvac_contractor", "general_contractor", "point_of_interest", "establishment"],
    "electrical": ["electrician", "point_of_interest", "establishment"],
    "lawncare": ["general_contractor", "landscaping", "point_of_interest", "establishment"],
    "handyman": ["general_contractor", "point_of_interest", "establishment"],
    "appliance_repair": ["home_goods_store", "point_of_interest", "establishment"],
}

STATE_AREA_CODES = {
    "TX": ["214", "512", "713", "817", "469", "832", "210"],
    "NC": ["704", "980", "336"],
    "AZ": ["602", "480", "623"],
    "GA": ["404", "678", "770", "470"],
    "FL": ["813", "727"],
    "NV": ["702", "725"],
    "TN": ["615", "629"],
    "CO": ["303", "720"],
}

LICENSE_CLASS = {
    "plumbing": {
        "TX": "Master Plumber", "NC": "P-1 Plumbing Contractor",
        "AZ": "CR-37 Plumbing", "GA": "Master Plumber",
        "FL": "Certified Plumbing Contractor", "NV": "C-1 Plumbing",
        "TN": "Contractor - Plumbing", "CO": "Master Plumber",
    },
    "hvac": {
        "TX": "TACLA Class A Contractor", "NC": "H-3 Refrigeration & A/C",
        "AZ": "CR-39 Air Conditioning", "GA": "Class I Conditioned Air",
        "FL": "Certified Mechanical Contractor", "NV": "C-21 Refrigeration & A/C",
        "TN": "Contractor - Mechanical", "CO": "HVAC Contractor",
    },
    "electrical": {
        "TX": "Master Electrician", "NC": "Unlimited Electrical",
        "AZ": "CR-11 Electrical", "GA": "Electrical Non-Restricted",
        "FL": "Certified Electrical Contractor", "NV": "C-2 Electrical",
        "TN": "Contractor - Electrical", "CO": "Master Electrician",
    },
    "lawncare": {
        "TX": "Commercial Pesticide Applicator", "NC": "Certified Landscape Applicator",
        "AZ": "Pest Control Applicator", "GA": "Licensed Landscape Contractor",
        "FL": "Limited Landscape Contractor", "NV": "C-10 Landscape",
        "TN": "Certified Landscape Applicator", "CO": "Landscape Certification",
    },
    "handyman": {
        "TX": "Handyman (exempt under $10k)", "NC": "Handyman (exempt under $30k)",
        "AZ": "Handyman Exemption ($1k)", "GA": "Handyman (exempt under $2.5k)",
        "FL": "Handyman (exempt under $2.5k)", "NV": "Handyman Class C (limited)",
        "TN": "Home Improvement Contractor", "CO": "Handyman (local registration)",
    },
    "appliance_repair": {
        "TX": "Appliance Installer (TDLR)", "NC": "Appliance Repair",
        "AZ": "CR-42 Appliance", "GA": "Appliance Repair",
        "FL": "Appliance Repair (Reg.)", "NV": "C-10.1 Appliance",
        "TN": "Appliance Repair", "CO": "Appliance Repair",
    },
}

LICENSE_PREFIX = {
    "plumbing": {"TX": "M-", "NC": "P1-", "AZ": "ROC-CR37-", "GA": "MP", "FL": "CFC", "NV": "C1-", "TN": "CMC-P-", "CO": "MP."},
    "hvac": {"TX": "TACLA-", "NC": "H3-", "AZ": "ROC-CR39-", "GA": "CN", "FL": "CMC", "NV": "C21-", "TN": "CMC-M-", "CO": "HV."},
    "electrical": {"TX": "TECL-", "NC": "UL-", "AZ": "ROC-CR11-", "GA": "EN", "FL": "EC", "NV": "C2-", "TN": "CMC-E-", "CO": "ME."},
    "lawncare": {"TX": "TDA-CP-", "NC": "NCDA-CLA-", "AZ": "AZDA-PCA-", "GA": "GDA-LA", "FL": "FLDA-LC", "NV": "C10-", "TN": "TDA-CLA-", "CO": "CDA-LA."},
    "handyman": {"TX": "HRI-", "NC": "HM-", "AZ": "HDY-", "GA": "HM-", "FL": "HM-", "NV": "HCC-", "TN": "HIC-", "CO": "HR-"},
    "appliance_repair": {"TX": "TDLR-A-", "NC": "AR-", "AZ": "ROC-CR42-", "GA": "AR-", "FL": "AR", "NV": "C101-", "TN": "AR-", "CO": "AR."},
}

INSURANCE_CARRIERS = [
    "The Hartford", "Travelers", "Liberty Mutual Commercial", "Nationwide",
    "Progressive Commercial", "State Farm Business", "Chubb",
    "Zurich North America", "CNA", "AmTrust Financial", "Cincinnati Insurance",
]


# ---------------------------------------------------------------------------
# Vendor-name generation
# ---------------------------------------------------------------------------

PERSONAL_PREFIXES = [
    "Hernandez", "Johnson", "Williams", "Rodriguez", "Martinez", "O'Brien",
    "Patel", "Kim", "Nguyen", "Carter", "Stewart", "Blake", "Thompson",
    "Cruz", "Bailey", "Walsh", "Dawson", "Moreno", "Reyes", "Foster",
    "Novak", "Harris", "Brennan", "Chen", "Okafor", "Mendoza", "Clarke",
    "Whitaker", "Pham", "Delgado", "Fitzgerald", "Knox",
]

GEOGRAPHIC_PREFIXES_BY_STATE = {
    "TX": ["Lone Star", "Big D", "Republic", "Trinity", "Gulf Coast", "West Texas", "Pecan", "Alamo"],
    "NC": ["Carolina", "Piedmont", "Tarheel", "Queen City", "Blue Ridge", "Charlotte Metro"],
    "AZ": ["Valley of the Sun", "Desert", "Saguaro", "Arizona", "Camelback", "Sonoran"],
    "GA": ["Peach State", "Metro Atlanta", "Southern", "Georgia", "Chattahoochee", "Piedmont"],
    "FL": ["Sunshine State", "Gulf Coast", "Bayshore", "Florida", "Tampa Bay", "Gulfside"],
    "NV": ["Silver State", "Mojave", "High Desert", "Nevada", "Sin City", "Vegas Valley"],
    "TN": ["Music City", "Volunteer", "Cumberland", "Tennessee", "Mid-State", "Nashville Metro"],
    "CO": ["Mile High", "Front Range", "Rocky Mountain", "Colorado", "Centennial", "Denver Metro"],
}

DESCRIPTIVE_PREFIXES = [
    "Apex", "Reliable", "BlueWave", "Rapid", "Quality", "Prime", "Elite",
    "Ace", "All-Pro", "Honest", "Fair", "Precision", "Ironclad",
    "Summit", "Cornerstone", "Keystone", "Evergreen", "True North", "Steady",
    "Signature", "Heritage", "Classic", "First Call", "On-Point", "Direct",
    "Bulldog", "Hometown", "Pioneer", "Champion", "Anchor",
]

TRADE_NOUNS = {
    "plumbing": [
        "Plumbing", "Plumbing Co.", "Plumbing Services", "Plumbing & Drain",
        "Pipeworks", "Drain & Sewer", "Rooter", "Plumbing Pros",
        "Plumbing Group", "Commercial Plumbing", "Plumbing Solutions",
    ],
    "hvac": [
        "HVAC", "HVAC Services", "Heating & Cooling", "Heating & Air",
        "Air Conditioning", "Climate Control", "Mechanical Services",
        "HVAC Contractors", "Air Systems", "Comfort Systems", "Cooling Pros",
    ],
    "electrical": [
        "Electric", "Electrical", "Electric Co.", "Electrical Services",
        "Power Systems", "Electrical Contractors", "Electric Group",
        "Wiring Solutions", "Electrical Pros", "Circuit Specialists",
    ],
    "lawncare": [
        "Lawn Care", "Landscaping", "Lawn Service", "Grounds",
        "Lawn & Landscape", "Yard Pros", "Turf Management", "Greenworks",
        "Landscape Solutions", "Commercial Grounds", "Outdoor Services",
    ],
    "handyman": [
        "Handyman Services", "Home Services", "Property Services",
        "Handyman Pros", "Maintenance", "Home Repair", "Property Maintenance",
        "Facilities Services", "Repair & Remodel", "Craftsman Services",
    ],
    "appliance_repair": [
        "Appliance Repair", "Appliance Service", "Commercial Appliance",
        "Refrigeration Services", "Appliance Pros", "Restaurant Equipment Repair",
        "Commercial Kitchen Service", "Coolers & Freezers", "Appliance Techs",
    ],
}


# ---------------------------------------------------------------------------
# Archetypes (weight / signal ranges)
# ---------------------------------------------------------------------------

ARCHETYPES = [
    ("premium", 0.20, {
        "rating": (4.5, 4.9), "reviews": (220, 1200),
        "bbb_rating": ["A+", "A+", "A"], "bbb_accredited_p": 0.9,
        "license_status": "active", "insurance_verified_p": 0.98,
        "coverage": (1_500_000, 3_000_000), "years": (10, 32),
        "emergency_p": 0.7, "accepts_commercial_p": 1.0,
        "employee_band": ["11-50", "51-200"], "price_level": [2, 3],
        "workers_comp_p": 0.95,
    }),
    ("solid_mid", 0.25, {
        "rating": (4.1, 4.5), "reviews": (60, 420),
        "bbb_rating": ["A", "A-", "B+"], "bbb_accredited_p": 0.6,
        "license_status": "active", "insurance_verified_p": 0.9,
        "coverage": (1_000_000, 2_000_000), "years": (4, 16),
        "emergency_p": 0.4, "accepts_commercial_p": 0.95,
        "employee_band": ["1-10", "11-50"], "price_level": [2, 2, 3],
        "workers_comp_p": 0.8,
    }),
    ("suspicious_five_star", 0.12, {
        "rating": (4.7, 5.0), "reviews": (3, 16),
        "bbb_rating": ["NR", "NR", "B"], "bbb_accredited_p": 0.05,
        "license_status": "active", "insurance_verified_p": 0.35,
        "coverage": (300_000, 1_000_000), "years": (0, 3),
        "emergency_p": 0.3, "accepts_commercial_p": 0.5,
        "employee_band": ["1-10"], "price_level": [1, 2],
        "workers_comp_p": 0.3,
    }),
    ("mediocre", 0.18, {
        "rating": (3.6, 4.1), "reviews": (80, 520),
        "bbb_rating": ["B", "B-", "C+"], "bbb_accredited_p": 0.3,
        "license_status": "active", "insurance_verified_p": 0.75,
        "coverage": (500_000, 1_500_000), "years": (3, 20),
        "emergency_p": 0.3, "accepts_commercial_p": 0.75,
        "employee_band": ["1-10", "11-50"], "price_level": [1, 2],
        "workers_comp_p": 0.6,
    }),
    ("low_quality", 0.10, {
        "rating": (2.8, 3.6), "reviews": (40, 310),
        "bbb_rating": ["C", "C-", "D"], "bbb_accredited_p": 0.05,
        "license_status": "active", "insurance_verified_p": 0.4,
        "coverage": (250_000, 1_000_000), "years": (1, 10),
        "emergency_p": 0.2, "accepts_commercial_p": 0.5,
        "employee_band": ["1-10"], "price_level": [1],
        "workers_comp_p": 0.3,
    }),
    ("out_of_radius", 0.07, {
        "rating": (4.0, 4.7), "reviews": (60, 400),
        "bbb_rating": ["A", "A-"], "bbb_accredited_p": 0.7,
        "license_status": "active", "insurance_verified_p": 0.9,
        "coverage": (1_000_000, 2_000_000), "years": (5, 20),
        "emergency_p": 0.3, "accepts_commercial_p": 0.9,
        "employee_band": ["1-10", "11-50"], "price_level": [2],
        "workers_comp_p": 0.75,
        "distance_override": (22, 34),
    }),
    ("expired_license", 0.04, {
        "rating": (3.8, 4.3), "reviews": (50, 300),
        "bbb_rating": ["B", "C+"], "bbb_accredited_p": 0.2,
        "license_status": "expired", "insurance_verified_p": 0.55,
        "coverage": (500_000, 1_000_000), "years": (5, 18),
        "emergency_p": 0.2, "accepts_commercial_p": 0.6,
        "employee_band": ["1-10"], "price_level": [1, 2],
        "workers_comp_p": 0.5,
    }),
    ("residential_only", 0.04, {
        "rating": (4.3, 4.8), "reviews": (80, 400),
        "bbb_rating": ["A-", "B+"], "bbb_accredited_p": 0.5,
        "license_status": "active", "insurance_verified_p": 0.9,
        "coverage": (500_000, 1_500_000), "years": (3, 12),
        "emergency_p": 0.15, "accepts_commercial_p": 0.0,
        "employee_band": ["1-10"], "price_level": [2],
        "workers_comp_p": 0.6,
    }),
]

ARCHETYPE_WEIGHTS = [a[1] for a in ARCHETYPES]


# ---------------------------------------------------------------------------
# Geo helpers
# ---------------------------------------------------------------------------

def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def offset_latlng(lat: float, lng: float, distance_miles: float, bearing_deg: float) -> tuple[float, float]:
    R = 3958.7613
    d = distance_miles / R
    b = math.radians(bearing_deg)
    p1 = math.radians(lat)
    l1 = math.radians(lng)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(p1),
        math.cos(d) - math.sin(p1) * math.sin(p2),
    )
    return math.degrees(p2), math.degrees(l2)


# ---------------------------------------------------------------------------
# Field generators
# ---------------------------------------------------------------------------

def gen_place_id(rng: random.Random) -> str:
    chars = string.ascii_letters + string.digits + "-_"
    return "ChIJ" + "".join(rng.choices(chars, k=27))


def gen_vendor_name(rng: random.Random, trade: str, state: str) -> str:
    style = rng.choices(
        ["personal", "geographic", "descriptive", "personal_sons", "personal_partners"],
        weights=[0.35, 0.20, 0.30, 0.10, 0.05],
    )[0]
    noun = rng.choice(TRADE_NOUNS[trade])
    if style == "personal":
        return f"{rng.choice(PERSONAL_PREFIXES)} {noun}"
    if style == "personal_sons":
        return f"{rng.choice(PERSONAL_PREFIXES)} & Sons {noun}"
    if style == "personal_partners":
        a, b = rng.sample(PERSONAL_PREFIXES, 2)
        return f"{a} & {b} {noun}"
    if style == "geographic":
        geo = rng.choice(GEOGRAPHIC_PREFIXES_BY_STATE.get(state, ["Metro"]))
        return f"{geo} {noun}"
    return f"{rng.choice(DESCRIPTIVE_PREFIXES)} {noun}"


def gen_phone(rng: random.Random, state: str) -> str:
    ac = rng.choice(STATE_AREA_CODES.get(state, ["555"]))
    return f"({ac}) 555-{rng.randint(100, 199):04d}"


def gen_address_line(rng: random.Random) -> str:
    num = rng.randint(100, 9999)
    street = rng.choice([
        "Magnolia", "Oak", "Elm", "Maple", "Cedar", "Cherry", "Willow",
        "Commerce", "Industrial", "Enterprise", "Market", "Main",
        "Jefferson", "Madison", "Lincoln", "Harrison", "Washington",
        "4th", "5th", "6th", "12th", "28th", "44th",
        "Reserve", "Cottonwood", "Bluebonnet", "Buckeye", "Cypress",
        "Freeway", "Bayshore", "Highland", "Ridgeview", "Parkway",
        "Sunset", "Riverside", "Brookside", "Hillcrest", "Lakeview",
    ])
    suffix = rng.choices(
        ["St", "Ave", "Rd", "Blvd", "Ln", "Dr", "Way", "Pkwy"],
        weights=[5, 4, 4, 3, 1, 2, 1, 1],
    )[0]
    return f"{num} {street} {suffix}"


def gen_zip(rng: random.Random, base_zip: str) -> str:
    base_int = int(base_zip)
    delta = rng.randint(-40, 40)
    return f"{max(1, min(99999, base_int + delta)):05d}"


def domain_from_name(name: str, rng: random.Random) -> str:
    slug = "".join(c for c in name.lower() if c.isalnum() or c == " ")
    slug = slug.replace(" and ", " ").replace("  ", " ").strip().replace(" ", "")
    slug = slug[:24]
    tld = rng.choices([".com", ".com", ".com", ".net", ".co"], weights=[5, 5, 5, 2, 1])[0]
    return f"https://www.{slug}{tld}"


def gen_hours(rng: random.Random, is_24_7: bool) -> dict:
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    if is_24_7:
        return {
            "open_now": True,
            "weekday_text": [f"{d}: Open 24 hours" for d in days],
        }
    open_hour = rng.choice([7, 7, 8, 8, 9])
    close_hour_24 = rng.choice([17, 17, 18, 18, 19])
    weekday_line = f"{open_hour}:00 AM \u2013 {close_hour_24 - 12}:00 PM"
    weekday_text = [f"{d}: {weekday_line}" for d in days[:5]]

    if rng.random() < 0.6:
        sat_open = rng.choice([8, 9])
        sat_close_pm = rng.choice([1, 2, 3])
        weekday_text.append(f"Saturday: {sat_open}:00 AM \u2013 {sat_close_pm}:00 PM")
    else:
        weekday_text.append("Saturday: Closed")

    weekday_text.append("Sunday: Closed")
    return {"open_now": rng.random() < 0.7, "weekday_text": weekday_text}


def gen_license(rng: random.Random, trade: str, state: str, status: str, years_in_business: int) -> dict:
    prefix = LICENSE_PREFIX[trade].get(state, "LIC-")
    digits = f"{rng.randint(10000, 999999):06d}"
    number = f"{prefix}{digits}"

    today = date(2026, 4, 19)
    # license first issued somewhere during their business life, bounded by 20yrs
    issued_years_ago = min(years_in_business, rng.randint(max(1, years_in_business - 3), max(2, years_in_business))) if years_in_business > 0 else 0
    issued = today - timedelta(days=int(issued_years_ago * 365.25))

    if status == "active":
        # renewed periodically; expires 1-3 years out
        expires = today + timedelta(days=rng.randint(60, 1095))
    else:  # expired
        expires = today - timedelta(days=rng.randint(30, 540))

    return {
        "number": number,
        "state": state,
        "class": LICENSE_CLASS[trade][state],
        "status": status,
        "issued_date": issued.isoformat(),
        "expires_date": expires.isoformat(),
    }


def gen_bbb(rng: random.Random, spec: dict, years_in_business: int) -> dict:
    rating = rng.choice(spec["bbb_rating"])
    accredited = rng.random() < spec["bbb_accredited_p"]
    years_accredited = min(rng.randint(1, max(1, years_in_business)), years_in_business) if accredited else 0
    if rating == "NR":
        complaints_total = 0
        complaints_resolved = 0
    else:
        # more complaints as rating drops, scaled by longevity
        rating_tier = {"A+": 0, "A": 1, "A-": 2, "B+": 3, "B": 5, "B-": 7, "C+": 10, "C": 14, "C-": 18, "D": 25}
        base = rating_tier.get(rating, 5)
        complaints_total = rng.randint(base, base + max(2, years_in_business))
        resolved_ratio = rng.uniform(0.55, 0.98) if rating in {"A+", "A", "A-", "B+"} else rng.uniform(0.3, 0.85)
        complaints_resolved = int(complaints_total * resolved_ratio)
    return {
        "rating": rating,
        "accredited": accredited,
        "years_accredited": years_accredited,
        "complaints_total": complaints_total,
        "complaints_resolved": complaints_resolved,
    }


def gen_insurance(rng: random.Random, spec: dict) -> dict:
    verified = rng.random() < spec["insurance_verified_p"]
    if not verified:
        return {
            "verified": False,
            "carrier": None,
            "general_liability_usd": None,
            "workers_comp_verified": False,
        }
    coverage_low, coverage_high = spec["coverage"]
    # round to 250k bucket
    raw = rng.randint(coverage_low, coverage_high)
    bucket = round(raw / 250_000) * 250_000
    return {
        "verified": True,
        "carrier": rng.choice(INSURANCE_CARRIERS),
        "general_liability_usd": bucket,
        "workers_comp_verified": rng.random() < spec["workers_comp_p"],
    }


# ---------------------------------------------------------------------------
# Vendor generation
# ---------------------------------------------------------------------------

def pick_archetype(rng: random.Random) -> tuple[str, dict]:
    """Weighted archetype pick. All archetypes are eligible — the generator emits raw
    aggregated signals as they would appear from Google Places + BBB + licensing boards.
    Filtering (e.g., excluding residential-only vendors for a commercial job) is the
    scoring layer's responsibility, not the data source's."""
    idx = rng.choices(range(len(ARCHETYPES)), weights=ARCHETYPE_WEIGHTS)[0]
    name, _, spec = ARCHETYPES[idx]
    return name, spec


def gen_vendor(
    rng: random.Random,
    archetype_name: str,
    spec: dict,
    trade: str,
    origin_lat: float,
    origin_lng: float,
    origin_city: str,
    origin_state: str,
    origin_zip: str,
) -> dict:
    # --- geography ---
    if "distance_override" in spec:
        low, high = spec["distance_override"]
    else:
        low, high = 0.4, RADIUS_MILES - 0.5
    dist = rng.uniform(low, high)
    bearing = rng.uniform(0, 360)
    vlat, vlng = offset_latlng(origin_lat, origin_lng, dist, bearing)
    actual_distance = haversine_miles(origin_lat, origin_lng, vlat, vlng)

    # --- identity ---
    name = gen_vendor_name(rng, trade, origin_state)
    address = gen_address_line(rng)
    zip_code = gen_zip(rng, origin_zip)
    formatted_address = f"{address}, {origin_city}, {origin_state} {zip_code}, USA"
    vicinity = f"{address}, {origin_city}"
    phone = gen_phone(rng, origin_state)
    website = domain_from_name(name, rng)

    # --- rating + reviews ---
    r_low, r_high = spec["rating"]
    rating = round(rng.uniform(r_low, r_high), 1)
    rev_low, rev_high = spec["reviews"]
    user_ratings_total = rng.randint(rev_low, rev_high)

    # --- hours ---
    is_24_7 = rng.random() < spec["emergency_p"]
    hours = gen_hours(rng, is_24_7)

    # --- business attributes ---
    years_in_business = rng.randint(*spec["years"])
    price_level = rng.choice(spec["price_level"])
    employee_count_band = rng.choice(spec["employee_band"])
    service_radius_miles = rng.choice([15, 20, 25, 30, 35, 40])
    accepts_commercial = rng.random() < spec["accepts_commercial_p"]
    accepts_credit_card = rng.random() < 0.92
    min_service_charge_usd = rng.choice([0, 49, 75, 95, 125, 150, 185, 225])

    # --- regulatory ---
    license_info = gen_license(rng, trade, origin_state, spec["license_status"], years_in_business)
    bbb = gen_bbb(rng, spec, years_in_business)
    insurance = gen_insurance(rng, spec)

    # --- business status ---
    # tiny chance of temporarily closed; otherwise operational
    bstatus = rng.choices(
        ["OPERATIONAL", "OPERATIONAL", "OPERATIONAL", "OPERATIONAL", "CLOSED_TEMPORARILY"],
        weights=[94, 0, 0, 0, 6],
    )[0]

    place_id = gen_place_id(rng)

    return {
        # --- Google Places shape ---
        "place_id": place_id,
        "name": name,
        "formatted_address": formatted_address,
        "vicinity": vicinity,
        "geometry": {
            "location": {"lat": round(vlat, 6), "lng": round(vlng, 6)},
        },
        "rating": rating,
        "user_ratings_total": user_ratings_total,
        "types": TRADE_GOOGLE_TYPES[trade],
        "business_status": bstatus,
        "opening_hours": hours,
        "formatted_phone_number": phone,
        "international_phone_number": "+1 " + phone.replace("(", "").replace(") ", "-"),
        "website": website,
        "price_level": price_level,
        "url": f"https://maps.google.com/?cid={place_id[-12:]}",
        "photos": [],
        # --- Non-Google signals (aggregated from BBB, state boards, insurance reg) ---
        "extended_signals": {
            "distance_miles": round(actual_distance, 2),
            "years_in_business": years_in_business,
            "employee_count_band": employee_count_band,
            "service_radius_miles": service_radius_miles,
            "emergency_service_24_7": is_24_7,
            "accepts_commercial": accepts_commercial,
            "accepts_credit_card": accepts_credit_card,
            "min_service_charge_usd": min_service_charge_usd,
            "bbb": bbb,
            "license": license_info,
            "insurance": insurance,
            "_archetype": archetype_name,  # debug / audit only; scoring MUST ignore this
        },
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def generate() -> dict:
    rng = random.Random(RNG_SEED)
    with REQUESTS_PATH.open() as f:
        requests_doc = json.load(f)

    places: dict = {
        "_meta": {
            "description": (
                "Google Places-shaped vendor fixtures for vendor-discovery v0. "
                "Keyed by work_order_id. Each value represents what a real Google Places "
                "'Nearby Search' + 'Place Details' call would return for a 20-mile radius "
                "query centered on the work order's location, extended with multi-source "
                "signals (BBB, state license board, insurance registry, years-in-business). "
                "Vendors are sampled from weighted archetypes so scoring has real differentiation."
            ),
            "schema_version": 1,
            "generator": "generate_places.py",
            "rng_seed": RNG_SEED,
            "radius_miles": RADIUS_MILES,
            "archetype_distribution": {a[0]: a[1] for a in ARCHETYPES},
            "per_request_vendor_count": list(VENDORS_PER_REQUEST),
        },
    }

    for req in requests_doc["requests"]:
        wo = req["work_order"]
        wo_id = wo["id"]
        trade = wo["trade"]

        vendor_count = rng.randint(*VENDORS_PER_REQUEST)
        results = []
        for _ in range(vendor_count):
            arch_name, spec = pick_archetype(rng)
            v = gen_vendor(
                rng=rng,
                archetype_name=arch_name,
                spec=spec,
                trade=trade,
                origin_lat=wo["lat"],
                origin_lng=wo["lng"],
                origin_city=wo["city"],
                origin_state=wo["state"],
                origin_zip=wo["zip"],
            )
            results.append(v)

        # sort by distance ascending, as a real Places nearby-search would roughly do
        results.sort(key=lambda v: v["extended_signals"]["distance_miles"])

        places[wo_id] = {
            "query": {
                "location": {"lat": wo["lat"], "lng": wo["lng"]},
                "radius_meters": int(RADIUS_MILES * 1609.344),
                "type": TRADE_GOOGLE_TYPES[trade][0],
                "keyword": f"commercial {trade.replace('_', ' ')}",
            },
            "result_count": len(results),
            "results": results,
        }

    return places


def main() -> None:
    out = generate()
    with OUT_PATH.open("w") as f:
        json.dump(out, f, indent=2)
    total_vendors = sum(
        len(v["results"]) for k, v in out.items() if k != "_meta"
    )
    print(f"Wrote {OUT_PATH.name}: {len(out) - 1} requests, {total_vendors} vendors total")


if __name__ == "__main__":
    main()
