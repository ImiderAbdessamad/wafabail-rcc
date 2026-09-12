"""Routage local des pages financières avant extraction sémantique.

Ce module ne dépend pas obligatoirement d'un moteur OCR. RapidOCR est chargé
dynamiquement lorsqu'il est installé ; sinon les métriques visuelles et le
texte PDF natif restent disponibles et le pipeline GLM continue de fonctionner.
"""
from __future__ import annotations

import io
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Callable, Literal, Sequence

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat

from app.config import (
    CLASSIFICATION_MAX_IMAGE_DIMENSION,
    DIRECT_FINANCIAL_MAX_IMAGE_DIMENSION,
    LOCAL_OCR_ENABLED,
    LOCAL_OCR_TEXT_MAX_SIDE,
    NATIVE_TEXT_MIN_CHARS,
    ORIENTATION_OCR_MAX_SIDE,
)
from app.services.financial_orientation_detector import rank_page_orientations
from app.services.page_preprocessor import detect_content_box

logger = logging.getLogger(__name__)

DocumentSourceKind = Literal["born_digital", "hybrid", "image_only"]
PageRoute = Literal[
    "native_docling_glm",
    "hybrid_local_ocr_glm",
    "scan_local_ocr_glm",
    "scan_glm_fallback",
]

_PCGM_TERMS = (
    "bilan",
    "actif",
    "passif",
    "immobilisations",
    "capitaux propres",
    "dettes",
    "tresorerie",
    "exercice",
    "compte de produits",
    "charges",
    "resultat",
    "chiffre d affaires",
    "fournisseurs",
    "clients",
    "total",
)
_NUMBER_RE = re.compile(r"(?<!\w)[(\-]?\d[\d\s.,]{1,}(?!\w)")


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'")
    value = re.sub(r"[^a-z0-9'\s]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _image_to_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _downscale(image: Image.Image, max_side: int) -> Image.Image:
    result = image.copy()
    if max(result.size) > max_side:
        result.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return result


@dataclass(frozen=True)
class PageVisualAudit:
    width: int
    height: int
    mean_luminance: float
    contrast: float
    dark_ratio: float
    white_ratio: float
    sharpness: float
    inverted: bool
    content_box: tuple[int, int, int, int]


@dataclass(frozen=True)
class LocalOcrObservation:
    angle: int
    text: str = ""
    confidence: float = 0.0
    word_count: int = 0
    numeric_count: int = 0
    keyword_hits: int = 0
    score: float = 0.0
    status: str = "not_run"
    error: str | None = None


@dataclass(frozen=True)
class OrientationAssessment:
    selected_angle: int
    confidence: float
    method: str
    alternatives: tuple[int, ...]
    scores: dict[int, float]
    observations: dict[int, LocalOcrObservation] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedFinancialPage:
    page_number: int
    source_kind: DocumentSourceKind
    route: PageRoute
    orientation: OrientationAssessment
    visual: PageVisualAudit
    extraction_image: bytes
    classification_image: bytes
    native_text: str
    local_ocr_text: str
    local_ocr_confidence: float = 0.0
    local_ocr_status: str = "not_run"
    warnings: tuple[str, ...] = ()


OcrRunner = Callable[[bytes, int], LocalOcrObservation]
TextOcrRunner = Callable[[bytes], LocalOcrObservation]


def native_text_is_useful(text: str) -> bool:
    """Reject native layers that are long enough but contain mostly PDF noise."""
    stripped = (text or "").strip()
    if len(stripped) < NATIVE_TEXT_MIN_CHARS:
        return False
    alnum = sum(character.isalnum() for character in stripped)
    if alnum / max(len(stripped), 1) < 0.45:
        return False
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]{2,}", stripped)
    return len(tokens) >= 4


def classify_document_source(native_texts: Sequence[str]) -> DocumentSourceKind:
    if not native_texts:
        return "image_only"
    useful = [native_text_is_useful(text) for text in native_texts]
    coverage = sum(useful) / len(useful)
    if coverage >= 0.75:
        return "born_digital"
    if coverage <= 0.10:
        return "image_only"
    return "hybrid"


