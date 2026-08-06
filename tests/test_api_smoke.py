"""Recette de bout en bout de l'API RCC, sans Ollama.

Un job d'extraction terminé est injecté directement dans le job store, ce qui
permet de vérifier auth, dossiers, corrections, conformité et piste d'audit
sans dépendre du modèle de vision.

    python tests/test_api_smoke.py
"""
import os, shutil, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = tempfile.mkdtemp(prefix="wbrcc_")
os.environ["DATA_DIR"] = TMP
os.environ["DB_PATH"] = os.path.join(TMP, "t.sqlite3")
os.environ["PDF_STORAGE_DIR"] = os.path.join(TMP, "pdfs")

from fastapi.testclient import TestClient
from main import app
from app.services.financial_job_store import job_store
from app.schemas.rcc import RccAnalysisResult, RccField, FieldEvidence, AccountingControlView, RCC_ELEMENTS
from app.schemas.direct_financial_extraction import DocumentSummary, ExtractionSummary, CompanyInfo, ExerciseInfo

def make_result():
    fields = []
    for num, code, label, src in RCC_ELEMENTS:
        if code == "TYPE_RESULTAT":
            fields.append(RccField(number=num, code=code, label=label, source=src,
                                   status="derived", note="Bénéficiaire", confidence=0.95))
            continue
        conf = 0.6 if code == "CA_EXPORT" else 0.93
        status = "conflicting" if code == "PASSIF_CIRCULANT" else "confirmed"
        val = None if status == "conflicting" else float(1000 * num)
        fields.append(RccField(
            number=num, code=code, label=label, source=src, value=val,
            status=status, confidence=0.0 if status == "conflicting" else conf,
            value_n1=float(900 * num) if code in {"CHIFFRE_AFFAIRES", "TOTAL_BILAN"} else None,
            evidence=[FieldEvidence(page_number=1 + num % 3, raw_label=label.upper(),
                                    raw_value=str(1000 * num), column_name="NET_N",
                                    page_type="BILAN_ACTIF", confidence=conf)],
        ))
    return RccAnalysisResult(
        document=DocumentSummary(filename="Bilan_TEST_2025.pdf", pages_total=4, pages_processed=4,
                                 pages_skipped=0, pages_failed=0,
                                 company=CompanyInfo(raison_sociale="SOTRAMEX INDUSTRIE SARL",
                                                     ice="001874552000073", ville="Casablanca"),
                                 exercise=ExerciseInfo(debut="01/01/2025", fin="31/12/2025")),
        extraction=ExtractionSummary(model="glm4v"),
        fields=fields, completeness_pct=95.0, warnings=["Page 3 partiellement lue"],
        controls=[
            AccountingControlView(code="bilan_equilibre", status="failed", label="Total Actif = Total Passif",
                                  expected=100.0, observed=140.0, difference=40.0,
                                  affected_fields=["TOTAL_BILAN"], message="Déséquilibre"),
            AccountingControlView(code="resultat_net", status="passed", label="RN cohérent",
                                  expected=19000.0, observed=19000.0, difference=0.0, message="ok"),
        ],
    )

c = TestClient(app)
FAILURES = 0


def ok(name, cond, extra=""):
    """Assertion non fatale : le script rend un résumé complet en une passe."""
    global FAILURES
    if not cond:
        FAILURES += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(extra) if not cond else ""))

# --- auth
r = c.get("/api/v1/rcc/dossiers"); ok("401 sans session", r.status_code == 401, r.status_code)
r = c.post("/api/v1/auth/login", json={"username": "x@y.z", "password": "bad"})
ok("401 mauvais identifiants", r.status_code == 401, r.status_code)
r = c.post("/api/v1/auth/login", json={"username": "analyste@wafabail.ma", "password": "wafabail2026"})
ok("login ok", r.status_code == 200, r.text)
ok("me ok", c.get("/api/v1/auth/me").status_code == 200)

# --- liste vide
r = c.get("/api/v1/rcc/dossiers"); ok("liste vide", r.status_code == 200 and r.json()["total"] == 0, r.text[:200])

