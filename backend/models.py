"""
models.py
---------
SQLAlchemy ORM model definitions for the mapping engine.

Tables:
  master_cities   — Canonical city records (one per unique real-world city)
  master_hotels   — Canonical hotel records (one per unique real-world hotel)
  supplier_cities — Supplier-specific city records mapped to a master city
  supplier_hotels — Supplier-specific hotel records mapped to a master hotel
"""

import re
import unicodedata
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from database import Base


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, remove non-alphanumeric chars for matching."""
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_str.lower())


# ---------------------------------------------------------------------------
# Master City
# ---------------------------------------------------------------------------

class MasterCity(Base):
    __tablename__ = "master_cities"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    normalized_name = Column(String(255), nullable=False, index=True)
    state_code = Column(String(50), nullable=True)
    country_code = Column(String(10), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    # One master city can be referenced by many supplier city records
    supplier_cities = relationship("SupplierCity", back_populates="master_city")
    # One master city can contain many master hotels
    master_hotels = relationship("MasterHotel", back_populates="master_city")

    __table_args__ = (
        # Composite uniqueness: same normalized name + state + country = same city
        UniqueConstraint(
            "normalized_name", "state_code", "country_code",
            name="uq_master_city_identity"
        ),
        Index("ix_master_city_country_norm", "country_code", "normalized_name"),
    )

    def __repr__(self):
        return f"<MasterCity id={self.id} name={self.name!r} country={self.country_code}>"


# ---------------------------------------------------------------------------
# Master Hotel
# ---------------------------------------------------------------------------

class MasterHotel(Base):
    __tablename__ = "master_hotels"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(500), nullable=False)
    normalized_name = Column(String(500), nullable=False, index=True)

    # Geographic position (extracted from supplier JSON payload)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    # Address details
    street = Column(Text, nullable=True)
    postal_code = Column(String(50), nullable=True)
    city_code = Column(String(20), nullable=True)
    country_code = Column(String(10), nullable=False, index=True)
    stars = Column(SmallInteger, nullable=True)
    hotel_type = Column(String(100), nullable=True)

    # Link to the master city record
    master_city_id = Column(
        BigInteger, ForeignKey("master_cities.id"), nullable=True, index=True
    )
    master_city = relationship("MasterCity", back_populates="master_hotels")

    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    supplier_hotels = relationship("SupplierHotel", back_populates="master_hotel")

    __table_args__ = (
        Index("ix_master_hotel_country_norm", "country_code", "normalized_name"),
        # Spatial index would be added via raw SQL in init_db if PostGIS is available;
        # earthdistance works on plain float columns with a btree index.
        Index("ix_master_hotel_latlon", "latitude", "longitude"),
    )

    def __repr__(self):
        return (
            f"<MasterHotel id={self.id} name={self.name!r} "
            f"lat={self.latitude} lon={self.longitude}>"
        )


# ---------------------------------------------------------------------------
# Supplier City  (raw supplier record → master mapping)
# ---------------------------------------------------------------------------

class SupplierCity(Base):
    __tablename__ = "supplier_cities"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Supplier identity
    supplier_name = Column(String(100), nullable=False, index=True)
    supplier_id = Column(BigInteger, nullable=True)          # row id in supplier DB
    supplier_city_id = Column(String(100), nullable=True)    # supplier's own city key

    # Raw fields as received
    city_name = Column(String(255), nullable=False)
    state_code = Column(String(50), nullable=True)
    country_code = Column(String(10), nullable=False)
    city_code = Column(String(50), nullable=True)

    # Full raw meta blob (JSONB for flexible querying)
    meta = Column(JSONB, nullable=True)

    # Mapped master record
    master_city_id = Column(
        BigInteger, ForeignKey("master_cities.id"), nullable=False, index=True
    )
    master_city = relationship("MasterCity", back_populates="supplier_cities")

    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "supplier_name", "supplier_city_id",
            name="uq_supplier_city_per_supplier"
        ),
    )

    def __repr__(self):
        return (
            f"<SupplierCity supplier={self.supplier_name!r} "
            f"city_name={self.city_name!r} master_id={self.master_city_id}>"
        )


# ---------------------------------------------------------------------------
# Supplier Hotel  (raw supplier record → master mapping)
# ---------------------------------------------------------------------------

class SupplierHotel(Base):
    __tablename__ = "supplier_hotels"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Supplier identity
    supplier_name = Column(String(100), nullable=False, index=True)
    supplier_id = Column(BigInteger, nullable=True)
    supplier_hotel_id = Column(String(100), nullable=True)

    # Raw fields as received
    name = Column(String(500), nullable=False)
    city_code = Column(String(20), nullable=True)
    state_code = Column(String(50), nullable=True)
    country_code = Column(String(10), nullable=False)
    zone_code = Column(String(50), nullable=True)
    stars = Column(SmallInteger, nullable=True)
    hotel_type = Column(String(100), nullable=True)

    # Full raw address blob (JSONB)
    address = Column(JSONB, nullable=True)

    # Extracted coordinates for fast spatial queries
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    # Mapped master record
    master_hotel_id = Column(
        BigInteger, ForeignKey("master_hotels.id"), nullable=False, index=True
    )
    master_hotel = relationship("MasterHotel", back_populates="supplier_hotels")

    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "supplier_name", "supplier_hotel_id",
            name="uq_supplier_hotel_per_supplier"
        ),
        Index("ix_supplier_hotel_latlon", "latitude", "longitude"),
    )

    def __repr__(self):
        return (
            f"<SupplierHotel supplier={self.supplier_name!r} "
            f"name={self.name!r} master_id={self.master_hotel_id}>"
        )
