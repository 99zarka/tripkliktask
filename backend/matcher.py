"""
matcher.py
----------
Core deduplication and matching logic for the mapping engine.

City Matching Strategy:
  - Normalize name (lowercase, strip accents, remove punctuation).
  - Match against master_cities using: normalized_name + country_code (exact),
    then fallback to pg_trgm similarity search (fuzzy).
  - Threshold: similarity >= 0.85 required to count as a match.

Hotel Matching Strategy (Two-Pass):
  Pass 1 — Geographic Blocking:
    Use earthdistance (cube extension) to find all master hotels within
    a configurable radius (default 300 m) of the incoming coordinates.
    Falls back to country + city_code filter if no coordinates available.
  Pass 2 — Similarity Scoring:
    For each geographic candidate, compute a composite score:
      - Name similarity  (weight 0.7) via thefuzz token_set_ratio
      - Street similarity (weight 0.3) via thefuzz partial_ratio
    Accept the best candidate if composite score >= HOTEL_SCORE_THRESHOLD.

Edge cases:
  - Intra-supplier duplicates: same supplier_hotel_id → deduplicated at DB level.
  - Hotels without coordinates: skip geo-blocking, use name+country fuzzy only.
  - No match found → create new master entity.
"""

import re
import unicodedata
from math import asin, cos, radians, sin, sqrt
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session
from thefuzz import fuzz

from models import MasterCity, MasterHotel, SupplierCity, SupplierHotel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CITY_SIMILARITY_THRESHOLD = 0.85    # pg_trgm similarity (0–1)
HOTEL_GEO_RADIUS_M = 300            # metres for geographic blocking
HOTEL_SCORE_THRESHOLD = 72          # composite score 0–100
HOTEL_NAME_WEIGHT = 0.70
HOTEL_STREET_WEIGHT = 0.30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Lowercase, strip accents, remove all non-alphanumeric characters."""
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_str.lower())


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Return distance in metres between two WGS-84 coordinates."""
    R = 6_371_000  # Earth radius in metres
    φ1, φ2 = radians(lat1), radians(lat2)
    Δφ = radians(lat2 - lat1)
    Δλ = radians(lon2 - lon1)
    a = sin(Δφ / 2) ** 2 + cos(φ1) * cos(φ2) * sin(Δλ / 2) ** 2
    return R * 2 * asin(sqrt(a))


def composite_hotel_score(
    incoming_name: str,
    incoming_street: Optional[str],
    candidate_name: str,
    candidate_street: Optional[str],
) -> float:
    """Return a weighted composite similarity score (0–100)."""
    name_score = fuzz.token_set_ratio(
        incoming_name.lower(), candidate_name.lower()
    )
    if incoming_street and candidate_street:
        street_score = fuzz.partial_ratio(
            incoming_street.lower(), candidate_street.lower()
        )
    else:
        street_score = name_score  # fallback: trust name only

    return HOTEL_NAME_WEIGHT * name_score + HOTEL_STREET_WEIGHT * street_score


# ---------------------------------------------------------------------------
# City Matcher
# ---------------------------------------------------------------------------

def find_or_create_master_city(
    db: Session,
    city_name: str,
    state_code: Optional[str],
    country_code: str,
) -> tuple[MasterCity, bool]:
    """
    Return (MasterCity, is_new).
    Tries exact normalized match first, then pg_trgm fuzzy match.
    """
    norm = normalize_name(city_name)
    state_up = (state_code or "").upper().strip() or None

    # --- Exact normalized match (fast path) ---
    query = db.query(MasterCity).filter(
        MasterCity.normalized_name == norm,
        MasterCity.country_code == country_code,
    )
    if state_up:
        query = query.filter(MasterCity.state_code == state_up)

    exact = query.first()
    if exact:
        return exact, False

    # --- Fuzzy match via pg_trgm (handles misspellings, diacritics) ---
    # Only search within the same country to avoid false positives
    fuzzy_sql = text("""
        SELECT id, similarity(normalized_name, :norm) AS sim
        FROM master_cities
        WHERE country_code = :country
          AND similarity(normalized_name, :norm) >= :threshold
        ORDER BY sim DESC
        LIMIT 1
    """)
    row = db.execute(
        fuzzy_sql,
        {
            "norm": norm,
            "country": country_code,
            "threshold": CITY_SIMILARITY_THRESHOLD,
        },
    ).fetchone()

    if row:
        master = db.get(MasterCity, row.id)
        return master, False

    # --- No match — create new master ---
    master = MasterCity(
        name=city_name.strip(),
        normalized_name=norm,
        state_code=state_up,
        country_code=country_code,
    )
    db.add(master)
    db.flush()  # get the auto-generated ID before commit
    return master, True


# ---------------------------------------------------------------------------
# Hotel Matcher
# ---------------------------------------------------------------------------

def _geo_candidates(
    db: Session,
    latitude: float,
    longitude: float,
    country_code: str,
    radius_m: float = HOTEL_GEO_RADIUS_M,
) -> list[MasterHotel]:
    """
    Use earthdistance to retrieve candidate master hotels within `radius_m`
    metres of the given coordinates, scoped to the same country.

    Falls back gracefully if the earthdistance extension is not installed.
    """
    try:
        geo_sql = text("""
            SELECT id
            FROM master_hotels
            WHERE country_code = :country
              AND latitude  IS NOT NULL
              AND longitude IS NOT NULL
              AND earth_distance(
                    ll_to_earth(latitude, longitude),
                    ll_to_earth(:lat, :lon)
                  ) <= :radius
        """)
        rows = db.execute(
            geo_sql,
            {
                "country": country_code,
                "lat": latitude,
                "lon": longitude,
                "radius": radius_m,
            },
        ).fetchall()
        ids = [r.id for r in rows]
        if not ids:
            return []
        return db.query(MasterHotel).filter(MasterHotel.id.in_(ids)).all()
    except Exception:
        # Extension not available — fall back to bounding-box approximation
        deg = radius_m / 111_000  # rough metres-to-degrees
        return (
            db.query(MasterHotel)
            .filter(
                MasterHotel.country_code == country_code,
                MasterHotel.latitude.between(latitude - deg, latitude + deg),
                MasterHotel.longitude.between(longitude - deg, longitude + deg),
            )
            .all()
        )


