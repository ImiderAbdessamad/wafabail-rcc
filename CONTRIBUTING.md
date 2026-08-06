# Contributing to Wafabail RCC

Thank you for considering contributing to Wafabail RCC! This document provides guidelines and instructions for contributing.

## Development Setup

### Prerequisites

- Python 3.11+
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
copy .env.example .env       # Windows
# cp .env.example .env       # macOS/Linux
```

## Branch Strategy

- `main` — stable production branch
- Feature branches — use descriptive names like `feature/add-export`, `fix/extraction-timeout`

## Pull Request Process

1. Create a branch from `main`
2. Make your changes following the code style guidelines below
3. Add/update tests if applicable
4. Ensure the application starts without errors
5. Run the smoke tests: `python -m pytest tests/ -v`
6. Submit a PR with a clear description

## Code Style

### Python

- **PEP 8** compliant
- **Type hints** on all function signatures
- **French docstrings** for domain-specific modules (this is a Moroccan financial application)
- `snake_case` for variables, functions, and modules
- `PascalCase` for classes
- Use `from __future__ import annotations` for modern type syntax
- Prefer `async def` for I/O-bound operations

### Frontend (JavaScript)

- Vanilla JS (no frameworks)
- `camelCase` for variables and functions
- No build step — files are served directly
- ES module-style organization in `static/js/`

### Data Models

- Use Pydantic v2 `BaseModel` for all API schemas
- Use `Field()` for validation constraints and descriptions
- Use `Literal` types for enums/string unions

## Architecture Decisions

### Why Ollama + GLM-4V?

- Runs **locally** — no cloud API costs, no data privacy concerns
- GLM-4V has strong OCR capabilities for tables and structured documents
- Ollama provides a simple REST API for model inference

### Why SQLite?

- Zero setup — ships with Python's standard library
- Single-process application behind Uvicorn
- Adequate for the expected volume (dozens of dossiers/day, not thousands)
- Easy to replace with PostgreSQL later if needed

### Why Vanilla JS?

- Minimal frontend complexity
- No build toolchain required
- Fast iteration for a specialist internal tool
- Small bundle size

## Reporting Issues

When reporting bugs, please include:

- Python version (`python --version`)
- Ollama version and loaded model
- Steps to reproduce
- Error logs from the server console
- The PDF file (if possible and not confidential)
