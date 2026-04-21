"""
test_matcher.py
---------------
Unit tests for the matching logic, covering string normalization,
composite scoring, two-pass candidate ranking, and geographic calculations.
Mocks the SQLAlchemy database session to focus purely on python-side logic.
"""

from unittest.mock import MagicMock, patch

import pytest

# Ensure backend imports work
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname((os.path.abspath(__file__)))))

from matcher import (
    normalize_name,
    haversine_m,
    composite_hotel_score,
    find_or_create_master_city,
    find_or_create_master_hotel,
)
from models import MasterCity, MasterHotel


# ---------------------------------------------------------------------------
# Test Helpers: String Matching & Math
# ---------------------------------------------------------------------------

def test_normalize_name():
    """Edge Case: Ensure diacritics, punc, and spaces strictly normalize to ascii."""
    assert normalize_name("Alexandria") == "alexandria"
    assert normalize_name("São Paulo") == "saopaulo"       # diacritic drop
    assert normalize_name("O'Hare!") == "ohare"             # punc drop
    assert normalize_name("  Paris  ") == "paris"           # whitespace drop
    assert normalize_name(None) == ""


def test_haversine_formula():
    """Edge Case: Distances over curvature of the earth."""
    # NYC to London (approx 5570 km)
    nyc = (40.7128, -74.0060)
    london = (51.5074, -0.1278)
    dist_m = haversine_m(nyc[0], nyc[1], london[0], london[1])
    # allow 1% margin
    assert 5500000 < dist_m < 5650000

    # Same location
    dist_zero = haversine_m(10.0, 10.0, 10.0, 10.0)
    assert dist_zero == 0.0

    # Cross hemisphere (Tokyo to Buenos Aires approx 18,000 km)
    tokyo = (35.6762, 139.6503)
    ba = (-34.6037, -58.3816)
    dist_cross = haversine_m(tokyo[0], tokyo[1], ba[0], ba[1])
    assert 18000000 < dist_cross < 18500000


def test_composite_score():
    """Edge Case: Name variation with and without street."""
    # Perfect match
    score1 = composite_hotel_score("Hilton Paris", "123 Main St", "Hilton Paris", "123 Main St")
    assert score1 == 100.0

    # Strong match but slightly different name
    score2 = composite_hotel_score("Hilton Hotel Paris", "123 Main St", "Hilton Paris", "123 Main St")
    assert score2 > 80.0

    # Missing street drops to pure name score (less stable, but should still score)
    score3 = composite_hotel_score("Hilton", None, "Hilton", "123 Main st")
    score4 = composite_hotel_score("Hilton", None, "Hilton", None)
    assert score3 == 100.0
    assert score4 == 100.0

    # Empty inputs must not crash
    score5 = composite_hotel_score("", "", "", "")
    assert score5 == 0.0


# ---------------------------------------------------------------------------
# Test Business Logic: City Match
# ---------------------------------------------------------------------------

def test_find_or_create_city_exact_match():
    """Edge Case: An exact normalized match hits cache before DB trigrams."""
    mock_db = MagicMock()
    mock_city = MasterCity(id=1, name="Cairo", normalized_name="cairo", country_code="EG")

    # state_code is None, so it only chains one .filter() call
    mock_db.query.return_value.filter.return_value.first.return_value = mock_city

    master, is_new, confidence = find_or_create_master_city(mock_db, "Cairo", None, "EG")
    assert is_new is False
    assert master.id == 1


@patch('matcher.text')
def test_find_or_create_city_fuzzy_match(mock_text):
    """Edge Case: Missing exact match but triggers pg_trgm fuzzy match."""
    mock_db = MagicMock()
    # No exact match (only one filter call since state_code is None)
    mock_db.query.return_value.filter.return_value.first.return_value = None
    
    # Mock postgres raw execute returning a fuzzy matched ID
    mock_row = MagicMock()
    mock_row.id = 42
    mock_db.execute.return_value.fetchone.return_value = mock_row

    # Mock fetching the actual master city
    mock_city = MasterCity(id=42, name="Alexndria", normalized_name="alexndria", country_code="EG")
    mock_db.get.return_value = mock_city

    master, is_new, confidence = find_or_create_master_city(mock_db, "Alexandria", None, "EG")
    assert is_new is False
    assert master.id == 42


def test_find_or_create_city_no_match():
    """Edge Case: Absolutely no match creates new city."""
    mock_db = MagicMock()
    # No exact match (two filter calls since state_code is "XX")
    mock_db.query.return_value.filter.return_value.filter.return_value.first.return_value = None
    # No fuzzy
    mock_db.execute.return_value.fetchone.return_value = None

    master, is_new, confidence = find_or_create_master_city(mock_db, "New City", "XX", "US")
    assert is_new is True
    assert master.name == "New City"
    assert master.country_code == "US"
    # Ensure add and flush are called
    mock_db.add.assert_called_once()
    mock_db.flush.assert_called_once()
    assert confidence == 0.0


