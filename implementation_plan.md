# AI-Assisted Global Mapping Engine Implementation Plan

This plan outlines the architecture for an API-driven mapping solution that ingests travel inventory from global suppliers, evaluates them against existing records, and accurately groups matching entities (Cities and Hotels).

## Plan Summary

1. **Data Validation & CSV Cleaning**: Implement a preprocessing step to validate incoming CSV data (e.g. nested JSON format, missing values) and automatically clean the CSV files before ingestion.
2. **Database Setup**: Configure PostgreSQL with SQLAlchemy ORM. When running the code directly locally for testing, we will use the Aiven PostgreSQL Database (`postgres://avnadmin:AVNS_d6wr1pxxL-JGTF-VDAk@pg-tripkliktask-zarka-5f8e.d.aivencloud.com:27736/defaultdb?sslmode=require`). When deployed as a Docker container, the entire app (including the DB) will be strictly self-contained within Docker.
3. **Matching Engine**: Implement geographic blocking (for hotels) and text-similarity fuzzy matching (for names/addresses) to intelligently deduplicate and group records.
4. **Backend API**: Build a containerized FastAPI application with ingestion endpoints (`POST /cities/` and `POST /hotels/`) for real-time inventory processing.
5. **Data Visualization**: Develop a lightweight Streamlit dashboard to visually explore the Master Catalog and mapping results.
6. **Orchestration & Verification**: Use Docker Compose to spin up the entire ecosystem and create test scripts to stream the clean CSV data into the system.

## User Review Required

> [!IMPORTANT]
> **Matching Logic & Accuracy vs Speed**: To ensure maximum accuracy while keeping speed scalable:
> - **For Cities**: We will normalize strings (removing spaces, punctuation, lowercase) and match primarily against `name`, `state_code`, and `country_code` using text similarity (e.g., Jaro-Winkler or Levenshtein distance).
> - **For Hotels**: We will use a Two-Pass approach. 
>   1. **Geographic Blocking**: Filter existing DB records within a tight radius (e.g., 5-10km) of the incoming hotel's coordinates.
>   2. **Similarity Scoring**: On the filtered candidates, we will evaluate name similarity and address similarity.
> Do you approve using exact/fuzzy algorithmic approaches initially over relying entirely on an LLM, given the constraint to support "millions of records globally"?

> [!CAUTION]
> **Database Choice**: I recommend using **PostgreSQL** because it supports geographic extensions (PostGIS) or efficient radius calculations via Earthdistance, as well as Trigram matching (`pg_trgm`) for fuzzy text searches. This fits the "extreme scalability" requirement far better than SQLite. Do you agree with this DB selection?

## Proposed Changes

### 1. Data Validation & Preprocessing

#### [NEW] [backend/clean_csvs.py](file:///c:/Users/MO/Desktop/VS/tripkliktask/backend/clean_csvs.py)
- A standalone utility script to validate and clean the raw CSV files.
- **Validation**: Checks for missing mandatory fields (`name`, `country_code`, etc), ensures coordinates (`latitude`, `longitude`) inside the nested JSON are valid numbers, and validates correct JSON structures.
- **Cleaning Actions**: Standardizes text (removing leading/trailing spaces), normalizes null string types like `"N/A"` or `"null"` back to true NULL values, and exports cleaned versions of the CSV files for the ingestion endpoints to consume flawlessly.

### 2. Database Setup & Migrations

#### [NEW] [docker-compose.yml](file:///c:/Users/MO/Desktop/VS/tripkliktask/docker-compose.yml)
- Set up three services: `db` (Postgres), `api` (FastAPI), and `frontend` (Streamlit).
- The `docker-compose.yml` is the primary deployment method, and it runs the entire app fully containerized. To support direct local testing (running outside of Docker), the code will allow overriding the database connection to use the remote Aiven service via a local `.env` configuration.

