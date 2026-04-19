"""
clean_csvs.py
-------------
Validates and cleans the raw supplier CSV files before ingestion.

Cleaning actions:
- Parses and validates the nested JSON in 'address' (Hotels) and 'meta' (Cities).
- Extracts and validates lat/long coordinates (must be numeric, within range).
- Normalizes string-encoded nulls ("N/A", "null", "none", "") to Python None.
- Strips leading/trailing whitespace from all string fields.
- Drops rows that are critically invalid (missing name or country_code).
- Outputs cleaned CSVs alongside the originals with a '_clean' suffix.
"""

import json
import os
import re

import pandas as pd

RAW_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(RAW_DIR)

CITIES_INPUT = os.path.join(PROJECT_ROOT, "Sample Hotel Data - Cities.csv")
HOTELS_INPUT = os.path.join(PROJECT_ROOT, "Sample Hotel Data - Hotels.csv")

CITIES_OUTPUT = os.path.join(PROJECT_ROOT, "cities_clean.csv")
HOTELS_OUTPUT = os.path.join(PROJECT_ROOT, "hotels_clean.csv")

# Strings that represent a missing value in the raw data
NULL_STRINGS = {"n/a", "null", "none", "na", "", "nan"}


def normalize_null(value):
    """Convert common null-string representations to Python None."""
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in NULL_STRINGS:
        return None
    return s


def safe_parse_json(raw):
    """Try to parse a JSON string; return {} on failure."""
    if raw is None:
        return {}
    try:
        # The CSV stores JSON with doubled quotes for escaping — handle both forms.
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        # Try fixing doubled-quote escaping pattern from CSV export
        try:
            fixed = re.sub(r'""', '"', str(raw)).strip('"')
            return json.loads(fixed)
        except Exception:
            return {}


def validate_coordinate(value, min_val, max_val):
    """Return a float coordinate if valid, else None."""
    try:
        f = float(value)
        if min_val <= f <= max_val:
            return f
        return None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Cities
# ---------------------------------------------------------------------------

def clean_cities(input_path: str, output_path: str) -> pd.DataFrame:
    print(f"[Cities] Reading: {input_path}")
    df = pd.read_csv(input_path, dtype=str)

    original_count = len(df)
    print(f"[Cities] Loaded {original_count} rows.")

    # Strip whitespace from all string columns
    df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

    # Normalize null-like strings
    for col in df.columns:
        df[col] = df[col].apply(normalize_null)

    # Drop rows missing mandatory fields
    mandatory = ["city_name", "country_code"]
    before = len(df)
    df = df.dropna(subset=mandatory)
    dropped = before - len(df)
    if dropped:
        print(f"[Cities] Dropped {dropped} rows missing mandatory fields.")

    # Parse 'meta' JSON if it exists
    if "meta" in df.columns:
        df["meta_parsed"] = df["meta"].apply(safe_parse_json)
    else:
        df["meta_parsed"] = [{}] * len(df)

    # Normalize key text fields
    df["city_name"] = df["city_name"].str.strip()
    df["country_code"] = df["country_code"].str.upper().str.strip()
    if "state_code" in df.columns:
        df["state_code"] = df["state_code"].apply(
            lambda x: str(x).upper().strip() if x else None
        )

    print(f"[Cities] Clean rows: {len(df)} / {original_count}")
    df.to_csv(output_path, index=False)
    print(f"[Cities] Saved clean file -> {output_path}")
    return df


# ---------------------------------------------------------------------------
# Hotels
# ---------------------------------------------------------------------------

def clean_hotels(input_path: str, output_path: str) -> pd.DataFrame:
    print(f"\n[Hotels] Reading: {input_path}")
    df = pd.read_csv(input_path, dtype=str)

    original_count = len(df)
    print(f"[Hotels] Loaded {original_count} rows.")

    # Strip whitespace from all string columns
    df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

    # Normalize null-like strings
    for col in df.columns:
        df[col] = df[col].apply(normalize_null)

    # Drop rows missing mandatory fields
    mandatory = ["name", "country_code"]
    before = len(df)
    df = df.dropna(subset=mandatory)
    dropped = before - len(df)
    if dropped:
        print(f"[Hotels] Dropped {dropped} rows missing mandatory fields.")

    # Parse the nested 'address' JSON and extract lat/long
    if "address" in df.columns:
        df["address_parsed"] = df["address"].apply(safe_parse_json)
    else:
        df["address_parsed"] = [{}] * len(df)

    df["latitude"] = df["address_parsed"].apply(
        lambda d: validate_coordinate(d.get("latitude"), -90, 90)
    )
    df["longitude"] = df["address_parsed"].apply(
        lambda d: validate_coordinate(d.get("longitude"), -180, 180)
    )
    df["street"] = df["address_parsed"].apply(
        lambda d: normalize_null(d.get("street"))
    )
    df["postal_code"] = df["address_parsed"].apply(
        lambda d: normalize_null(d.get("postal_code"))
    )
    df["phone_number"] = df["address_parsed"].apply(
        lambda d: normalize_null(d.get("phone_number"))
    )

    # Warn about missing coordinates (keep the rows but flag them)
    missing_coords = df["latitude"].isna() | df["longitude"].isna()
    if missing_coords.sum():
        print(
            f"[Hotels] WARNING: {missing_coords.sum()} rows have invalid/missing coordinates. "
            "They will be ingested but will NOT qualify for geo-blocking matching."
        )

    # Normalize text fields
    df["name"] = df["name"].str.strip()
    df["country_code"] = df["country_code"].str.upper().str.strip()
    if "city_code" in df.columns:
        df["city_code"] = df["city_code"].apply(
            lambda x: str(x).upper().strip() if x else None
        )
    if "stars" in df.columns:
        df["stars"] = pd.to_numeric(df["stars"], errors="coerce")

    print(f"[Hotels] Clean rows: {len(df)} / {original_count}")
    df.to_csv(output_path, index=False)
    print(f"[Hotels] Saved clean file -> {output_path}")
    return df


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_cleaning():
    print("=" * 60)
    print("Starting CSV Validation & Cleaning")
    print("=" * 60)

    cities_df = clean_cities(CITIES_INPUT, CITIES_OUTPUT)
    hotels_df = clean_hotels(HOTELS_INPUT, HOTELS_OUTPUT)

    print("\n" + "=" * 60)
    print("Cleaning complete.")
    print(f"  Cities : {len(cities_df)} clean rows -> {CITIES_OUTPUT}")
    print(f"  Hotels : {len(hotels_df)} clean rows -> {HOTELS_OUTPUT}")
    print("=" * 60)

    return cities_df, hotels_df


if __name__ == "__main__":
    run_cleaning()
