"""
app.py — Streamlit Dashboard for the Travel Mapping Engine
----------------------------------------------------------
Pages:
  📊 Overview   — Live stats (totals, supplier vs master compression ratio)
  🏙️ Cities     — Browse & search master cities, see linked supplier records
  🏨 Hotels     — Browse & search master hotels with an interactive map
  📥 Ingest CSV — Upload a raw CSV and stream it to the API in real-time
"""

import json
import os
import time

import pandas as pd
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_BASE = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Travel Mapping Engine",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_get(path: str, params: dict = None):
    try:
        r = requests.get(f"{API_BASE}{path}", params=params, timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        st.error("❌ Cannot connect to the API. Make sure the backend is running.")
        return None
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def api_post(path: str, payload: dict):
    try:
        r = requests.post(f"{API_BASE}{path}", json=payload, timeout=10)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.HTTPError as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)


def _sanitize(obj):
    """Recursively replace float NaN / Infinity with None for JSON safety."""
    import math
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

st.sidebar.image("https://i.imgur.com/your-logo.png", use_column_width=True) if False else None
st.sidebar.title("🗺️ Mapping Engine")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigate",
    ["📊 Overview", "🏙️ Cities", "🏨 Hotels", "📥 Ingest CSV"],
    label_visibility="collapsed",
)

st.sidebar.markdown("---")
st.sidebar.caption(f"API: `{API_BASE}`")

# ---------------------------------------------------------------------------
# Page: Overview
# ---------------------------------------------------------------------------

if page == "📊 Overview":
    st.title("📊 Mapping Engine Overview")

    stats = api_get("/stats")
    if stats:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Master Cities", f"{stats['master_cities']:,}")
        col2.metric("Master Hotels", f"{stats['master_hotels']:,}")
        col3.metric("Supplier Cities", f"{stats['supplier_cities']:,}")
        col4.metric("Supplier Hotels", f"{stats['supplier_hotels']:,}")

        st.markdown("---")
        st.subheader("Deduplication Compression")

        if stats["supplier_cities"] > 0:
            city_ratio = stats["supplier_cities"] / max(stats["master_cities"], 1)
            st.metric(
                "City Compression Ratio",
                f"{city_ratio:.2f}x",
                help="How many supplier records map to each master city on average",
            )

        if stats["supplier_hotels"] > 0:
            hotel_ratio = stats["supplier_hotels"] / max(stats["master_hotels"], 1)
            st.metric(
                "Hotel Compression Ratio",
                f"{hotel_ratio:.2f}x",
                help="How many supplier records map to each master hotel on average",
            )

        if stats["master_cities"] == 0 and stats["master_hotels"] == 0:
            st.info("No data ingested yet. Use the **Ingest CSV** page to load data.")

# ---------------------------------------------------------------------------
# Page: Cities
# ---------------------------------------------------------------------------

