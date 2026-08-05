# Wafabail RCC

API FastAPI d'extraction bilancielle pour enrichir le modèle **EKIP**.

Pipeline identique à `projet-wafabail-ocr` (`financial-documents` / GLM Vision),
mais la réponse ne contient **que** les postes RCC métier.

## Champs retournés

| # | Code | Label |
|---|------|-------|
| 1 | `ACTIFS_IMMOBILISES` | Actifs immobilisés |
| 2 | `TOTAL_BILAN` | Total bilan |
| 3 | `CHIFFRE_AFFAIRES` | Chiffre d'affaires |
| 4 | `CA_EXPORT` | Chiffre d'affaires à l'export |
| 5 | `DETTES_BANCAIRES_MLT` | Dettes bancaires MLT |
| 6 | `DETTES_BANCAIRES_CT` | Dettes bancaires CT |
| 7 | `PASSIF_CIRCULANT` | Passif circulant |
| 8 | `DETTES_FOURNISSEURS` | Dettes fournisseurs |
| 9 | `COMPTE_COURANT_ASSOCIES` | Compte courant d'associés |
| 10 | `TRESORERIE_PASSIF` | Trésorerie passif |
| 11 | `ACTIF_CIRCULANT` | Actif circulant |
| 12 | `CREANCES_CLIENTS` | Créances clients |
| 13 | `TRESORERIE_ACTIF` | Trésorerie actif |
| 14 | `CAISSE` | Caisse actif |
| 15 | `ACHATS_REVENDUS` | Achats revendus |
| 16 | `ACHATS_CONSOMMES` | Achats consommés |
| 17 | `AUTRES_CHARGES_EXTERNES` | Autres charges externes |
| 18 | `CHARGES_INTERETS` | Charges d'intérêts |
| 19 | `RESULTAT_NET` | Résultat net |
| 20 | `TYPE_RESULTAT` | Type de résultat (Bénéficiaire / Déficitaire / Nul) |

## Installation

```bash
cd wafabail-rcc
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Éditez `.env` (`OLLAMA_URL`, modèles GLM).

## Lancement

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

- UI : http://127.0.0.1:8001/
- OpenAPI : http://127.0.0.1:8001/docs
- Santé : http://127.0.0.1:8001/health

## Endpoints

| Méthode | URL | Description |
|---------|-----|-------------|
| `POST` | `/api/v1/rcc/jobs` | Créer un job (PDF liasse) |
| `GET` | `/api/v1/rcc/jobs/{id}` | Statut |
| `GET` | `/api/v1/rcc/jobs/{id}/stream` | Progression SSE |
| `GET` | `/api/v1/rcc/jobs/{id}/result` | Résultat RCC |

## Exemple de résultat

```json
{
  "document": { "filename": "liasse.pdf", "pages_total": 12, "...": "..." },
  "extraction": { "model": "...", "page_audit": [] },
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
      "confidence": 0.9
    }
  ],
  "completeness_pct": 85.0,
  "warnings": []
}
```

`TYPE_RESULTAT` : `value` est toujours `null` ; la valeur métier est dans `note`
(`Bénéficiaire` / `Déficitaire` / `Nul`).