# --- job fictif terminé
job = job_store.create(pdf_bytes=b"%PDF-1.4 fake", filename="Bilan_TEST_2025.pdf")
from app.services.dossier_store import store_pdf
store_pdf(job.job_id, b"%PDF-1.4 fake")
job_store.update(job.job_id, status="completed", result=make_result())

r = c.post("/api/v1/rcc/dossiers", json={"job_id": job.job_id, "credit_amount": 4200000, "sector": "Industrie"})
ok("création dossier", r.status_code == 201, r.text[:400])
d = r.json(); did = d["dossier"]["id"]
ok("id métier RCC-", did.startswith("RCC-"), did)
ok("nom repris de l'OCR", d["dossier"]["client_name"] == "SOTRAMEX INDUSTRIE SARL", d["dossier"]["client_name"])
ok("ICE repris", d["dossier"]["ice"] == "001874552000073")
ok("exercice repris", d["dossier"]["exercice_date"] == "31/12/2025", d["dossier"]["exercice_date"])
ok("has_document", d["dossier"]["has_document"] is True)
comp = d["compliance"]
ok("bloqué par contrôle échoué", comp["can_validate"] is False and comp["blockers"] > 0, comp["blockers"])
ok("conflicting détecté", "PASSIF_CIRCULANT" in comp["conflicting_fields"], comp["conflicting_fields"])
ok("confiance faible détectée", "CA_EXPORT" in comp["low_confidence_fields"], comp["low_confidence_fields"])
ok("sections conformité", len(comp["sections"]) == 5, [s["title"] for s in comp["sections"]])

# --- idempotence création
r2 = c.post("/api/v1/rcc/dossiers", json={"job_id": job.job_id})
ok("création idempotente", r2.status_code == 201 and r2.json()["dossier"]["id"] == did, r2.status_code)

# --- validation bloquée
r = c.patch(f"/api/v1/rcc/dossiers/{did}", json={"status": "validated"})
ok("validation refusée (409)", r.status_code == 409, r.status_code)

# --- rejet sans motif
r = c.patch(f"/api/v1/rcc/dossiers/{did}", json={"status": "rejected"})
ok("rejet sans motif refusé (422)", r.status_code == 422, r.status_code)

# --- overrides
r = c.put(f"/api/v1/rcc/dossiers/{did}/overrides", json={"overrides": [
    {"field_code": "CA_EXPORT", "corrected_value": 4210000},
    {"field_code": "PASSIF_CIRCULANT", "corrected_value": 9940000},
]})
ok("overrides enregistrés", r.status_code == 200, r.text[:300])
body = r.json()
ov = {o["field_code"]: o for o in body["dossier"]["overrides"]}
ok("original_value = valeur OCR", ov["CA_EXPORT"]["original_value"] == 4000.0, ov["CA_EXPORT"])
ok("valeur effective corrigée", body["effective_values"]["CA_EXPORT"] == 4210000)
ok("conflit levé par correction", "PASSIF_CIRCULANT" not in body["compliance"]["conflicting_fields"])
ok("confiance faible levée", "CA_EXPORT" not in body["compliance"]["low_confidence_fields"])

# --- l'OCR n'est jamais écrasé
r = c.get(f"/api/v1/rcc/dossiers/{did}")
snap = {f["code"]: f for f in r.json()["dossier"]["result"]["fields"]}
ok("valeur OCR intacte", snap["CA_EXPORT"]["value"] == 4000.0, snap["CA_EXPORT"]["value"])
ok("evidence exposée", len(snap["CA_EXPORT"]["evidence"]) == 1, snap["CA_EXPORT"]["evidence"])
ok("N-1 exposé", snap["CHIFFRE_AFFAIRES"]["value_n1"] == 2700.0, snap["CHIFFRE_AFFAIRES"]["value_n1"])
ok("controls exposés", len(r.json()["dossier"]["result"]["controls"]) == 2)

# --- correction ramenée à la valeur OCR = suppression de la surcharge
r = c.put(f"/api/v1/rcc/dossiers/{did}/overrides", json={"overrides": [{"field_code": "CA_EXPORT", "corrected_value": 4000}]})
ok("retour valeur OCR supprime l'override",
   all(o["field_code"] != "CA_EXPORT" for o in r.json()["dossier"]["overrides"]),
   r.json()["dossier"]["overrides"])