# ---------------------------------------------------------------------------
# Test Business Logic: Hotel Match (Two-Pass)
# ---------------------------------------------------------------------------

@patch('matcher._geo_candidates')
@patch('matcher._name_only_candidates')
def test_find_or_create_hotel_geo_match(mock_name_candidates, mock_geo_candidates):
    """Edge Case: Hotel with coords finds candidate via Pass 1 (geo) and matches Pass 2 (score)."""
    mock_db = MagicMock()
    
    # Candidate returned by Earthdistance
    candidate = MasterHotel(
        id=99, name="Ritz Carlton", normalized_name="ritzcarlton", 
        street="123 Broad St"
    )
    mock_geo_candidates.return_value = [candidate]

    master, is_new, confidence = find_or_create_master_hotel(
        db=mock_db,
        name="The Ritz-Carlton",
        country_code="US",
        latitude=40.0,
        longitude=-74.0,
        street="123 Broad St",
        city_code=None,
        stars=5,
        hotel_type=None,
        master_city_id=1
    )

    # Geo search fired
    mock_geo_candidates.assert_called_once()
    # Name only fallback shouldn't fire
    mock_name_candidates.assert_not_called()

    assert is_new is False
    assert master.id == 99
    assert confidence > 0


@patch('matcher._geo_candidates')
@patch('matcher._name_only_candidates')
def test_find_or_create_hotel_no_coords_with_city_code(mock_name_candidates, mock_geo_candidates):
    """Edge Case: Hotel missing coords but HAS city_code forces city_code down to fallback query."""
    mock_db = MagicMock()
    
    mock_name_candidates.return_value = []

    master, is_new, confidence = find_or_create_master_hotel(
        db=mock_db,
        name="Hilton Paris",
        country_code="FR",
        latitude=None,   # missing
        longitude=None,  # missing
        street=None,
        city_code="PAR", # present
        stars=5,
        hotel_type=None,
        master_city_id=10
    )

    # Geo candidates strictly circumvented
    mock_geo_candidates.assert_not_called()
    # Verify city_code="PAR" was properly routed down to the fallback query constraint
    mock_name_candidates.assert_called_once_with(mock_db, "hiltonparis", "FR", city_code="PAR")
    assert is_new is True
    assert confidence == 0.0


@patch('matcher._geo_candidates')
@patch('matcher._name_only_candidates')
def test_find_or_create_hotel_score_rejection(mock_name_candidates, mock_geo_candidates):
    """Edge Case: Found geoblock candidate, but composite score falls below 72/100 threshold."""
    mock_db = MagicMock()
    
    # Candidate physically close, but completely different name
    candidate = MasterHotel(
        id=88, name="Holiday Inn", normalized_name="holidayinn", 
        street="Different Street"
    )
    mock_geo_candidates.return_value = [candidate]

    master, is_new, confidence = find_or_create_master_hotel(
        db=mock_db,
        name="Marriott",
        country_code="US",
        latitude=40.0,
        longitude=-74.0,
        street="123 Broad St",
        city_code=None,
        stars=4,
        hotel_type=None,
        master_city_id=1
    )

    # Geo candidates found 1 result
    mock_geo_candidates.assert_called_once()
    
    # But names are "Holiday Inn" vs "Marriott" -> rejected. Creates new.
    assert is_new is True
    assert master.name == "Marriott"
    assert confidence == 0.0


# ---------------------------------------------------------------------------
# Test Business Logic: Upsert Deduplication Tracking
# ---------------------------------------------------------------------------

from matcher import upsert_supplier_hotel

def test_upsert_hotel_existing_bypass():
    """Possibility: If the identical supplier ID arrives twice, the exact record must simply return via cache."""
    mock_db = MagicMock()
    
    existing_record = MagicMock()
    # Mock the DB query returning an existing supplier record
    mock_db.query.return_value.filter.return_value.first.return_value = existing_record
    
    mock_master_hotel = MagicMock()
    mock_master_hotel.id = 1
    
    result = upsert_supplier_hotel(
        db=mock_db,
        master_hotel=mock_master_hotel,
        supplier_name="SupX",
        supplier_id=None,
        supplier_hotel_id="1005",
        name="Duped Hotel Payload",
        city_code="NY",
        state_code="NY",
        zone_code=None,
        country_code="US",
        stars=4,
        hotel_type=None,
        address={},
        latitude=40.0,
        longitude=-74.0
    )
    
    # Assert DB did not `add` anything new because it found existing cache
    mock_db.add.assert_not_called()
    assert result == existing_record
