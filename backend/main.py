"""
main.py
-------
FastAPI application entry point.

Endpoints:
  POST /cities/  — Ingest a supplier city record, return mapped master city.
  POST /hotels/  — Ingest a supplier hotel record, return mapped master hotel.
  GET  /cities/  — List master cities (paginated).
  GET  /hotels/  — List master hotels (paginated).
  GET  /health   — Simple liveness check.
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

import models  # noqa: F401 — ensure models are registered with Base
from database import get_db, init_db
from matcher import (
    find_or_create_master_city,
    find_or_create_master_hotel,
    upsert_supplier_city,
    upsert_supplier_hotel,
)
from schemas import (
    CityIngest,
    HotelIngest,
    MasterCityOut,
    MasterHotelOut,
)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run DB initialisation on startup."""
    init_db()
    yield


app = FastAPI(
    title="Travel Mapping Engine",
    description=(
        "Ingest supplier city and hotel records, deduplicate them, "
        "and map each to a canonical master entity."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# City ingestion
# ---------------------------------------------------------------------------

@app.post("/cities/", response_model=MasterCityOut, tags=["Cities"])
def add_city(payload: CityIngest, db: Session = Depends(get_db)):
    """
    Accepts a supplier city record. Matches it against the master catalog
    using normalized string matching (exact) + pg_trgm fuzzy fallback.

    Returns the matched or newly created MasterCity, with `is_new` flag.
    """
    try:
        master, is_new = find_or_create_master_city(
            db=db,
            city_name=payload.city_name,
            state_code=payload.state_code,
            country_code=payload.country_code,
        )

        upsert_supplier_city(
            db=db,
            master_city=master,
            supplier_name=payload.supplier_name,
            supplier_id=payload.supplier_id,
            supplier_city_id=str(payload.supplier_city_id) if payload.supplier_city_id else None,
            city_name=payload.city_name,
            city_code=payload.city_code,
            state_code=payload.state_code,
            country_code=payload.country_code,
            meta=payload.meta if isinstance(payload.meta, dict) else None,
        )

        db.commit()
        db.refresh(master)

        return MasterCityOut(
            id=master.id,
            name=master.name,
            normalized_name=master.normalized_name,
            state_code=master.state_code,
            country_code=master.country_code,
            is_new=is_new,
        )

    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Hotel ingestion
# ---------------------------------------------------------------------------

@app.post("/hotels/", response_model=MasterHotelOut, tags=["Hotels"])
def add_hotel(payload: HotelIngest, db: Session = Depends(get_db)):
    """
    Accepts a detailed supplier hotel record.

    Two-pass matching:
    1. Geographic blocking — earthdistance radius search (300 m default).
    2. Composite name + street similarity scoring (threshold 72/100).

    Returns the matched or newly created MasterHotel, with `is_new` flag.
    """
    try:
        addr = payload.get_parsed_address()

        # Attempt to resolve city if we have enough info
        master_city_id = None
        if payload.city_code and payload.country_code:
            city_match = find_or_create_master_city(
                db=db,
                city_name=payload.city_code,   # use city_code as name proxy
                state_code=payload.state_code,
                country_code=payload.country_code,
            )
            master_city_id = city_match[0].id

        master, is_new = find_or_create_master_hotel(
            db=db,
            name=payload.name,
            country_code=payload.country_code,
            latitude=addr.latitude,
            longitude=addr.longitude,
            street=addr.street,
            city_code=payload.city_code,
            stars=payload.stars,
            hotel_type=payload.hotel_type,
            master_city_id=master_city_id,
        )

        upsert_supplier_hotel(
            db=db,
            master_hotel=master,
            supplier_name=payload.supplier_name,
            supplier_id=payload.supplier_id,
            supplier_hotel_id=str(payload.supplier_hotel_id) if payload.supplier_hotel_id else None,
            name=payload.name,
            city_code=payload.city_code,
            state_code=payload.state_code,
            zone_code=payload.zone_code,
            country_code=payload.country_code,
            stars=payload.stars,
            hotel_type=payload.hotel_type,
            address=payload.address if isinstance(payload.address, dict) else None,
            latitude=addr.latitude,
            longitude=addr.longitude,
        )

        db.commit()
        db.refresh(master)

        return MasterHotelOut(
            id=master.id,
            name=master.name,
            normalized_name=master.normalized_name,
            latitude=master.latitude,
            longitude=master.longitude,
            country_code=master.country_code,
            stars=master.stars,
            is_new=is_new,
        )

    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Read endpoints (for the dashboard)
# ---------------------------------------------------------------------------

@app.get("/cities/", tags=["Cities"])
def list_cities(
    country_code: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List master cities, optionally filtered by country_code."""
    query = db.query(models.MasterCity)
    if country_code:
        query = query.filter(
            models.MasterCity.country_code == country_code.upper()
        )
    cities = query.offset(skip).limit(limit).all()
    total = query.count()
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "results": [
            {
                "id": c.id,
                "name": c.name,
                "state_code": c.state_code,
                "country_code": c.country_code,
                "supplier_count": len(c.supplier_cities),
            }
            for c in cities
        ],
    }


@app.get("/hotels/", tags=["Hotels"])
def list_hotels(
    country_code: str | None = None,
    city_id: int | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List master hotels, optionally filtered by country or master city."""
    query = db.query(models.MasterHotel)
    if country_code:
        query = query.filter(
            models.MasterHotel.country_code == country_code.upper()
        )
    if city_id:
        query = query.filter(models.MasterHotel.master_city_id == city_id)

    hotels = query.offset(skip).limit(limit).all()
    total = query.count()
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "results": [
            {
                "id": h.id,
                "name": h.name,
                "latitude": h.latitude,
                "longitude": h.longitude,
                "country_code": h.country_code,
                "stars": h.stars,
                "type": h.hotel_type,
                "supplier_count": len(h.supplier_hotels),
            }
            for h in hotels
        ],
    }


@app.get("/cities/{city_id}/hotels", tags=["Cities"])
def get_city_hotels(city_id: int, db: Session = Depends(get_db)):
    """Return all master hotels belonging to a specific master city."""
    city = db.get(models.MasterCity, city_id)
    if not city:
        raise HTTPException(status_code=404, detail="City not found")
    return {
        "master_city": {"id": city.id, "name": city.name, "country_code": city.country_code},
        "hotels": [
            {"id": h.id, "name": h.name, "stars": h.stars, "supplier_count": len(h.supplier_hotels)}
            for h in city.master_hotels
        ],
    }


@app.get("/stats", tags=["System"])
def get_stats(db: Session = Depends(get_db)):
    """Ingestion statistics overview."""
    return {
        "master_cities": db.query(models.MasterCity).count(),
        "master_hotels": db.query(models.MasterHotel).count(),
        "supplier_cities": db.query(models.SupplierCity).count(),
        "supplier_hotels": db.query(models.SupplierHotel).count(),
    }
