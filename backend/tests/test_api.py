"""
test_api.py
-----------
Tests all FastAPI endpoints to ensure correct routing, payload parsing,
and standard response formatting.

Uses `TestClient` to dispatch requests and `unittest.mock` to intercept
matcher capabilities and Database session calls, heavily isolating the API boundary.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname((os.path.abspath(__file__)))))

from main import app, get_db
from models import MasterCity, MasterHotel

client = TestClient(app)

# ---------------------------------------------------------------------------
# Setup Mock DB Environment
# ---------------------------------------------------------------------------

def override_get_db():
    try:
        mock_db = MagicMock()
        
        # Mock basic `.count()` for stats and list endpoints
        mock_db.query.return_value.count.return_value = 10
        mock_db.query.return_value.filter.return_value.count.return_value = 10
        mock_db.query.return_value.outerjoin.return_value.count.return_value = 10
        mock_db.query.return_value.outerjoin.return_value.filter.return_value.count.return_value = 10
        mock_db.query.return_value.outerjoin.return_value.offset.return_value.limit.return_value.all.return_value = []
        mock_db.query.return_value.outerjoin.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        # Make the generic offset return empty list by default
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        
        # Return DB
        yield mock_db
    finally:
        pass

app.dependency_overrides[get_db] = override_get_db


# ---------------------------------------------------------------------------
# Test GET Endpoints
# ---------------------------------------------------------------------------

def test_health_check_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_stats_endpoint():
    response = client.get("/stats")
    assert response.status_code == 200
    data = response.json()
    assert "master_cities" in data
    assert "master_hotels" in data
    assert "supplier_cities" in data
    assert "supplier_hotels" in data


def test_list_cities_endpoint():
    # Setup specific generator for Cities
    def override_get_db_cities():
        mock_db = MagicMock()
        mock_db.query.return_value.count.return_value = 10
        mock_db.query.return_value.outerjoin.return_value.count.return_value = 10
        mock_db.query.return_value.outerjoin.return_value.filter.return_value.count.return_value = 10
        mock_city = MasterCity(id=1, name="Mock City", state_code="MC", country_code="US")
        mock_city.supplier_cities = []
        mock_db.query.return_value.outerjoin.return_value.offset.return_value.limit.return_value.all.return_value = [(mock_city, 3)]
        mock_db.query.return_value.outerjoin.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = [(mock_city, 3)]
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db_cities
    
    response = client.get("/cities/")
    assert response.status_code == 200
    data = response.json()
    
    assert data["total"] == 10
    assert data["results"][0]["name"] == "Mock City"
    assert data["results"][0]["supplier_count"] == 3


@patch('main.Session', new_callable=MagicMock)
def test_list_hotels_endpoint(mock_session):
    # Setup specific generator for Hotels
    def override_get_db_hotels():
        mock_db = MagicMock()
        mock_db.query.return_value.count.return_value = 10
        mock_db.query.return_value.outerjoin.return_value.count.return_value = 10
        mock_hotel = MasterHotel(id=1, name="Mock Hotel", latitude=10.0, longitude=10.0, country_code="US", stars=5)
        mock_hotel.supplier_hotels = []
        mock_db.query.return_value.outerjoin.return_value.offset.return_value.limit.return_value.all.return_value = [(mock_hotel, 2)]
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db_hotels
    
    response = client.get("/hotels/")
    assert response.status_code == 200
    data = response.json()
    
    assert data["total"] == 10

    response = client.get("/hotels/?limit=25&country_code=US")
    assert response.status_code == 200
    assert response.json()["limit"] == 25


def test_city_hotels_endpoint():
    """Mock `db.get` to return a MasterCity object holding MasterHotels."""
    mock_db = MagicMock()
    mock_city = MasterCity(id=88, name="Las Vegas", country_code="US")
    
    mock_hotel = MasterHotel(id=99, name="Bellagio", stars=5)
    mock_hotel.supplier_hotels = []
    
    mock_city.master_hotels = [mock_hotel]
    mock_db.get.return_value = mock_city
    
    # Temporarily force this explicit mock 
    app.dependency_overrides[get_db] = lambda: mock_db
    
    response = client.get("/cities/88/hotels")
    assert response.status_code == 200
    data = response.json()
    
    assert data["master_city"]["name"] == "Las Vegas"
    assert data["hotels"][0]["name"] == "Bellagio"
    
    # Restore main mock
    app.dependency_overrides[get_db] = override_get_db


def test_city_hotels_not_found():
    mock_db = MagicMock()
    mock_db.get.return_value = None
    app.dependency_overrides[get_db] = lambda: mock_db
    
    response = client.get("/cities/999/hotels")
    assert response.status_code == 404
    
    app.dependency_overrides[get_db] = override_get_db


# ---------------------------------------------------------------------------
# Test POST Boundaries (Using matcher patching)
# ---------------------------------------------------------------------------

@patch('main.find_or_create_master_city')
@patch('main.upsert_supplier_city')
def test_post_city_endpoint(mock_upsert, mock_find):
    """Test standard valid payload flow natively injecting into router."""
    # Define our matcher response payload: (MasterCity, is_new boolean)
    mock_master = MasterCity(id=50, name="Tokyo", normalized_name="tokyo", state_code=None, country_code="JP")
    mock_find.return_value = (mock_master, True, 0.0)  # 3-tuple: entity, is_new, confidence
    
    payload = {
        "city_name": "Tokyo",
        "country_code": "JP",
        "supplier_name": "DummySupplier"
    }
    
    response = client.post("/cities/", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    assert data["id"] == 50
    assert data["name"] == "Tokyo"
    assert data["is_new"] is True
    assert "confidence_score" in data
    
    # Ensure our API endpoint actually triggered the deduplicating matcher engines internally
    mock_find.assert_called_once()
    mock_upsert.assert_called_once()


@patch('main.find_or_create_master_hotel')
@patch('main.upsert_supplier_hotel')
def test_post_hotel_endpoint(mock_upsert, mock_find):
    """Test standardized Hotel insertion."""
    mock_master = MasterHotel(id=101, name="Pyramid", normalized_name="pyramid", latitude=30.0, longitude=30.0, country_code="EG", stars=4)
    mock_find.return_value = (mock_master, False, 87.5)  # 3-tuple: entity, is_new, confidence
    
    payload = {
        "name": "Pyramid",
        "country_code": "eg",   # should uppercase natively by schema
        "supplier_name": "SupH",
        "address": {"latitude": 30.0, "longitude": 30.0}
    }
    
    response = client.post("/hotels/", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    assert data["id"] == 101
    assert data["country_code"] == "EG"
    assert data["is_new"] is False
    assert data["latitude"] == 30.0
    assert "confidence_score" in data
    
    mock_find.assert_called_once()
    mock_upsert.assert_called_once()