elif page == "🏙️ Cities":
    st.title("🏙️ Master Cities")

    col1, col2, col3, col4 = st.columns([2, 1, 1, 2])
    country_filter = col1.text_input("Filter by country code (e.g. EG, ES)", "").upper()
    limit = col2.selectbox("Results per page", [25, 50, 100, 200], index=1, key="city_limit")
    page_num = col3.number_input("Page", min_value=1, step=1, key="city_page")
    dupes_only = col4.checkbox("Only show deduplicated entries (Suppliers > 1)", key="city_dupes")

    skip = (page_num - 1) * limit
    params = {
        "country_code": country_filter or None,
        "duplicates_only": dupes_only,
        "limit": limit,
        "skip": skip
    }
    data = api_get("/cities/", params=params)

    if data:
        st.caption(f"Showing {len(data['results'])} of {data['total']:,} master cities")
        df = pd.DataFrame(data["results"])
        if not df.empty:
            st.dataframe(
                df.rename(columns={
                    "id": "Master ID",
                    "name": "City Name",
                    "state_code": "State",
                    "country_code": "Country",
                    "supplier_count": "Suppliers",
                }),
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("---")
            st.subheader("🔍 Inspect Deduplication Mappings")
            city_id = st.number_input("Enter Master City ID", min_value=1, step=1, key="city_inspect")
            
            if st.button("Load Deduplicated Suppliers"):
                detail = api_get(f"/cities/{int(city_id)}/suppliers")
                if detail:
                    st.success(f"Matched {len(detail['suppliers'])} supplier records to Master City '{detail['master_city']['name']}'")
                    st.json(detail['suppliers'])

# ---------------------------------------------------------------------------
# Page: Hotels
# ---------------------------------------------------------------------------

elif page == "🏨 Hotels":
    st.title("🏨 Master Hotels")

    col1, col2, col3, col4 = st.columns([2, 1, 1, 2])
    country_filter = col1.text_input("Filter by country code", "").upper()
    limit = col2.selectbox("Results per page", [25, 50, 100, 200], index=1, key="hotel_limit")
    page_num = col3.number_input("Page", min_value=1, step=1, key="hotel_page")
    dupes_only = col4.checkbox("Only show deduplicated entries (Suppliers > 1)", key="hotel_dupes")

    skip = (page_num - 1) * limit
    params = {
        "country_code": country_filter or None,
        "duplicates_only": dupes_only,
        "limit": limit,
        "skip": skip
    }
    data = api_get("/hotels/", params=params)

    if data:
        st.caption(f"Showing {len(data['results'])} of {data['total']:,} master hotels")
        df = pd.DataFrame(data["results"])

        if not df.empty:
            st.dataframe(
                df.rename(columns={
                    "id": "Master ID",
                    "name": "Hotel Name",
                    "country_code": "Country",
                    "stars": "⭐",
                    "type": "Type",
                    "supplier_count": "Suppliers",
                }),
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("---")
            st.subheader("🔍 Inspect Deduplication Mappings")
            hotel_id = st.number_input("Enter Master Hotel ID", min_value=1, step=1, key="hotel_inspect")
            if st.button("Load Deduplicated Suppliers"):
                detail = api_get(f"/hotels/{int(hotel_id)}/suppliers")
                if detail:
                    st.success(f"Matched {len(detail['suppliers'])} supplier records to Master Hotel '{detail['master_hotel']['name']}'")
                    st.json(detail['suppliers'])

# ---------------------------------------------------------------------------
# Page: Ingest CSV
# ---------------------------------------------------------------------------

elif page == "📥 Ingest CSV":
    st.title("📥 Ingest Supplier CSV")
    st.markdown(
        "Upload a cleaned supplier CSV and stream each row to the mapping API. "
        "The engine will deduplicate records in real-time."
    )

    entity_type = st.radio("Entity type", ["Cities", "Hotels"], horizontal=True)
    supplier_name = st.text_input("Supplier name", placeholder="e.g. SupplierA")
    uploaded = st.file_uploader("Upload CSV", type=["csv"])

    if uploaded and supplier_name and st.button("🚀 Start Ingestion"):
        df = pd.read_csv(uploaded, dtype=str)
        df = df.where(pd.notna(df), None)  # convert NaN → None

        total = len(df)
        new_masters = 0
        errors = 0

        progress = st.progress(0, text="Starting ingestion…")
        status_box = st.empty()
        results_placeholder = st.empty()
        log_lines = []

        endpoint = f"/{entity_type.lower()}/"

        for i, row in df.iterrows():
            row_dict = row.to_dict()

            if entity_type == "Cities":
                payload = {
                    "city_name": row_dict.get("city_name") or row_dict.get("name"),
                    "state_code": row_dict.get("state_code"),
                    "country_code": row_dict.get("country_code", ""),
                    "supplier_name": supplier_name,
                    "supplier_id": row_dict.get("supplier_id"),
                    "supplier_city_id": row_dict.get("id") or row_dict.get("supplier_city_id"),
                    "city_code": row_dict.get("city_code"),
                    "meta": None,
                }
                payload = _sanitize(payload)
            else:  # Hotels
                # Parse address JSON if present
                raw_address = row_dict.get("address")
                if raw_address and isinstance(raw_address, str):
                    try:
                        parsed_address = json.loads(raw_address)
                    except Exception:
                        parsed_address = {}
                else:
                    parsed_address = {}

                payload = {
                    "name": row_dict.get("name", ""),
                    "country_code": row_dict.get("country_code", ""),
                    "supplier_name": supplier_name,
                    "supplier_id": row_dict.get("supplier_id"),
                    "supplier_hotel_id": row_dict.get("supplier_hotel_id") or row_dict.get("id"),
                    "city_code": row_dict.get("city_code"),
                    "state_code": row_dict.get("state_code"),
                    "zone_code": row_dict.get("zone_code"),
                    "stars": row_dict.get("stars"),
                    "hotel_type": row_dict.get("type"),
                    "address": parsed_address,
                }
                payload = _sanitize(payload)

            result, err = api_post(endpoint, payload)

            if err:
                errors += 1
                log_lines.append(f"❌ Row {i + 1}: {err}")
            elif result:
                if result.get("is_new"):
                    new_masters += 1
                log_lines.append(
                    f"✅ Row {i + 1}: master_id={result.get('id')} "
                    f"{'[NEW]' if result.get('is_new') else '[MATCHED]'}"
                )

            pct = (i + 1) / total
            progress.progress(pct, text=f"Processing row {i + 1} / {total}")
            results_placeholder.text("\n".join(log_lines[-20:]))  # show last 20 lines

        progress.progress(1.0, text="Done!")
        status_box.success(
            f"Ingestion complete: {total} rows processed | "
            f"{new_masters} new master records created | "
            f"{total - new_masters - errors} matched | "
            f"{errors} errors"
        )
