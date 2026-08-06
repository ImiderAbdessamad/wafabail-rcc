/*
 * Projection des 20 codes RCC (app/schemas/rcc.py::RCC_ELEMENTS) vers les
 * groupes de l'écran de validation.
 *
 * - TOTAL_BILAN et RESULTAT_NET sont calculés par le pipeline : verrouillés,
 *   affichés tels quels, jamais recalculés côté client.
 * - TYPE_RESULTAT est dérivé côté serveur par rcc_mapper.py : rendu en tag.
 * - Les 17 autres postes sont saisissables par l'analyste.
 */

export const LOCKED_CODES = new Set(["TOTAL_BILAN", "RESULTAT_NET"]);
export const TAG_CODES = new Set(["TYPE_RESULTAT"]);

/** Seuil aligné sur CONFIDENCE_REVIEW_THRESHOLD côté serveur. */
export const CONFIDENCE_THRESHOLD = 0.8;

export const GROUPS = [
  {
    key: "A",
    letter: "A",
    title: "Actif",
    subtitle: "Emplois — ce que possède l'entreprise",
    codes: [
      "ACTIFS_IMMOBILISES",
      "ACTIF_CIRCULANT",
      "CREANCES_CLIENTS",
      "TRESORERIE_ACTIF",
      "CAISSE",
      "TOTAL_BILAN",
    ],
  },
  {
    key: "B",
    letter: "B",
    title: "Passif",
    subtitle: "Ressources — comment c'est financé",
    codes: [
      "PASSIF_CIRCULANT",
      "DETTES_FOURNISSEURS",
      "DETTES_BANCAIRES_MLT",
      "DETTES_BANCAIRES_CT",
      "TRESORERIE_PASSIF",
      "COMPTE_COURANT_ASSOCIES",
    ],
  },
  {
    key: "C",
    letter: "C",
    title: "Compte de produits et charges",
    subtitle: "Activité de l'exercice",
    codes: [
      "CHIFFRE_AFFAIRES",
      "CA_EXPORT",
      "ACHATS_REVENDUS",
      "ACHATS_CONSOMMES",
      "AUTRES_CHARGES_EXTERNES",
      "CHARGES_INTERETS",
      "RESULTAT_NET",
      "TYPE_RESULTAT",
    ],
  },
];

/** Définition de ce que représente chaque poste, en langage gestionnaire. */
export const FORMULAS = {
  ACTIFS_IMMOBILISES:
    "Immobilisations en non-valeurs + incorporelles + corporelles + financières",
  ACTIF_CIRCULANT:
    "Stocks + créances clients + avances fournisseurs + créances État, personnel et associés + autres créances + titres de placement",
  CREANCES_CLIENTS:
    "Montant que les clients doivent encore payer pour des ventes déjà réalisées",
  TRESORERIE_ACTIF:
    "Chèques en attente d'encaissement + disponibilités en banque + espèces en caisse",
  CAISSE: "Argent disponible en espèces dans la caisse de l'entreprise",
  TOTAL_BILAN:
    "Total général de l'actif — calculé par le pipeline d'extraction et contrôlé contre le total du passif",

  PASSIF_CIRCULANT:
    "Dettes fournisseurs + État + organismes sociaux + personnel + associés + autres dettes à court terme",
  DETTES_FOURNISSEURS:
    "Montant dû aux fournisseurs pour des achats reçus mais pas encore payés",
  DETTES_BANCAIRES_MLT:
    "Emprunts bancaires remboursables sur une durée supérieure à un an",
  DETTES_BANCAIRES_CT:
    "Crédits d'escompte + crédits de trésorerie + découverts bancaires",
  TRESORERIE_PASSIF:
    "Concours bancaires courants inscrits en trésorerie au passif du bilan",
  COMPTE_COURANT_ASSOCIES:
    "Argent prêté par les associés à l'entreprise, net des avances consenties aux associés",

  CHIFFRE_AFFAIRES: "Ventes de marchandises + ventes de biens et services produits",
  CA_EXPORT: "Part des ventes réalisées auprès de clients situés à l'étranger",
  ACHATS_REVENDUS:
    "Achats de marchandises de l'exercice + variation du stock de marchandises",
  ACHATS_CONSOMMES:
    "Achats de matières et fournitures de l'exercice + variation des stocks correspondants",
  AUTRES_CHARGES_EXTERNES:
    "Locations, entretien, primes d'assurance, honoraires, transports et autres charges externes de l'exercice",
  CHARGES_INTERETS:
    "Intérêts payés sur les emprunts et dettes financières pendant l'exercice",
  RESULTAT_NET:
    "Résultat d'exploitation + résultat financier + résultat non courant − impôt sur les sociétés — calculé par le pipeline",
  TYPE_RESULTAT:
    "« Bénéficiaire » si le résultat net est positif, « Déficitaire » s'il est négatif, « Nul » s'il est égal à zéro — dérivé côté serveur",
};

/** Ordre d'affichage : index du code dans son groupe. */
export const CODE_ORDER = new Map(
  GROUPS.flatMap((group, groupIndex) =>
    group.codes.map((code, index) => [code, groupIndex * 100 + index])
  )
);

/**
 * État d'affichage d'un champ, dérivé exclusivement des données serveur.
 *
 * @param {object} field   RccField renvoyé par l'API
 * @param {object|null} override  correction analyste éventuelle
 * @returns {{state: string, confLabel: string, confClass: string, editable: boolean}}
 */
export function fieldState(field, override) {
  const code = field.code;

  if (TAG_CODES.has(code)) {
    return { state: "tag", confLabel: "dérivé", confClass: "conf-lock", editable: false };
  }
  if (LOCKED_CODES.has(code)) {
    return { state: "locked", confLabel: "verrouillé", confClass: "conf-lock", editable: false };
  }
  if (override) {
    return override.verified
      ? { state: "verified", confLabel: "vérifié", confClass: "conf-ok", editable: true }
      : { state: "edited", confLabel: "corrigé", confClass: "conf-ok", editable: true };
  }
  if (field.status === "conflicting" || field.status === "invalid") {
    return { state: "conflict", confLabel: "incohérence", confClass: "conf-bad", editable: true };
  }
  if (field.status === "missing" || field.value == null) {
    return { state: "missing", confLabel: "non lu", confClass: "conf-bad", editable: true };
  }
  if (field.confidence < CONFIDENCE_THRESHOLD) {
    return { state: "low", confLabel: "à vérifier", confClass: "conf-low", editable: true };
  }
  return {
    state: "ok",
    confLabel: `OCR ${Math.round(field.confidence * 100)} %`,
    confClass: "conf-ok",
    editable: true,
  };
}

export const STATUS_META = {
  pending: { label: "À réviser", cls: "badge-warn" },
  validated: { label: "Validé", cls: "badge-ok" },
  rejected: { label: "Rejeté", cls: "badge-bad" },
  escalated: { label: "Arbitrage demandé", cls: "badge-dark" },
};

export const REJECT_MOTIFS = [
  "Bilan non signé ou non certifié",
  "Incohérence comptable non résolue",
  "Document illisible / OCR non exploitable",
  "Exercice non clos ou hors périmètre",
  "Autre motif (à préciser)",
];