def audit_page_image(image_bytes: bytes) -> PageVisualAudit:
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = source.convert("RGB")
    sample = _downscale(image, 900)
    gray = ImageOps.grayscale(sample)
    stat = ImageStat.Stat(gray)
    luminance = float(stat.mean[0])
    contrast = float(stat.stddev[0])
    histogram = gray.histogram()
    total = max(sum(histogram), 1)
    dark_ratio = sum(histogram[:64]) / total
    white_ratio = sum(histogram[240:]) / total
    edges = gray.filter(ImageFilter.FIND_EDGES)
    sharpness = float(ImageStat.Stat(edges).mean[0])
    inverted = luminance < 125 and dark_ratio > 0.35 and white_ratio < 0.35
    return PageVisualAudit(
        width=image.width,
        height=image.height,
        mean_luminance=round(luminance, 3),
        contrast=round(contrast, 3),
        dark_ratio=round(dark_ratio, 5),
        white_ratio=round(white_ratio, 5),
        sharpness=round(sharpness, 3),
        inverted=inverted,
        content_box=detect_content_box(image),
    )


_RAPID_ENGINE: Any | None = None


def rapidocr_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("rapidocr") is not None
    except Exception:  # noqa: BLE001
        return False


def run_rapidocr_orientation(image_bytes: bytes, angle: int) -> LocalOcrObservation:
    """OCR basse résolution utilisé pour départager 0/90/180/270."""
    global _RAPID_ENGINE
    try:
        import numpy as np
        from rapidocr import RapidOCR

        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
        if angle:
            image = image.rotate(-angle, expand=True)
        image = _downscale(image, ORIENTATION_OCR_MAX_SIDE)
        if _RAPID_ENGINE is None:
            _RAPID_ENGINE = RapidOCR()
        # Désactiver le classifieur d'angle de lignes ici : sinon RapidOCR
        # redresse chaque ligne isolément et rend les quatre rotations de page
        # artificiellement similaires. Cette passe doit évaluer la page entière.
        result = _RAPID_ENGINE(np.asarray(image), use_cls=False)
        texts = [str(item).strip() for item in (getattr(result, "txts", ()) or ()) if str(item).strip()]
        confidences = [float(item) for item in (getattr(result, "scores", ()) or ())]
        text = "\n".join(texts)
        folded = _fold(text)
        keyword_hits = sum(1 for term in _PCGM_TERMS if term in folded)
        numeric_count = len(_NUMBER_RE.findall(text))
        confidence = mean(confidences) if confidences else 0.0
        # Les mots comptables distinguent surtout 90 de 270 ; les nombres et la
        # confiance évitent qu'une page sparse gagne avec un unique faux positif.
        score = (
            keyword_hits * 7.0
            + min(len(texts), 160) * 0.12
            + min(numeric_count, 120) * 0.18
            + confidence * 5.0
        )
        return LocalOcrObservation(
            angle=angle,
            text=text,
            confidence=round(confidence, 4),
            word_count=len(texts),
            numeric_count=numeric_count,
            keyword_hits=keyword_hits,
            score=round(score, 4),
            status="success",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("RapidOCR orientation angle=%s indisponible: %s", angle, exc)
        return LocalOcrObservation(
            angle=angle,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )


def run_rapidocr_text(image_bytes: bytes) -> LocalOcrObservation:
    """Read the final upright, polarity-corrected page at extraction resolution."""
    global _RAPID_ENGINE
    try:
        import numpy as np
        from rapidocr import RapidOCR

        with Image.open(io.BytesIO(image_bytes)) as source:
            image = _downscale(source.convert("RGB"), LOCAL_OCR_TEXT_MAX_SIDE)
        if _RAPID_ENGINE is None:
            _RAPID_ENGINE = RapidOCR()
        # The page is already upright. The line classifier is useful here for
        # small local skews and no longer interferes with four-way page scoring.
        result = _RAPID_ENGINE(np.asarray(image), use_cls=True)
        texts = [
            str(item).strip()
            for item in (getattr(result, "txts", ()) or ())
            if str(item).strip()
        ]
        confidences = [float(item) for item in (getattr(result, "scores", ()) or ())]
        text = "\n".join(texts)
        folded = _fold(text)
        keyword_hits = sum(1 for term in _PCGM_TERMS if term in folded)
        numeric_count = len(_NUMBER_RE.findall(text))
        confidence = mean(confidences) if confidences else 0.0
        score = (
            keyword_hits * 7.0
            + min(len(texts), 240) * 0.12
            + min(numeric_count, 180) * 0.18
            + confidence * 5.0
        )
        return LocalOcrObservation(
            angle=0,
            text=text,
            confidence=round(confidence, 4),
            word_count=len(texts),
            numeric_count=numeric_count,
            keyword_hits=keyword_hits,
            score=round(score, 4),
            status="success",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("RapidOCR texte final indisponible: %s", exc)
        return LocalOcrObservation(
            angle=0,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )


def assess_orientation(
    image_bytes: bytes,
    *,
    native_text: str = "",
    ocr_runner: OcrRunner | None = None,
) -> OrientationAssessment:
    """Sélectionne l'angle avec OCR sémantique, puis géométrie en secours.

    PyMuPDF applique déjà la rotation déclarée lors du rendu. On ne réapplique
    donc jamais aveuglément la métadonnée PDF ici.
    """
    geometry = rank_page_orientations(image_bytes, declared_rotation=None)
    geometry_scores = {angle: float(score) for angle, score in geometry}

    if native_text_is_useful(native_text):
        alternatives = tuple(angle for angle, _ in geometry if angle != 0)
        return OrientationAssessment(
            selected_angle=0,
            confidence=0.98,
            method="native_text_rendered_orientation",
            alternatives=(0, *alternatives),
            scores=geometry_scores,
        )

    runner = ocr_runner
    if runner is None and LOCAL_OCR_ENABLED and rapidocr_available():
        runner = run_rapidocr_orientation

    observations: dict[int, LocalOcrObservation] = {}
    if runner is not None:
        for angle in (0, 90, 180, 270):
            observations[angle] = runner(image_bytes, angle)
        successful = [item for item in observations.values() if item.status == "success"]
        if successful:
            ranked = sorted(
                successful,
                key=lambda item: (item.score, geometry_scores.get(item.angle, 0.0)),
                reverse=True,
            )
            best = ranked[0]
            second = ranked[1] if len(ranked) > 1 else None
            margin = best.score - (second.score if second else 0.0)
            confidence = 1.0 - math.exp(-max(margin, 0.0) / 8.0)
            if best.keyword_hits >= 2 or best.word_count >= 8:
                ordered = tuple(item.angle for item in ranked)
                return OrientationAssessment(
                    selected_angle=best.angle,
                    confidence=round(max(0.55, min(confidence, 0.99)), 3),
                    method="rapidocr_semantic_cascade",
                    alternatives=ordered,
                    scores={item.angle: item.score for item in ranked},
                    observations=observations,
                )

    best_angle = geometry[0][0]
    best_score = geometry[0][1]
    second_score = geometry[1][1] if len(geometry) > 1 else 0.0
    ratio = best_score / max(second_score, 1e-6)
    confidence = min(max((ratio - 1.0) * 1.6, 0.2), 0.7)
    return OrientationAssessment(
        selected_angle=best_angle,
        confidence=round(confidence, 3),
        method="geometry_fallback",
        alternatives=tuple(angle for angle, _ in geometry),
        scores=geometry_scores,
        observations=observations,
    )


def _normalize_visual_state(image: Image.Image, audit: PageVisualAudit) -> tuple[Image.Image, list[str]]:
    warnings: list[str] = []
    normalized = image.convert("RGB")
    if audit.inverted:
        normalized = ImageOps.invert(normalized)
        warnings.append("PolaritÃ© sombre dÃ©tectÃ©e et inversÃ©e localement.")
    normalized = ImageOps.autocontrast(normalized, cutoff=1)
    if audit.contrast < 28:
        normalized = ImageEnhance.Contrast(normalized).enhance(1.25)
        warnings.append("Contraste faible renforcÃ©.")
    return normalized, warnings


def prepare_financial_page(
    *,
    page_number: int,
    image_bytes: bytes,
    native_text: str,
    source_kind: DocumentSourceKind,
    ocr_runner: OcrRunner | None = None,
    text_ocr_runner: TextOcrRunner | None = None,
) -> PreparedFinancialPage:
    visual = audit_page_image(image_bytes)
    native_useful = native_text_is_useful(native_text)
    with Image.open(io.BytesIO(image_bytes)) as source:
        source_image = source.convert("RGB")
    # Correct polarity and weak contrast before semantic orientation. This is
    # essential for white-on-black scans, where OCR on the raw page is sparse.
    normalized, warnings = _normalize_visual_state(source_image, visual)
    normalized_before_orientation = _image_to_png(normalized)
    orientation = assess_orientation(
        normalized_before_orientation,
        native_text=native_text,
        ocr_runner=ocr_runner,
    )
    image = normalized
    if orientation.selected_angle:
        image = image.rotate(-orientation.selected_angle, expand=True)
    crop_box = detect_content_box(image)
    if crop_box != (0, 0, image.width, image.height):
        image = image.crop(crop_box)

    extraction = _downscale(image, DIRECT_FINANCIAL_MAX_IMAGE_DIMENSION)
    classification = _downscale(image, CLASSIFICATION_MAX_IMAGE_DIMENSION)
    local_text = ""
    local_confidence = 0.0
    local_status = "not_run"
    selected_observation = orientation.observations.get(orientation.selected_angle)
    if selected_observation and selected_observation.status == "success":
        local_text = selected_observation.text
        local_confidence = selected_observation.confidence
        local_status = "orientation_pass"

    final_observation: LocalOcrObservation | None = None
    if text_ocr_runner is not None and not native_useful:
        final_observation = text_ocr_runner(_image_to_png(extraction))
    elif (
        not native_useful
        and ocr_runner is None
        and LOCAL_OCR_ENABLED
        and rapidocr_available()
    ):
        final_observation = run_rapidocr_text(_image_to_png(extraction))
    if final_observation is not None:
        if final_observation.status == "success":
            local_status = "success"
            if (
                len(final_observation.text.strip()) >= len(local_text.strip())
                or final_observation.keyword_hits
                > (selected_observation.keyword_hits if selected_observation else 0)
            ):
                local_text = final_observation.text
                local_confidence = final_observation.confidence
        elif not local_text:
            local_status = final_observation.status

    if native_useful:
        route: PageRoute = "native_docling_glm"
    elif local_text:
        route = "scan_local_ocr_glm" if source_kind == "image_only" else "hybrid_local_ocr_glm"
    else:
        route = "scan_glm_fallback"
        warnings.append("OCR local indisponible ou sans texte; secours GLM uniquement.")
    if orientation.confidence < 0.65:
        warnings.append(
            f"Orientation peu certaine ({orientation.method}, confiance={orientation.confidence:.2f})."
        )

    return PreparedFinancialPage(
        page_number=page_number,
        source_kind=source_kind,
        route=route,
        orientation=orientation,
        visual=visual,
        extraction_image=_image_to_png(extraction),
        classification_image=_image_to_png(classification),
        native_text=native_text,
        local_ocr_text=local_text,
        local_ocr_confidence=local_confidence,
        local_ocr_status=local_status,
        warnings=tuple(warnings),
    )


def extraction_variant_for_angle(image_bytes: bytes, angle: int) -> bytes:
    """Produit la même variante normalisée pour une orientation alternative."""
    audit = audit_page_image(image_bytes)
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = source.convert("RGB")
    if angle:
        image = image.rotate(-angle, expand=True)
    normalized, _warnings = _normalize_visual_state(image, audit)
    crop_box = detect_content_box(normalized)
    if crop_box != (0, 0, normalized.width, normalized.height):
        normalized = normalized.crop(crop_box)
    return _image_to_png(_downscale(normalized, DIRECT_FINANCIAL_MAX_IMAGE_DIMENSION))


def normalized_pages_to_pdf(pages: Sequence[PreparedFinancialPage]) -> bytes:
    """Construit un PDF raster normalisé pour Docling sur les documents scannés."""
    if not pages:
        return b""
    output: Any | None = None
    try:
        import fitz

        output = fitz.open()
        for page in pages:
            image_doc = fitz.open(stream=page.extraction_image, filetype="png")
            try:
                one_page_pdf = fitz.open("pdf", image_doc.convert_to_pdf())
                try:
                    output.insert_pdf(one_page_pdf)
                finally:
                    one_page_pdf.close()
            finally:
                image_doc.close()
        return output.tobytes(garbage=3, deflate=True)
    finally:
        if output is not None:
            output.close()
