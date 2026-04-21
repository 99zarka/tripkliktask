# 🌍 AI-Assisted Global Mapping Engine

A production-ready, AI-assisted, API-driven travel mapping engine built with **FastAPI** and **PostgreSQL**. Its primary objective is to ingest unmapped city and hotel data from global travel suppliers, accurately deduplicate them in real-time, and unify them into a highly robust **Master Catalog**.

> ### ⚡ TL;DR & Quick Start
> - **🚀 1-Click Installation:** Simply run `setup_env.bat` on Windows to fully bootstrap dependencies, then `start_local.bat` to launch locally, or `start_docker.bat` for an isolated containerized ecosystem.
> - **Goal:** Map, deduplicate, and merge millions of supplier travel records (cities/hotels) into a clean, unified Master Catalog.
> - **Tech Stack:** FastAPI, PostgreSQL, AnyIO, Python 3.13, Docker, Streamlit (Bonus UI).
> - **Methodology:** 'Accuracy > Speed'. Prioritizes zero false positives using strict geo-spatial blocking (`earthdistance`) and fuzzy confidence scoring (`thefuzz` & `pg_trgm`).
> - **Bonus UI & Docs:** Fully interactive Streamlit dashboard and complete [Swagger API Documentation](http://localhost:8000/docs).
> - **AI-First:** Engineered entirely via LLMs (Claude Sonnet & Gemini Pro) tracking prompting workflows, debugging, and advanced DB scaling strategies.

---

This project was built adhering strictly to an **AI-First methodology**, leveraging advanced LLMs to scaffold, refactor, and harden the architecture for extreme scalability and accuracy.

---

## 🎯 Business & Architecture Approach

Aggregating travel data means dealing with different naming conventions, random misspellings, varying string structures, and massive redundancies. To protect business revenue, this engine adopts an **Accuracy > Speed** methodology, ensuring we never mistakenly map "Alexandria, Egypt" to "Alexandria, Virginia", while preserving the capability to scale to millions of rows globally.

### 🧠 Mapping Logic & Algorithms

The deduplication flow operates sequentially on ingestion (`POST /cities/` and `POST /hotels/`).

#### 1. City Deduplication Strategy
- **Normalization:** Incoming city names are stripped of accents, diacritics, spaces, and punctuation via Unicode NFKD (`São Paulo` → `saopaulo`).
- **Exact Pass:** The system queries the `master_cities` index for an exact match on `normalized_name + country_code`.
- **Fuzzy pg_trgm Pass:** If no exact match is found, the system utilizes PostgreSQL's `pg_trgm` natively to execute a strict fuzzy string search inside the specific `country_code` boundary. The acceptance threshold is strictly set at `0.85` to heavily reject false positives.
- If no match occurs, a clean master city is instantiated.

#### 2. Hotel Deduplication Strategy (Two-Pass Ranking)
- **Pass 1 — Geographic Spatial Blocking:**
  Utilizes the PostgreSQL `earthdistance` (cube) extension. If the incoming payload provides coordinates, the system instantly filters the database down to physical master hotels situated within a tight **300 meter radius**.
  *Fallback:* If the payload lacks coordinates (Null data), it strictly limits the query to the same `country_code` and `city_code` utilizing `pg_trgm` name matching.
- **Pass 2 — Composite Confidence Scoring:**
  The narrow list of geographic candidates is dumped into Python where the AI-assisted `thefuzz` library calculates a specialized weighted confidence metric:
  `Score = (0.70 * Name_Similarity) + (0.30 * Street_Similarity)`
  The engine merges the payload only if the highest candidate surpasses a strict `72/100` confidence score threshold.

### 🛡️ Edge Case Handling

| Edge Case Encountered | Engineering Solution |
|---|---|
| **Intra-Supplier Duplicates** | Suppliers often send their *own* exact duplicates. Fixed via `UNIQUE CONSTRAINT` on `(supplier_name, supplier_hotel_id)` explicitly at the DB level, allowing graceful bypass responses. |
| **Silent Float "NaN" Serializations** | Raw DataFrames passed `NaN` as floats, instantly destroying fastAPI JSON serialization. Fixed using a custom recursive `_sanitize` payload wrapper in the API bridge before network transit. |
| **N+1 SQL Timeout Queries** | Reading paginated data lazily executed `len(supplier)` relationships, querying the DB 5,000 extra times. Fixed by injecting strict `func.count` SQL aggregations mapping deeply nested counts instantly in one outerjoin. |
| **No Geographic Coordinates**| Shifted matching algorithm entirely. Circumvents earthdistance, delegates directly to fuzzy-scoring explicitly bounded within matching `country_code` and `city_code` restrictions. |

### 📈 Extreme Scalability Factors
1. **Database-Level Analytics:** Computations pushed directly into Postgres. `pg_trgm` uses heavily optimized `GIN` indexes, meaning finding misspelled cities among 5,000,000 rows executes in `O(log N)` complexity.
2. **Spatial Indexes:** PostGres mathematical `earthdistance` filters coordinates before pulling anything into the python lifecycle, bypassing python garbage collection bottlenecks.
3. **Environment Uniformity:** Dependencies consolidated dynamically into standard root requirements utilizing an automated virtual-environment batch setup.

---

## 🤖 AI-First Methodology & Prompt Logs

The entirety of this architecture, from the spatial indexing to the backend JSON-serialization patching, was programmed by operating advanced LLM Agent models (primarily Claude 3.5 Sonnet & Gemini 3.1 Pro via local IDE integration). 

> **Prompt Log Deliverable:** 
> The exact conversation histories, debugging phases, and architectural adjustments requested via `task.txt` have been natively exported and can be found attached alongside the original submission (or inside the `.gemini/antigravity/` local log caches if requested).

---

## ✨ Bonus: Interactive Dashboard UI

To exceed the deliverables specified, the system includes a fully functional interactive mapping visualizer utilizing Streamlit.
- **Real-Time Data Ingestion:** Upload the raw supplier CSVs directly through the UI. It visually streams them to the FastAPI endpoints dynamically reporting deduplication matches.
- **Manual Mapping Inspector:** Directly look up any Master ID and structurally view the raw JSON payloads of the overlapping suppliers that successfully merged into it!
- **Data Tables & Pagination:** Traverse visually over millions of records completely linked to API-level limits avoiding browser memory crashes.

---

## ⚙️ Quick Start

### 1. Local Development (Windows Batch)

The repository has been highly tuned to automate developer environments.

```cmd
# 1. Automatically builds your Python Virtual Environment and downloads all dependencies
setup_env.bat

# 2. Runs the FastAPI and Streamlit architecture concurrently (Requires Docker/Postgres for DB)
start_local.bat

# 3. Fire the full testing suite verifying PyTest boundaries
test.bat
```

### 2. Docker Cloud Deployment (Required)

The ecosystem is completely encapsulated and containerized suitable for deployment (e.g. Google Cloud Run or AWS ECS).

```bash
docker-compose up --build
```

| Service | Access Map |
|---|---|
| **API + Swagger UI** | http://localhost:8000/docs |
| **Bonus Dashboard UI** | http://localhost:8501 |
| **PostgreSQL Database** | localhost:5432 |
