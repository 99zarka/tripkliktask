"""
scripts/ingest.py
-----------------
Standalone ingestion script. Reads the *clean* CSV files output by
clean_csvs.py and streams each row to the running FastAPI endpoints.

Usage (local, API running on localhost):
    python scripts/ingest.py

Usage (inside Docker, API on 'api' host — run from docker exec):
    python scripts/ingest.py --api http://api:8000

Options:
    --api         Base URL of the API (default: http://localhost:8000)
    --cities      Path to clean cities CSV
    --hotels      Path to clean hotels CSV
    --supplier    Supplier name to tag rows with (default: "SampleSupplier")
    --limit       Max rows to ingest (default: all)
    --skip-clean  Skip the CSV cleaning step (use if already clean)
"""

import argparse
import json
import os
import sys
import time

import pandas as pd
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_CITIES_CSV = os.path.join(PROJECT_ROOT, "cities_clean.csv")
DEFAULT_HOTELS_CSV = os.path.join(PROJECT_ROOT, "hotels_clean.csv")

# Fall back to raw CSV paths (e.g. inside Docker volume mount)
RAW_CITIES_CSV = "/data/cities.csv"
RAW_HOTELS_CSV = "/data/hotels.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def post(session: requests.Session, url: str, payload: dict):
    try:
        r = session.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.HTTPError as e:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return None, str(e)


def ingest_cities(session, api_base, df, supplier_name, limit=None):
    url = f"{api_base}/cities/"
    if limit:
        df = df.head(limit)

    total = len(df)
    new_count = matched_count = error_count = 0

    print(f"\n[Cities] Ingesting {total} rows as supplier='{supplier_name}' ...")

    for i, row in df.iterrows():
        payload = {
            "city_name": row.get("city_name") or row.get("name") or "",
            "state_code": row.get("state_code"),
            "country_code": str(row.get("country_code", "")).upper(),
            "supplier_name": supplier_name,
            "supplier_id": _safe_int(row.get("supplier_id")),
            "supplier_city_id": str(row.get("id", "")) or None,
            "city_code": row.get("city_code"),
            "meta": _safe_json(row.get("meta")),
        }

        result, err = post(session, url, payload)

        if err:
            error_count += 1
            _print_row(i + 1, total, status="ERR", detail=err)
        else:
            if result.get("is_new"):
                new_count += 1
                tag = "NEW    "
            else:
                matched_count += 1
                tag = "MATCHED"
            _print_row(i + 1, total, status=tag,
                       detail=f"master_id={result['id']}  name={result['name']!r}")

    print(
        f"\n[Cities] Done →  new={new_count}  matched={matched_count}  errors={error_count}"
    )
    return new_count, matched_count, error_count


def ingest_hotels(session, api_base, df, supplier_name, limit=None):
    url = f"{api_base}/hotels/"
    if limit:
        df = df.head(limit)

    total = len(df)
    new_count = matched_count = error_count = 0

    print(f"\n[Hotels] Ingesting {total} rows as supplier='{supplier_name}' ...")

    for i, row in df.iterrows():
        raw_address = row.get("address")
        parsed_address = _safe_json(raw_address) or {}

        # Prefer pre-extracted columns (written by clean_csvs.py)
        if not parsed_address and row.get("latitude"):
            parsed_address = {
                "latitude": row.get("latitude"),
                "longitude": row.get("longitude"),
                "street": row.get("street"),
                "postal_code": row.get("postal_code"),
            }

        payload = {
            "name": str(row.get("name", "")).strip(),
            "country_code": str(row.get("country_code", "")).upper(),
            "supplier_name": supplier_name,
            "supplier_id": _safe_int(row.get("supplier_id")),
            "supplier_hotel_id": str(row.get("supplier_hotel_id") or row.get("id") or ""),
            "city_code": row.get("city_code"),
            "state_code": row.get("state_code"),
            "zone_code": row.get("zone_code"),
            "stars": _safe_int(row.get("stars")),
            "hotel_type": row.get("type"),
            "address": parsed_address,
        }

        result, err = post(session, url, payload)

        if err:
            error_count += 1
            _print_row(i + 1, total, status="ERR", detail=err)
        else:
            if result.get("is_new"):
                new_count += 1
                tag = "NEW    "
            else:
                matched_count += 1
                tag = "MATCHED"
            _print_row(
                i + 1, total, status=tag,
                detail=f"master_id={result['id']}  name={result['name']!r}"
            )

    print(
        f"\n[Hotels] Done →  new={new_count}  matched={matched_count}  errors={error_count}"
    )
    return new_count, matched_count, error_count


