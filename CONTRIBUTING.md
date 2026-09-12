# Contributing to Wafabail RCC

Thank you for considering contributing to Wafabail RCC! This document provides guidelines and instructions for contributing.

## Development Setup

### Prerequisites

- Python 3.11+
- Node.js 20+ with npm
- Ollama with GLM-4V model
- Git

### Quick Start

```bash
git clone https://github.com/ImiderAbdessamad/wafabail-rcc.git
cd wafabail-rcc
py -3 -m venv .venv
.venv\Scripts\activate       # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
# Optional but required for the full local hybrid OCR path:
pip install -r requirements-ocr.txt
copy .env.example .env       # Windows
# cp .env.example .env       # macOS/Linux

cd frontend
npm ci
npm run build
cd ..
```

## Branch Strategy

- `main` — stable production branch
- Feature branches — use descriptive names like `feature/add-export`, `fix/extraction-timeout`

## Pull Request Process

1. Create a branch from `main`
2. Make your changes following the code style guidelines below
3. Add/update tests if applicable
4. Ensure the application starts without errors
5. Run the focused suite: `python -m unittest tests/test_hybrid_pipeline.py tests/test_scoring_analysis.py tests/test_rcc_exports.py -v`
6. Run the API smoke suite: `python tests/test_api_smoke.py`
7. Build the frontend and run `npm audit`
8. Submit a PR with a clear description

## Code Style

### Python

- **PEP 8** compliant
- **Type hints** on all function signatures
- **French docstrings** for domain-specific modules (this is a Moroccan financial application)
- `snake_case` for variables, functions, and modules
- `PascalCase` for classes
- Use `from __future__ import annotations` for modern type syntax
- Prefer `async def` for I/O-bound operations

### Frontend (React)

- React 18 with functional components and hooks
- `camelCase` for variables/functions and `PascalCase` for components
- Keep API access centralized in `frontend/src/lib/api.js`
- Preserve accessible labels, focus states, reduced-motion behavior, and responsive layouts
- Run `npm run build`; FastAPI serves the generated `static/` bundle
- Do not edit hashed files in `static/assets/` by hand

### Data Models

- Use Pydantic v2 `BaseModel` for all API schemas
- Use `Field()` for validation constraints and descriptions
- Use `Literal` types for enums/string unions

## Architecture Decisions

### Why Ollama + GLM-4V?

- Runs locally when `OLLAMA_URL` targets localhost, reducing external data exposure
- GLM-4V has strong OCR capabilities for tables and structured documents
- Ollama provides a simple REST API for model inference
- A remote Ollama-compatible endpoint transmits rendered statement images and requires explicit privacy approval

### Why SQLite?

- Zero setup — ships with Python's standard library
- Single-process application behind Uvicorn
- Appropriate for the current single-worker architecture and internal validation workflow
- Easy to replace with PostgreSQL later if needed

### Why React + Vite?

- The analyst workflow contains synchronized dossier, evidence, compliance, audit, and export state
- Component boundaries keep the validation screen maintainable
- Vite provides a development proxy and deterministic production bundle
- PDF.js is lazy-loaded for controlled evidence rendering

## Reporting Issues

When reporting bugs, please include:

- Python version (`python --version`)
- Ollama version and loaded model
- Steps to reproduce
- Error logs from the server console
- The PDF file (if possible and not confidential)

Never upload customer statements, exports, `.env` files, databases, or credentials to a public issue.
