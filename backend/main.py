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

import logging
import sys
import os
from contextlib import asynccontextmanager

# Ensure local log directory exists dynamically
os.makedirs("logs", exist_ok=True)

# Configure dual-logging engine (Console + Persistent File)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/engine.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

import time
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import func
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
    logger.info("Initializing Database schemas and extensions...")
    init_db()
    logger.info("Database initialized successfully.")
    yield
    logger.info("Application shutting down.")


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
# Request / Response logging middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every incoming request and outgoing response with timing."""
    start = time.perf_counter()
    client = request.client.host if request.client else "unknown"
    logger.info(f"REQUEST  {request.method} {request.url.path} from {client}")

    try:
        response: Response = await call_next(request)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.error(
            f"RESPONSE {request.method} {request.url.path} - UNHANDLED ERROR "
            f"({elapsed_ms:.1f}ms): {exc}",
            exc_info=True,
        )
        raise

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        f"RESPONSE {request.method} {request.url.path} "
        f"status={response.status_code} ({elapsed_ms:.1f}ms)"
    )
    return response


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
    logger.info(
        f"POST /cities/ - supplier={payload.supplier_name!r} "
        f"city={payload.city_name!r} country={payload.country_code}"
    )
    try:
        master, is_new, confidence_score = find_or_create_master_city(
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

        result = MasterCityOut(
            id=master.id,
            name=master.name,
            normalized_name=master.normalized_name,
            state_code=master.state_code,
            country_code=master.country_code,
            is_new=is_new,
            confidence_score=confidence_score,
        )
        logger.info(
            f"POST /cities/ - master_id={master.id} name={master.name!r} "
            f"is_new={is_new} confidence={confidence_score}"
        )
        return result

    except Exception as exc:
        db.rollback()
        logger.error(f"Error during city ingestion (payload={payload.city_name}): {exc}", exc_info=True)
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
    logger.info(
        f"POST /hotels/ - supplier={payload.supplier_name!r} "
        f"name={payload.name!r} country={payload.country_code}"
    )
    try:
        addr = payload.get_parsed_address()

        # Attempt to resolve city if we have enough info
        master_city_id = None
        if payload.city_code and payload.country_code:
            city_match, _, _ = find_or_create_master_city(
                db=db,
                city_name=payload.city_code,   # use city_code as name proxy
                state_code=payload.state_code,
                country_code=payload.country_code,
            )
            master_city_id = city_match.id

        master, is_new, confidence_score = find_or_create_master_hotel(
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

        result = MasterHotelOut(
            id=master.id,
            name=master.name,
            normalized_name=master.normalized_name,
            latitude=master.latitude,
            longitude=master.longitude,
            country_code=master.country_code,
            stars=master.stars,
            is_new=is_new,
            confidence_score=confidence_score,
        )
        logger.info(
            f"POST /hotels/ - master_id={master.id} name={master.name!r} "
            f"is_new={is_new} confidence={confidence_score}"
        )
        return result

    except Exception as exc:
        db.rollback()
        logger.error(f"Error during hotel ingestion (payload={payload.name}): {exc}", exc_info=True)
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
    # Single query with COUNT subquery — avoids N+1 lazy loads
    count_sub = (
        db.query(
            models.SupplierCity.master_city_id,
            func.count(models.SupplierCity.id).label("cnt"),
        )
        .group_by(models.SupplierCity.master_city_id)
        .subquery()
    )
    query = (
        db.query(models.MasterCity, func.coalesce(count_sub.c.cnt, 0).label("supplier_count"))
        .outerjoin(count_sub, models.MasterCity.id == count_sub.c.master_city_id)
    )
    if country_code:
        query = query.filter(models.MasterCity.country_code == country_code.upper())

    total = query.count()
    rows = query.offset(skip).limit(limit).all()

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
                "supplier_count": supplier_count,
            }
            for c, supplier_count in rows
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
    # Single query with COUNT subquery — avoids N+1 lazy loads
    count_sub = (
        db.query(
            models.SupplierHotel.master_hotel_id,
            func.count(models.SupplierHotel.id).label("cnt"),
        )
        .group_by(models.SupplierHotel.master_hotel_id)
        .subquery()
    )
    query = (
        db.query(models.MasterHotel, func.coalesce(count_sub.c.cnt, 0).label("supplier_count"))
        .outerjoin(count_sub, models.MasterHotel.id == count_sub.c.master_hotel_id)
    )
    if country_code:
        query = query.filter(models.MasterHotel.country_code == country_code.upper())
    if city_id:
        query = query.filter(models.MasterHotel.master_city_id == city_id)

    total = query.count()
    rows = query.offset(skip).limit(limit).all()

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
                "supplier_count": supplier_count,
            }
            for h, supplier_count in rows
        ],
    }


@app.get("/hotels/{hotel_id}/suppliers", tags=["Hotels"])
def get_hotel_suppliers(hotel_id: int, db: Session = Depends(get_db)):
    """Return all raw supplier hotels that have been deduplicated into this master hotel."""
    hotel = db.get(models.MasterHotel, hotel_id)
    if not hotel:
        raise HTTPException(status_code=404, detail="Hotel not found")
    
    return {
        "master_hotel": {"id": hotel.id, "name": hotel.name, "country_code": hotel.country_code},
        "suppliers": [
            {
                "supplier_name": s.supplier_name,
                "supplier_hotel_id": s.supplier_hotel_id,
                "name": s.name,
                "city_code": s.city_code,
                "address": s.address,
                "latitude": s.latitude,
                "longitude": s.longitude,
            }
            for s in hotel.supplier_hotels
        ]
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


@app.get("/cities/{city_id}/suppliers", tags=["Cities"])
def get_city_suppliers(city_id: int, db: Session = Depends(get_db)):
    """Return all raw supplier cities that have been deduplicated into this master city."""
    city = db.get(models.MasterCity, city_id)
    if not city:
        raise HTTPException(status_code=404, detail="City not found")
    
    return {
        "master_city": {"id": city.id, "name": city.name, "country_code": city.country_code},
        "suppliers": [
            {
                "supplier_name": s.supplier_name,
                "supplier_city_id": s.supplier_city_id,
                "city_name": s.city_name,
                "state_code": s.state_code,
                "meta": s.meta,
            }
            for s in city.supplier_cities
        ]
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
