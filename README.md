# 🗺️ Travel Mapping Engine

An AI-assisted, API-driven mapping engine that ingests city and hotel inventory from multiple travel suppliers, deduplicates them, and unifies them into a single **Master Catalog**.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   docker-compose                    │
│                                                     │
│   ┌──────────┐    ┌──────────────┐    ┌──────────┐ │
│   │Streamlit │───▶│  FastAPI API │───▶│ Postgres │ │
│   │:8501     │    │  :8000       │    │ :5432    │ │
│   └──────────┘    └──────┬───────┘    └──────────┘ │
│                          │                         │
│                    ┌─────▼──────┐                  │
│                    │  matcher   │                  │
│                    │  + models  │                  │
│                    └────────────┘                  │
└─────────────────────────────────────────────────────┘
```

**Local dev** → API connects to remote **Aiven PostgreSQL** via `DATABASE_URL` in `.env`.  
**Docker deploy** → fully self-contained; `docker-compose up` spins up everything.

---

## Mapping Algorithm

### Cities

1. **Normalize** the incoming city name (strip accents, lowercase, remove punctuation).
2. **Exact match** — query `master_cities` by `normalized_name + country_code`.
3. **Fuzzy fallback** — use PostgreSQL `pg_trgm` similarity search (threshold >= 0.85) within the same country.
4. If no match → create a new master city.

### Hotels (Two-Pass)

**Pass 1 — Geographic Blocking**
Use `earthdistance` to find all master hotels within **300 m** of the incoming coordinates.
Falls back to a bounding-box approximation if coordinates are missing.

**Pass 2 — Composite Similarity Scoring**
For each geographic candidate, compute:

```
score = 0.70 x name_similarity + 0.30 x street_similarity
```

where similarity is measured using `thefuzz.token_set_ratio` (name) and `partial_ratio` (street).
Accept the best match if `score >= 72`.

### Edge Cases Handled

| Case | Handling |
|---|---|
| Same supplier, duplicate hotel ID | UNIQUE constraint on (supplier_name, supplier_hotel_id) |
| Cross-supplier duplicates | Geo-block + composite fuzzy score |
| Missing coordinates | Name-only fuzzy via pg_trgm, no geo-block |
| "Alexandria, Egypt" vs "Alexandria, VA" | country_code scoping in every query |
| Diacritics / transliterations | Unicode normalization (unicodedata.NFKD) |
| Null strings ("N/A", "null") | Cleaning step in clean_csvs.py |

---

## Quick Start

### Local Development

```bash
# 1. Install backend deps
cd backend
pip install -r requirements.txt

# 2. Set up environment (copy and edit .env)
cp .env.example .env   # leave DATABASE_URL blank to use Aiven

# 3. Clean the CSV data
python backend/clean_csvs.py

# 4. Start the API
uvicorn backend.main:app --reload

# 5. Ingest sample data
python scripts/ingest.py

# 6. Start the frontend (new terminal)
pip install -r frontend/requirements.txt
streamlit run frontend/app.py
```

### Docker Deployment

```bash
docker-compose up --build
```

| Service | URL |
|---|---|
| API + Swagger UI | http://localhost:8000/docs |
| Streamlit Dashboard | http://localhost:8501 |
| PostgreSQL | localhost:5432 |

---

## Project Structure

```
tripkliktask/
├── backend/
│   ├── clean_csvs.py      # CSV validation & cleaning
│   ├── database.py        # SQLAlchemy engine + session
│   ├── models.py          # ORM models (Master + Supplier tables)
│   ├── schemas.py         # Pydantic request/response models
│   ├── matcher.py         # Core deduplication logic
│   ├── main.py            # FastAPI app + endpoints
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── app.py             # Streamlit dashboard
│   ├── requirements.txt
│   └── Dockerfile
├── scripts/
│   └── ingest.py          # Bulk CSV ingestion script
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Scalability Notes

- **pg_trgm GIN index** on `normalized_name` makes fuzzy city search O(log N) at any scale.
- **earthdistance float index** on `(latitude, longitude)` limits geo candidates to a tiny spatial slice before fuzzy scoring.
- **Connection pooling** (`pool_size=10, max_overflow=20`) prevents DB saturation under concurrent load.
- **Pydantic validation** rejects malformed records at the API boundary — never reaches the DB.
- All matching thresholds are **configurable constants** at the top of `matcher.py`.
