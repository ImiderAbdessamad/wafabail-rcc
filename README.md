<p align="center">
  <strong>🏦 Wafabail RCC</strong><br/>
  <em>AI-Powered Financial Document Extraction for the EKIP Credit Model</em>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" />
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" />
  <img alt="Ollama" src="https://img.shields.io/badge/Ollama-GLM_Vision-black?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0id2hpdGUiPjxjaXJjbGUgY3g9IjEyIiBjeT0iMTIiIHI9IjEwIi8+PC9zdmc+" />
  <img alt="License" src="https://img.shields.io/badge/License-Proprietary-red" />
</p>

---

## Table of Contents

1. [Overview](#overview)
2. [What Problem Does This Solve?](#what-problem-does-this-solve)
3. [Architecture](#architecture)
4. [The Extraction Pipeline](#the-extraction-pipeline)
5. [RCC Fields Reference](#rcc-fields-reference)
6. [Tech Stack](#tech-stack)
7. [Project Structure](#project-structure)
8. [Prerequisites](#prerequisites)
9. [Installation](#installation)
10. [Configuration](#configuration)
11. [Running the Application](#running-the-application)
12. [API Reference](#api-reference)
13. [Dossier Management & Validation Workflow](#dossier-management--validation-workflow)
14. [Authentication](#authentication)
15. [Compliance Engine](#compliance-engine)
16. [Accounting Controls](#accounting-controls)
17. [Frontend (Web UI)](#frontend-web-ui)
18. [Testing](#testing)
19. [Demo Seeding](#demo-seeding)
20. [Deployment Notes](#deployment-notes)
21. [Troubleshooting](#troubleshooting)
22. [Contributing](#contributing)
23. [Authors](#authors)

---

## Overview

**Wafabail RCC** is a FastAPI-based web application that automates the extraction of financial data from Moroccan tax filing documents (*liasses fiscales*). It runs the **v10 « robust » extraction engine** — a grid-guided, multi-model vision pipeline served by [Ollama](https://ollama.ai) — to read scanned or digitally-generated PDF pages and extract exactly **20 financial fields** required by the **EKIP** credit risk model.

The system also provides a **dossier management workflow** where analysts can review, correct, validate, or reject extracted data before it's transmitted to EKIP, with a full **audit trail** and **compliance engine**.

### Key Capabilities

| Capability | Description |
|-----------|-------------|
| 🤖 **Grid-guided OCR** | The printed table grid is detected, then each cell is read in isolation — no row shifting |
| 🧠 **Multi-model consensus** | Three vision models read and cross-check every amount; disagreements are surfaced, not guessed |
| 📊 **20 RCC Fields** | Extracts exactly the financial fields needed for EKIP credit scoring |
| 🚫 **No invented figures** | A blank cell stays blank, a value that fails its section arithmetic is flagged for review |
| 🔄 **Async Processing** | Background job processing with real-time progress via SSE |
| ✅ **Accounting Controls** | Seven arithmetic identities checked on the extracted evidence |
| 📋 **Dossier Management** | Full validation queue with status tracking (pending → validated/rejected) |
| ✏️ **Analyst Corrections** | Analysts can override AI-extracted values with full audit trail |
| 🔒 **Authentication** | Session-based analyst login with secure cookie handling |
| 📜 **Compliance Engine** | Server-side compliance rules that must pass before validation |
| 🔍 **Audit Trail** | Every correction and status change is timestamped and attributed |
| 🖥️ **Web UI** | Drag-and-drop interface with real-time progress and results display |

---

## What Problem Does This Solve?

Wafabail processes lease credit applications that require analysis of the applicant's financial statements. These statements arrive as **scanned PDF documents** (Moroccan PCGM format) containing:

- **Balance sheets** (Bilan Actif / Passif)
- **Income statements** (Compte de Produits et Charges — CPC)
- **Detailed CPC breakdowns**
- **Tax result computations** (Résultat Fiscal)
- **Management balance statements** (État des Soldes de Gestion — ESG)

Previously, analysts had to **manually read and type** 20+ financial values from these documents — a slow, error-prone process. This application automates that extraction using computer vision AI. The engine trades speed for reliability: it re-reads and cross-checks every amount, so a document takes several minutes to process, but what reaches the analyst is either a corroborated figure or an explicitly flagged one — never a plausible-looking guess.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Web Browser                          │
│  ┌─────────────────┐  ┌──────────────────────────────────┐  │
│  │  Upload Zone     │  │  Progress / Results / Dossier UI │  │
│  │  (drag & drop)   │  │  (SSE real-time updates)         │  │
│  └───────┬─────────┘  └──────────────┬───────────────────┘  │
│          │                           │                       │
└──────────┼───────────────────────────┼───────────────────────┘
           │ POST /api/v1/rcc/jobs     │ GET /stream, /result
           ▼                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI Application                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
│  │  RCC     │  │  Auth    │  │ Dossiers │  │ Health /    │  │
│  │  Router  │  │  Router  │  │  Router  │  │ Static UI   │  │
│  └────┬─────┘  └──────────┘  └────┬─────┘  └─────────────┘  │
│       │                           │                          │
│  ┌────▼──────────────────────┐  ┌─▼──────────────────────┐   │
│  │  Extraction Engine (v10)  │  │  Dossier Store          │   │
│  │                           │  │  (SQLite persistence)   │   │
│  │  Render→Orient→Classify→ │  │                         │   │
│  │  Grid→Ensemble OCR→Map→  │  │  Compliance Engine       │   │
│  │  Resolve→Controls→RCC    │  │                         │   │
│  └────────────┬──────────────┘  └─────────────────────────┘   │
│               │                                               │
│  ┌────────────▼──────────────┐  ┌─────────────────────────┐   │
│  │   Job Store (in-memory)   │  │  Auth Service            │   │
│  │   TTL=60min, pub/sub SSE  │  │  (session cookies)       │   │
│  └───────────────────────────┘  └─────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
                        │
                        ▼
           ┌──────────────────────────────────────┐
           │   Ollama Server                      │
           │   GLM-4.6V   orientation / classify  │
           │   glm-ocr    cell reading            │
           │   qwen3-vl   independent verify      │
           │   qwen3.5    label mapping           │
           │   gemma4     label arbitration       │
           └──────────────────────────────────────┘
```

---

## The Extraction Pipeline

The extraction engine is `app/services/ocr_lab_core_v10.py` — a vendored copy of the
reference laboratory script
(`documentation-wafabail/use-case-RCC/wafabail_ocr_lab_core_v10_robust.py`, pipeline
version `v10-robust-grid-ensemble-recovery`). Prompts, thresholds, grid geometry,
resolution rules and controls are identical to the script, so what is validated in the
lab is exactly what the API serves. `app/services/rcc_lab_pipeline.py` only runs the
engine in a worker thread and projects its output onto the API schema.

The engine's guiding principle is **never invent a number**. A blank cell stays blank
(it is never turned into a zero), a value read by a single model is only kept when the
arithmetic of its section confirms it, and any disagreement is surfaced to the analyst
instead of being silently arbitrated.

### Step 1 — Native text vs scan

Each page is first tested for usable embedded text (≥ 80 characters and ≥ 15 words).
Vector PDFs are read directly from their text layer — no model call at all. Scanned
pages are rendered at **220 DPI** and go through the vision pipeline.

### Step 2 — Orientation and page type

`scan_layout_agent` asks GLM-4.6V for the page rotation, has **Qwen3-VL verify it
independently**, crops to the visible content, then classifies the page into one of nine
types:

| Type | Description |
|------|-------------|
| `IDENTIFICATION` | Company identity page (name, tax ID, ICE, RC, address, exercise dates) |
| `BILAN_ACTIF` | Balance sheet — assets side |
| `BILAN_PASSIF` | Balance sheet — liabilities side |
| `CPC` | Income statement (Compte de Produits et Charges) |
| `DETAIL_CPC` | Detailed breakdown of CPC line items |
| `RESULTAT_FISCAL` | Tax result computation (not extracted) |
| `ESG` | Management balance statement (not extracted) |
| `AUTRE` | Non-financial page (skipped) |
| `VIDE` | Blank page (skipped) |

Only `IDENTIFICATION`, `BILAN_ACTIF`, `BILAN_PASSIF`, `CPC` and `DETAIL_CPC` are extracted.

### Step 3 — Grid-guided reading

For balance sheet and CPC pages, OpenCV detects the printed table grid (progressive
tolerances handle scans where a separator is drawn twice a few pixels apart). Each
expected row is then located on the page, and **each cell is cropped and read in
isolation** rather than asking a model to transcribe a whole table. This is what removes
the classic failure mode where a blank line shifts every amount below it onto the wrong
label.

If the grid cannot be detected, the engine falls back to the full-table reading mode.

### Step 4 — Multi-model cell consensus

Every located cell goes through an ensemble:

1. `glm-ocr` reads the cell (original and contrast-enhanced views)
2. `qwen3-vl` re-reads it independently
3. `GLM-4.6V` arbitrates when the two disagree

Each cell ends up in one of three explicit states — `value`, `blank` or `uncertain` —
and a constraint solver checks the reading against the row's own arithmetic
(`Brut − Amortissements = Net`, `Opérations propres + exercices antérieurs = Total`).
Section subtotals are also verified against their components; a section that does not add
up is re-read before being accepted.

### Step 5 — Semantic mapping (amount-blind)

Row labels are mapped to canonical field codes by `qwen3.5`, with `gemma4` arbitrating
ambiguous cases. **Neither model ever sees the amounts**: semantic reasoning decides what
a row *means*, but can never rewrite, choose or invent a figure. A rule-based alias map
handles the standard DGI wording and acts as the fallback when the mapper fails.

### Step 6 — Resolution of the 20 RCC fields

`resolve_rcc` selects one value per RCC field from the evidence, preferring the page type
where the field is authoritative. `RESULTAT_NET` additionally requires corroboration
across independent pages (CPC and Bilan Passif). Each field is returned with a status:

| Engine status | API status | Meaning |
|---------------|-----------|---------|
| `confirmed` / `cross_validated` | `confirmed` | Read directly, and corroborated when found twice |
| `low_confidence` / `needs_review` | `ambiguous` | Value present but OCR or a control is unresolved |
| `derived` / `partial` / `proxy` | `derived` | Computed from printed rows, or taken from a neighbouring line |
| `blank_on_form` / `missing` | `missing` | Row visibly blank, or not found — never coerced to 0 |
| `conflicting` (both variants) | `conflicting` | Pages disagree — the analyst arbitrates |

`CA_EXPORT` is summed from the three explicitly printed export rows, and `TYPE_RESULTAT`
is derived from the sign of `RESULTAT_NET` (`Bénéficiaire`, `Déficitaire` or `Nul`).

### Step 7 — Accounting controls and status propagation

Seven arithmetic controls run on the extracted evidence (see
[Accounting Controls](#accounting-controls)). A numeric field touched by a failed control,
or by an unresolved cross-check, is downgraded to `needs_review` rather than being left as
a confident read — it will show up as *à vérifier* in the validation screen.

### Step 8 — Projection onto the API schema

`rcc_lab_pipeline.build_result` turns the engine's four DataFrames into
`RccAnalysisResult`: the 20 ordered fields with their evidence (page, label, raw value,
column, confidence), the N-1 figures read on the same rows, the page audit, the controls,
and French warnings. No amount is recomputed at this stage.

---

## RCC Fields Reference

These are the 20 financial fields extracted by the system:

| # | Code | Label (FR) | Description (EN) | Source |
|---|------|-----------|-------------------|--------|
| 1 | `ACTIFS_IMMOBILISES` | Actifs immobilisés | Fixed/long-term assets | Bilan Actif |
| 2 | `TOTAL_BILAN` | Total bilan | Total balance sheet | Bilan Actif |
| 3 | `CHIFFRE_AFFAIRES` | Chiffre d'affaires | Revenue / turnover | CPC |
| 4 | `CA_EXPORT` | CA à l'export | Export revenue | CPC |
| 5 | `DETTES_BANCAIRES_MLT` | Dettes bancaires MLT | Medium/long-term bank debt | Bilan Passif |
| 6 | `DETTES_BANCAIRES_CT` | Dettes bancaires CT | Short-term bank debt | Bilan Passif |
| 7 | `PASSIF_CIRCULANT` | Passif circulant | Current liabilities | Bilan Passif |
| 8 | `DETTES_FOURNISSEURS` | Dettes fournisseurs | Supplier debt / accounts payable | Bilan Passif |
| 9 | `COMPTE_COURANT_ASSOCIES` | Compte courant d'associés | Shareholder current accounts | Bilan |
| 10 | `TRESORERIE_PASSIF` | Trésorerie passif | Cash liabilities | Bilan Passif |
| 11 | `ACTIF_CIRCULANT` | Actif circulant | Current assets | Bilan Actif |
| 12 | `CREANCES_CLIENTS` | Créances clients | Customer receivables | Bilan Actif |
| 13 | `TRESORERIE_ACTIF` | Trésorerie actif | Cash assets | Bilan Actif |
| 14 | `CAISSE` | Caisse | Petty cash | Bilan Actif |
| 15 | `ACHATS_REVENDUS` | Achats revendus | Resold purchases | CPC |
| 16 | `ACHATS_CONSOMMES` | Achats consommés | Consumed purchases / materials | CPC |
| 17 | `AUTRES_CHARGES_EXTERNES` | Autres charges externes | Other external charges | CPC |
| 18 | `CHARGES_INTERETS` | Charges d'intérêts | Interest charges | CPC |
| 19 | `RESULTAT_NET` | Résultat net | Net profit/loss | CPC |
| 20 | `TYPE_RESULTAT` | Type de résultat | Profitable / Deficit / Neutral (derived) | Derived |

> **Note:** `TYPE_RESULTAT` never has a numeric `value`. Its business meaning is stored in the `note` field: `"Bénéficiaire"` (profitable), `"Déficitaire"` (deficit), or `"Nul"` (neutral).

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Backend** | [FastAPI](https://fastapi.tiangolo.com/) ≥ 0.115 | Async web framework with auto-generated OpenAPI docs |
| **AI Vision** | GLM-4.6V-Flash, `glm-ocr`, `qwen3-vl` via [Ollama](https://ollama.ai) | Orientation, cell reading and independent verification |
| **AI Reasoning** | `qwen3.5`, `gemma4` via Ollama | Amount-blind label mapping and arbitration |
| **PDF Processing** | [PyMuPDF](https://pymupdf.readthedocs.io/) | PDF rendering to images + native text extraction |
| **Table Geometry** | [OpenCV](https://opencv.org/) + [NumPy](https://numpy.org/) | Grid detection, row/column projection, morphology |
| **Tabular Output** | [pandas](https://pandas.pydata.org/) | Audit, evidence, RCC and control frames |
| **Image Processing** | [Pillow](https://pillow.readthedocs.io/) (PIL) | Image rotation, downscaling, contrast enhancement |
| **HTTP Client** | [requests](https://requests.readthedocs.io/) (engine), [httpx](https://www.python-httpx.org/) | Ollama API calls |
| **Data Validation** | [Pydantic](https://docs.pydantic.dev/) v2 | Schema validation, serialization, and API documentation |
| **Database** | SQLite (stdlib) | Dossier persistence, audit trail, sessions |
| **Frontend** | React 18 + React Router + Vite | Responsive single-page UI with a component architecture, drag-and-drop, and SSE progress |
| **Env Config** | [python-dotenv](https://pypi.org/project/python-dotenv/) | Environment variable management |
| **ASGI Server** | [Uvicorn](https://www.uvicorn.org/) | Production-grade async server |

---

## Project Structure

```
wafabail-rcc/
├── main.py                          # FastAPI app entry point, CORS, routes
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment variable template
├── .gitignore
│
├── app/
│   ├── __init__.py
│   ├── config.py                    # All settings from environment variables
│   ├── db.py                        # SQLite schema, migrations, connection pool
│   │
│   ├── routers/                     # FastAPI route handlers
│   │   ├── __init__.py
│   │   ├── rcc.py                   # RCC extraction job endpoints
│   │   ├── auth.py                  # Login/logout/session endpoints
│   │   └── dossiers.py              # Dossier CRUD, overrides, audit endpoints
│   │
│   ├── schemas/                     # Pydantic data models
│   │   ├── __init__.py
│   │   ├── rcc.py                   # 20 RCC field definitions + response models
│   │   ├── direct_financial_extraction.py  # Page types, candidate models, GLM schemas
│   │   ├── financial_analysis.py    # Financial dataset, ratios, controls
│   │   ├── financial_mapping.py     # Field mapping schemas
│   │   ├── dossier.py               # Dossier, override, audit schemas
│   │   └── pdf_extraction.py        # PDF extraction schemas
│   │
│   └── services/                    # Business logic
│       ├── __init__.py
│       │
│       │  # ── Core Pipeline ──────────────────────────────────
│       ├── ocr_lab_core_v10.py     # Extraction engine (vendored lab script)
│       ├── rcc_lab_pipeline.py     # Engine runner + projection to RccAnalysisResult
│       │
│       │  # ── Infrastructure ─────────────────────────────────
│       ├── financial_job_store.py   # In-memory job store with TTL & pub/sub
│       ├── auth.py                  # Session authentication service
│       ├── dossier_store.py         # SQLite-backed dossier persistence
│       ├── rcc_compliance.py        # Compliance rules engine
│       │
│       │  # ── Superseded by the v10 engine, kept for reference ─
│       └── direct_*.py, financial_*.py, rcc_mapper.py, ...
│

├── frontend/                        # React + Vite source application
│   ├── src/                         # Screens, components, hooks, API client and styles
│   ├── package.json                 # Frontend scripts and dependencies
│   └── vite.config.js               # Development API proxy and production output
├── static/                          # Generated Vite assets served by FastAPI
│
├── scripts/
│   ├── run_extraction.py            # Run the pipeline on a local PDF (no API)
│   └── seed_demo.py                 # Generate demo dossiers for testing
│
└── tests/
    └── test_api_smoke.py            # API smoke tests
```

---

## Prerequisites

1. **Python 3.11+** — required for modern type hint syntax (`X | None`)
2. **Ollama** — reachable server hosting the five models of the pipeline
3. **Git** — for version control

### Load the Five Models

The engine refuses to start if any required model is missing (`check_models`), so pull
them all before the first run:

```bash
# Install Ollama from https://ollama.ai, then:
ollama pull hf.co/unsloth/GLM-4.6V-Flash-GGUF:Q4_K_M   # orientation, classification, arbitration
ollama pull glm-ocr:q8_0                               # cell OCR
ollama pull qwen3-vl:30b                               # independent verification
ollama pull qwen3.5:9b                                 # label mapping (amount-blind)
ollama pull gemma4:latest                              # label arbitration (amount-blind)

# Verify they are all listed:
curl http://localhost:11434/api/tags
```

Point `RCC_OLLAMA_URL` (or `OLLAMA_URL`) at a remote server if the models are hosted
elsewhere.

> **Hardware note:** the ensemble reads each table cell with several models, which is what
> buys the accuracy. Budget a GPU with 24 GB VRAM or more; on a small GPU the run still
> completes but page latency grows substantially.

---

## Installation

### Windows

```bash
cd wafabail-rcc
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

### macOS / Linux

```bash
cd wafabail-rcc
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env` with your configuration (see [Configuration](#configuration) below).

---

## Configuration

All settings are read from environment variables (loaded from `.env` via `python-dotenv`).

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `glm4v` | Default Ollama model name |
| `OLLAMA_VISION_MODEL` | Same as `OLLAMA_MODEL` | Vision model override |
| `OLLAMA_TIMEOUT` | `300` | Request timeout in seconds |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins (comma-separated) |
| `MAX_UPLOAD_MB` | `50` | Maximum PDF upload size in megabytes |

### Extraction Engine Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `RCC_OLLAMA_URL` | Falls back to `OLLAMA_URL` | Server hosting the five models |
| `RCC_VISION_MODEL` | `hf.co/unsloth/GLM-4.6V-Flash-GGUF:Q4_K_M` | Orientation, classification, tie-break |
| `RCC_OCR_MODEL` | `glm-ocr:q8_0` | Isolated cell reading |
| `RCC_VERIFY_MODEL` | `qwen3-vl:30b` | Independent verification of each cell |
| `RCC_MAPPER_MODEL` | `qwen3.5:9b` | Label → field mapping (never sees amounts) |
| `RCC_ADJUDICATOR_MODEL` | `gemma4:latest` | Arbitration of ambiguous labels |
| `RCC_USE_GLM_VERIFICATION` | `true` | Disable to skip scanned pages entirely (native text only) |
| `RCC_USE_REASONING_MAPPER` | `true` | Disable to fall back to rule-based label mapping |
| `RCC_USE_ADJUDICATOR` | `true` | Disable if `gemma4` is not available on the server |
| `RCC_REQUEST_TIMEOUT_SECONDS` | `600` | Per-call Ollama timeout |
| `RCC_KEEP_ALIVE` | `20m` | How long Ollama keeps the models loaded |
| `RCC_RENDER_DPI` | `220` | PDF → image rendering quality |
| `RCC_EXTRACT_MAX_SIDE` | `2400` | Max image dimension sent to the models |
| `DIRECT_FINANCIAL_MAX_PAGES` | `60` | Upper bound accepted for `max_pages` |
| `DIRECT_FINANCIAL_JOB_TTL_MINUTES` | `60` | Job expiration time in memory |

### Authentication & Database Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `ANALYST_USERNAME` | `analyst` | Login username |
| `ANALYST_PASSWORD` | `wafabail2025` | Login password |
| `ANALYST_DISPLAY_NAME` | `Analyste RCC` | Display name in UI |
| `SESSION_TTL_HOURS` | `12` | Session duration in hours |
| `SESSION_COOKIE_NAME` | `wfrcc_session` | Cookie name |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` for HTTPS |
| `DB_PATH` | `data/rcc.db` | SQLite database path |
| `PDF_STORAGE_DIR` | `data/pdfs` | Directory for stored PDFs |
| `CONFIDENCE_REVIEW_THRESHOLD` | `0.7` | Below this, analyst must review the field |

---

## Running the Application

### Development Mode

```bash
# Terminal 1 — FastAPI
uvicorn main:app --reload --host 127.0.0.1 --port 8001

# Terminal 2 — React development server with /api proxy
cd frontend
npm install
npm run dev
```

For a production-style local build, run `npm run build` from `frontend/`.
The generated files are written to `static/` and then served by FastAPI.

### Access Points

| URL | Description |
|-----|-------------|
| http://127.0.0.1:8001/ | Web UI — drag-and-drop interface |
| http://127.0.0.1:8001/docs | OpenAPI interactive documentation (Swagger) |
| http://127.0.0.1:8001/redoc | ReDoc API documentation |
| http://127.0.0.1:8001/health | Health check endpoint |

---

## API Reference

### RCC Extraction Jobs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/rcc/jobs` | Upload a PDF and start extraction |
| `GET` | `/api/v1/rcc/jobs/{job_id}` | Check job status and progress |
| `GET` | `/api/v1/rcc/jobs/{job_id}/stream` | Real-time progress via Server-Sent Events |
| `GET` | `/api/v1/rcc/jobs/{job_id}/result` | Get the final 20 RCC fields |

#### Create a Job

```bash
curl -X POST http://127.0.0.1:8001/api/v1/rcc/jobs \
  -F "file=@liasse_fiscale.pdf" \
  -F "max_pages=20"
```

**Response:**
```json
{
  "job_id": "a1b2c3d4e5f6...",
  "status": "queued",
  "stream_url": "/api/v1/rcc/jobs/a1b2c3d4e5f6.../stream",
  "result_url": "/api/v1/rcc/jobs/a1b2c3d4e5f6.../result"
}
```

#### Monitor Progress (SSE)

```bash
curl -N http://127.0.0.1:8001/api/v1/rcc/jobs/{job_id}/stream
```

Events emitted:

| Event | Description |
|-------|-------------|
| `job_status` | Initial status snapshot |
| `pdf_validated` | PDF validation passed |
| `pages_rendered` | All pages converted to images |
| `page_classified` | A page's type has been identified |
| `page_extracted` | A page's financial data has been extracted |
| `page_skipped` | A non-financial page was skipped |
| `page_failed` | A page extraction failed |
| `resolving_fields` | Candidate resolution started |
| `running_controls` | Accounting controls running |
| `result_ready` | Extraction complete — result available |
| `job_failed` | Job failed with an error |

#### Get Result

```bash
curl http://127.0.0.1:8001/api/v1/rcc/jobs/{job_id}/result
```

**Response (example):**
```json
{
  "document": {
    "filename": "liasse.pdf",
    "pages_total": 12,
    "pages_processed": 8,
    "pages_skipped": 3,
    "pages_failed": 1,
    "document_type": "LIASSE_FISCALE",
    "company": {
      "raison_sociale": "ACME SARL",
      "identifiant_fiscal": "12345678",
      "ice": "001234567000089"
    },
    "exercise": {
      "debut": "01/01/2024",
      "fin": "31/12/2024"
    }
  },
  "extraction": {
    "model": "hf.co/unsloth/GLM-4.6V-Flash-GGUF:Q4_K_M",
    "page_audit": [ ... ],
    "warnings": [ ... ]
  },
  "fields": [
    {
      "number": 1,
      "code": "ACTIFS_IMMOBILISES",
      "label": "Actifs immobilisés",
      "value": 1234567.0,
      "unit": "MAD",
      "source": "Bilan Actif",
      "status": "confirmed",
      "note": null,
      "confidence": 0.92
    },
    ...
  ],
  "completeness_pct": 85.0,
  "warnings": []
}
```

### Dossier Management

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/v1/rcc/dossiers` | ✅ | List dossiers (filter by status, search) |
| `POST` | `/api/v1/rcc/dossiers` | ✅ | Create dossier from completed job |
| `GET` | `/api/v1/rcc/dossiers/{id}` | ✅ | Get dossier detail + compliance |
| `PATCH` | `/api/v1/rcc/dossiers/{id}` | ✅ | Update status (validate/reject/escalate) |
| `PUT` | `/api/v1/rcc/dossiers/{id}/overrides` | ✅ | Save analyst corrections |
| `GET` | `/api/v1/rcc/dossiers/{id}/overrides` | ✅ | Get correction history |
| `GET` | `/api/v1/rcc/dossiers/{id}/audit` | ✅ | Get audit trail for a dossier |
| `GET` | `/api/v1/rcc/dossiers/{id}/file` | ✅ | Download original PDF |
| `POST` | `/api/v1/rcc/dossiers/{id}/attach` | ✅ | Attach a new extraction to existing dossier |

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/auth/login` | Login (returns session cookie) |
| `POST` | `/api/v1/auth/logout` | Logout (clears session) |
| `GET` | `/api/v1/auth/me` | Get current user info |

### Audit Trail

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/v1/rcc/audit` | ✅ | Global audit log (all dossiers) |

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check: `{"status": "ok"}` |
| `GET` | `/` | Serves the web UI |

---

## Dossier Management & Validation Workflow

The dossier system implements a review workflow:

```
                    ┌──────────┐
  Extraction ──────►│ pending  │◄──────── Created from job result
  completes        └────┬─────┘
                        │
           ┌────────────┼────────────┐
           ▼            ▼            ▼
    ┌──────────┐  ┌──────────┐  ┌──────────┐
    │validated │  │ rejected │  │escalated │
    │          │  │(+ motif) │  │          │
    └──────────┘  └──────────┘  └──────────┘
         │                           │
         ▼                           ▼
    Sent to EKIP              Senior review
```

### Key Rules

- **Validation requires** all compliance rules to pass (no blockers)
- **Rejection requires** a mandatory reason (`motif`)
- **Escalation** flags the dossier for senior/supervisor review
- **Corrections** are stacked — the original OCR value is **never overwritten**
- Every action is **timestamped and attributed** to the analyst

---

## Authentication

The system uses **session-based authentication** with HttpOnly cookies:

- Single analyst account configured via environment variables
- **Constant-time credential comparison** (using `hmac.compare_digest`) to prevent timing attacks
- Sessions stored in SQLite with automatic expiration
- Cookie: HttpOnly, SameSite=Lax, configurable Secure flag

### Default Credentials

```
Username: analyst
Password: wafabail2025
```

> ⚠️ **Change the default password** in production via the `ANALYST_PASSWORD` environment variable.

---

## Compliance Engine

Before a dossier can be validated, it must pass **server-side compliance checks** organized in 5 sections:

| Section | What It Checks |
|---------|---------------|
| **Pièces obligatoires** | PDF attached, OCR extraction completed, all pages processed |
| **Complétude des champs RCC** | All 20 fields populated, exercise date identified, client identified |
| **Cohérence comptable** | Pipeline accounting controls (balance equilibrium, result consistency) |
| **Cohérence des postes RCC** | RCC-specific identities (e.g., Total = Assets + Current + Cash) |
| **Contrôle RCC** | No unresolved conflicts, low-confidence fields reviewed, audit trail exists |

### Blocking vs Warning

- **Blocking rules** must all pass before a dossier can be validated
- **Warning rules** are informational — the analyst should justify but they don't block

### How Analysts Lift Blockers

- **Missing fields** → correct via the override system
- **Conflicting fields** → provide a manual correction
- **Low-confidence fields** → either correct the value or verify/confirm the OCR value
- **Failed accounting controls** → correct the affected RCC fields

---

## Accounting Controls

The engine verifies seven accounting identities directly on the extracted evidence:

| Control Code | Rule | Scope |
|-------------|------|-------|
| `bilan_equilibre` | Total Assets = Total Liabilities | Document |
| `bilan_actif_totaux` | Fixed + current assets + cash = Total assets | Bilan Actif |
| `bilan_passif_totaux` | Permanent + current liabilities + cash = Total liabilities | Bilan Passif |
| `cpc_ventes_chiffre_affaires` | Goods sales + services sales = Revenue | CPC |
| `resultat_net` | Net result in the CPC = net result carried in the liabilities | Document |
| `actif_brut_amort_net` | Gross − Depreciation = Net | Every asset row |
| `cpc_operations_total` | Own operations + prior periods = Exercise total | Every CPC row |

The last two run on every printed row; the API aggregates them into a single control each,
reporting how many rows were checked and which ones disagree.

Tolerance: `0.02 MAD` (the engine works in `Decimal`, never in floating point).

A failed control does not erase the value — it downgrades the affected fields to
*needs review* so the analyst sees both the reading and the discrepancy.

---

## Frontend (Web UI)

The web interface provides:

- **Drag-and-drop upload** — deposit a tax return PDF and follow its extraction in real time via SSE
- **Human RCC validation queue** — search, filter, correct, verify, escalate, reject, or validate dossiers with a complete audit trail
- **In-app PDF evidence review** — the original document is rendered in the dossier view; selecting a field or extracted zone opens its precise source page and highlights the matched OCR evidence
- **Extracted zones register** — each evidence item exposes the field, original label/value, source page, OCR confidence, and source excerpt
- **Analyst exports** — download a native Excel workbook with `Synthèse`, `Données RCC`, `Zones extraites`, and `Contrôles` sheets, or a branded PDF validation report ready to archive or share
- **Responsive and accessible interface** — keyboard navigation, visible focus states, reduced-motion support, and French financial formatting

### Technology

The frontend is built with **React 18**, **React Router**, and **Vite**. It communicates with the backend via:

- `fetch()` for REST API calls
- `EventSource` for SSE real-time progress
- `pdfjs-dist` for controlled PDF rendering and evidence highlighting
- `xlsx` for native Excel exports and `jspdf` / `jspdf-autotable` for analyst PDF reports

---

## Testing

### Run Smoke Tests

```bash
# Make sure the server is running first
uvicorn main:app --host 127.0.0.1 --port 8001

# In another terminal:
python tests/test_api_smoke.py
```

### Run the Projection Tests

```bash
python tests/test_rcc_projection.py
```

Feeds a synthetic balanced tax return through the real engine functions
(`resolve_rcc`, `run_controls`) and the API projection, then checks the 20 fields,
their statuses, evidence, N-1 figures, controls and page audit — no Ollama needed.

### Run the Engine on a PDF (no API)

```bash
py -3 scripts/run_extraction.py path/to/liasse.pdf --json out.json
```

Prints the 20 RCC fields with their status and confidence, the accounting controls and the
warnings — the fastest way to check an engine change before exposing it through the API.

The smoke tests in `tests/test_api_smoke.py` cover:
- Health check endpoint
- PDF upload and job creation
- Job status polling
- Result retrieval
- Authentication, dossier workflow, overrides, compliance rules, audit history, search, and PDF delivery
- Error handling (invalid files, missing jobs)

---

## Demo Seeding

To populate the database with sample dossiers for development/demo:

```bash
python scripts/seed_demo.py
```

This creates realistic-looking dossier records with various statuses, completeness levels, and sample field values — useful for testing the dossier management UI without running actual extractions.

---

## Deployment Notes

### Production Checklist

- [ ] Change default analyst password (`ANALYST_PASSWORD`)
- [ ] Set `SESSION_COOKIE_SECURE=true` (requires HTTPS)
- [ ] Configure `ALLOWED_ORIGINS` to your domain (not `*`)
- [ ] Ensure Ollama is running with the GLM model loaded
- [ ] Set appropriate `DIRECT_FINANCIAL_MAX_PAGES` limit
- [ ] Configure a persistent `DB_PATH` and `PDF_STORAGE_DIR`
- [ ] Set up log rotation for production logs

### Running with Uvicorn (Production)

```bash
uvicorn main:app \
  --host 0.0.0.0 \
  --port 8001 \
  --workers 1 \
  --log-level info
```

> **Important:** Use `--workers 1` because the extraction pipeline uses a global `asyncio.Lock` to serialize GLM calls (the model can only handle one request at a time).

### Behind a Reverse Proxy (Nginx)

```nginx
server {
    listen 443 ssl;
    server_name rcc.wafabail.ma;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE support
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }

    client_max_body_size 50M;
}
```

---

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| `Required Ollama model(s) missing: [...]` | One of the five models is not pulled | Pull the listed model, or disable the optional one via `RCC_USE_ADJUDICATOR=false` |
| Job fails immediately with a connection error | `RCC_OLLAMA_URL` unreachable | Check the server with `curl $RCC_OLLAMA_URL/api/tags` |
| Every page is `UNCLASSIFIED` | Vision model not answering the layout prompt | Check `RCC_VISION_MODEL` matches a model listed by `/api/tags` |
| Many fields `missing` on a readable page | Grid not detected on a low-quality scan | Increase `RCC_RENDER_DPI` (220 → 300) and re-run |
| Very slow extraction (minutes per page) | Expected — the ensemble reads each cell several times | Reduce scope with `max_pages`, or run on a larger GPU |
| `Fichier trop volumineux` | PDF exceeds limit | Increase `MAX_UPLOAD_MB` or split the PDF |
| Field shows `needs review` with a value | An accounting control did not balance | Compare with the highlighted evidence and correct it in the dossier |
| Field shows `conflicting` | Two pages disagree on the amount | The analyst arbitrates; both readings are kept in the evidence panel |
| Session expired immediately | Clock skew | Ensure server time is accurate (NTP) |

---

## Contributing

1. **Fork** the repository
2. **Create a feature branch** from `main`
3. **Follow existing patterns** — the codebase uses:
   - Type hints everywhere
   - Pydantic v2 for data models
   - `async` / `await` for I/O operations
   - French docstrings and variable names (domain-specific)
4. **Add tests** for new functionality
5. **Submit a Pull Request** with a clear description

### Code Style

- Python: Follow PEP 8, use type hints, French docstrings for domain logic
- Frontend: React components and hooks, Vite build step, ES module-style organization
- Naming: `snake_case` for Python, `camelCase` for JavaScript

---


---

