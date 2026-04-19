"""
test_schemas.py
---------------
Unit tests verifying the Pydantic v2 ingestion models.
Focuses on edge case testing for corrupted JSON injection and geographic validation.
"""

import pytest

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname((os.path.abspath(__file__)))))

from schemas import HotelIngest, CityIngest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# City Parameter Validation
# ---------------------------------------------------------------------------

def test_city_ingest_cleanup():
    """Edge Case: Spaces and cases aggressively cleaned by model validators."""
    payload = {
        "city_name": "  San Francisco  ",
        "country_code": "  us  ",
        "supplier_name": "TestSupplier"
    }
    
    city = CityIngest(**payload)
    
    assert city.city_name == "San Francisco"
    assert city.country_code == "US"


def test_city_ingest_missing_optional_fields():
    """Possibility: Optional fields are heavily null."""
    payload = {
        "city_name": "Paris",
        "country_code": "FR",
        "supplier_name": "SupX"
    }
    city = CityIngest(**payload)
    assert city.state_code is None
    assert city.supplier_id is None
    assert city.meta is None


def test_city_ingest_validation_failure():
    """Possibility: Extreme length or missing mandatory fields must fail."""
    with pytest.raises(ValidationError):
        # Missing country code
        CityIngest(city_name="Rome", supplier_name="X")
    
    with pytest.raises(ValidationError):
        # Empty city name
        CityIngest(city_name="   ", country_code="IT", supplier_name="X")


# ---------------------------------------------------------------------------
# Hotel Address & Coordinate Validation
# ---------------------------------------------------------------------------

def test_hotel_address_json_parsing_success():
    """Edge Case: Validate auto-cast of JSON strings to dicts for address."""
    payload = {
        "name": "Grand Hotel",
        "country_code": "UK",
        "supplier_name": "Test",
        "address": '{"latitude": 51.5, "longitude": -0.1, "street": "Piccadilly"}'
    }
    
    hotel = HotelIngest(**payload)
    addr = hotel.get_parsed_address()
    
    assert addr.latitude == 51.5
    assert addr.longitude == -0.1
    assert addr.street == "Piccadilly"


def test_hotel_address_corrupt_json():
    """Edge Case: Validate recovery or graceful degradation on garbage JSON payload."""
    payload = {
        "name": "Busted Hotel",
        "country_code": "UK",
        "supplier_name": "Test",
        "address": '{"latitude": 51.5, garbage string ]}'
    }
    
    hotel = HotelIngest(**payload)
    addr = hotel.get_parsed_address()
    
    # Defaults to empty on absolute corruption
    assert addr.latitude is None
    assert addr.longitude is None
    assert addr.street is None


def test_hotel_coordinate_bounds_clipping():
    """Edge Case: Coordinates extremely out-of-bounds (e.g. lat=900) stripped correctly."""
    payload = {
        "name": "Invalid Location Hotel",
        "country_code": "IN",
        "supplier_name": "Test",
        "address": {"latitude": 900.5, "longitude": -200.0}
    }
    
    hotel = HotelIngest(**payload)
    addr = hotel.get_parsed_address()
    
    # Latitudes above 90 and longitudes beneath -180 should be None'd natively
    assert addr.latitude is None
    assert addr.longitude is None


def test_hotel_address_pure_string_failure():
    """Possibility: Address is passed as an non-JSON empty string or invalid type."""
    hotel1 = HotelIngest(name="A", country_code="US", supplier_name="X", address="")
    assert hotel1.get_parsed_address().latitude is None
    
    hotel2 = HotelIngest(name="A", country_code="US", supplier_name="X", address="Not a json { ] dict")
    assert hotel2.get_parsed_address().latitude is None


def test_hotel_stars_validation():
    """Possibility: Hotel stars out of valid bounds (ex: 8 stars, negative stars) fail pydantic limits."""
    # Valid
    h_valid = HotelIngest(name="A", country_code="AE", supplier_name="X", stars=7)
    assert h_valid.stars == 7

    # Invalid negative
    with pytest.raises(ValidationError):
        HotelIngest(name="A", country_code="AE", supplier_name="X", stars=-1)

    # Invalid high
    with pytest.raises(ValidationError):
        HotelIngest(name="A", country_code="AE", supplier_name="X", stars=8)
