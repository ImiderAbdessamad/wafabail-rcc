# Wafabail RCC

Hybrid OCR, financial-statement extraction, analyst validation, and auditable scoring ratios for Moroccan RCC credit workflows.

> **Current scope:** RCC and scoring only. CIN/ICE extraction is not part of this repository. Mistral OCR is not used. A final credit score is deliberately not calculated until Wafabail approves the official scoring policy.

## Start here

| I am… | Go to |
|---|---|
| An analyst validating a dossier | [Analyst workflow](#analyst-workflow) |
| A new developer | [Five-minute demo setup](#five-minute-demo-setup) |
| Setting up real OCR | [Enable the full hybrid OCR stack](#enable-the-full-hybrid-ocr-stack) |
| Integrating with the API | [API guide](#api-guide) |
| Reviewing architecture or controls | [Architecture](#architecture) and [Extraction pipeline](#extraction-pipeline) |
| Deploying the service | [Production deployment](#production-deployment) |
| Debugging an extraction | [Diagnostics and troubleshooting](#diagnostics-and-troubleshooting) |

## What this application does

Wafabail RCC accepts a Moroccan financial-statement PDF, analyzes its balance-sheet and income-statement pages, and produces:

- the 20 RCC fields required by the downstream EKIP data model;
- N and N-1 values when the source supports both periods;
- page-level evidence, OCR confidence, engine, extraction method, and original labels;
- accounting controls and RCC-specific consistency checks;
- 12 deterministic financial ratios derived from the same resolved dataset;
- an analyst validation dossier with corrections, verification actions, compliance blockers, and a complete audit trail;
- authenticated CSV and paginated PDF exports.

The application is intentionally human-in-the-loop. OCR values are never silently treated as final when evidence is missing, confidence is low, engines disagree, or accounting controls fail.

### At a glance

| Area | Current implementation |
|---|---|
| Input | PDF, maximum 50 MB by default |
| Primary use case | Moroccan liasse fiscale: bilan actif, bilan passif, CPC |
| Local OCR | RapidOCR |
| Table extraction | Docling / TableFormer |
| Vision-language extraction | GLM through an Ollama-compatible API |
| Persistence | SQLite plus stored source PDFs |
| UI | React 18, React Router, Vite, PDF.js |
| API | FastAPI with authenticated jobs, SSE progress, dossiers, audit, and exports |
| Exports | UTF-8 CSV and deterministic A4 PDF |
| Final decision | Human analyst |
| Final credit score | Disabled while policy is unapproved |

## Important boundaries

The repository does **not** provide:

- a guarantee that OCR is correct for every document;
- an approved final credit score or automated credit decision;
- a direct network connector that writes validated data into EKIP;
- Mistral OCR;
- CIN/ICE use-case code;
- multi-role RBAC;
- durable background jobs across process restarts.

In the current application, marking a dossier as validated records that RCC compliance permits downstream EKIP handoff. An external EKIP integration must be implemented and governed separately.

## Five-minute demo setup

The demo mode exercises authentication, the dashboard, PDF evidence review, corrections, compliance, audit, scoring surfaces, and exports without calling GLM, RapidOCR, or Docling.

### Prerequisites

- Git
- Python 3.11 or newer
- Node.js 20 or newer with npm

### Windows PowerShell

~~~powershell
git clone https://github.com/ImiderAbdessamad/wafabail-rcc.git
Set-Location wafabail-rcc

py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Copy-Item .env.example .env

Set-Location frontend
npm ci
npm run build
Set-Location ..

python scripts\seed_demo.py --reset
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8001
~~~

### macOS or Linux

~~~bash
git clone https://github.com/ImiderAbdessamad/wafabail-rcc.git
cd wafabail-rcc

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cp .env.example .env

cd frontend
npm ci
npm run build
cd ..

python scripts/seed_demo.py --reset
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8001
~~~

Open [http://127.0.0.1:8001](http://127.0.0.1:8001).

Development credentials from <code>.env.example</code>:

- username: <code>analyste@wafabail.ma</code>
- password: <code>wafabail2026</code>

> <code>scripts/seed_demo.py --reset</code> deletes existing dossiers in the configured local database before inserting demo records. Never run it against production data.

> The example password is for local development only. Replace it before exposing the application to any shared network.

## Enable the full hybrid OCR stack

Demo mode does not prove that OCR dependencies or the vision model are available. Install and verify the complete stack before processing real statements.

### 1. Install optional OCR dependencies

With the Python virtual environment activated:

~~~bash
python -m pip install -r requirements.txt -r requirements-ocr.txt
~~~

The optional stack is pinned in <code>requirements-ocr.txt</code>:

- RapidOCR for local OCR and orientation support;
- ONNX Runtime for RapidOCR inference;
- Docling for local document and table parsing.

Docling may download model assets the first time it runs. Plan for network access, disk space, and a slower first execution.

If optional dependencies are absent, the application degrades toward the existing GLM route instead of failing at import time. That fallback is useful for development but is not equivalent to the fully tested hybrid pipeline.

### 2. Install and start Ollama

Install Ollama from [ollama.com](https://ollama.com), then load the configured vision model.

~~~bash
ollama pull hf.co/unsloth/GLM-4.6V-Flash-GGUF:Q4_K_M
ollama list
ollama serve
~~~

The supplied <code>.env.example</code> points the application to:

~~~text
OLLAMA_URL=http://localhost:11434
DIRECT_FINANCIAL_MODEL=hf.co/unsloth/GLM-4.6V-Flash-GGUF:Q4_K_M
~~~

If your Ollama instance uses a different model name, update <code>OLLAMA_MODEL</code>, <code>OLLAMA_VISION_MODEL</code>, and <code>DIRECT_FINANCIAL_MODEL</code>.

### 3. Run a local diagnostic before using the UI

Place private test documents under the ignored <code>data/samples</code> directory.

~~~bash
python scripts/diagnose_hybrid_pipeline.py data/samples/statement.pdf --max-pages 4
python scripts/diagnose_docling_adapter.py data/samples/statement.pdf --max-pages 4
~~~

The diagnostics expose source classification, orientation, inversion correction, OCR text size, Docling tables, conservative candidates, and N/N-1 column roles.

### 4. Start the application

~~~bash
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8001
~~~

For frontend development, keep FastAPI running on port 8001 and run Vite in a second terminal:

~~~bash
cd frontend
npm run dev
~~~

Vite serves the UI on [http://127.0.0.1:5173](http://127.0.0.1:5173) and proxies <code>/api</code> to FastAPI on port 8001.

## Analyst workflow

### 1. Sign in

Open the application and sign in with the analyst account configured through <code>ANALYST_USERNAME</code> and <code>ANALYST_PASSWORD</code>.

The current version has one role: <em>valideur RCC</em>. Every correction and decision is attributed to the configured display name.

### 2. Import a statement

Open **Import & extraction OCR**, select a PDF, and optionally restrict the number of processed pages.

The server checks the filename extension, MIME type, PDF signature, upload-size limit, and configured maximum page count.

The job runs asynchronously. The UI follows authenticated Server-Sent Events and displays the current page, step, processed pages, skipped pages, and failures.

### 3. Review extraction quality first

Before editing figures, use the dossier synthesis to check:

- RCC completeness;
- mean OCR confidence;
- accounting-control coverage;
- blocking compliance rules;
- document source type;
- engines used;
- available scoring ratios.

Low completeness, conflicts, failed controls, or weak confidence require evidence review.

### 4. Compare each value with its evidence

The validation screen keeps the original PDF and RCC form together. Selecting a field opens its source page and evidence when available.

Evidence may contain the page, document section, source label and value, N/N-1 column role, confidence, engine, extraction method, orientation, and source excerpt.

Never correct a value from memory when the source document can be checked.

### 5. Correct or verify

- **Correct** when the OCR value is wrong.
- **Verify** when the OCR value is correct but requires human confirmation.
- **Escalate** when the source is ambiguous or the analyst cannot resolve a conflict.

The original OCR snapshot is never overwritten. Analyst overrides are stored separately with the original value, effective value, actor, and timestamp.

### 6. Resolve blockers

Validation is blocked until all server-side blocking rules pass. Typical actions:

- fill missing RCC fields;
- arbitrate conflicting engine candidates;
- verify or correct fields below the confidence threshold;
- resolve actionable accounting inconsistencies;
- attach a missing statement;
- confirm identity and exercise metadata.

### 7. Export

The dossier header exposes **CSV** for structured analysis and **Rapport PDF** for review, archive, or controlled sharing.

The browser waits for pending and in-flight analyst saves. If a correction cannot be persisted, the export is cancelled instead of downloading stale data.

### 8. Decide

- **Validate** only when the compliance report permits it.
- **Reject** with a mandatory reason.
- **Request arbitration** when senior review is required.

All decisions are appended to the audit trail.

## Architecture

~~~mermaid
flowchart LR
    USER["Analyst browser"] --> UI["React validation UI"]
    UI --> API["FastAPI API"]
    API --> AUTH["Session authentication"]
    API --> JOB["In-memory extraction job"]
    JOB --> RENDER["PDF render + native text"]
    RENDER --> ROUTER["Source, polarity, orientation, page routing"]
    ROUTER --> LOCAL["RapidOCR"]
    ROUTER --> TABLES["Docling tables"]
    ROUTER --> GLM["GLM via Ollama-compatible API"]
    LOCAL --> RESOLVE["Candidate resolver"]
    TABLES --> RESOLVE
    GLM --> RESOLVE
    RESOLVE --> CONTROLS["Accounting controls"]
    RESOLVE --> RCC["20 RCC fields"]
    RESOLVE --> RATIOS["12 scoring ratios"]
    CONTROLS --> DOSSIER["Compliance + analyst dossier"]
    RCC --> DOSSIER
    RATIOS --> DOSSIER
    DOSSIER --> DB["SQLite + stored source PDF"]
    DOSSIER --> EXPORTS["CSV + A4 PDF"]
~~~

### Design principles

1. **One extraction, multiple consumers.** RCC and scoring use the same resolved financial facts and provenance.
2. **Local-first processing.** RapidOCR and Docling run locally. GLM is local when <code>OLLAMA_URL</code> points to localhost.
3. **Evidence before confidence.** Every accepted candidate should retain its origin.
4. **Conservative resolution.** Ragged tables, unknown period columns, and equivalent-engine disagreements are rejected or marked conflicting rather than guessed.
5. **Immutable OCR source.** Human changes become auditable overrides.
6. **Server-authoritative compliance.** The browser displays decisions calculated by the backend.
7. **No fabricated score.** Ratios can be deterministic while the final policy remains unapproved.

## Extraction pipeline

### 1. Validation and rendering

The upload endpoint reads the PDF in bounded chunks, rejects unsupported input, and renders pages with PyMuPDF. Native text is retained for born-digital detection and recovery.

### 2. Document-source classification

The router classifies the document as <code>born_digital</code>, <code>hybrid</code>, or <code>image_only</code>.

Classification considers usable alphanumeric content and token density rather than raw character count alone, preventing noisy PDF text layers from being mistaken for reliable native text.

### 3. Polarity, contrast, and orientation

For scan-like pages, weak contrast and white-on-black polarity are corrected before semantic orientation OCR. The page is evaluated at 0°, 90°, 180°, and 270°.

PyMuPDF-rendered rotation metadata is not applied a second time.

### 4. Final local OCR pass

Orientation OCR is optimized for routing, not final numeric coverage. After normalization, a dedicated higher-resolution RapidOCR pass produces the text used for classification and evidence.

### 5. Page classification

Pages are classified as bilan actif, bilan passif, CPC, supporting pages, or non-financial pages. Skipped pages remain visible in the page audit.

### 6. Route-specific extraction

The router selects a strategy based on source quality:

- native text and Docling for born-digital documents;
- normalized raster PDF plus Docling for eligible scans;
- RapidOCR text for local evidence and routing;
- GLM vision for semantic extraction and recovery.

Common audit routes include <code>native_docling_glm</code>, <code>scan_local_ocr_glm</code>, and <code>hybrid_local_ocr_glm</code>.

### 7. Candidate normalization and resolution

Candidates from all engines are normalized into a shared financial dataset.

The resolver:

- normalizes Moroccan/French financial labels;
- parses spaces, commas, decimals, parentheses, and negative amounts;
- distinguishes N from N-1;
- preserves engine provenance;
- deduplicates compatible candidates;
- requires exact Docling row/header geometry;
- marks material engine disagreements as conflicts;
- avoids silently replacing missing values with zero.

### 8. Accounting controls

Controls are evaluated when their required inputs exist. A missing denominator or component produces <code>not_testable</code>, not a false pass or zero.

Core controls include Total Actif ≈ Total Passif, treasury consistency, and result consistency when the required CPC components are available.

Tolerance is the larger of 1 MAD or 0.01% of the reference value.

### 9. RCC mapping and scoring ratios

The resolved dataset is projected into the 20 RCC fields. The same dataset feeds scoring formulas, so no second OCR run can create a competing version of the facts.

### 10. Compliance and human validation

The server combines document availability, processed-page status, field completeness, identity and exercise metadata, accounting controls, RCC-specific identities, conflicts, low-confidence review, and audit presence.

Only a dossier with zero blocking rules can be validated.

For deeper implementation detail:

- [Hybrid OCR pipeline](docs/HYBRID_OCR_PIPELINE.md)
- [RCC and scoring review](docs/OCR_EXTRACTION_REVIEW_RCC_SCORING.md)

## RCC data contract

| # | Code | Label | Primary source |
|---:|---|---|---|
| 1 | <code>ACTIFS_IMMOBILISES</code> | Actifs immobilisés | Bilan actif |
| 2 | <code>TOTAL_BILAN</code> | Total bilan | Bilan actif |
| 3 | <code>CHIFFRE_AFFAIRES</code> | Chiffre d'affaires | CPC |
| 4 | <code>CA_EXPORT</code> | Chiffre d'affaires à l'export | CPC |
| 5 | <code>DETTES_BANCAIRES_MLT</code> | Dettes bancaires MLT | Bilan passif |
| 6 | <code>DETTES_BANCAIRES_CT</code> | Dettes bancaires CT | Bilan passif |
| 7 | <code>PASSIF_CIRCULANT</code> | Passif circulant | Bilan passif |
| 8 | <code>DETTES_FOURNISSEURS</code> | Dettes fournisseurs | Bilan passif |
| 9 | <code>COMPTE_COURANT_ASSOCIES</code> | Compte courant d'associés | Bilan |
| 10 | <code>TRESORERIE_PASSIF</code> | Trésorerie passif | Bilan passif |
| 11 | <code>ACTIF_CIRCULANT</code> | Actif circulant | Bilan actif |
| 12 | <code>CREANCES_CLIENTS</code> | Créances clients | Bilan actif |
| 13 | <code>TRESORERIE_ACTIF</code> | Trésorerie actif | Bilan actif |
| 14 | <code>CAISSE</code> | Caisse actif | Bilan actif |
| 15 | <code>ACHATS_REVENDUS</code> | Achats revendus | CPC |
| 16 | <code>ACHATS_CONSOMMES</code> | Achats consommés | CPC |
| 17 | <code>AUTRES_CHARGES_EXTERNES</code> | Autres charges externes | CPC |
| 18 | <code>CHARGES_INTERETS</code> | Charges d'intérêts | CPC |
| 19 | <code>RESULTAT_NET</code> | Résultat net | CPC |
| 20 | <code>TYPE_RESULTAT</code> | Bénéficiaire, déficitaire, or nul | Derived |

Each field can expose its effective N value, source N-1 value, status, confidence, document family, and evidence records.

## Scoring ratios and governance

The repository includes both the running scoring implementation and the original
functional/reference package. See [Scoring use case](use-case-Scoring/README.md)
for the exact mapping between the specifications, reference workbooks, API,
frontend, exports, and tests.

| Ratio | Formula |
|---|---|
| Autonomie financière | Fonds propres / Total bilan × 100 |
| Ratio d'endettement | Dettes financières / Fonds propres |
| Capacité de remboursement | Dettes financières / CAF |
| CAF / chiffre d'affaires | CAF / CA × 100 |
| Rentabilité commerciale | Résultat net / CA × 100 |
| Rentabilité financière | Résultat net / Fonds propres × 100 |
| Rentabilité économique | Résultat net / (Fonds propres + dettes financières) × 100 |
| Fonds de roulement / CA | FDR / CA × 100 |
| Trésorerie en jours de CA | Trésorerie nette / CA × 360 |
| Délai clients | Clients / CA × 360 |
| Délai fournisseurs | Fournisseurs / achats × 360 |
| Croissance du CA | (CA N − CA N-1) / CA N-1 × 100 |

Every ratio exposes its formula, source components, status, and provenance. Missing values, conflicts, and zero denominators produce <code>non_calculable</code>.

~~~json
{
  "policy_version": "reference_workbooks_v1_unapproved",
  "policy_status": "unapproved_reference",
  "score": null,
  "score_status": "not_computed_policy_unapproved"
}
~~~

The final score must remain disabled until Wafabail approves conversion scales, official weights, veto policy, policy versioning, and behavioural/bank-statement requirements.

## Dossier workflow and compliance

### Statuses

| Status | Meaning |
|---|---|
| <code>pending</code> | Extraction available and waiting for review |
| <code>validated</code> | Compliance permits downstream handoff |
| <code>rejected</code> | Rejected with a mandatory reason |
| <code>escalated</code> | Requires supervisor or senior arbitration |

### Compliance sections

| Section | Examples |
|---|---|
| Pièces obligatoires | PDF attached, OCR completed, pages processed |
| Complétude des champs RCC | All 20 fields, exercise date, company identity |
| Cohérence comptable | Balance and result controls |
| Cohérence des postes RCC | Total and containment identities recalculated on effective values |
| Contrôle RCC | No unresolved conflicts, low-confidence fields reviewed, audit present |

Corrections are evaluated immediately in the effective-value view. The OCR source remains immutable.

## CSV and PDF exports

### CSV

<code>GET /api/v1/rcc/dossiers/{id}/export.csv</code> returns UTF-8 with BOM, a semicolon delimiter, and a stable 21-column schema containing:

- metadata;
- RCC values for N and available N-1 periods;
- confidence and review status;
- page, engine, method, source label, and source value;
- accounting controls and compliance rules;
- financial ratios;
- page-level extraction audit;
- technical warnings.

### PDF

<code>GET /api/v1/rcc/dossiers/{id}/export.pdf</code> returns a deterministic A4 report with dossier identity, effective values, confidence, review state, controls, compliance, ratios, pipeline audit, repeated headers, and page numbering.

Both endpoints require an authenticated session and return <code>Cache-Control: no-store</code>.

## API guide

Interactive documentation:

- [Swagger UI](http://127.0.0.1:8001/docs)
- [ReDoc](http://127.0.0.1:8001/redoc)

### Authentication

| Method | Endpoint | Purpose |
|---|---|---|
| POST | <code>/api/v1/auth/login</code> | Create an HttpOnly analyst session |
| POST | <code>/api/v1/auth/logout</code> | Destroy the current session |
| GET | <code>/api/v1/auth/me</code> | Return the current analyst |

### RCC extraction jobs

| Method | Endpoint | Purpose |
|---|---|---|
| POST | <code>/api/v1/rcc/jobs</code> | Upload a PDF and queue extraction |
| GET | <code>/api/v1/rcc/jobs/{job_id}</code> | Read current progress |
| GET | <code>/api/v1/rcc/jobs/{job_id}/stream</code> | Authenticated SSE progress |
| GET | <code>/api/v1/rcc/jobs/{job_id}/result</code> | Return the completed RCC result |

The multipart upload accepts required <code>file</code> and optional <code>max_pages</code>.

### Scoring facade

| Method | Endpoint | Purpose |
|---|---|---|
| POST | <code>/api/v1/scoring/jobs</code> | Create the same shared extraction job |
| GET | <code>/api/v1/scoring/jobs/{job_id}</code> | Read progress with scoring URLs |
| GET | <code>/api/v1/scoring/jobs/{job_id}/stream</code> | Scoring SSE facade |
| GET | <code>/api/v1/scoring/jobs/{job_id}/result</code> | Return ratios, controls, provenance, and warnings |

The scoring facade does not execute a second OCR pass.

### Dossiers and audit

| Method | Endpoint | Purpose |
|---|---|---|
| GET | <code>/api/v1/rcc/dossiers</code> | List, search, filter, and paginate |
| POST | <code>/api/v1/rcc/dossiers</code> | Create from a completed job |
| GET | <code>/api/v1/rcc/dossiers/{id}</code> | Dossier, compliance, effective values |
| PATCH | <code>/api/v1/rcc/dossiers/{id}</code> | Validate, reject, or escalate |
| PUT | <code>/api/v1/rcc/dossiers/{id}/overrides</code> | Save corrections or verification |
| GET | <code>/api/v1/rcc/dossiers/{id}/overrides</code> | Read correction history |
| GET | <code>/api/v1/rcc/dossiers/{id}/audit</code> | Read dossier audit |
| GET | <code>/api/v1/rcc/dossiers/{id}/file</code> | Display original PDF |
| GET | <code>/api/v1/rcc/dossiers/{id}/export.csv</code> | Download CSV |
| GET | <code>/api/v1/rcc/dossiers/{id}/export.pdf</code> | Download report |
| POST | <code>/api/v1/rcc/dossiers/{id}/attach</code> | Attach a completed extraction |
| GET | <code>/api/v1/rcc/audit</code> | Read global audit |

List filters are <code>status</code>, <code>search</code>, <code>limit</code>, and <code>offset</code>.

### System

| Method | Endpoint | Authentication | Purpose |
|---|---|---|---|
| GET | <code>/health</code> | No | Health response |
| GET | <code>/</code> | No | Built React application |

### Minimal command-line flow

The examples use a cookie jar and POSIX quoting. On Windows, use <code>curl.exe</code> and adapt JSON quoting if necessary.

~~~bash
curl -c cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"username":"analyste@wafabail.ma","password":"wafabail2026"}' \
  http://127.0.0.1:8001/api/v1/auth/login

curl -b cookies.txt \
  -F "file=@data/samples/statement.pdf" \
  -F "max_pages=4" \
  http://127.0.0.1:8001/api/v1/rcc/jobs

curl -b cookies.txt \
  http://127.0.0.1:8001/api/v1/rcc/jobs/JOB_ID

curl -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"job_id":"JOB_ID"}' \
  http://127.0.0.1:8001/api/v1/rcc/dossiers
~~~

### Common HTTP responses

| Status | Meaning |
|---:|---|
| 401 | Missing or expired analyst session |
| 404 | Job/dossier missing or in-memory job expired |
| 409 | Result not ready or validation blocked |
| 413 | Upload exceeds the configured limit |
| 422 | Invalid PDF/request, failed job, or missing rejection reason |

## Configuration reference

Copy <code>.env.example</code> to <code>.env</code>. The local file is ignored by Git and must never be committed.

### Model and upload

| Variable | Code default | Purpose |
|---|---|---|
| <code>OLLAMA_URL</code> | <code>http://localhost:11434</code> | Ollama-compatible base URL |
| <code>OLLAMA_MODEL</code> | <code>glm4v</code> | General fallback |
| <code>OLLAMA_VISION_MODEL</code> | OLLAMA_MODEL | Vision fallback |
| <code>OLLAMA_MAPPING_MODEL</code> | <code>qwen3:8b</code> | Candidate-resolution mapping alias |
| <code>OLLAMA_TIMEOUT</code> | <code>300</code> | Base timeout in seconds |
| <code>ALLOWED_ORIGINS</code> | <code>*</code> | Comma-separated CORS origins |
| <code>MAX_UPLOAD_MB</code> | <code>50</code> | Upload-size limit |

### Financial extraction

| Variable | Default | Purpose |
|---|---:|---|
| <code>DIRECT_FINANCIAL_MODEL</code> | Vision fallback | GLM financial model |
| <code>DIRECT_FINANCIAL_TIMEOUT_SECONDS</code> | 300 | Request timeout |
| <code>DIRECT_FINANCIAL_NUM_CTX</code> | 8192 | Context size |
| <code>DIRECT_FINANCIAL_NUM_PREDICT</code> | 4096 | Maximum generated tokens |
| <code>DIRECT_FINANCIAL_KEEP_ALIVE</code> | <code>10m</code> | Ollama keep-alive |
| <code>DIRECT_FINANCIAL_MAX_ATTEMPTS</code> | 2 | Extraction attempts |
| <code>DIRECT_FINANCIAL_RENDER_DPI</code> | 240 | PDF render DPI |
| <code>DIRECT_FINANCIAL_CLASSIFY_DPI</code> | 72 | Classification DPI |
| <code>DIRECT_FINANCIAL_MAX_PAGES</code> | 60 | Maximum pages |
| <code>DIRECT_FINANCIAL_PAGE_DELAY_SECONDS</code> | 0.5 | Delay between page calls |
| <code>DIRECT_FINANCIAL_MAX_IMAGE_DIMENSION</code> | 2600 | Maximum GLM image dimension |
| <code>DIRECT_FINANCIAL_REGION_FALLBACK</code> | true | Region-level fallback |
| <code>DIRECT_FINANCIAL_JOB_TTL_MINUTES</code> | 60 | In-memory job retention |

### Hybrid local pipeline

| Variable | Default | Purpose |
|---|---:|---|
| <code>HYBRID_FINANCIAL_PIPELINE</code> | true | Source-aware routing |
| <code>LOCAL_OCR_ENABLED</code> | true | RapidOCR |
| <code>DOCLING_ENABLED</code> | true | Docling extraction |
| <code>DOCLING_SCAN_ENABLED</code> | true | Normalized scan PDF for Docling |
| <code>NATIVE_TEXT_MIN_CHARS</code> | 80 | Native-text threshold |
| <code>ORIENTATION_OCR_MAX_SIDE</code> | 1400 | Orientation OCR size |
| <code>LOCAL_OCR_TEXT_MAX_SIDE</code> | 2200 | Final local OCR size |
| <code>CLASSIFICATION_MAX_IMAGE_DIMENSION</code> | 1200 | Classification image size |
| <code>DOCLING_MAX_PAGES</code> | 20 | Docling page limit |

### Persistence and authentication

| Variable | Default | Purpose |
|---|---|---|
| <code>DATA_DIR</code> | <code>data</code> | Runtime-data root |
| <code>DB_PATH</code> | <code>data/wafabail_rcc.sqlite3</code> | SQLite database |
| <code>PDF_STORAGE_DIR</code> | <code>data/pdfs</code> | Stored source PDFs |
| <code>ANALYST_USERNAME</code> | <code>analyste@wafabail.ma</code> | Username |
| <code>ANALYST_PASSWORD</code> | <code>wafabail2026</code> | Development password; override in production |
| <code>ANALYST_DISPLAY_NAME</code> | <code>Analyste RCC</code> | Audit actor |
| <code>SESSION_COOKIE_NAME</code> | <code>wb_rcc_session</code> | HttpOnly cookie |
| <code>SESSION_TTL_HOURS</code> | 12 | Session lifetime |
| <code>SESSION_COOKIE_SECURE</code> | false | HTTPS-only cookie |
| <code>CONFIDENCE_REVIEW_THRESHOLD</code> | 0.8 | Human-review threshold |

## Data, privacy, and security

The following are intentionally excluded from Git:

- <code>.env</code> and environment variants;
- <code>data/</code>, including SQLite, stored PDFs, and private samples;
- virtual environments and <code>node_modules</code>;
- logs, caches, temporary files, and root-level PDFs.

### Remote model warning

When <code>OLLAMA_URL</code> points to localhost, GLM requests stay on the local Ollama service.

When it points to NiceGPU or another remote Ollama-compatible host, rendered statement images and prompts are transmitted to that endpoint. Dummy-document approval does not authorize production customer statements.

Before remote processing, confirm endpoint ownership, region, encryption, retention, training policy, access control, logging, incident response, and formal organizational authorization.

### Security checklist

- Replace development credentials.
- Use HTTPS and <code>SESSION_COOKIE_SECURE=true</code>.
- Restrict <code>ALLOWED_ORIGINS</code>.
- Limit network and filesystem access.
- Encrypt and back up the database and source PDFs according to policy.
- Never log page images, credentials, session tokens, or unnecessary financial values.
- Treat CSV/PDF exports as sensitive documents.

## Runtime and storage model

- Completed dossiers, overrides, sessions, and audit events persist in SQLite.
- Source PDFs persist under <code>PDF_STORAGE_DIR</code>.
- Active extraction jobs live in application memory and expire after the configured TTL.
- A process restart loses active jobs but not completed dossiers.
- Extraction is serialized by a process-local lock.

Run **one Uvicorn worker** unless the job architecture is replaced with a shared queue and shared state store.

## Testing and quality gates

### Focused regression suite

~~~bash
python -m unittest tests/test_hybrid_pipeline.py tests/test_scoring_analysis.py tests/test_rcc_exports.py -v
~~~

Coverage includes source routing, orientation, inverted pages, final OCR, normalized PDFs, strict Docling geometry, N/N-1, provenance, conflicts, scoring edge cases, CSV, and PDF.

### API smoke suite

~~~bash
python tests/test_api_smoke.py
~~~

This uses FastAPI TestClient and temporary storage. It does **not** require Ollama or a running Uvicorn server.

It covers authentication, dossiers, idempotency, overrides, verification, compliance, decisions, audit, search, source-PDF delivery, scoring, exports, and logout protection.

### Frontend and dependencies

~~~bash
cd frontend
npm ci
npm run build
npm audit
~~~

~~~bash
python -m pip check
~~~

### Manual acceptance checklist

- Import one born-digital statement and one scan.
- Include a rotated or inverted test page.
- Confirm routes and orientations in the audit.
- Compare every weak/conflicting value with the PDF.
- Confirm N/N-1 on a balance sheet and CPC.
- Verify failed saves prevent export.
- Open the CSV in the target spreadsheet application.
- Visually inspect every PDF export page.
- Confirm blockers prevent validation.
- Confirm final score remains null.

## Production deployment

This repository does not include a Docker image or managed job queue.

### Build

~~~bash
python -m pip install -r requirements.txt -r requirements-ocr.txt
cd frontend
npm ci
npm run build
cd ..
~~~

### Run

~~~bash
python -m uvicorn main:app --host 0.0.0.0 --port 8001 --workers 1 --proxy-headers
~~~

### Reverse proxy requirements

- Terminate HTTPS.
- Forward original protocol/client information.
- Disable buffering for SSE.
- Use OCR-compatible timeouts.
- Match upload limits to <code>MAX_UPLOAD_MB</code>.
- Restrict access to intended Wafabail users/networks.

Persist and back up <code>DB_PATH</code> and <code>PDF_STORAGE_DIR</code>.

### Pre-production checklist

- [ ] Credentials stored outside Git
- [ ] HTTPS and secure cookies enabled
- [ ] CORS restricted
- [ ] Model endpoint approved
- [ ] RapidOCR and Docling verified
- [ ] Persistent storage mounted and backed up
- [ ] One-worker limitation accepted or queue implemented
- [ ] SSE buffering disabled
- [ ] Representative RCC corpus passed
- [ ] CSV/PDF approved by business users
- [ ] Scoring limitation accepted
- [ ] EKIP integration behavior defined
- [ ] Privacy and retention approval recorded

## Diagnostics and troubleshooting

| Symptom | Checks and action |
|---|---|
| UI is blank or 404 | Run <code>npm ci</code> and <code>npm run build</code> in <code>frontend</code> |
| Login fails | Check <code>.env</code> and restart FastAPI |
| API returns 401 | Sign in again and verify cookie/HTTPS configuration |
| Ollama connection refused | Run <code>ollama serve</code> and check <code>OLLAMA_URL</code> |
| Model not found | Check <code>ollama list</code> and model environment variables |
| RapidOCR/Docling unavailable | Activate the intended venv and install <code>requirements-ocr.txt</code> |
| First Docling run is slow | Allow model download/warm-up |
| Scan remains unreadable | Inspect polarity, orientation, OCR characters, and route |
| N/N-1 values shift | Inspect header roles; reject ragged/unknown geometry |
| Engines disagree | Keep the field conflicting and arbitrate against the PDF |
| Job 404 after restart | Active jobs are in memory; restart extraction |
| Job waits behind another | Extraction is serialized |
| SSE arrives in one block | Disable reverse-proxy buffering |
| Validation returns 409 | Resolve every blocking compliance rule |
| Export has an old value | Check for failed-save messages; exports require persisted state |
| Score is null | Expected while policy is unapproved |

~~~bash
python scripts/diagnose_hybrid_pipeline.py data/samples/statement.pdf --max-pages 4 --dpi 180
python scripts/diagnose_docling_adapter.py data/samples/statement.pdf --max-pages 4
~~~

Do not attach confidential PDFs to public GitHub issues.

## Project structure

~~~text
wafabail-rcc/
├── app/
│   ├── routers/                  # Auth, jobs, scoring, dossiers
│   ├── schemas/                  # Pydantic contracts
│   ├── services/                 # OCR, resolution, controls, exports
│   ├── config.py
│   └── db.py
├── frontend/
│   ├── src/                      # React analyst application
│   ├── package.json
│   └── vite.config.js
├── static/                       # Production frontend bundle
├── scripts/                      # Demo and diagnostics
├── tests/                        # Regression and API smoke tests
├── docs/                         # Detailed technical documentation
├── use-case-Scoring/             # Scoring specifications and reference models
├── data/                         # Ignored runtime/private data
├── .env.example
├── requirements.txt
├── requirements-ocr.txt
└── main.py
~~~

## Team contribution guide

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

Minimum pull-request expectations:

- explain functional impact;
- identify affected routes, schemas, and screens;
- add regression tests for extraction/financial logic;
- preserve evidence and audit behavior;
- never weaken conflict handling to improve apparent completeness;
- never add external document transmission without privacy approval;
- update documentation when contracts change;
- build the frontend;
- run tests, API smoke, <code>pip check</code>, and <code>npm audit</code>;
- confirm no environment file, database, PDF, export, or customer data is staged.

Recommended branches: <code>feature/...</code>, <code>fix/...</code>, <code>docs/...</code>, and <code>test/...</code>.

### Definition of done

1. Implementation and API contract agree.
2. Analyst workflow remains understandable.
3. Weak/conflicting values remain visible.
4. Financial edge cases have tests.
5. Documentation is updated.
6. Privacy implications are documented.
7. Verification gates pass.

## Repository publication notes

- Keep private samples under ignored <code>data/samples</code>.
- The committed <code>static</code> directory is the deployable frontend used by FastAPI.
- Production source maps are disabled.
- No public open-source license is declared. Treat the code as internal/proprietary until the owner adds an explicit license.

## Further documentation

- [Hybrid OCR pipeline](docs/HYBRID_OCR_PIPELINE.md)
- [RCC and scoring technical review](docs/OCR_EXTRACTION_REVIEW_RCC_SCORING.md)
- [Scoring use case and implementation map](use-case-Scoring/README.md)
- [Contribution standards](CONTRIBUTING.md)

---

**Wafabail RCC** is a decision-support and validation system. It improves extraction consistency and auditability; it does not replace the analyst's responsibility to verify financial statements and make the authorized business decision.