# ---------------------------------------------------------------------------
# Internal utils
# ---------------------------------------------------------------------------

def _safe_int(v):
    try:
        return int(float(v)) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_json(v):
    if v is None:
        return None
    if isinstance(v, dict):
        return v
    try:
        return json.loads(str(v))
    except Exception:
        return None


def _print_row(idx, total, status, detail=""):
    pct = idx / total * 100
    print(f"  [{idx:>6}/{total}  {pct:5.1f}%]  {status}  {detail}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ingest CSV data into the Mapping Engine API")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--cities", default=None, help="Path to cities CSV")
    parser.add_argument("--hotels", default=None, help="Path to hotels CSV")
    parser.add_argument("--supplier", default="SampleSupplier", help="Supplier name tag")
    parser.add_argument("--limit", type=int, default=None, help="Max rows per entity")
    parser.add_argument("--skip-clean", action="store_true", help="Skip cleaning step")
    args = parser.parse_args()

    # ---- Run CSV cleaning if needed ----
    if not args.skip_clean:
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))
        try:
            from clean_csvs import run_cleaning
            run_cleaning()
        except Exception as e:
            print(f"[WARNING] CSV cleaning failed: {e}  - proceeding with raw files.")

    # ---- Resolve CSV paths ----
    cities_path = args.cities or (
        DEFAULT_CITIES_CSV if os.path.exists(DEFAULT_CITIES_CSV)
        else RAW_CITIES_CSV
    )
    hotels_path = args.hotels or (
        DEFAULT_HOTELS_CSV if os.path.exists(DEFAULT_HOTELS_CSV)
        else RAW_HOTELS_CSV
    )

    # ---- Health check ----
    print(f"\nConnecting to API at {args.api} ...")
    try:
        r = requests.get(f"{args.api}/health", timeout=10)
        r.raise_for_status()
        print("API is healthy [OK]")
    except Exception as e:
        print(f"ERROR: Cannot reach API - {e}")
        sys.exit(1)

    session = requests.Session()
    t_start = time.time()

    total_new = total_matched = total_errors = 0

    # ---- Cities ----
    if os.path.exists(cities_path):
        df_cities = pd.read_csv(cities_path, dtype=str).where(
            pd.notna(pd.read_csv(cities_path, dtype=str)), None
        )
        n, m, e = ingest_cities(session, args.api, df_cities, args.supplier, args.limit)
        total_new += n; total_matched += m; total_errors += e
    else:
        print(f"[Cities] File not found: {cities_path} - skipping.")

    # ---- Hotels ----
    if os.path.exists(hotels_path):
        df_hotels = pd.read_csv(hotels_path, dtype=str).where(
            pd.notna(pd.read_csv(hotels_path, dtype=str)), None
        )
        n, m, e = ingest_hotels(session, args.api, df_hotels, args.supplier, args.limit)
        total_new += n; total_matched += m; total_errors += e
    else:
        print(f"[Hotels] File not found: {hotels_path} - skipping.")

    elapsed = time.time() - t_start
    print("\n" + "=" * 60)
    print(f"Ingestion complete in {elapsed:.1f}s")
    print(f"  Total new master records : {total_new}")
    print(f"  Total matched            : {total_matched}")
    print(f"  Total errors             : {total_errors}")
    print("=" * 60)


if __name__ == "__main__":
    main()