#### [NEW] [backend/models.py](file:///c:/Users/MO/Desktop/VS/tripkliktask/backend/models.py)
- Define SQLAlchemy ORM entities:
  - `MasterCity`: id, name, normalized_name, state_code, country_code.
  - `MasterHotel`: id, name, normalized_name, city_id, latitude, longitude, address, postal_code.
  - `SupplierCity`: tracking supplier variations. Will store the raw original data (including the `meta` JSON column).
  - `SupplierHotel`: tracking supplier variations. Will store the raw original data (including extractable `address` JSON mapping to lat/long).
- **Why ORM?** We will use SQLAlchemy instead of raw SQL schemas. This provides better Pythonic integration with FastAPI, automatic injection protection, and easier DB connection management. We will leverage Postgres JSONB fields for the `meta` and `address` columns to allow flexible querying and parsing of nested fields (like latitude/longitude).

### 3. Backend API (FastAPI)

#### [NEW] [backend/requirements.txt](file:///c:/Users/MO/Desktop/VS/tripkliktask/backend/requirements.txt)
- `fastapi`, `uvicorn`, `sqlalchemy`, `psycopg2-binary`, `thefuzz`, `pandas` (for CSV cleaning), and `pydantic` (for strong data validation schemas).

#### [NEW] [backend/schemas.py](file:///c:/Users/MO/Desktop/VS/tripkliktask/backend/schemas.py)
- Define Pydantic models for incoming API payloads to enforce real-time data validation and integrity, acting as a second layer of defense.

#### [NEW] [backend/main.py](file:///c:/Users/MO/Desktop/VS/tripkliktask/backend/main.py)
- Two primary ingestion endpoints per `task.txt`: 
  - `POST /cities/`: Accepts `city_name`, `state_code`, `country_code`, `supplier_name` (along with raw nested meta payload).
  - `POST /hotels/`: Accepts detailed hotel data based on the provided CSV structure, extracting nested `address` JSON to identify `latitude`, `longitude`, `street`, etc.

#### [NEW] [backend/matcher.py](file:///c:/Users/MO/Desktop/VS/tripkliktask/backend/matcher.py)
- Logic for handling deduplication:
  - If identical master is found -> Return master ID.
  - If a supplier duplicate is detected -> Link to the same master ID.
  - Otherwise -> Create and return new master entity.

#### [NEW] [backend/Dockerfile](file:///c:/Users/MO/Desktop/VS/tripkliktask/backend/Dockerfile)

### 3. Frontend Dashboard (Streamlit)

#### [NEW] [frontend/app.py](file:///c:/Users/MO/Desktop/VS/tripkliktask/frontend/app.py)
- Connects to the Database or API to visualize the "Master Catalog".
- Provides a simple interface to search entities, view their group composition (how many supplier records belong to a master entity).

#### [NEW] [frontend/Dockerfile](file:///c:/Users/MO/Desktop/VS/tripkliktask/frontend/Dockerfile)

## Open Questions

- We need a script to actually ingest and test the provided Google Sheets CSV data through our endpoints. Should we build this script as a part of the backend initialization, or as a standalone Python file (e.g. `ingest.py`)?
- Does the API need any specific authentication, or should it be open for this assessment context?

## Verification Plan

### Automated Tests
- Build an ingestion script (`test_ingest.py`) that reads the sample CSV data.
- Stream records line by line to our `POST` endpoints.
- Ensure that the total number of master records generated is significantly smaller than the total rows ingested (proving deductive grouping).

### Manual Verification
- Spin up `docker-compose up --build`.
- Access the Streamlit dashboard on local browser to visually inspect random mapped clusters.
- Confirm hotel mapping precision (e.g., verifying that mismatched locations or wildly different names were correctly kept distinct).

## Final Deliverables
- Clean code in an orchestratable Docker package.
- Business & Architecture README documenting the algorithm and edge case behaviors.
- **The Prompt Log**: This persistent chat conversation will be exported or linked per `task.txt` requirements to demonstrate the AI-assisted engineering workflow.