def _name_only_candidates(
    db: Session,
    norm_name: str,
    country_code: str,
) -> list[MasterHotel]:
    """Fuzzy name-only candidate search when no coordinates are available."""
    try:
        sql = text("""
            SELECT id
            FROM master_hotels
            WHERE country_code = :country
              AND similarity(normalized_name, :norm) >= :threshold
            ORDER BY similarity(normalized_name, :norm) DESC
            LIMIT 10
        """)
        rows = db.execute(
            sql,
            {
                "country": country_code,
                "norm": norm_name,
                "threshold": CITY_SIMILARITY_THRESHOLD,
            },
        ).fetchall()
        ids = [r.id for r in rows]
        if not ids:
            return []
        return db.query(MasterHotel).filter(MasterHotel.id.in_(ids)).all()
    except Exception:
        return []


def find_or_create_master_hotel(
    db: Session,
    name: str,
    country_code: str,
    latitude: Optional[float],
    longitude: Optional[float],
    street: Optional[str],
    city_code: Optional[str],
    stars: Optional[int],
    hotel_type: Optional[str],
    master_city_id: Optional[int],
) -> tuple[MasterHotel, bool]:
    """
    Two-Pass matching:
      Pass 1 — Geographic blocking  (if coordinates available)
      Pass 2 — Composite score filter

    Returns (MasterHotel, is_new).
    """
    norm = normalize_name(name)

    # ---------- Pass 1: candidate retrieval ----------
    if latitude is not None and longitude is not None:
        candidates = _geo_candidates(db, latitude, longitude, country_code)
    else:
        candidates = _name_only_candidates(db, norm, country_code)

    # ---------- Pass 2: scoring ----------
    best_master: Optional[MasterHotel] = None
    best_score: float = 0.0

    for candidate in candidates:
        score = composite_hotel_score(
            name, street, candidate.name, candidate.street
        )
        if score > best_score:
            best_score = score
            best_master = candidate

    if best_master and best_score >= HOTEL_SCORE_THRESHOLD:
        return best_master, False

    # ---------- No match — create new master ----------
    master = MasterHotel(
        name=name.strip(),
        normalized_name=norm,
        latitude=latitude,
        longitude=longitude,
        street=street,
        country_code=country_code,
        city_code=city_code,
        stars=stars,
        hotel_type=hotel_type,
        master_city_id=master_city_id,
    )
    db.add(master)
    db.flush()
    return master, True


# ---------------------------------------------------------------------------
# Supplier record upsert helpers
# ---------------------------------------------------------------------------

def upsert_supplier_city(
    db: Session,
    master_city: MasterCity,
    supplier_name: str,
    supplier_id: Optional[int],
    supplier_city_id: Optional[str],
    city_name: str,
    city_code: Optional[str],
    state_code: Optional[str],
    country_code: str,
    meta: Optional[dict],
) -> SupplierCity:
    """Create or return existing SupplierCity record."""
    if supplier_city_id:
        existing = (
            db.query(SupplierCity)
            .filter(
                SupplierCity.supplier_name == supplier_name,
                SupplierCity.supplier_city_id == supplier_city_id,
            )
            .first()
        )
        if existing:
            return existing

    record = SupplierCity(
        supplier_name=supplier_name,
        supplier_id=supplier_id,
        supplier_city_id=supplier_city_id,
        city_name=city_name,
        city_code=city_code,
        state_code=state_code,
        country_code=country_code,
        meta=meta,
        master_city_id=master_city.id,
    )
    db.add(record)
    return record


def upsert_supplier_hotel(
    db: Session,
    master_hotel: MasterHotel,
    supplier_name: str,
    supplier_id: Optional[int],
    supplier_hotel_id: Optional[str],
    name: str,
    city_code: Optional[str],
    state_code: Optional[str],
    zone_code: Optional[str],
    country_code: str,
    stars: Optional[int],
    hotel_type: Optional[str],
    address: Optional[dict],
    latitude: Optional[float],
    longitude: Optional[float],
) -> SupplierHotel:
    """Create or return existing SupplierHotel record."""
    if supplier_hotel_id:
        existing = (
            db.query(SupplierHotel)
            .filter(
                SupplierHotel.supplier_name == supplier_name,
                SupplierHotel.supplier_hotel_id == supplier_hotel_id,
            )
            .first()
        )
        if existing:
            return existing

    record = SupplierHotel(
        supplier_name=supplier_name,
        supplier_id=supplier_id,
        supplier_hotel_id=supplier_hotel_id,
        name=name,
        city_code=city_code,
        state_code=state_code,
        zone_code=zone_code,
        country_code=country_code,
        stars=stars,
        hotel_type=hotel_type,
        address=address,
        latitude=latitude,
        longitude=longitude,
        master_hotel_id=master_hotel.id,
    )
    db.add(record)
    return record