# --- audit
r = c.get(f"/api/v1/rcc/dossiers/{did}/audit")
items = r.json()["items"]
ok("audit contient ingestion", any(i["action"] == "Ingestion" for i in items), [i["action"] for i in items])
ok("audit contient correction", any(i["action"] == "Correction" for i in items), [i["action"] for i in items])
ok("audit avec contexte dossier", all(i["dossier_id"] == did and i["client_name"] for i in items))
ok("audit antéchronologique", items == sorted(items, key=lambda i: i["timestamp"], reverse=True))


# --- confirmation d'un poste à faible confiance (sans modification de valeur)
r = c.put(f"/api/v1/rcc/dossiers/{did}/overrides", json={"overrides": [
    {"field_code": "CA_EXPORT", "corrected_value": None, "verified": True}]})
b = r.json()
ov2 = {o["field_code"]: o for o in b["dossier"]["overrides"]}
ok("verified enregistré", ov2.get("CA_EXPORT", {}).get("verified") is True, ov2.get("CA_EXPORT"))
ok("verified conserve la valeur OCR", b["effective_values"]["CA_EXPORT"] == 4000.0, b["effective_values"]["CA_EXPORT"])
ok("verified lève la confiance faible", "CA_EXPORT" not in b["compliance"]["low_confidence_fields"], b["compliance"]["low_confidence_fields"])
r = c.get(f"/api/v1/rcc/dossiers/{did}/audit")
ok("audit distingue Vérification", any(i["action"] == "Vérification" for i in r.json()["items"]),
   [i["action"] for i in r.json()["items"]])

# --- un contrôle échoué se lève après reprise des postes actionnables
r = c.get(f"/api/v1/rcc/dossiers/{did}")
coh = [s for s in r.json()["compliance"]["sections"] if s["title"] == "Cohérence comptable"][0]
bal = [x for x in coh["rules"] if "Actif" in x["label"]][0]
ok("contrôle non corrigeable = non bloquant", bal["blocking"] is False, bal)

# --- rejet avec motif
r = c.patch(f"/api/v1/rcc/dossiers/{did}", json={"status": "rejected", "motif": "Incohérence comptable non résolue", "comment": "Passif tronqué p.2"})
ok("rejet avec motif", r.status_code == 200 and r.json()["dossier"]["status"] == "rejected", r.text[:200])
ok("décideur tracé", r.json()["dossier"]["decided_by"] == "Analyste RCC")
r = c.get("/api/v1/rcc/audit")
ok("journal global contient le rejet", any(i["action"] == "Rejet" for i in r.json()["items"]))

# --- filtres & recherche
ok("filtre rejected", c.get("/api/v1/rcc/dossiers?status=rejected").json()["total"] == 1)
ok("filtre pending", c.get("/api/v1/rcc/dossiers?status=pending").json()["total"] == 0)
ok("recherche raison sociale", c.get("/api/v1/rcc/dossiers?search=sotramex").json()["total"] == 1)
ok("recherche ICE", c.get("/api/v1/rcc/dossiers?search=001874552").json()["total"] == 1)
ok("recherche vide", c.get("/api/v1/rcc/dossiers?search=zzzz").json()["total"] == 0)
cnt = c.get("/api/v1/rcc/dossiers").json()["counts"]
ok("compteurs par statut", cnt["rejected"] == 1 and cnt["all"] == 1, cnt)

# --- PDF
r = c.get(f"/api/v1/rcc/dossiers/{did}/file")
ok("PDF servi", r.status_code == 200 and r.content.startswith(b"%PDF"), r.status_code)
ok("404 dossier inconnu", c.get("/api/v1/rcc/dossiers/RCC-1900-0001").status_code == 404)

# --- logout
c.post("/api/v1/auth/logout")
ok("401 après logout", c.get("/api/v1/rcc/dossiers").status_code == 401)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{FAILURES} échec(s)." if FAILURES else "\nToutes les vérifications passent.")

sys.exit(1 if FAILURES else 0)
