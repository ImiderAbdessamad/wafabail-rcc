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

**Wafabail RCC** is a FastAPI-based web application that automates the extraction of financial data from Moroccan tax filing documents (*liasses fiscales*). It uses the **GLM-4V** vision model (served locally via [Ollama](https://ollama.ai)) to read scanned or digitally-generated PDF pages and extract exactly **20 financial fields** required by the **EKIP** credit risk model.

The system also provides a **dossier management workflow** where analysts can review, correct, validate, or reject extracted data before it's transmitted to EKIP, with a full **audit trail** and **compliance engine**.

### Key Capabilities

| Capability | Description |
|-----------|-------------|
| 🤖 **AI-Powered OCR** | GLM-4V vision model reads scanned financial tables page-by-page |
| 📊 **20 RCC Fields** | Extracts exactly the financial fields needed for EKIP credit scoring |
| 🔄 **Async Processing** | Background job processing with real-time progress via SSE |
| ✅ **Accounting Controls** | Automated sanity checks (balance sheet equilibrium, result consistency) |
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

Previously, analysts had to **manually read and type** 20+ financial values from these documents — a slow, error-prone process. This application automates that extraction using computer vision AI, reducing processing time from ~30 minutes to ~2 minutes per document while maintaining accuracy through human review.

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
│  │   Extraction Pipeline     │  │  Dossier Store          │   │
│  │                           │  │  (SQLite persistence)   │   │
│  │  PDF→PNG→Classify→GLM→   │  │                         │   │
│  │  Resolve→Controls→RCC    │  │  Compliance Engine       │   │
│  └────────────┬──────────────┘  └─────────────────────────┘   │
│               │                                               │
│  ┌────────────▼──────────────┐  ┌─────────────────────────┐   │
│  │   Job Store (in-memory)   │  │  Auth Service            │   │
│  │   TTL=60min, pub/sub SSE  │  │  (session cookies)       │   │
│  └───────────────────────────┘  └─────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
                        │
                        ▼
           ┌────────────────────────┐
           │   Ollama Server        │
           │   GLM-4V Vision Model  │
           │   (localhost:11434)     │
           └────────────────────────┘
```

---

## The Extraction Pipeline

When a PDF is uploaded, the system executes the following pipeline:

### Step 1 — PDF Validation & Rendering

- Validates the PDF signature (`%PDF` header), file size (≤ 50 MB), and MIME type
- Renders each page to a **PNG image** at 180 DPI using PyMuPDF
- Extracts **native text** (if the PDF has selectable text, not just scanned images)
- Caps processing at 60 pages maximum

### Step 2 — Orientation Detection

- Analyzes each page image to detect rotation (0°, 90°, 180°, 270°)
- Considers the PDF's declared rotation metadata
- Auto-rotates pages to upright orientation for accurate AI reading

### Step 3 — Page Classification (3-Level)

Each page is classified into one of 9 types:

| Type | Description |
|------|-------------|
| `IDENTIFICATION` | Company identity page (name, tax ID, ICE, address) |
| `BILAN_ACTIF` | Balance sheet — assets side |
| `BILAN_PASSIF` | Balance sheet — liabilities side |
| `CPC` | Income statement (Compte de Produits et Charges) |
| `DETAIL_CPC` | Detailed breakdown of CPC line items |
| `RESULTAT_FISCAL` | Tax result computation |
| `ESG` | Management balance statement (État des Soldes de Gestion) |
| `AUTRE` | Non-financial page (skipped) |
| `VIDE` | Blank page (skipped) |

**Classification levels:**

1. **Lexical analysis** — searches the native PDF text for keywords (e.g., "bilan actif", "capitaux propres", "compte de produits et charges")
2. **Continuation detection** — if the text contains terms related to the previous page's type, assumes it's a multi-page continuation
3. **GLM Vision fallback** — sends a downscaled image to the AI and asks "what type of financial page is this?"

### Step 4 — AI-Powered Data Extraction

For each financial page, the system:

1. Converts the page image to **enhanced JPEG** (auto-contrast + sharpening for better table readability)
2. Sends it to **GLM-4V via Ollama** with a structured prompt tailored to the page type
3. The AI returns JSON with **candidates** — each containing:
   - `field_code` — which financial field it represents
   - `raw_value` — the amount as read from the image (e.g., `"1 234 567,00"`)
   - `confidence` — AI confidence score (0.0 to 1.0)
   - `evidence` — the row label, column name, and source excerpt

**Fallback strategies when extraction fails:**

| Strategy | When Used |
|----------|-----------|
| **Regional fallback** | If full-page extraction returns 0 candidates → crop the page into regions and retry each |
| **Focused retry** | If key fields (TOTAL_ACTIF, CHIFFRE_AFFAIRES, etc.) are missing → send a targeted prompt asking specifically for those fields |
| **Type correction** | If classification might be wrong → try alternative page types (e.g., if BILAN_ACTIF fails, try BILAN_PASSIF) |
| **Orientation retry** | If rotated and extraction fails → retry at 0° |

### Step 5 — Native Text Recovery

Safety net: if the AI missed fields but the PDF has selectable text, regex-based extraction attempts to recover amounts directly from the text.

### Step 6 — Candidate Resolution & Deduplication

- Removes duplicate candidates (same field found on multiple pages)
- Selects the best candidate per field based on confidence, nature hierarchy (`GRAND_TOTAL` > `SECTION_TOTAL` > `SUBTOTAL` > `DETAIL`), and column role
- Parses raw amount strings (`"1 234 567,00"`) into `Decimal` numbers

### Step 7 — Accounting Controls

Validates extracted data against fundamental accounting identities:

| Control | Rule |
|---------|------|
| Balance sheet equilibrium | Total Assets = Total Liabilities |
| Operating result | Revenue − Expenses = Operating Result |
| Financial result | Financial Income − Financial Charges = Financial Result |
| Current result | Operating + Financial = Current Result |
| Non-current result | Non-current Income − Non-current Charges = Non-current Result |
| Pre-tax result | Current + Non-current = Pre-tax Result |
| Net result | Pre-tax − Tax = Net Result |
| Net cash | Cash Assets − Cash Liabilities = Net Cash |

Failed controls **invalidate** affected fields (set to `conflicting` with `value = null`).

### Step 8 — RCC Field Mapping

Maps internal dataset fields to the **final 20 RCC fields** for EKIP, with intelligent fallback aliases:

- `DETTES_BANCAIRES_MLT` → tries `dettes_bancaires_mlt`, falls back to `dettes_financieres`
- `CHARGES_INTERETS` → tries `frais_financiers`, falls back to `charges_financieres`
- `TOTAL_BILAN` → tries `total_bilan`, `total_actif`, or `total_passif`
- `TYPE_RESULTAT` is **derived**: positive net result → `"Bénéficiaire"`, negative → `"Déficitaire"`, zero → `"Nul"`

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
| **AI Vision** | [GLM-4V](https://github.com/THUDM/GLM-4) via [Ollama](https://ollama.ai) | Vision-language model for reading financial tables from images |
| **PDF Processing** | [PyMuPDF](https://pymupdf.readthedocs.io/) (`fitz`) | PDF rendering to images + native text extraction |
| **Image Processing** | [Pillow](https://pillow.readthedocs.io/) (PIL) | Image rotation, downscaling, contrast enhancement |
| **HTTP Client** | [httpx](https://www.python-httpx.org/) | Async HTTP for Ollama API calls |
| **Data Validation** | [Pydantic](https://docs.pydantic.dev/) v2 | Schema validation, serialization, and API documentation |
| **Database** | SQLite (stdlib) | Dossier persistence, audit trail, sessions |
| **Frontend** | Vanilla HTML + CSS + JS | Single-page UI with drag-and-drop, SSE progress, and results |
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
│       ├── direct_financial_extraction_pipeline.py  # Main orchestrator
│       ├── direct_glm_financial_client.py           # Ollama/GLM API client
│       ├── financial_page_classifier.py             # Page type classification
│       ├── financial_orientation_detector.py         # Page rotation detection
│       ├── page_preprocessor.py                     # Image cropping for regions
│       │
│       │  # ── Resolution & Validation ────────────────────────
│       ├── direct_financial_resolver.py     # Candidate dedup & best-pick
│       ├── financial_candidate_resolver.py  # Complex candidate resolution
│       ├── financial_dataset_builder.py     # Structured dataset construction
│       ├── financial_controls.py            # Accounting sanity checks
│       ├── financial_normalizer.py          # Value normalization
│       │
│       │  # ── Mapping & Recovery ─────────────────────────────
│       ├── rcc_mapper.py                    # Dataset → 20 RCC fields
│       ├── native_financial_recovery.py     # Fallback: text-based extraction
│       ├── amount_parser.py                 # "1 234 567,00" → Decimal
│       ├── label_normalizer.py              # French label normalization
│       ├── markdown_financial_parser.py     # Parse markdown tables from GLM
│       │
│       │  # ── Infrastructure ─────────────────────────────────
│       ├── financial_job_store.py   # In-memory job store with TTL & pub/sub
│       ├── auth.py                  # Session authentication service
│       ├── dossier_store.py         # SQLite-backed dossier persistence
│       └── rcc_compliance.py        # Compliance rules engine
│
├── static/                          # Frontend assets
│   ├── index.html                   # Main single-page application
│   ├── style.css                    # Styling
│   ├── app.js                       # Client-side logic (upload, SSE, rendering)
│   └── js/                          # Modular JS components
│       ├── api.js                   # API client functions
│       ├── fields.js                # RCC field rendering
│       ├── modal.js                 # Modal dialogs
│       ├── util.js                  # Utility functions
│       └── views/                   # View components
│
├── scripts/
│   └── seed_demo.py                 # Generate demo dossiers for testing
│
└── tests/
    └── test_api_smoke.py            # API smoke tests
```

---

## Prerequisites

1. **Python 3.11+** — required for modern type hint syntax (`X | None`)
2. **Ollama** — must be installed and running locally with a vision model loaded
3. **Git** — for version control

### Install Ollama & Load the Model

```bash
# Install Ollama from https://ollama.ai
# Then pull the vision model:
ollama pull hf.co/unsloth/GLM-4.6V-Flash-GGUF:Q4_K_M

# Verify it's running:
curl http://localhost:11434/api/tags
```

> **Hardware Note:** GLM-4V requires a GPU with at least 6 GB VRAM for acceptable performance. CPU-only inference is possible but very slow (~5 min/page vs ~15 sec/page on GPU).

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

### Extraction Pipeline Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DIRECT_FINANCIAL_MODEL` | Same as vision model | Model for financial extraction |
| `DIRECT_FINANCIAL_TIMEOUT_SECONDS` | `300` | Per-page extraction timeout |
| `DIRECT_FINANCIAL_NUM_CTX` | `8192` | Context window size (tokens) |
| `DIRECT_FINANCIAL_NUM_PREDICT` | `4096` | Max output tokens per extraction |
| `DIRECT_FINANCIAL_KEEP_ALIVE` | `10m` | How long Ollama keeps the model loaded |
| `DIRECT_FINANCIAL_MAX_ATTEMPTS` | `2` | Retry attempts per page on failure |
| `DIRECT_FINANCIAL_RENDER_DPI` | `180` | PDF → PNG rendering quality |
| `DIRECT_FINANCIAL_MAX_PAGES` | `60` | Maximum pages to process per PDF |
| `DIRECT_FINANCIAL_PAGE_DELAY_SECONDS` | `0.5` | Delay between pages (GPU cooling) |
| `DIRECT_FINANCIAL_MAX_IMAGE_DIMENSION` | `1600` | Max image dimension in pixels |
| `DIRECT_FINANCIAL_REGION_FALLBACK` | `true` | Enable crop-and-retry fallback |
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
uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

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

The system verifies fundamental accounting identities:

| Control Code | Rule | Affected Fields |
|-------------|------|-----------------|
| `bilan_equilibre` | Total Assets ≈ Total Liabilities | TOTAL_ACTIF, TOTAL_PASSIF |
| `tresorerie_nette` | Cash Net = Cash Assets − Cash Liabilities | TRESORERIE_ACTIF, TRESORERIE_PASSIF |
| `resultat_exploitation` | Operating Result = Revenue − Expenses | PRODUITS_EXPLOITATION, CHARGES_EXPLOITATION |
| `resultat_financier` | Financial Result = Fin. Income − Fin. Charges | PRODUITS_FINANCIERS, CHARGES_FINANCIERES |
| `resultat_courant` | Current Result = Operating + Financial | RESULTAT_EXPLOITATION, RESULTAT_FINANCIER |
| `resultat_non_courant` | NC Result = NC Income − NC Charges | PRODUITS_NON_COURANTS, CHARGES_NON_COURANTES |
| `resultat_avant_impot` | Pre-tax = Current + Non-current | RESULTAT_COURANT, RESULTAT_NON_COURANT |
| `resultat_net` | Net = Pre-tax − Tax | RESULTAT_AVANT_IMPOT, IMPOT_SUR_RESULTATS |

Tolerance: `max(1.00 MAD, 0.01% of reference value)`

---

## Frontend (Web UI)

The web interface provides:

- **📤 Drag-and-drop upload** — drop a PDF or click to browse
- **📊 Real-time progress** — page-by-page extraction with a live progress bar via SSE
- **📋 Results table** — all 20 RCC fields with values, confidence badges, and status indicators
- **📄 JSON export** — copy results to clipboard for integration
- **🏢 Company info** — auto-detected company name and completeness score

### Technology

The frontend is built with **vanilla HTML, CSS, and JavaScript** — no framework dependencies. It communicates with the backend via:

- `fetch()` for REST API calls
- `EventSource` for SSE real-time progress

---

## Testing

### Run Smoke Tests

```bash
# Make sure the server is running first
uvicorn main:app --host 127.0.0.1 --port 8001

# In another terminal:
python -m pytest tests/ -v
```

The smoke tests in `tests/test_api_smoke.py` cover:
- Health check endpoint
- PDF upload and job creation
- Job status polling
- Result retrieval
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
| `504 Gateway Timeout` on first request | Ollama model cold-start | The app does a warmup ping — wait ~30s for the model to load |
| `Modèle introuvable sur Ollama` | Model not pulled | Run `ollama pull hf.co/unsloth/GLM-4.6V-Flash-GGUF:Q4_K_M` |
| 0 candidates on every page | Wrong model or low DPI | Check `DIRECT_FINANCIAL_MODEL` and try increasing `DIRECT_FINANCIAL_RENDER_DPI` |
| Very slow extraction (~5 min/page) | CPU-only Ollama | Use a GPU with ≥ 6 GB VRAM |
| `Fichier trop volumineux` | PDF exceeds limit | Increase `MAX_UPLOAD_MB` or split the PDF |
| `done_reason=length` errors | AI output truncated | Increase `DIRECT_FINANCIAL_NUM_PREDICT` (default 4096) |
| Balance sheet shows `conflicting` | Accounting check failed | The AI misread a value — analyst must correct it in the dossier |
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
- Frontend: Vanilla JS, no build step, ES module-style organization
- Naming: `snake_case` for Python, `camelCase` for JavaScript

---

## Authors

- **Wafabail Development Team**
- Built for the EKIP credit risk model integration

---

<p align="center">
  <em>Made with ❤️ for Moroccan financial document processing</em>
</p>
