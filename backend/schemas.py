"""
schemas.py
----------
Pydantic v2 request / response schemas for the mapping engine API.

Acts as the second layer of validation after CSV cleaning.
All incoming API payloads are validated against these schemas before
any business logic executes.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# City schemas
# ---------------------------------------------------------------------------

class CityIngest(BaseModel):
    """Payload for POST /cities/"""
    city_name: str = Field(..., min_length=1, max_length=255)
    state_code: Optional[str] = Field(None, max_length=50)
    country_code: str = Field(..., min_length=2, max_length=10)
    supplier_name: str = Field(..., min_length=1, max_length=100)

    # Optional extra supplier fields
    supplier_id: Optional[int] = None
    supplier_city_id: Optional[str] = None
    city_code: Optional[str] = None
    meta: Optional[Any] = None  # Raw JSONB blob

    @field_validator("country_code", mode="before")
    @classmethod
    def upper_country(cls, v):
        return v.strip().upper() if v else v

    @field_validator("state_code", mode="before")
    @classmethod
    def upper_state(cls, v):
        return v.strip().upper() if v else None

    @field_validator("city_name", mode="before")
    @classmethod
    def strip_name(cls, v):
        return v.strip() if v else v


class MasterCityOut(BaseModel):
    """Response for city ingestion."""
    id: int
    name: str
    normalized_name: str
    state_code: Optional[str]
    country_code: str
    is_new: bool = Field(description="True if a new master record was created")
    confidence_score: float = Field(
        description="Match confidence: 100.0=exact, 0.0=new, 0-100=fuzzy similarity"
    )

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Hotel schemas
# ---------------------------------------------------------------------------

class HotelAddress(BaseModel):
    """Parsed from the nested JSON 'address' field in the supplier CSV."""
    street: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    postal_code: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None

    @field_validator("latitude", mode="before")
    @classmethod
    def validate_lat(cls, v):
        if v is None:
            return None
        try:
            f = float(v)
            return f if -90 <= f <= 90 else None
        except (TypeError, ValueError):
            return None

    @field_validator("longitude", mode="before")
    @classmethod
    def validate_lon(cls, v):
        if v is None:
            return None
        try:
            f = float(v)
            return f if -180 <= f <= 180 else None
        except (TypeError, ValueError):
            return None


class HotelIngest(BaseModel):
    """Payload for POST /hotels/"""
    name: str = Field(..., min_length=1, max_length=500)
    country_code: str = Field(..., min_length=2, max_length=10)
    supplier_name: str = Field(..., min_length=1, max_length=100)

    # Address can be either a pre-parsed dict or a raw JSON string
    address: Optional[Any] = None

    # Optional supplier fields
    supplier_id: Optional[int] = None
    supplier_hotel_id: Optional[str] = None
    city_code: Optional[str] = None
    state_code: Optional[str] = None
    zone_code: Optional[str] = None
    stars: Optional[int] = Field(None, ge=0, le=7)
    hotel_type: Optional[str] = None

    @field_validator("country_code", mode="before")
    @classmethod
    def upper_country(cls, v):
        return v.strip().upper() if v else v

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v):
        return v.strip() if v else v

    @model_validator(mode="after")
    def parse_address(self):
        """Ensure address is always a dict (or None)."""
        import json, re
        raw = self.address
        if raw is None:
            return self
        if isinstance(raw, dict):
            return self
        if isinstance(raw, str):
            try:
                self.address = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    fixed = re.sub(r'""', '"', raw).strip('"')
                    self.address = json.loads(fixed)
                except Exception:
                    self.address = {}
        return self

    def get_parsed_address(self) -> HotelAddress:
        """Return a validated HotelAddress from the raw address blob."""
        raw = self.address or {}
        return HotelAddress(**raw) if isinstance(raw, dict) else HotelAddress()


class MasterHotelOut(BaseModel):
    """Response for hotel ingestion."""
    id: int
    name: str
    normalized_name: str
    latitude: Optional[float]
    longitude: Optional[float]
    country_code: str
    stars: Optional[int]
    is_new: bool = Field(description="True if a new master record was created")
    confidence_score: float = Field(
        description="Match confidence: 100.0=exact geo match, 0.0=new, else composite score"
    )

    model_config = {"from_attributes": True}
