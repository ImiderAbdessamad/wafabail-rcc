"""Recette de la projection moteur v10 → réponse API, sans Ollama.

Des lignes d'évidence sont fabriquées à la main, puis passées dans les vraies
fonctions du moteur (`resolve_rcc`, `run_controls`) et dans l'adaptateur
(`build_result`). On vérifie ainsi le contrat rendu à l'interface — postes,
statuts, preuves, N-1, contrôles — sans dépendre des modèles de vision.

    python tests/test_rcc_projection.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import ocr_lab_core_v10 as engine
from app.services.rcc_lab_pipeline import build_result

FAILURES = 0


def ok(name, cond, extra=""):
    """Assertion non fatale : le script rend un résumé complet en une passe."""
    global FAILURES
    if not cond:
        FAILURES += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(extra) if not cond else ""))


def row(page, page_type, code, label, cells, *, confidence=0.95, source="glm_ocr_cell_vision",
        notes=("row_present=true",)):
    return engine.EvidenceRow(
        page, page_type, source, code, label, dict(cells),
        confidence, 0, None, list(notes),
    )


# --- Liasse synthétique cohérente -------------------------------------------
# Actif : 400 000 + 550 000 + 50 000 = 1 000 000 = passif.
evidence = [
    row(1, "IDENTIFICATION", "RAISON_SOCIALE", "Raison sociale", {"TEXT": "SOTRAMEX INDUSTRIE SARL"},
        source="glm_vision", notes=()),
    row(1, "IDENTIFICATION", "ICE", "ICE", {"TEXT": "001874552000073"}, source="glm_vision", notes=()),
    row(1, "IDENTIFICATION", "EXERCICE_DEBUT", "Du", {"TEXT": "01/01/2025"}, source="glm_vision", notes=()),
    row(1, "IDENTIFICATION", "EXERCICE_FIN", "Au", {"TEXT": "31/12/2025"}, source="glm_vision", notes=()),

    row(2, "BILAN_ACTIF", "ACTIFS_IMMOBILISES", "TOTAL I", {"BRUT": "600 000,00", "AMORT_PROV": "200 000,00", "NET_N": "400 000,00", "NET_N1": "380 000,00"}),
    row(2, "BILAN_ACTIF", "ACTIF_CIRCULANT", "TOTAL II", {"NET_N": "550 000,00"}),
    row(2, "BILAN_ACTIF", "CLIENTS", "Clients et comptes rattachés", {"NET_N": "320 000,00"}),
    row(2, "BILAN_ACTIF", "TRESORERIE_ACTIF", "TOTAL III", {"NET_N": "50 000,00"}),
    row(2, "BILAN_ACTIF", "CAISSE", "Caisse, Régies d'avances", {"NET_N": "5 000,00"}),
    row(2, "BILAN_ACTIF", "TOTAL_ACTIF", "TOTAL GENERAL I+II+III", {"NET_N": "1 000 000,00", "NET_N1": "980 000,00"}),

    row(3, "BILAN_PASSIF", "PASSIF_TOTAL_I", "TOTAL I (A+B+C+D+E)", {"EXERCICE_N": "600 000,00"}),
    row(3, "BILAN_PASSIF", "DETTES_FINANCEMENT", "Dettes de financement", {"EXERCICE_N": "150 000,00", "EXERCICE_N1": "180 000,00"}),
    row(3, "BILAN_PASSIF", "PASSIF_CIRCULANT", "TOTAL II", {"EXERCICE_N": "350 000,00"}),
    row(3, "BILAN_PASSIF", "FOURNISSEURS", "Fournisseurs et comptes rattachés", {"EXERCICE_N": "210 000,00"}),
    row(3, "BILAN_PASSIF", "COMPTE_COURANT_ASSOCIES", "Comptes d'associés créditeurs", {"EXERCICE_N": "40 000,00"}),
    row(3, "BILAN_PASSIF", "DETTES_BANCAIRES_CT", "Crédits de trésorerie", {"EXERCICE_N": "30 000,00"}),
    row(3, "BILAN_PASSIF", "TRESORERIE_PASSIF", "TOTAL III", {"EXERCICE_N": "50 000,00"}),
    row(3, "BILAN_PASSIF", "TOTAL_PASSIF", "TOTAL GENERAL I+II+III", {"EXERCICE_N": "1 000 000,00"}),
    row(3, "BILAN_PASSIF", "RESULTAT_NET", "Résultat net de l'exercice", {"EXERCICE_N": "120 000,00"}),

    row(4, "CPC", "CHIFFRE_AFFAIRES", "Chiffre d'affaires", {"OP_N": "2 000 000,00", "TOTAL_N": "2 000 000,00", "TOTAL_N1": "1 800 000,00"}),
    row(4, "CPC", "VENTES_MARCHANDISES", "Ventes de marchandises", {"TOTAL_N": "500 000,00"}),
    row(4, "CPC", "VENTES_BIENS_SERVICES", "Ventes de biens et services produits", {"TOTAL_N": "1 500 000,00"}),
    row(4, "CPC", "ACHATS_REVENDUS", "Achats revendus de marchandises", {"TOTAL_N": "400 000,00"}),
    row(4, "CPC", "ACHATS_CONSOMMES", "Achats consommés de matières", {"TOTAL_N": "700 000,00"}),
    row(4, "CPC", "AUTRES_CHARGES_EXTERNES", "Autres charges externes", {"TOTAL_N": "300 000,00"}),
    row(4, "CPC", "CHARGES_INTERETS", "Charges d'intérêts", {"TOTAL_N": "25 000,00"}),
    row(4, "CPC", "RESULTAT_NET", "RESULTAT NET", {"TOTAL_N": "120 000,00", "TOTAL_N1": "95 000,00"}),

    # Une seule ligne export imprimée : somme partielle attendue.
    row(5, "DETAIL_CPC", "EXPORT_BIENS", "Ventes de biens à l'export", {"EXERCICE_N": "180 000,00"}),
]

audit = engine.pd.DataFrame([
    {"page": 1, "mode": "scan_glm", "page_type": "IDENTIFICATION", "rotation": 0, "scan_extraction_mode": "glm_vision", "extraction_error": None},
    {"page": 2, "mode": "scan_glm", "page_type": "BILAN_ACTIF", "rotation": 0, "scan_extraction_mode": "glm_ocr_cell_vision", "extraction_error": None},
    {"page": 3, "mode": "scan_glm", "page_type": "BILAN_PASSIF", "rotation": 0, "scan_extraction_mode": "glm_ocr_cell_vision", "extraction_error": None},
    {"page": 4, "mode": "scan_glm", "page_type": "CPC", "rotation": 0, "scan_extraction_mode": "glm_ocr_cell_vision", "extraction_error": None},
    {"page": 5, "mode": "scan_glm", "page_type": "DETAIL_CPC", "rotation": 0, "scan_extraction_mode": "glm_ocr_cell_vision", "extraction_error": None},
    {"page": 6, "mode": "scan_glm", "page_type": "ESG", "rotation": 0, "scan_extraction_mode": None, "extraction_error": None},
    {"page": 7, "mode": "scan_glm", "page_type": "UNCLASSIFIED", "rotation": None, "scan_extraction_mode": None, "extraction_error": "layout: timeout"},
])

rcc = engine.resolve_rcc(evidence)
controls = engine.run_controls(evidence, rcc)
rcc = engine._propagate_validation_status(rcc, controls, evidence)

result = build_result(
    filename="liasse_test.pdf", audit_frame=audit, rcc_frame=rcc,
    controls_frame=controls, evidence=evidence, pages_total=7,
)
by_code = {f.code: f for f in result.fields}
controls_by_code = {c.code: c for c in result.controls}


# --- Contrat de la réponse ---------------------------------------------------
ok("20 postes rendus", len(result.fields) == 20, len(result.fields))
ok("postes ordonnés 1..20", [f.number for f in result.fields] == list(range(1, 21)))
ok("codes conformes au référentiel",
   all(f.label and f.source for f in result.fields))

ok("total bilan lu", by_code["TOTAL_BILAN"].value == 1_000_000.0, by_code["TOTAL_BILAN"].value)
ok("actif circulant lu", by_code["ACTIF_CIRCULANT"].value == 550_000.0)
ok("créances clients lues", by_code["CREANCES_CLIENTS"].value == 320_000.0)
ok("caisse lue", by_code["CAISSE"].value == 5_000.0)
ok("chiffre d'affaires lu", by_code["CHIFFRE_AFFAIRES"].value == 2_000_000.0)
ok("charges d'intérêts lues", by_code["CHARGES_INTERETS"].value == 25_000.0)
ok("résultat net recoupé CPC/passif", by_code["RESULTAT_NET"].value == 120_000.0)

ok("statut confirmé sur lecture nette", by_code["TOTAL_BILAN"].status == "confirmed",
   by_code["TOTAL_BILAN"].status)
ok("dettes MLT signalées comme proxy", by_code["DETTES_BANCAIRES_MLT"].status == "derived",
   by_code["DETTES_BANCAIRES_MLT"].status)
ok("note proxy en français", (by_code["DETTES_BANCAIRES_MLT"].note or "").startswith("Proxy"),
   by_code["DETTES_BANCAIRES_MLT"].note)

ok("CA export sommé sur les lignes imprimées", by_code["CA_EXPORT"].value == 180_000.0,
   by_code["CA_EXPORT"].value)
ok("CA export marqué comme dérivé", by_code["CA_EXPORT"].status == "derived",
   by_code["CA_EXPORT"].status)

ok("type de résultat sans valeur numérique", by_code["TYPE_RESULTAT"].value is None)
ok("type de résultat = Bénéficiaire", by_code["TYPE_RESULTAT"].note == "Bénéficiaire",
   by_code["TYPE_RESULTAT"].note)

# --- Preuves et N-1 ----------------------------------------------------------
evidence_total = by_code["TOTAL_BILAN"].evidence
ok("preuve rattachée au total bilan", len(evidence_total) >= 1)
ok("preuve : page source", evidence_total and evidence_total[0].page_number == 2)
ok("preuve : colonne lue", evidence_total and evidence_total[0].column_name == "NET_N",
   evidence_total[0].column_name if evidence_total else None)
ok("preuve : valeur brute conservée", evidence_total and evidence_total[0].raw_value == "1 000 000,00")

ok("N-1 du total bilan", by_code["TOTAL_BILAN"].value_n1 == 980_000.0, by_code["TOTAL_BILAN"].value_n1)
ok("N-1 du chiffre d'affaires", by_code["CHIFFRE_AFFAIRES"].value_n1 == 1_800_000.0)
ok("N-1 du résultat net", by_code["RESULTAT_NET"].value_n1 == 95_000.0)
ok("pas de N-1 inventé sur la caisse", by_code["CAISSE"].value_n1 is None)

# --- Contrôles ---------------------------------------------------------------
ok("code d'équilibre attendu par l'interface", "bilan_equilibre" in controls_by_code,
   list(controls_by_code))
ok("bilan équilibré", controls_by_code["bilan_equilibre"].status == "passed")
ok("totaux actif vérifiés", controls_by_code["bilan_actif_totaux"].status == "passed")
ok("totaux passif vérifiés", controls_by_code["bilan_passif_totaux"].status == "passed")
ok("ventes = chiffre d'affaires", controls_by_code["cpc_ventes_chiffre_affaires"].status == "passed")
ok("résultat net cohérent", controls_by_code["resultat_net"].status == "passed")
ok("contrôle ligne à ligne agrégé", "actif_brut_amort_net" in controls_by_code)
ok("tolérance exposée", all(c.tolerance == 0.02 for c in result.controls))
ok("libellés de contrôle en français",
   all("=" in c.label or "Total" in c.label for c in result.controls))

# --- Document et pages -------------------------------------------------------
doc = result.document
ok("raison sociale reprise", doc.company.raison_sociale == "SOTRAMEX INDUSTRIE SARL")
ok("ICE repris", doc.company.ice == "001874552000073")
ok("exercice reconstitué", doc.exercise.label == "Du 01/01/2025 au 31/12/2025", doc.exercise.label)
ok("nombre de pages du document", doc.pages_total == 7, doc.pages_total)
ok("pages exploitées comptées", doc.pages_processed == 5, doc.pages_processed)
ok("page non classée comptée en échec", doc.pages_failed == 1, doc.pages_failed)
ok("page ESG ignorée", doc.pages_skipped == 1, doc.pages_skipped)
ok("audit page par page", len(result.extraction.page_audit) == 7)
ok("type de page inconnu ramené à AUTRE",
   result.extraction.page_audit[-1].detected_type == "AUTRE",
   result.extraction.page_audit[-1].detected_type)
ok("version du moteur exposée", "v10" in result.extraction.model, result.extraction.model)

ok("complétude cohérente", result.completeness_pct >= 95.0, result.completeness_pct)
ok("avertissement sur la page en échec",
   any("7" in w for w in result.warnings), result.warnings)

# Une cellule vide + note de vérification non résolue ne doit pas devenir
# « à vérifier » : pandas stocke None en NaN, ce qui faisait basculer le statut.
blank_evidence = [
    row(1, "BILAN_ACTIF", "TRESORERIE_ACTIF", "TOTAL III", {},
        confidence=0.45, notes=("row_present=true", "verification_unresolved=true")),
    row(1, "BILAN_ACTIF", "CAISSE", "Caisse", {"NET_N": "1 000,00"},
        confidence=0.45, notes=("row_present=true", "verification_unresolved=true")),
]
blank_rcc = engine.resolve_rcc(blank_evidence)
blank_controls = engine.run_controls(blank_evidence, blank_rcc)
blank_rcc = engine._propagate_validation_status(blank_rcc, blank_controls, blank_evidence)
blank_result = build_result(
    filename="blank.pdf", audit_frame=engine.pd.DataFrame(), rcc_frame=blank_rcc,
    controls_frame=blank_controls, evidence=blank_evidence, pages_total=1,
)
blank_fields = {f.code: f for f in blank_result.fields}
ok("trésorerie vide reste manquante", blank_fields["TRESORERIE_ACTIF"].status == "missing",
   blank_fields["TRESORERIE_ACTIF"].status)
ok("trésorerie vide sans note de contrôle", blank_fields["TRESORERIE_ACTIF"].note in {None, "Ligne vide sur la liasse"},
   blank_fields["TRESORERIE_ACTIF"].note)
ok("caisse avec montant passe en relecture", blank_fields["CAISSE"].status == "ambiguous",
   blank_fields["CAISSE"].status)

print("\n" + ("Toutes les vérifications passent." if not FAILURES else f"{FAILURES} échec(s)."))
sys.exit(1 if FAILURES else 0)
