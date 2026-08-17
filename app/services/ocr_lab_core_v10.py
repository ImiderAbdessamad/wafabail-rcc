"""Moteur d'extraction RCC — copie vendorisée de
`documentation-wafabail/use-case-RCC/wafabail_ocr_lab_core_v10_robust.py`.

Le contenu est repris à l'identique : mêmes prompts, mêmes seuils, même
géométrie de grille, mêmes règles de résolution et mêmes contrôles. Les seules
adaptations pour l'API sont concentrées dans `analyze_pdf` :

- les `print` de progression deviennent des `logger.info` ;
- un callback optionnel `progress(event, data)` permet au job SSE de suivre le
  traitement page par page.

Toute évolution du script de laboratoire doit être répercutée ici telle quelle.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field, asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable

import cv2
import numpy as np
import pandas as pd
import pymupdf
import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat, ImageDraw

logger = logging.getLogger(__name__)

# ---------------------------
# Configuration
# ---------------------------
OLLAMA_URL = "https://ollama-lnhh4y-11434.svc-usw2.nicegpu.com".rstrip("/")
VISION_MODEL = "hf.co/unsloth/GLM-4.6V-Flash-GGUF:Q4_K_M"
MAPPER_MODEL = "qwen3.5:9b"
ADJUDICATOR_MODEL = "gemma4:latest"
# Backward-compatible alias used by the Colab cells.
MODEL = VISION_MODEL
REQUEST_TIMEOUT = 600
KEEP_ALIVE = "20m"
CLASSIFY_MAX_SIDE = 1050
EXTRACT_MAX_SIDE = 2400
RENDER_DPI = 220
STRICT_MODE = True
PIPELINE_VERSION = "v8-grid-guided-glm"
GRID_GUIDED_SCAN = True
GRID_FALLBACK_TO_FULL_TABLE = True

RELEVANT_PAGE_TYPES = {"IDENTIFICATION", "BILAN_ACTIF", "BILAN_PASSIF", "CPC", "DETAIL_CPC"}
ALL_PAGE_TYPES = [
    "IDENTIFICATION", "BILAN_ACTIF", "BILAN_PASSIF", "CPC",
    "RESULTAT_FISCAL", "ESG", "DETAIL_CPC", "AUTRE", "VIDE"
]

# ---------------------------
# Small utilities
# ---------------------------
def fold(text: str | None) -> str:
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("’", "'")
    # Apostrophes are word separators in French labels (d'associés -> d associes).
    s = s.replace("'", " ").replace("/", " ")
    s = re.sub(r"[^a-z0-9+\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_amount(raw: str | None) -> Decimal | None:
    """Parse Moroccan/French-formatted amounts without using float.

    Blank stays None. Explicit zero becomes Decimal('0').
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("\u00a0", " ").replace("\u202f", " ").replace("−", "-")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    if s.endswith("-"):
        negative = True
        s = s[:-1]
    if s.startswith("-"):
        negative = True
        s = s[1:]
    s = re.sub(r"(?i)\b(MAD|DH|DHS)\b", "", s).strip()
    s = re.sub(r"[^0-9., ]", "", s)
    s = s.replace(" ", "")
    if not s or not re.search(r"\d", s):
        return None

    # Choose the right-most punctuation as decimal separator when both exist.
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        thousands = "." if dec == "," else ","
        s = s.replace(thousands, "")
        if dec != ".":
            s = s.replace(dec, ".")
    elif "," in s:
        parts = s.split(",")
        if len(parts[-1]) in (1, 2):
            s = "".join(parts[:-1]).replace(".", "") + "." + parts[-1]
        else:
            s = "".join(parts)
    elif "." in s:
        parts = s.split(".")
        if len(parts[-1]) in (1, 2):
            s = "".join(parts[:-1]) + "." + parts[-1]
        else:
            s = "".join(parts)
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return -value if negative else value


def amount_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def image_bytes(img: Image.Image, *, quality: int = 90, max_side: int | None = None) -> bytes:
    im = img.convert("RGB")
    if max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def render_page(page: pymupdf.Page, dpi: int = RENDER_DPI) -> Image.Image:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def native_text_quality(page: pymupdf.Page) -> tuple[bool, str, int]:
    text = page.get_text("text", sort=True) or ""
    words = page.get_text("words", sort=True) or []
    # enough actual selectable words to trust native layout tools
    good = len(text.strip()) >= 80 and len(words) >= 15
    return good, text, len(words)

def _json_candidate(content: str) -> str:
    """Return the most likely JSON object from a model response."""
    s = (content or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
    start = s.find("{")
    if start >= 0:
        s = s[start:]
    return s.strip()


def _loads_json_loose(content: str) -> dict[str, Any]:
    """Parse structured model output with conservative repair for common comma/fence errors.

    This intentionally does NOT invent missing values. It only repairs JSON punctuation.
    """
    s = _json_candidate(content)
    try:
        out = json.loads(s)
        if not isinstance(out, dict):
            raise ValueError("Expected JSON object")
        return out
    except json.JSONDecodeError:
        pass

    # Keep the largest balanced object when the model adds trailing prose.
    depth = 0
    in_str = False
    esc = False
    end = None
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end:
        s = s[:end]

    # Conservative punctuation repairs only.
    repaired = s
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)  # trailing comma
    repaired = re.sub(r"}\s*{", "},{", repaired)          # adjacent array objects
    repaired = re.sub(r"]\s*\"", '],"', repaired)       # missing comma before next key
    repaired = re.sub(r"}\s*\"", '},"', repaired)       # missing comma before next key

    # Iteratively insert a comma only where the JSON parser explicitly expects one
    # between two otherwise-complete JSON tokens.
    for _ in range(6):
        try:
            out = json.loads(repaired)
            if not isinstance(out, dict):
                raise ValueError("Expected JSON object")
            return out
        except json.JSONDecodeError as e:
            if "Expecting ',' delimiter" not in e.msg:
                break
            left = repaired[:e.pos].rstrip()
            right = repaired[e.pos:].lstrip()
            if not left or not right:
                break
            if left[-1] in ('}', ']', '"') and right[0] in ('{', '"'):
                insert_at = len(repaired[:e.pos].rstrip())
                repaired = repaired[:insert_at] + "," + repaired[insert_at:]
            else:
                break
    out = json.loads(repaired)  # raise the final precise error if still invalid
    if not isinstance(out, dict):
        raise ValueError("Expected JSON object")
    return out


# ---------------------------
# Ollama client
# ---------------------------
class OllamaClient:
    """One Ollama endpoint, three roles.

    - vision_model: image reading / orientation / raw table OCR
    - mapper_model: semantic row -> RCC field mapping; NEVER receives numbers
    - adjudicator_model: optional tie-breaker for ambiguous mappings; NEVER receives numbers
    """
    def __init__(
        self,
        base_url: str = OLLAMA_URL,
        model: str = VISION_MODEL,
        mapper_model: str = MAPPER_MODEL,
        adjudicator_model: str = ADJUDICATOR_MODEL,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.mapper_model = mapper_model
        self.adjudicator_model = adjudicator_model
        self.session = requests.Session()

    def tags(self) -> dict[str, Any]:
        r = self.session.get(f"{self.base_url}/api/tags", timeout=45)
        r.raise_for_status()
        return r.json()

    def check_model(self, model: str | None = None) -> dict[str, Any]:
        data = self.tags()
        target = model or self.model
        names = {m.get("name") for m in data.get("models", [])}
        if target not in names:
            raise RuntimeError(f"Model not found in /api/tags: {target}")
        return data

    def check_models(self, *, require_adjudicator: bool = False) -> dict[str, Any]:
        data = self.tags()
        names = {m.get("name") for m in data.get("models", [])}
        required = [self.model, self.mapper_model]
        if require_adjudicator:
            required.append(self.adjudicator_model)
        missing = [m for m in required if m not in names]
        if missing:
            raise RuntimeError(f"Required Ollama model(s) missing: {missing}")
        return data

    def chat_json(
        self,
        *,
        prompt: str,
        images: list[bytes] | None,
        schema: dict[str, Any],
        system: str = "",
        model: str | None = None,
        think: bool = False,
        timeout: int = REQUEST_TIMEOUT,
        attempts: int = 3,
        num_ctx: int = 8192,
        num_predict: int = 2200,
    ) -> dict[str, Any]:
        msgs: list[dict[str, Any]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        user: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            user["images"] = [b64(x) for x in images]
        msgs.append(user)

        body = {
            "model": model or self.model,
            "messages": msgs,
            "stream": False,
            # We use reasoning-capable models, but do not request their private
            # thinking trace. We only need the final structured classification.
            "think": think,
            "format": schema,
            "keep_alive": KEEP_ALIVE,
            "options": {
                "temperature": 0,
                "seed": 42,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        }
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                if attempt > 1:
                    body["options"]["num_predict"] = min(int(num_predict * (1.0 + 0.55 * (attempt - 1))), 7000)
                    body["options"]["temperature"] = 0.05 * (attempt - 1)
                    body["options"]["seed"] = 42 + attempt
                r = self.session.post(f"{self.base_url}/api/chat", json=body, timeout=timeout)
                r.raise_for_status()
                payload = r.json()
                content = (payload.get("message") or {}).get("content", "")
                if not content:
                    raise RuntimeError(f"Empty Ollama response: {payload}")
                try:
                    return _loads_json_loose(content)
                except Exception as parse_exc:
                    done_reason = payload.get("done_reason")
                    excerpt_pos = getattr(parse_exc, "pos", None)
                    if isinstance(excerpt_pos, int):
                        lo, hi = max(0, excerpt_pos - 180), min(len(content), excerpt_pos + 180)
                        excerpt = content[lo:hi].replace("\n", " ")
                    else:
                        excerpt = content[-360:].replace("\n", " ")
                    raise RuntimeError(
                        f"Malformed JSON from Ollama model={body['model']} "
                        f"(done_reason={done_reason}, chars={len(content)}): "
                        f"{parse_exc}; excerpt={excerpt!r}"
                    ) from parse_exc
            except Exception as exc:
                last = exc
                if attempt < attempts:
                    time.sleep(1.5 * attempt)
        raise RuntimeError(f"Ollama call failed after {attempts} attempts for model={body['model']}: {last}")

# ---------------------------
# Page classification from native text
# ---------------------------
def classify_native(text: str) -> str:
    t = fold(text)
    if "detail des postes du c p c" in t or "detail des postes du cpc" in t:
        return "DETAIL_CPC"
    if "passage du resultat net comptable" in t or "resultat net fiscal" in t:
        return "RESULTAT_FISCAL"
    if "etat des soldes de gestion" in t or "capacite d autofinancement" in t:
        return "ESG"
    if "compte de produits et charges" in t:
        return "CPC"
    if "bilan actif" in t or ("immobilisations corporelles" in t and "tresorerie actif" in t):
        return "BILAN_ACTIF"
    if "bilan passif" in t or ("capitaux propres" in t and "tresorerie passif" in t):
        return "BILAN_PASSIF"
    if "identification du contribuable" in t or "raison sociale" in t and "identifiant fiscal" in t:
        return "IDENTIFICATION"
    if len(t) < 10:
        return "VIDE"
    return "AUTRE"

# ---------------------------
# Orientation + scan layout agent
# ---------------------------
def _axis_score(im: Image.Image) -> float:
    sample = im.convert("RGB").copy()
    sample.thumbnail((500, 500), Image.Resampling.BILINEAR)
    gray = ImageOps.grayscale(sample)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    a = np.asarray(edges, dtype=np.uint8)
    binary = (a > 40).astype(np.float32)
    row = binary.sum(axis=1)
    col = binary.sum(axis=0)
    hvar = float(np.var(row))
    vvar = float(np.var(col))
    contrast = float(ImageStat.Stat(gray).stddev[0])
    return (hvar / (vvar + 1.0)) * (1.0 + contrast / 100.0)


def orientation_pair(im: Image.Image) -> tuple[list[int], float]:
    s0 = _axis_score(im)
    s90 = _axis_score(im.rotate(-90, expand=True))
    if s0 >= s90:
        ratio = s0 / max(s90, 1e-9)
        return [0, 180], ratio
    ratio = s90 / max(s0, 1e-9)
    return [90, 270], ratio


def crop_to_visible_content(im: Image.Image, pad_ratio: float = 0.025) -> Image.Image:
    """Crop large white margins without assuming portrait/landscape orientation."""
    rgb = im.convert("RGB")
    gray = np.asarray(ImageOps.grayscale(rgb))
    mask = gray < 246
    ys, xs = np.where(mask)
    if len(xs) < 100 or len(ys) < 100:
        return rgb
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    pad = int(max(rgb.size) * pad_ratio)
    x0, y0 = max(0, x0-pad), max(0, y0-pad)
    x1, y1 = min(rgb.width, x1+pad), min(rgb.height, y1+pad)
    crop = rgb.crop((x0, y0, x1, y1))
    # Avoid pathological crops caused by a stray mark.
    if crop.width * crop.height < 0.08 * rgb.width * rgb.height:
        return rgb
    return crop


def make_orientation_montage(im: Image.Image, candidates: list[int]) -> bytes:
    tiles = []
    for angle in candidates:
        cand = im if angle == 0 else im.rotate(-angle, expand=True)
        cand = crop_to_visible_content(cand)
        cand = cand.copy()
        cand.thumbnail((1150, 1050), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (1190, 1130), "white")
        tile.paste(cand, ((1190 - cand.width) // 2, 62))
        d = ImageDraw.Draw(tile)
        d.text((24, 20), f"CANDIDATE {angle} degrees clockwise", fill="black")
        tiles.append(tile)
    canvas = Image.new("RGB", (1190 * len(tiles), 1130), "white")
    for i, t in enumerate(tiles):
        canvas.paste(t, (i * 1190, 0))
    return image_bytes(canvas, quality=93, max_side=2400)


_ORIENTATION_SCHEMA = {
    "type": "object",
    "properties": {
        "rotation": {"type": "integer", "enum": [0, 90, 180, 270]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["rotation", "confidence"],
    "additionalProperties": False,
}

_PAGE_TYPE_SCHEMA = {
    "type": "object",
    "properties": {
        "page_type": {"type": "string", "enum": ALL_PAGE_TYPES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["page_type", "confidence"],
    "additionalProperties": False,
}


def _orientation_once(client: OllamaClient, im: Image.Image, candidates: list[int]) -> dict[str, Any]:
    montage = make_orientation_montage(im, candidates)
    prompt = f"""
This is a montage of the SAME Moroccan financial-statement page at clockwise rotations {candidates}.
Choose ONLY the rotation where French/Arabic text is physically upright and reads normally left-to-right.
Ignore whether the page is portrait or landscape. Read an actual title/row label before choosing.
Return only rotation and confidence. Rotation MUST be one of {candidates}.
""".strip()
    out = client.chat_json(prompt=prompt, images=[montage], schema=_ORIENTATION_SCHEMA, num_predict=220)
    if int(out.get("rotation", -1)) not in candidates:
        raise RuntimeError(f"Orientation agent returned invalid rotation {out}")
    return out


def _orientation_qwen_verify(client: OllamaClient, im: Image.Image, proposed_rotation: int) -> dict[str, Any] | None:
    """Independent visual second opinion for 0-vs-180 / 90-vs-270."""
    opposite = (int(proposed_rotation) + 180) % 360
    candidates = [int(proposed_rotation), opposite]
    montage = make_orientation_montage(im, candidates)
    prompt = f"""
Independent orientation verification for a Moroccan financial-statement page.
The two candidates are the SAME page at clockwise rotations {candidates}.
Choose the candidate where actual French/Arabic words, row labels, digits and the page number are physically upright.
Do NOT choose based on portrait/landscape shape. Read at least one title or row label.
Return only rotation and confidence.
""".strip()
    try:
        out = client.chat_json(
            prompt=prompt,
            images=[montage],
            schema=_ORIENTATION_SCHEMA,
            model=client.mapper_model,
            think=False,
            num_ctx=5000,
            num_predict=180,
            attempts=2,
        )
        if int(out.get("rotation", -1)) not in candidates:
            return None
        return out
    except Exception:
        return None


def scan_layout_agent(client: OllamaClient, im: Image.Image) -> dict[str, Any]:
    pair, ratio = orientation_pair(im)
    candidates = pair if ratio >= 1.08 else [0, 90, 180, 270]
    orient = _orientation_once(client, im, candidates)

    # A zero/low confidence answer must never be trusted silently. Retry with the
    # complementary candidate set / all rotations at higher visual scale.
    if float(orient.get("confidence", 0)) < 0.65:
        retry_candidates = [0, 90, 180, 270] if len(candidates) == 2 else candidates
        orient2 = _orientation_once(client, im, retry_candidates)
        if float(orient2.get("confidence", 0)) >= float(orient.get("confidence", 0)):
            orient = orient2
            candidates = retry_candidates

    glm_rotation = int(orient["rotation"])
    rotation = glm_rotation
    orientation_source = "glm_only"
    qorient = _orientation_qwen_verify(client, im, glm_rotation)
    qwen_rotation = None
    if qorient is not None:
        qwen_rotation = int(qorient["rotation"])
        qconf = float(qorient.get("confidence", 0.0) or 0.0)
        if qwen_rotation == glm_rotation:
            orientation_source = "glm+qwen_agree"
        elif qconf >= 0.75:
            rotation = qwen_rotation
            orientation_source = "qwen_override_180"
        else:
            orientation_source = "glm_kept_low_qwen_conf"

    oriented = im if rotation == 0 else im.rotate(-rotation, expand=True)
    view = crop_to_visible_content(oriented)
    prepared = image_bytes(view, quality=94, max_side=2000)
    prompt = """
Classify this ONE UPRIGHT Moroccan DGI financial-statement page by reading its visible title and row vocabulary.
Return exactly one page_type:
IDENTIFICATION, BILAN_ACTIF, BILAN_PASSIF, CPC, RESULTAT_FISCAL, ESG, DETAIL_CPC, AUTRE, VIDE.
Strong anchors:
- 'Bilan (actif)' or ACTIF/Immobilisations/Tresorerie-Actif -> BILAN_ACTIF
- 'Bilan (passif)' or CAPITAUX PROPRES/DETTES DU PASSIF/Tresorerie-Passif -> BILAN_PASSIF
- 'COMPTE DE PRODUITS ET CHARGES' -> CPC
- 'DETAIL DES POSTES DU C.P.C.' -> DETAIL_CPC
- 'PASSAGE DU RESULTAT NET COMPTABLE...' -> RESULTAT_FISCAL
- 'ETAT DES SOLDES DE GESTION' -> ESG
- taxpayer identification box -> IDENTIFICATION
Do not infer from page number alone. Return only page_type and confidence.
""".strip()
    typ = client.chat_json(prompt=prompt, images=[prepared], schema=_PAGE_TYPE_SCHEMA, num_predict=260)

    # If page type is low confidence, retry once with the full oriented page as a
    # separate visual view instead of the content crop.
    if float(typ.get("confidence", 0)) < 0.65:
        full = image_bytes(oriented, quality=94, max_side=2200)
        typ2 = client.chat_json(prompt=prompt, images=[full], schema=_PAGE_TYPE_SCHEMA, num_predict=260)
        if float(typ2.get("confidence", 0)) >= float(typ.get("confidence", 0)):
            typ = typ2

    qconf_final = float(qorient.get("confidence", 0.0) or 0.0) if qorient is not None else None
    orient_conf_final = float(orient.get("confidence", 0.0) or 0.0)
    if qconf_final is not None:
        orient_conf_final = min(orient_conf_final, qconf_final) if qwen_rotation == glm_rotation else qconf_final

    return {
        "rotation": rotation,
        "page_type": typ["page_type"],
        "confidence": min(orient_conf_final, float(typ.get("confidence", 0))),
        "orientation_confidence": orient_conf_final,
        "type_confidence": float(typ.get("confidence", 0)),
        "axis_ratio": ratio,
        "orientation_source": orientation_source,
        "glm_rotation": glm_rotation,
        "qwen_rotation": qwen_rotation,
    }

# ---------------------------
# Table crop for scanned pages
# ---------------------------
def detect_grid_crop(im: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int] | None]:
    arr = np.asarray(im.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 35, 12
    )
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, w // 25), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, h // 25)))
    hor = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)
    ver = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk)
    grid = cv2.bitwise_or(hor, ver)
    grid = cv2.dilate(grid, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[tuple[int, int, int, int, int]] = []
    for c in contours:
        x, y, ww, hh = cv2.boundingRect(c)
        area = ww * hh
        if area < 0.001 * w * h:
            continue
        if ww < 0.08 * w or hh < 0.03 * h:
            continue
        boxes.append((x, y, ww, hh, area))
    if not boxes:
        return im, None
    boxes.sort(key=lambda b: b[4], reverse=True)
    largest = boxes[0][4]
    keep = [b for b in boxes if b[4] >= max(largest * 0.03, 0.0008 * w * h)]
    x0 = min(b[0] for b in keep)
    y0 = min(b[1] for b in keep)
    x1 = max(b[0] + b[2] for b in keep)
    y1 = max(b[1] + b[3] for b in keep)
    pad = max(16, int(0.018 * max(w, h)))
    box = (max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad))
    crop = im.crop(box)
    # If the crop is suspiciously tiny, retain full page to avoid losing evidence.
    if crop.width * crop.height < 0.055 * w * h:
        return im, None
    return crop, box

# ---------------------------
# Native table extraction
# ---------------------------
@dataclass
class EvidenceRow:
    page: int
    page_type: str
    source: str
    field_code: str
    raw_label: str
    cells: dict[str, str | None]
    confidence: float = 1.0
    rotation: int = 0
    crop_box: tuple[int, int, int, int] | None = None
    notes: list[str] = field(default_factory=list)
    mapping_source: str | None = None
    mapping_confidence: float | None = None
    mapping_note: str | None = None


def _best_table(page: pymupdf.Page):
    for strategy in ("lines_strict", "lines", "text"):
        try:
            tf = page.find_tables(strategy=strategy)
            if tf.tables:
                return max(tf.tables, key=lambda t: t.row_count * t.col_count)
        except Exception:
            continue
    return None


def _join_label(row: list[Any]) -> str:
    parts = []
    for x in row[:2]:
        if x:
            s = re.sub(r"\s+", " ", str(x).replace("\n", " ")).strip()
            if s and s not in parts:
                parts.append(s)
    return " | ".join(parts)


def extract_native_table_rows(page: pymupdf.Page, page_type: str, page_no: int) -> list[EvidenceRow]:
    table = _best_table(page)
    if table is None:
        return []
    matrix = table.extract()
    rows: list[EvidenceRow] = []
    for r in matrix[1:]:
        label = _join_label(r)
        if not label:
            continue
        cells: dict[str, str | None] = {}
        def at(i: int) -> str | None:
            if i >= len(r) or r[i] is None:
                return None
            v = re.sub(r"\s+", " ", str(r[i])).strip()
            return v or None
        if page_type == "BILAN_ACTIF" and len(r) >= 6:
            cells = {"BRUT": at(2), "AMORT_PROV": at(3), "NET_N": at(4), "NET_N1": at(5)}
        elif page_type == "BILAN_PASSIF" and len(r) >= 4:
            cells = {"EXERCICE_N": at(2), "EXERCICE_N1": at(3)}
        elif page_type == "CPC" and len(r) >= 6:
            cells = {"OP_N": at(2), "OP_PREV": at(3), "TOTAL_N": at(4), "TOTAL_N1": at(5)}
        elif page_type == "DETAIL_CPC" and len(r) >= 4:
            cells = {"EXERCICE_N": at(2), "EXERCICE_N1": at(3)}
        else:
            continue
        rows.append(EvidenceRow(page_no, page_type, "native_table", "ROW", label, cells, 1.0, 0))
    return rows


def extract_native_identification(text: str, page_no: int) -> list[EvidenceRow]:
    fields: dict[str, str] = {}
    patterns = {
        "IDENTIFIANT_FISCAL": r"Identifiant fiscal\s+([^\n]+)",
        "RAISON_SOCIALE": r"Raison Sociale\s*:\s*([^\n]+)",
        "ICE": r"ICE\s*:\s*([^\n]+)",
        "TAXE_PROFESSIONNELLE": r"Art\. Taxe professionnelle\s+([^\n]+)",
        "ADRESSE": r"Adresse\s*:\s*([^\n]+)",
        "VILLE": r"Ville\s*:\s*([^\n]+)",
    }
    for code, pat in patterns.items():
        m = re.search(pat, text, re.I)
        if m:
            fields[code] = re.sub(r"\s+", " ", m.group(1)).strip()
    out = []
    for code, val in fields.items():
        out.append(EvidenceRow(page_no, "IDENTIFICATION", "native_text", code, code, {"TEXT": val}))
    return out

# ---------------------------
# Scan OCR -> RAW evidence only, then semantic mapping
# ---------------------------
# The VLM sees numbers. The reasoning mapper NEVER sees numbers.
# This separation is deliberate: semantic reasoning is allowed to decide what a row MEANS,
# but it can never rewrite, select, or invent a financial amount.

COLUMNS = ["BRUT", "AMORT_PROV", "NET_N", "NET_N1", "EXERCICE_N", "EXERCICE_N1", "OP_N", "OP_PREV", "TOTAL_N", "TOTAL_N1"]

# Canonical internal evidence codes used by the RCC resolver.
FIELD_CODES: dict[str, list[str]] = {
    "BILAN_ACTIF": [
        "TOTAL_ACTIF", "ACTIFS_IMMOBILISES", "ACTIF_CIRCULANT", "STOCKS",
        "CLIENTS", "TRESORERIE_ACTIF", "CAISSE"
    ],
    "BILAN_PASSIF": [
        "TOTAL_PASSIF", "FONDS_PROPRES", "RESULTAT_NET", "DETTES_FINANCEMENT",
        "DETTES_BANCAIRES_CT", "PASSIF_CIRCULANT", "FOURNISSEURS",
        "COMPTE_COURANT_ASSOCIES", "TRESORERIE_PASSIF"
    ],
    "CPC": [
        "CHIFFRE_AFFAIRES", "VENTES_MARCHANDISES", "VENTES_BIENS_SERVICES",
        "ACHATS_REVENDUS", "ACHATS_CONSOMMES", "AUTRES_CHARGES_EXTERNES",
        "CHARGES_INTERETS", "CHARGES_FINANCIERES", "RESULTAT_EXPLOITATION", "RESULTAT_NET"
    ],
    "DETAIL_CPC": [
        "ACHATS_REVENDUS_TOTAL", "ACHATS_CONSOMMES_TOTAL", "AUTRES_CHARGES_EXTERNES_TOTAL",
        "EXPORT_MARCHANDISES", "EXPORT_BIENS", "EXPORT_SERVICES"
    ],
}

# Physical row labels we ask the vision model to locate.  These are NOT RCC field codes.
# Keeping them separate prevents the VLM from deciding accounting semantics.
RAW_ANCHORS: dict[str, list[tuple[str, str]]] = {
    "BILAN_ACTIF": [
        ("a01", "TOTAL I (A+B+C+D+E)"),
        ("a02", "Stocks (F)"),
        ("a03", "TOTAL II (F+G+H+I)"),
        ("a04", "Clients et comptes rattachés"),
        ("a05", "TOTAL III / TRESORERIE - ACTIF"),
        ("a06", "Caisse, Régie d'avances et accréditifs"),
        ("a07", "TOTAL GENERAL I+II+III"),
    ],
    "BILAN_PASSIF": [
        ("p01", "Total des capitaux propres (A)"),
        ("p02", "Résultat net de l'exercice (2)"),
        ("p03", "DETTES DE FINANCEMENT (C) / total of that section"),
        ("p04", "Crédits de trésorerie / Concours bancaires courants"),
        ("p05", "TOTAL II (F+G+H)"),
        ("p06", "Fournisseurs et comptes rattachés"),
        ("p07", "Comptes d'associés"),
        ("p08", "TOTAL III / TRESORERIE - PASSIF"),
        ("p09", "TOTAL GENERAL I+II+III"),
    ],
    "CPC": [
        ("c01", "Chiffre(s) d'affaires"),
        ("c02", "Ventes de marchandises (en l'état)"),
        ("c03", "Ventes de biens et services produits"),
        ("c04", "Achats revendus de marchandises"),
        ("c05", "Achats consommés de matières et fournitures"),
        ("c06", "Autres charges externes"),
        ("c07", "Charges d'intérêts"),
        ("c08", "Total / CHARGES FINANCIERES"),
        ("c09", "III. RESULTAT D'EXPLOITATION (I-II)"),
        ("c10", "Résultat net de l'exercice / résultat net final"),
    ],
    "DETAIL_CPC": [
        ("d01", "611 - Achats revendus de marchandises : its Total row"),
        ("d02", "612 - Achats consommés de matières et fournitures : its Total row"),
        ("d03", "613/614 - Autres charges externes : its Total row"),
        ("d04", "Ventes de marchandises à l'étranger"),
        ("d05", "Ventes de biens à l'étranger"),
        ("d06", "Ventes de services à l'étranger"),
    ],
}

# Anchor label validators are optical guardrails only. They do not perform RCC mapping.
ANCHOR_HINTS: dict[str, list[str]] = {
    "a01": ["total i (a+b+c+d+e)", "total i a+b+c+d+e"],
    "a02": ["stocks (f)", "stocks f"],
    "a03": ["total ii (f+g+h+i)", "total ii f+g+h+i"],
    "a04": ["clients et comptes rattaches"],
    "a05": ["total iii", "tresorerie actif"],
    "a06": ["caisse regie d avances et accreditifs", "caisse"],
    "a07": ["total general i+ii+iii"],
    "p01": ["total des capitaux propres (a)", "total des capitaux propres"],
    "p02": ["resultat net de l exercice", "resultat net"],
    "p03": ["dettes de financement (c)", "dettes de financement"],
    "p04": ["credits de tresorerie", "concours bancaires courants"],
    "p05": ["total ii (f+g+h)", "total ii f+g+h"],
    "p06": ["fournisseurs et comptes rattaches"],
    "p07": ["comptes d associes"],
    "p08": ["total iii", "tresorerie passif"],
    "p09": ["total general i+ii+iii"],
    "c01": ["chiffres d affaires", "chiffre d affaires"],
    "c02": ["ventes de marchandises"],
    "c03": ["ventes de biens et services produits"],
    "c04": ["achats revendus"],
    "c05": ["achats consommes"],
    "c06": ["autres charges externes"],
    "c07": ["charges d interets"],
    "c08": ["charges financieres", "total v"],
    "c09": ["resultat d exploitation"],
    "c10": ["resultat net"],
    "d01": ["total"],
    "d02": ["total"],
    "d03": ["total"],
    "d04": ["ventes de marchandises a l etranger"],
    "d05": ["ventes de biens a l etranger"],
    "d06": ["ventes de services a l etranger"],
}

# High-risk adjacent/blank rows are always re-read alone. A dedicated read may
# explicitly prove that the target row is present but blank, preventing a
# neighboring row's amount from being copied into the target.
SINGLE_VERIFY_ANCHORS = {
    "c04",  # Achats revendus: often blank immediately above Achats consommés
    "c07",  # Charges d'intérêts may live on a continuation CPC page
    "d01", "d02", "d03",  # Detail CPC totals
    "d04", "d05", "d06",  # explicit export rows, often blank
}

# Strong deterministic mappings for standard DGI labels. The reasoning model is a fallback,
# not a replacement for exact known form semantics.
RULE_MAP: dict[str, dict[str, str]] = {
    "BILAN_ACTIF": {
        "a01": "ACTIFS_IMMOBILISES", "a02": "STOCKS", "a03": "ACTIF_CIRCULANT",
        "a04": "CLIENTS", "a05": "TRESORERIE_ACTIF", "a06": "CAISSE", "a07": "TOTAL_ACTIF",
    },
    "BILAN_PASSIF": {
        "p01": "FONDS_PROPRES", "p02": "RESULTAT_NET", "p03": "DETTES_FINANCEMENT",
        "p04": "DETTES_BANCAIRES_CT", "p05": "PASSIF_CIRCULANT", "p06": "FOURNISSEURS",
        "p07": "COMPTE_COURANT_ASSOCIES", "p08": "TRESORERIE_PASSIF", "p09": "TOTAL_PASSIF",
    },
    "CPC": {
        "c01": "CHIFFRE_AFFAIRES", "c02": "VENTES_MARCHANDISES", "c03": "VENTES_BIENS_SERVICES",
        "c04": "ACHATS_REVENDUS", "c05": "ACHATS_CONSOMMES", "c06": "AUTRES_CHARGES_EXTERNES",
        "c07": "CHARGES_INTERETS", "c08": "CHARGES_FINANCIERES", "c09": "RESULTAT_EXPLOITATION",
        "c10": "RESULTAT_NET",
    },
    "DETAIL_CPC": {
        "d01": "ACHATS_REVENDUS_TOTAL", "d02": "ACHATS_CONSOMMES_TOTAL",
        "d03": "AUTRES_CHARGES_EXTERNES_TOTAL", "d04": "EXPORT_MARCHANDISES",
        "d05": "EXPORT_BIENS", "d06": "EXPORT_SERVICES",
    },
}

FIELD_DEFINITIONS: dict[str, str] = {
    "TOTAL_ACTIF": "Bilan actif row TOTAL GENERAL I+II+III",
    "ACTIFS_IMMOBILISES": "Bilan actif TOTAL I (A+B+C+D+E), net current year",
    "ACTIF_CIRCULANT": "Bilan actif TOTAL II (F+G+H+I), net current year",
    "STOCKS": "Stocks (F), net current year",
    "CLIENTS": "Clients et comptes rattachés on the asset side",
    "TRESORERIE_ACTIF": "Bilan actif TOTAL III / trésorerie-actif",
    "CAISSE": "Caisse, Régie d'avances et accréditifs",
    "TOTAL_PASSIF": "Bilan passif TOTAL GENERAL I+II+III",
    "FONDS_PROPRES": "Total des capitaux propres (A)",
    "RESULTAT_NET": "Résultat net de l'exercice",
    "DETTES_FINANCEMENT": "Dettes de financement (C), current exercise only",
    "DETTES_BANCAIRES_CT": "Crédits de trésorerie / concours bancaires courants",
    "PASSIF_CIRCULANT": "Bilan passif TOTAL II (F+G+H)",
    "FOURNISSEURS": "Fournisseurs et comptes rattachés on passif",
    "COMPTE_COURANT_ASSOCIES": "Comptes d'associés on passif",
    "TRESORERIE_PASSIF": "Bilan passif TOTAL III / trésorerie-passif",
    "CHIFFRE_AFFAIRES": "Explicit chiffre(s) d'affaires row in CPC",
    "VENTES_MARCHANDISES": "Ventes de marchandises row in CPC",
    "VENTES_BIENS_SERVICES": "Ventes de biens et services produits row in CPC",
    "ACHATS_REVENDUS": "Achats revendus row in CPC",
    "ACHATS_CONSOMMES": "Achats consommés row in CPC",
    "AUTRES_CHARGES_EXTERNES": "Autres charges externes row in CPC",
    "CHARGES_INTERETS": "Charges d'intérêts row in CPC",
    "CHARGES_FINANCIERES": "Total charges financières row",
    "RESULTAT_EXPLOITATION": "III. Résultat d'exploitation (I-II)",
    "ACHATS_REVENDUS_TOTAL": "Detail CPC group 611 Total",
    "ACHATS_CONSOMMES_TOTAL": "Detail CPC group 612 Total",
    "AUTRES_CHARGES_EXTERNES_TOTAL": "Detail CPC group 613/614 Total",
    "EXPORT_MARCHANDISES": "Detail CPC Ventes de marchandises à l'étranger",
    "EXPORT_BIENS": "Detail CPC Ventes de biens à l'étranger",
    "EXPORT_SERVICES": "Detail CPC Ventes de services à l'étranger",
}


def _raw_rows_schema(anchor_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "anchor_id": {"type": "string", "enum": anchor_ids},
                        "raw_label": {"type": "string"},
                        "context": {"type": "string"},
                        "cells": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "column": {"type": "string", "enum": COLUMNS},
                                    "raw_value": {"type": "string"},
                                },
                                "required": ["column", "raw_value"],
                                "additionalProperties": False,
                            },
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["anchor_id", "raw_label", "context", "cells", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["rows"],
        "additionalProperties": False,
    }


def _raw_extraction_prompt(page_type: str, batch: list[tuple[str, str]]) -> str:
    anchors = "\n".join(f"- {aid}: {label}" for aid, label in batch)
    if page_type == "BILAN_ACTIF":
        columns = "BRUT, AMORT_PROV, NET_N, NET_N1"
        contract = "BRUT=Brut exercice; AMORT_PROV=Amortissements/provisions; NET_N=Net exercice; NET_N1=Net exercice précédent."
    elif page_type in {"BILAN_PASSIF", "DETAIL_CPC"}:
        columns = "EXERCICE_N, EXERCICE_N1"
        contract = "EXERCICE_N=current exercise; EXERCICE_N1=previous exercise."
    elif page_type == "CPC":
        columns = "OP_N, OP_PREV, TOTAL_N, TOTAL_N1"
        contract = "OP_N=operations proper to exercise; OP_PREV=prior-exercise operations; TOTAL_N=current total; TOTAL_N1=previous exercise total."
    else:
        raise ValueError(page_type)
    return f"""
You are a high-precision OCR/table-copy agent. Read ONE upright Moroccan DGI {page_type} page.
You are NOT allowed to decide RCC field names or accounting meaning. You only copy visible evidence.

Requested PHYSICAL row anchors:
{anchors}

Column contract: {contract}
Allowed columns: {columns}.

Rules:
- Return a row ONLY when that requested physical row is actually visible on this page.
- `anchor_id` identifies only which requested physical label you matched; it is NOT a financial field code.
- `raw_label` must be the exact visible row label as printed.
- `context` is a short visible heading/section immediately around the row; for Detail CPC Total rows include 611/612/613/614 context.
- Copy every digit exactly. Preserve minus sign, spaces and decimal comma.
- Never calculate, normalize, repair or infer an amount.
- Blank cell != 0. If blank, omit that column from cells. Only emit 0,00 when visibly printed.
- Never substitute TOTAL I for TOTAL GENERAL, or TOTAL II for TOTAL III.
- Keep JSON compact.
""".strip()


def _anchor_matches(anchor_id: str, raw_label: str, context: str = "") -> bool:
    f = fold(raw_label)
    ctx = fold(context)
    hints = ANCHOR_HINTS.get(anchor_id, [])
    if anchor_id in {"d01", "d02", "d03"}:
        # Generic Total is accepted only when its group context is explicit.
        if "total" not in f:
            return False
        required = {"d01": "611", "d02": "612", "d03": "613"}[anchor_id]
        if required == "613":
            return ("613" in ctx or "614" in ctx) and "autres charges externes" in ctx
        return required in ctx
    return any(fold(h) in f or f in fold(h) for h in hints if fold(h))


def _evidence_confidence(page_type: str, cells: dict[str, str | None], model_conf: float) -> tuple[float, list[str]]:
    notes: list[str] = []
    base = min(max(float(model_conf or 0.0), 0.0), 0.95)
    if base < 0.75:
        base = 0.80
    if page_type == "BILAN_ACTIF":
        b = parse_amount(cells.get("BRUT")); a = parse_amount(cells.get("AMORT_PROV")); n = parse_amount(cells.get("NET_N"))
        if b is not None and a is not None and n is not None:
            diff = (b - a) - n
            if abs(diff) <= Decimal("0.02"):
                return 0.99, notes
            notes.append(f"row arithmetic failed: BRUT-AMORT_PROV-NET_N={diff}")
            return 0.40, notes
    return base, notes


def _financial_numeric_columns(page_type: str) -> list[str]:
    return {
        "BILAN_ACTIF": ["BRUT", "AMORT_PROV", "NET_N", "NET_N1"],
        "BILAN_PASSIF": ["EXERCICE_N", "EXERCICE_N1"],
        "CPC": ["OP_N", "OP_PREV", "TOTAL_N", "TOTAL_N1"],
        "DETAIL_CPC": ["EXERCICE_N", "EXERCICE_N1"],
    }.get(page_type, [])


def _row_numeric_cell_count(row: EvidenceRow) -> int:
    return sum(parse_amount(row.cells.get(c)) is not None for c in _financial_numeric_columns(row.page_type))


def _decode_raw_anchor_rows(
    data: dict[str, Any],
    *,
    allowed_ids: list[str],
    page_type: str,
    page_no: int,
    rotation: int,
    crop_box=None,
    retry_tag: str | None = None,
    source: str = "glm_raw_vision",
) -> list[tuple[str, EvidenceRow]]:
    decoded: list[tuple[str, EvidenceRow]] = []
    for rr in data.get("rows", []):
        aid = str(rr.get("anchor_id", ""))
        label = str(rr.get("raw_label", "")).strip()
        context = str(rr.get("context", "")).strip()
        if aid not in allowed_ids or not label or not _anchor_matches(aid, label, context):
            continue
        cells: dict[str, str | None] = {}
        for c in rr.get("cells", []):
            col, raw = c.get("column"), c.get("raw_value")
            if col in COLUMNS and isinstance(raw, str) and raw.strip():
                cells[col] = raw.strip()
        conf, notes = _evidence_confidence(page_type, cells, float(rr.get("confidence", 0.7)))
        notes = list(notes)
        notes.append(f"anchor_id={aid}")
        if context:
            notes.append(f"context={context}")
        if retry_tag:
            notes.append(retry_tag)
        notes.append("row_present=true")
        if not cells:
            notes.append("explicit_blank=true")
        decoded.append((aid, EvidenceRow(
            page_no, page_type, source, "UNMAPPED", label, cells,
            conf, rotation, crop_box, notes, mapping_source=None, mapping_confidence=None, mapping_note=None,
        )))
    return decoded


def _better_raw_row(new: EvidenceRow, old: EvidenceRow | None) -> bool:
    if old is None:
        return True
    nc, oc = _row_numeric_cell_count(new), _row_numeric_cell_count(old)
    if nc != oc:
        return nc > oc
    # Prefer arithmetic-consistent / higher-confidence evidence when the amount coverage ties.
    return new.confidence > old.confidence


def extract_raw_scan_rows(client: OllamaClient, prepared: bytes, page_type: str, page_no: int, rotation: int, crop_box=None) -> list[EvidenceRow]:
    """Extract raw table rows with a two-pass strategy.

    Pass 1 is efficient batched OCR. Pass 2 retries each missing or value-less
    physical anchor individually. This directly targets the failure mode where
    GLM recognizes a label but omits its numeric cells, or silently skips one
    requested row inside a dense batch.

    A row with no explicit numeric cell is not emitted as financial evidence.
    Therefore a visible blank remains missing; it is never converted to zero.
    """
    anchors = RAW_ANCHORS[page_type]
    best: dict[str, EvidenceRow] = {}

    # Pass 1: small batches for throughput.
    batch_size = 3
    for start in range(0, len(anchors), batch_size):
        batch = anchors[start:start + batch_size]
        ids = [a for a, _ in batch]
        data = client.chat_json(
            prompt=_raw_extraction_prompt(page_type, batch),
            images=[prepared],
            schema=_raw_rows_schema(ids),
            model=client.model,
            think=False,
            num_ctx=10000,
            num_predict=1500,
        )
        for aid, row in _decode_raw_anchor_rows(
            data, allowed_ids=ids, page_type=page_type, page_no=page_no,
            rotation=rotation, crop_box=crop_box, retry_tag="pass=batch",
        ):
            if _better_raw_row(row, best.get(aid)):
                best[aid] = row

    # Pass 2: targeted single-anchor retry for omitted rows OR labels returned
    # without any explicit amount. Single-anchor prompts reduce attention competition
    # and malformed/partial JSON on dense financial tables.
    retry_anchors: list[tuple[str, str]] = []
    for aid, label in anchors:
        current = best.get(aid)
        if current is None or _row_numeric_cell_count(current) == 0 or aid in SINGLE_VERIFY_ANCHORS:
            retry_anchors.append((aid, label))

    for aid, label in retry_anchors:
        prompt = _raw_extraction_prompt(page_type, [(aid, label)]) + """

RETRY MODE FOR ONE ROW ONLY:
- Search the entire page for this exact physical row.
- If it is present, copy ALL numeric cells on that same row under the defined columns.
- Do not copy a nearby subtotal or similarly named row.
- If the row is not present, return {\"rows\": []}.
- If the row is present but its financial cells are truly blank, return the row with an empty cells array.
"""
        try:
            data = client.chat_json(
                prompt=prompt,
                images=[prepared],
                schema=_raw_rows_schema([aid]),
                model=client.model,
                think=False,
                num_ctx=12000,
                num_predict=900,
            )
        except Exception:
            # Keep the first-pass evidence; page-level pipeline must not fail only
            # because one optional anchor retry failed.
            continue
        for got_aid, row in _decode_raw_anchor_rows(
            data, allowed_ids=[aid], page_type=page_type, page_no=page_no,
            rotation=rotation, crop_box=crop_box, retry_tag="pass=single_anchor_retry",
        ):
            if got_aid in SINGLE_VERIFY_ANCHORS:
                best[got_aid] = row
            elif _better_raw_row(row, best.get(got_aid)):
                best[got_aid] = row

    # Keep physically present rows even when the current-value cell is blank.
    # They are negative evidence ("blank on form"), never numeric zero.
    out = list(best.values())
    anchor_order = {aid: i for i, (aid, _) in enumerate(anchors)}
    out.sort(key=lambda r: anchor_order.get(next((n.split('=',1)[1] for n in r.notes if n.startswith('anchor_id=')), ''), 999))
    return out



# ---------------------------
# V8: grid-guided scan extraction
# ---------------------------
@dataclass
class ScanGridGeometry:
    """Physical geometry of a scanned financial table.

    Python/OpenCV owns geometry. GLM only locates a physical row by label and
    transcribes already-isolated label/numeric cells. This prevents the VLM from
    shifting values to neighboring rows or columns on dense scanned tables.
    """
    label_x0: int
    label_x1: int
    column_bounds: list[int]
    row_bounds: list[int]
    row_intervals: list[tuple[int, int]]
    table_box: tuple[int, int, int, int]


def _cluster_positions(values: list[float | int], tolerance: int = 10) -> list[int]:
    vals = sorted(float(v) for v in values)
    if not vals:
        return []
    groups: list[list[float]] = []
    for v in vals:
        if not groups or v - groups[-1][-1] > tolerance:
            groups.append([v])
        else:
            groups[-1].append(v)
    return [int(round(sum(g) / len(g))) for g in groups]


def _contiguous_true(mask: np.ndarray) -> list[tuple[int, int]]:
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return []
    cuts = np.where(np.diff(idx) > 1)[0]
    starts = np.r_[idx[0], idx[cuts + 1]]
    ends = np.r_[idx[cuts] + 1, idx[-1] + 1]
    return [(int(a), int(b)) for a, b in zip(starts, ends)]


def detect_scan_grid_geometry(im: Image.Image, page_type: str) -> ScanGridGeometry:
    """Detect numeric columns and logical row bands from table ruling lines.

    The important design choice is that amount-column boundaries are obtained
    deterministically from vertical grid lines. Row boundaries are obtained from
    horizontal line projections in the numeric region, where DGI forms have the
    cleanest ruling lines.
    """
    expected_numeric_cols = {
        "BILAN_ACTIF": 4,
        "BILAN_PASSIF": 2,
        "CPC": 4,
        "DETAIL_CPC": 2,
    }.get(page_type)
    if expected_numeric_cols is None:
        raise ValueError(f"Grid geometry unsupported for page_type={page_type}")

    rgb = np.asarray(im.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 35, 12,
    )

    # Long vertical components identify table column separators. Very-near-page
    # borders are excluded because scanned pages often have black scanner edges.
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, h // 18)))
    ver = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk)
    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(ver, 8)
    vertical_components: list[tuple[float, int, int, int, int, int]] = []
    xs: list[float] = []
    for i in range(1, n):
        x, y, ww, hh, area = [int(v) for v in stats[i]]
        if hh <= 0.12 * h:
            continue
        if ww >= 0.03 * w:
            continue
        if x <= 0.02 * w or x + ww >= 0.98 * w:
            continue
        cx = x + ww / 2.0
        xs.append(cx)
        vertical_components.append((cx, x, y, ww, hh, area))

    x_lines = _cluster_positions(xs, tolerance=max(6, int(w * 0.005)))
    needed = expected_numeric_cols + 1
    if len(x_lines) < needed + 1:
        raise RuntimeError(
            f"Not enough vertical grid lines for {page_type}: found={x_lines}, needed>={needed+1}"
        )

    # DGI forms place numeric amount columns on the right. The right-most N+1
    # strong separators define N numeric cells exactly.
    column_bounds = x_lines[-needed:]
    amount_x0, amount_x1 = column_bounds[0], column_bounds[-1]
    preceding = [x for x in x_lines if x < amount_x0 - 10]
    label_x0 = preceding[-1] if preceding else max(0, amount_x0 - int(0.40 * w))
    label_x1 = amount_x0

    # Estimate vertical extent from the selected amount-column separators.
    y_spans: list[tuple[int, int]] = []
    for xb in column_bounds:
        for cx, _x, y, _ww, hh, _area in vertical_components:
            if abs(cx - xb) <= max(8, int(w * 0.006)):
                y_spans.append((y, y + hh))
    if not y_spans:
        raise RuntimeError("Could not determine table vertical extent from grid lines")
    y_min = max(0, min(a for a, _ in y_spans) - 6)
    y_max = min(h, max(b for _, b in y_spans) + 6)

    # Horizontal ruling is most reliable inside the numeric region because text
    # labels do not interrupt those lines as much as in the left description cell.
    amount_roi = bw[y_min:y_max, amount_x0:amount_x1]
    if amount_roi.size == 0 or amount_roi.shape[1] < 80:
        raise RuntimeError("Numeric table region is too small for row detection")
    hk = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(30, amount_roi.shape[1] // 12), 1)
    )
    hor = cv2.morphologyEx(amount_roi, cv2.MORPH_OPEN, hk)
    projection = (hor > 0).sum(axis=1)
    threshold = max(15.0, 0.10 * amount_roi.shape[1])
    segments = _contiguous_true(projection > threshold)
    centers = [
        y_min + (a + b) // 2
        for a, b in segments
        if 1 <= (b - a) <= max(16, int(h * 0.008))
    ]
    row_bounds = _cluster_positions(centers, tolerance=max(4, int(h * 0.002)))

    # Drop duplicate/near-identical boundaries and form candidate logical rows.
    clean_bounds: list[int] = []
    for y in row_bounds:
        if not clean_bounds or y - clean_bounds[-1] >= max(7, int(h * 0.002)):
            clean_bounds.append(y)
    row_intervals: list[tuple[int, int]] = []
    max_row_h = max(150, int(h * 0.07))
    for a, b in zip(clean_bounds, clean_bounds[1:]):
        rh = b - a
        if 10 <= rh <= max_row_h:
            row_intervals.append((a, b))

    if len(row_intervals) < 6:
        raise RuntimeError(
            f"Too few row intervals detected for {page_type}: {len(row_intervals)}"
        )

    return ScanGridGeometry(
        label_x0=int(label_x0),
        label_x1=int(label_x1),
        column_bounds=[int(x) for x in column_bounds],
        row_bounds=[int(y) for y in clean_bounds],
        row_intervals=row_intervals,
        table_box=(int(label_x0), int(y_min), int(amount_x1), int(y_max)),
    )


def _candidate_row_ids(im: Image.Image, geom: ScanGridGeometry) -> list[str]:
    """Keep intervals that contain visible ink in the label cell."""
    ids: list[str] = []
    for idx, (y0, y1) in enumerate(geom.row_intervals):
        if y1 - y0 < 10:
            continue
        label = np.asarray(
            im.crop((geom.label_x0 + 3, y0 + 2, geom.label_x1 - 3, y1 - 2)).convert("L")
        )
        if label.size == 0:
            continue
        if int((label < 235).sum()) < max(25, int(label.size * 0.0015)):
            continue
        ids.append(f"R{idx:02d}")
    return ids


def make_row_locator_montage(
    im: Image.Image,
    geom: ScanGridGeometry,
    row_ids: list[str],
) -> bytes:
    """Create a label-only row atlas. No numeric cells are shown to the locator."""
    tile_w, tile_h, cols = 820, 110, 2
    rows = max(1, math.ceil(len(row_ids) / cols))
    canvas = Image.new("RGB", (tile_w * cols, tile_h * rows), "white")
    draw = ImageDraw.Draw(canvas)
    for j, rid in enumerate(row_ids):
        idx = int(rid[1:])
        y0, y1 = geom.row_intervals[idx]
        strip = im.crop((
            geom.label_x0 + 2, max(0, y0 + 1),
            geom.label_x1 - 2, min(im.height, y1 - 1),
        )).convert("RGB")
        maxw, maxh = 700, 82
        scale = min(maxw / max(strip.width, 1), maxh / max(strip.height, 1))
        # Upscaling small row strips is intentional: the locator reads labels only.
        if abs(scale - 1.0) > 0.05:
            strip = strip.resize(
                (max(1, int(strip.width * scale)), max(1, int(strip.height * scale))),
                Image.Resampling.LANCZOS,
            )
        col, rr = j % cols, j // cols
        x0, yy = col * tile_w, rr * tile_h
        canvas.paste(strip, (x0 + 96, yy + (tile_h - strip.height) // 2))
        draw.text((x0 + 12, yy + 42), rid, fill="black")
        draw.rectangle((x0 + 4, yy + 4, x0 + tile_w - 5, yy + tile_h - 5), outline="gray")
    return image_bytes(canvas, quality=95, max_side=3200)


def _row_locator_schema(anchor_ids: list[str], row_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "anchor_id": {"type": "string", "enum": anchor_ids},
                        "row_id": {"type": "string", "enum": row_ids + ["ABSENT"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["anchor_id", "row_id", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["matches"],
        "additionalProperties": False,
    }


def locate_anchor_rows_by_geometry(
    client: OllamaClient,
    montage: bytes,
    page_type: str,
    anchors: list[tuple[str, str]],
    row_ids: list[str],
) -> dict[str, tuple[str, float]]:
    """Ask GLM only WHICH isolated label row contains each physical anchor."""
    found: dict[str, tuple[str, float]] = {}
    batch_size = 5
    for start in range(0, len(anchors), batch_size):
        batch = anchors[start:start + batch_size]
        ids = [aid for aid, _ in batch]
        wanted = "\n".join(f"- {aid}: {label}" for aid, label in batch)
        prompt = f"""
This image is a ROW ATLAS built deterministically from ONE upright Moroccan DGI {page_type} table.
Each tile contains ONLY the label/description cell of one physical row and is tagged Rxx.
No financial amounts are shown here.

Locate these requested physical row labels:
{wanted}

Rules:
- Return the row_id whose visible text is the requested row.
- If that row is NOT physically present on this page, return row_id=ABSENT.
- Never use a neighboring row just because it has a number on the original table.
- TOTAL I is not TOTAL GENERAL; TOTAL II is not TOTAL III.
- For spelling/OCR variation, require the same accounting label meaning, not a nearby label.
""".strip()
        data = client.chat_json(
            prompt=prompt,
            images=[montage],
            schema=_row_locator_schema(ids, row_ids),
            model=client.model,
            think=False,
            num_ctx=8000,
            num_predict=700,
            attempts=2,
        )
        for m in data.get("matches", []):
            aid = str(m.get("anchor_id", ""))
            rid = str(m.get("row_id", "ABSENT"))
            conf = float(m.get("confidence", 0.0) or 0.0)
            if aid in ids and rid in row_ids + ["ABSENT"]:
                found[aid] = (rid, conf)
    return found


def make_isolated_row_cell_montage(
    im: Image.Image,
    geom: ScanGridGeometry,
    row_index: int,
    page_type: str,
) -> bytes:
    """Create one row where every physical amount column is a separate panel."""
    columns = _financial_numeric_columns(page_type)
    if len(columns) != len(geom.column_bounds) - 1:
        raise RuntimeError(
            f"Geometry/column mismatch: {page_type} columns={columns}, bounds={geom.column_bounds}"
        )
    y0, y1 = geom.row_intervals[row_index]
    panels: list[tuple[str, Image.Image, int]] = []
    label = im.crop((geom.label_x0 + 3, y0 + 2, geom.label_x1 - 3, y1 - 2))
    panels.append(("LABEL", label, 720))
    for name, x0, x1 in zip(columns, geom.column_bounds[:-1], geom.column_bounds[1:]):
        cell = im.crop((x0 + 4, y0 + 2, x1 - 4, y1 - 2))
        panels.append((name, cell, 310))

    height = 170
    canvas = Image.new("RGB", (sum(width for _, _, width in panels), height), "white")
    draw = ImageDraw.Draw(canvas)
    xx = 0
    for name, panel, tile_w in panels:
        panel = panel.convert("RGB")
        maxw, maxh = tile_w - 14, 112
        scale = min(maxw / max(panel.width, 1), maxh / max(panel.height, 1))
        if abs(scale - 1.0) > 0.05:
            panel = panel.resize(
                (max(1, int(panel.width * scale)), max(1, int(panel.height * scale))),
                Image.Resampling.LANCZOS,
            )
        draw.text((xx + 8, 8), name, fill="black")
        canvas.paste(panel, (xx + 7, 42 + (maxh - panel.height) // 2))
        draw.rectangle((xx + 2, 2, xx + tile_w - 3, height - 3), outline="gray")
        xx += tile_w
    return image_bytes(canvas, quality=96, max_side=3200)


def transcribe_isolated_grid_row(
    client: OllamaClient,
    row_image: bytes,
    page_type: str,
    page_no: int,
    rotation: int,
    aid: str,
    expected_label: str,
    row_id: str,
    locator_confidence: float,
    table_box: tuple[int, int, int, int],
) -> EvidenceRow | None:
    columns = _financial_numeric_columns(page_type)
    prompt = f"""
This image contains ONE isolated physical row from an upright Moroccan DGI {page_type} table.
Python/OpenCV has already separated the LABEL cell and each numeric column into panels with explicit names.
Requested anchor: {aid}: {expected_label}

Your ONLY task is transcription.
- First verify that the LABEL panel is this requested physical row. If not, return rows=[].
- Copy raw_label exactly as visible.
- For each named numeric panel ({', '.join(columns)}), copy a value ONLY if digits are visibly printed in THAT panel.
- If a panel is blank, OMIT that column from cells.
- Never move a value from one panel to another.
- Never calculate, infer, repair, or normalize a number.
- Preserve minus sign, spaces and decimal comma exactly.
- Literal printed 0,00 is zero; visual blank is not zero.
""".strip()
    data = client.chat_json(
        prompt=prompt,
        images=[row_image],
        schema=_raw_rows_schema([aid]),
        model=client.model,
        think=False,
        num_ctx=5000,
        num_predict=600,
        attempts=2,
    )
    decoded = _decode_raw_anchor_rows(
        data,
        allowed_ids=[aid],
        page_type=page_type,
        page_no=page_no,
        rotation=rotation,
        crop_box=table_box,
        retry_tag=f"pass=grid_isolated;row_id={row_id};locator_conf={locator_confidence:.2f}",
        source="glm_grid_cell_vision",
    )
    if not decoded:
        return None
    return decoded[0][1]


def extract_grid_guided_scan_rows(
    client: OllamaClient,
    oriented: Image.Image,
    page_type: str,
    page_no: int,
    rotation: int,
) -> list[EvidenceRow]:
    """V8 extraction: OpenCV geometry -> GLM label location -> isolated-cell OCR.

    This is deliberately different from V7 full-table OCR. The VLM never has to
    infer which numeric column a value belongs to because each numeric cell is
    physically cropped by Python before GLM sees it.
    """
    if page_type == "DETAIL_CPC":
        # Detail CPC has many generic "Total" labels whose accounting group can be
        # several rows above. Keep the proven V7 contextual extractor for now.
        raise RuntimeError("grid-guided Detail CPC deferred to contextual extractor")

    geom = detect_scan_grid_geometry(oriented, page_type)
    row_ids = _candidate_row_ids(oriented, geom)
    if len(row_ids) < 5:
        raise RuntimeError(f"Too few visible label rows in row atlas: {len(row_ids)}")
    montage = make_row_locator_montage(oriented, geom, row_ids)
    anchors = RAW_ANCHORS[page_type]
    located = locate_anchor_rows_by_geometry(client, montage, page_type, anchors, row_ids)

    out: list[EvidenceRow] = []
    used_rows: dict[str, str] = {}
    for aid, expected_label in anchors:
        rid, lconf = located.get(aid, ("ABSENT", 0.0))
        if rid == "ABSENT" or rid not in row_ids:
            continue
        # If the locator tries to reuse the same physical row for two different
        # anchors, keep the higher-confidence claim and reject the weaker one.
        if rid in used_rows:
            continue
        row_index = int(rid[1:])
        row_image = make_isolated_row_cell_montage(oriented, geom, row_index, page_type)
        row = transcribe_isolated_grid_row(
            client, row_image, page_type, page_no, rotation,
            aid, expected_label, rid, lconf, geom.table_box,
        )
        if row is None:
            continue
        used_rows[rid] = aid
        out.append(row)

    anchor_order = {aid: i for i, (aid, _) in enumerate(anchors)}
    out.sort(key=lambda r: anchor_order.get(
        next((n.split("=", 1)[1] for n in r.notes if n.startswith("anchor_id=")), ""), 999
    ))
    return out


def _rule_map_from_anchor(page_type: str, row: EvidenceRow) -> str | None:
    aid = None
    for note in row.notes:
        if note.startswith("anchor_id="):
            aid = note.split("=", 1)[1]
            break
    if not aid:
        return None
    return RULE_MAP.get(page_type, {}).get(aid)


def _mapper_schema(page_type: str, row_ids: list[str]) -> dict[str, Any]:
    allowed = FIELD_CODES[page_type] + ["IGNORE"]
    return {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "row_id": {"type": "string", "enum": row_ids},
                        "field_code": {"type": "string", "enum": allowed},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "basis": {"type": "string", "enum": ["exact_label", "section_context", "neighbor_context", "not_relevant", "uncertain"]},
                    },
                    "required": ["row_id", "field_code", "confidence", "basis"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["mappings"],
        "additionalProperties": False,
    }


def _mapper_prompt(page_type: str, items: list[dict[str, str]]) -> str:
    defs = "\n".join(f"- {c}: {FIELD_DEFINITIONS.get(c, c)}" for c in FIELD_CODES[page_type])
    rows = "\n".join(
        f"- {x['row_id']}: label={x['raw_label']!r}; context={x['context']!r}; prev={x['prev']!r}; next={x['next']!r}"
        for x in items
    )
    return f"""
You are the semantic mapping agent for Moroccan DGI financial statements.
Map RAW OCR row labels to the allowed internal financial field codes.

IMPORTANT SECURITY/ACCURACY BOUNDARY:
- You receive NO amounts and must not output any amount.
- Decide only WHAT each row label means.
- Use page type + exact label + visible section/neighbor context.
- If the row is not one of the allowed fields, return IGNORE.
- TOTAL I is not TOTAL GENERAL. TOTAL II is not TOTAL III.
- For current-vs-previous-year values, do not decide columns here; Python handles that later.

Page type: {page_type}
Allowed fields:
{defs}

Rows:
{rows}
""".strip()


def reasoner_map_rows(client: OllamaClient, raw_rows: list[EvidenceRow], page_type: str, *, use_adjudicator: bool = True) -> list[EvidenceRow]:
    if not raw_rows:
        return []
    # Build number-free context. Neighbor labels help generic 'Total' rows.
    items = []
    for i, r in enumerate(raw_rows):
        ctx = " | ".join(n.split("=", 1)[1] for n in r.notes if n.startswith("context="))
        items.append({
            "row_id": f"r{i}",
            "raw_label": r.raw_label,
            "context": ctx,
            "prev": raw_rows[i-1].raw_label if i else "",
            "next": raw_rows[i+1].raw_label if i + 1 < len(raw_rows) else "",
        })
    row_ids = [x["row_id"] for x in items]
    qdata = client.chat_json(
        prompt=_mapper_prompt(page_type, items),
        images=None,
        schema=_mapper_schema(page_type, row_ids),
        model=client.mapper_model,
        think=False,
        num_ctx=10000,
        num_predict=1200,
    )
    qmap = {m["row_id"]: m for m in qdata.get("mappings", [])}

    mapped: list[EvidenceRow] = []
    for i, row in enumerate(raw_rows):
        rid = f"r{i}"
        rule_code = _rule_map_from_anchor(page_type, row)
        qm = qmap.get(rid, {})
        qcode = qm.get("field_code")
        qconf = float(qm.get("confidence", 0.0) or 0.0)
        qbasis = qm.get("basis", "uncertain")

        # Strong physical-anchor rules are authoritative, but Qwen acts as an independent semantic check.
        if rule_code:
            final_code = rule_code
            source = "rule+qwen_agree" if qcode == rule_code else "rule_qwen_disagree"
            map_conf = 1.0 if qcode == rule_code else 0.90
            note = f"rule={rule_code}; qwen={qcode}; qwen_conf={qconf:.2f}; basis={qbasis}"
            # If Qwen disagrees with an exact rule, optionally ask Gemma to adjudicate the semantic label only.
            if qcode not in (None, "IGNORE", rule_code) and use_adjudicator:
                ad_items = [items[i]]
                try:
                    gdata = client.chat_json(
                        prompt=_mapper_prompt(page_type, ad_items),
                        images=None,
                        schema=_mapper_schema(page_type, [rid]),
                        model=client.adjudicator_model,
                        think=False,
                        num_ctx=7000,
                        num_predict=500,
                        attempts=2,
                    )
                    gm = (gdata.get("mappings") or [{}])[0]
                    gcode = gm.get("field_code")
                    note += f"; gemma={gcode}; gemma_conf={float(gm.get('confidence',0) or 0):.2f}"
                    source = "rule+adjudicated"
                    # Exact DGI anchor remains authoritative; disagreement is visible in audit metadata.
                except Exception as exc:
                    note += f"; gemma_error={type(exc).__name__}"
            row.field_code = final_code
            row.mapping_source = source
            row.mapping_confidence = map_conf
            row.mapping_note = note
            row.confidence = min(row.confidence, map_conf)
            mapped.append(row)
            continue

        # No strong deterministic rule: Qwen may map, but only with high confidence.
        if qcode in FIELD_CODES[page_type] and qconf >= 0.88:
            final_code = qcode
            source = "qwen"
            map_conf = min(qconf, 0.95)
            note = f"qwen_conf={qconf:.2f}; basis={qbasis}"
            # Low-ish or context-based mappings can be adjudicated.
            if use_adjudicator and (qconf < 0.94 or qbasis in {"section_context", "neighbor_context"}):
                try:
                    gdata = client.chat_json(
                        prompt=_mapper_prompt(page_type, [items[i]]),
                        images=None,
                        schema=_mapper_schema(page_type, [rid]),
                        model=client.adjudicator_model,
                        think=False,
                        num_ctx=7000,
                        num_predict=500,
                        attempts=2,
                    )
                    gm = (gdata.get("mappings") or [{}])[0]
                    gcode = gm.get("field_code")
                    gconf = float(gm.get("confidence", 0.0) or 0.0)
                    note += f"; gemma={gcode}; gemma_conf={gconf:.2f}"
                    if gcode == qcode and gconf >= 0.75:
                        source = "qwen+gemma_agree"
                        map_conf = min(map_conf, gconf, 0.95)
                    elif gcode not in (None, "IGNORE", qcode):
                        row.field_code = "AMBIGUOUS"
                        row.mapping_source = "reasoner_disagreement"
                        row.mapping_confidence = 0.0
                        row.mapping_note = note
                        row.notes.append("semantic mapper disagreement; excluded from RCC resolution")
                        mapped.append(row)
                        continue
                except Exception as exc:
                    note += f"; gemma_error={type(exc).__name__}"
            row.field_code = final_code
            row.mapping_source = source
            row.mapping_confidence = map_conf
            row.mapping_note = note
            row.confidence = min(row.confidence, map_conf)
            mapped.append(row)
        else:
            row.field_code = "UNMAPPED"
            row.mapping_source = "qwen_reject"
            row.mapping_confidence = qconf
            row.mapping_note = f"qwen={qcode}; qwen_conf={qconf:.2f}; basis={qbasis}"
            mapped.append(row)
    return mapped

_IDENT_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_code": {"type": "string", "enum": [
                        "RAISON_SOCIALE", "IDENTIFIANT_FISCAL", "ICE", "TAXE_PROFESSIONNELLE", "ADRESSE", "VILLE"
                    ]},
                    "raw_value": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["field_code", "raw_value", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["fields"],
    "additionalProperties": False,
}


def extract_scan_page(
    client: OllamaClient,
    im: Image.Image,
    page_type: str,
    page_no: int,
    rotation: int,
    *,
    use_reasoning_mapper: bool = True,
    use_adjudicator: bool = True,
) -> list[EvidenceRow]:
    oriented = im if rotation == 0 else im.rotate(-rotation, expand=True)
    if page_type == "IDENTIFICATION":
        prepared = image_bytes(crop_to_visible_content(oriented), quality=93, max_side=EXTRACT_MAX_SIDE)
        prompt = """
Extract only these visible taxpayer-identification fields exactly: Raison Sociale, Identifiant fiscal, ICE,
Art. Taxe professionnelle, Adresse, Ville. Do not normalize identifiers; copy digits exactly. Blank means omit.
Return compact JSON only.
""".strip()
        data = client.chat_json(prompt=prompt, images=[prepared], schema=_IDENT_SCHEMA, model=client.model, think=False, num_predict=800)
        out = []
        for f in data.get("fields", []):
            out.append(EvidenceRow(page_no, page_type, "glm_vision", f["field_code"], f["field_code"], {"TEXT": f["raw_value"]}, float(f.get("confidence", 0.7)), rotation, mapping_source="direct_identification", mapping_confidence=1.0))
        return out

    raw_rows: list[EvidenceRow]
    if GRID_GUIDED_SCAN and page_type in {"BILAN_ACTIF", "BILAN_PASSIF", "CPC"}:
        try:
            raw_rows = extract_grid_guided_scan_rows(client, oriented, page_type, page_no, rotation)
        except Exception as grid_exc:
            if not GRID_FALLBACK_TO_FULL_TABLE:
                raise
            # Safe fallback keeps V7 compatibility if a non-standard scan has no
            # detectable grid. The fallback is explicitly tagged in evidence.
            crop, crop_box = detect_grid_crop(oriented)
            crop = ImageOps.autocontrast(crop.convert("RGB"), cutoff=0.5)
            crop = ImageEnhance.Sharpness(crop).enhance(1.08)
            prepared = image_bytes(crop, quality=94, max_side=EXTRACT_MAX_SIDE)
            raw_rows = extract_raw_scan_rows(client, prepared, page_type, page_no, rotation, crop_box)
            for r in raw_rows:
                r.notes.append(f"grid_fallback={type(grid_exc).__name__}:{grid_exc}")
    else:
        crop, crop_box = detect_grid_crop(oriented)
        crop = ImageOps.autocontrast(crop.convert("RGB"), cutoff=0.5)
        crop = ImageEnhance.Sharpness(crop).enhance(1.08)
        prepared = image_bytes(crop, quality=94, max_side=EXTRACT_MAX_SIDE)
        raw_rows = extract_raw_scan_rows(client, prepared, page_type, page_no, rotation, crop_box)
    if not use_reasoning_mapper:
        # Even without a reasoning model, strong physical-anchor rules can safely map standard DGI rows.
        for row in raw_rows:
            code = _rule_map_from_anchor(page_type, row)
            if code:
                row.field_code = code
                row.mapping_source = "rule_only"
                row.mapping_confidence = 1.0
        return raw_rows
    return reasoner_map_rows(client, raw_rows, page_type, use_adjudicator=use_adjudicator)

# ---------------------------
# Convert native generic rows into canonical target evidence
# ---------------------------
ALIASES: dict[str, list[str]] = {
    "TOTAL_ACTIF": ["total general i+ii+iii"],
    "ACTIFS_IMMOBILISES": ["total i (a+b+c+d+e)", "total i a+b+c+d+e"],
    "ACTIF_CIRCULANT": ["total ii (f+g+h+i)", "total ii f+g+h+i"],
    "STOCKS": ["stocks (f)", "total stocks", "stocks f"],
    "CLIENTS": ["clients et comptes rattaches"],
    "TRESORERIE_ACTIF": ["total iii"],
    "CAISSE": ["caisse regie d avances et accreditifs"],
    "TOTAL_PASSIF": ["total general i+ii+iii"],
    "FONDS_PROPRES": ["total des capitaux propres (a)", "total des capitaux propres"],
    "RESULTAT_NET": ["resultat net de l exercice", "resultat net (xi-xii)", "resultat net total des produits-total des charges", "resultat net"],
    "DETTES_FINANCEMENT": ["dettes de financement (c)", "total des dettes de financement"],
    "DETTES_BANCAIRES_CT": ["credits de tresorerie", "concours bancaires courants"],
    # RCC passif circulant = TOTAL II (F+G+H), not the narrower subsection F alone.
    "PASSIF_CIRCULANT": ["total ii (f+g+h)"],
    "FOURNISSEURS": ["fournisseurs et comptes rattaches"],
    "COMPTE_COURANT_ASSOCIES": ["comptes d associes"],
    "TRESORERIE_PASSIF": ["total iii"],
    "CHIFFRE_AFFAIRES": ["chiffres d affaires", "chiffre d affaires"],
    "VENTES_MARCHANDISES": ["ventes de marchandises (en l etat)", "ventes de marchandises"],
    "VENTES_BIENS_SERVICES": ["ventes de biens et services produits"],
    "ACHATS_REVENDUS": ["achats revendus(2) de marchandises", "achats revendus de marchandises"],
    "ACHATS_CONSOMMES": ["achats consommes(2) de matieres et fournitures", "achats consommes de matieres et fournitures"],
    "AUTRES_CHARGES_EXTERNES": ["autres charges externes"],
    "CHARGES_INTERETS": ["charges d interets"],
    "CHARGES_FINANCIERES": ["total v", "v charges financieres"],
    "RESULTAT_EXPLOITATION": ["iii resultat d exploitation (i-ii)", "resultat d exploitation"],
    "EXPORT_MARCHANDISES": ["ventes de marchandises a l etranger"],
    "EXPORT_BIENS": ["ventes de biens a l etranger"],
    "EXPORT_SERVICES": ["ventes de services a l etranger"],
}


def label_score(label: str, alias: str) -> float:
    l, a = fold(label), fold(alias)
    if not l or not a:
        return 0.0
    if a in l or l in a:
        # exact/substring is strong
        return 1.0 if l == a else 0.93
    # token overlap instead of fuzzy digit-sensitive matching
    ls, aset = set(l.split()), set(a.split())
    return len(ls & aset) / max(len(aset), 1)


def canonicalize_native_rows(rows: list[EvidenceRow]) -> list[EvidenceRow]:
    out: list[EvidenceRow] = []
    # Direct label matches
    for row in rows:
        candidates: list[tuple[float, str]] = []
        for code, aliases in ALIASES.items():
            # restrict by page type
            # Page-type allow-list is strict: never let an export row alias leak into BILAN_ACTIF, etc.
            allowed = set(FIELD_CODES.get(row.page_type, []))
            if code not in allowed:
                continue
            sc = max(label_score(row.raw_label, a) for a in aliases)
            if sc >= 0.88:
                candidates.append((sc, code))
        if candidates:
            sc, code = max(candidates)
            out.append(EvidenceRow(row.page, row.page_type, row.source, code, row.raw_label, row.cells, sc, row.rotation, row.crop_box))

    # DETAIL_CPC numbered group totals: associate generic Total with active 611/612/613/614 group.
    for page in sorted({r.page for r in rows if r.page_type == "DETAIL_CPC"}):
        current_group: str | None = None
        for row in [r for r in rows if r.page == page and r.page_type == "DETAIL_CPC"]:
            f = fold(row.raw_label)
            if "611" in f and "achats revendus" in f:
                current_group = "ACHATS_REVENDUS_TOTAL"
            elif "612" in f and "achats consommes" in f:
                current_group = "ACHATS_CONSOMMES_TOTAL"
            elif ("613" in f or "614" in f) and "autres charges externes" in f:
                current_group = "AUTRES_CHARGES_EXTERNES_TOTAL"
            elif re.fullmatch(r"(?:[^|]*\|\s*)?total", f) and current_group:
                out.append(EvidenceRow(row.page, row.page_type, row.source, current_group, row.raw_label, row.cells, 0.98, row.rotation, row.crop_box))
                current_group = None
    return out

# ---------------------------
# Strict resolution + controls
# ---------------------------
def value_from_row(row: EvidenceRow) -> Decimal | None:
    priority = {
        "BILAN_ACTIF": ["NET_N"],
        "BILAN_PASSIF": ["EXERCICE_N"],
        "CPC": ["TOTAL_N", "OP_N"],
        "DETAIL_CPC": ["EXERCICE_N"],
    }.get(row.page_type, [])
    for col in priority:
        v = parse_amount(row.cells.get(col))
        if v is not None:
            return v
    return None


def cell_value(row: EvidenceRow, col: str) -> Decimal | None:
    return parse_amount(row.cells.get(col))


def row_present_but_current_blank(row: EvidenceRow) -> bool:
    """Physical row exists, but its current-exercise value is blank."""
    if row.field_code in {"UNMAPPED", "AMBIGUOUS"}:
        return False
    if "row_present=true" not in row.notes and not row.source.startswith("native"):
        return False
    return value_from_row(row) is None


def rows_df(rows: list[EvidenceRow]) -> pd.DataFrame:
    records = []
    for r in rows:
        rec = {
            "page": r.page, "page_type": r.page_type, "source": r.source,
            "field_code": r.field_code, "raw_label": r.raw_label,
            "confidence": r.confidence, "rotation": r.rotation,
            "mapping_source": r.mapping_source,
            "mapping_confidence": r.mapping_confidence,
            "mapping_note": r.mapping_note,
            "row_present": ("row_present=true" in r.notes) or r.source.startswith("native"),
            "current_blank": row_present_but_current_blank(r),
        }
        rec.update({c: r.cells.get(c) for c in COLUMNS})
        rec["TEXT"] = r.cells.get("TEXT")
        records.append(rec)
    return pd.DataFrame(records)


def choose_row(rows: list[EvidenceRow], code: str, preferred_types: Iterable[str]) -> tuple[EvidenceRow | None, list[EvidenceRow]]:
    cands = [r for r in rows if r.field_code == code and value_from_row(r) is not None]
    if not cands:
        return None, []
    type_rank = {t: i for i, t in enumerate(preferred_types)}
    cands.sort(key=lambda r: (type_rank.get(r.page_type, 99), -r.confidence, r.page))
    vals = {value_from_row(r) for r in cands}
    if len(vals) > 1:
        return None, cands
    return cands[0], cands


def choose_consensus_row(rows: list[EvidenceRow], code: str, preferred_types: Iterable[str]) -> tuple[EvidenceRow | None, list[EvidenceRow]]:
    """Choose only when one explicit value is corroborated by independent pages/types.

    This is used for fields such as RESULTAT_NET where the same value is expected
    on CPC and Bilan Passif. A one-off disagreement is not silently preferred.
    """
    cands = [r for r in rows if r.field_code == code and value_from_row(r) is not None]
    if not cands:
        return None, []
    groups: dict[Decimal, list[EvidenceRow]] = {}
    for r in cands:
        v = value_from_row(r)
        if v is not None:
            groups.setdefault(v, []).append(r)
    if len(groups) == 1:
        return choose_row(rows, code, preferred_types)
    # Corroboration score is number of independent (page_type,page) sources.
    scored = []
    for value, rs in groups.items():
        independent = {(r.page_type, r.page) for r in rs}
        scored.append((len(independent), value, rs))
    scored.sort(key=lambda x: x[0], reverse=True)
    if len(scored) >= 2 and scored[0][0] == scored[1][0]:
        return None, cands
    if scored[0][0] < 2:
        return None, cands
    winning = scored[0][2]
    type_rank = {t: i for i, t in enumerate(preferred_types)}
    winning.sort(key=lambda r: (type_rank.get(r.page_type, 99), -r.confidence, r.page))
    return winning[0], cands


def resolve_rcc(rows: list[EvidenceRow]) -> pd.DataFrame:
    spec = [
        (1, "ACTIFS_IMMOBILISES", "ACTIFS_IMMOBILISES", ["BILAN_ACTIF"]),
        (2, "TOTAL_BILAN", "TOTAL_ACTIF", ["BILAN_ACTIF"]),
        (3, "CHIFFRE_AFFAIRES", "CHIFFRE_AFFAIRES", ["CPC"]),
        (5, "DETTES_BANCAIRES_MLT", "DETTES_FINANCEMENT", ["BILAN_PASSIF"]),
        (6, "DETTES_BANCAIRES_CT", "DETTES_BANCAIRES_CT", ["BILAN_PASSIF"]),
        (7, "PASSIF_CIRCULANT", "PASSIF_CIRCULANT", ["BILAN_PASSIF"]),
        (8, "DETTES_FOURNISSEURS", "FOURNISSEURS", ["BILAN_PASSIF"]),
        (9, "COMPTE_COURANT_ASSOCIES", "COMPTE_COURANT_ASSOCIES", ["BILAN_PASSIF"]),
        (10, "TRESORERIE_PASSIF", "TRESORERIE_PASSIF", ["BILAN_PASSIF"]),
        (11, "ACTIF_CIRCULANT", "ACTIF_CIRCULANT", ["BILAN_ACTIF"]),
        (12, "CREANCES_CLIENTS", "CLIENTS", ["BILAN_ACTIF"]),
        (13, "TRESORERIE_ACTIF", "TRESORERIE_ACTIF", ["BILAN_ACTIF"]),
        (14, "CAISSE", "CAISSE", ["BILAN_ACTIF"]),
        (15, "ACHATS_REVENDUS", "ACHATS_REVENDUS", ["DETAIL_CPC", "CPC"]),
        (16, "ACHATS_CONSOMMES", "ACHATS_CONSOMMES", ["DETAIL_CPC", "CPC"]),
        (17, "AUTRES_CHARGES_EXTERNES", "AUTRES_CHARGES_EXTERNES", ["DETAIL_CPC", "CPC"]),
        (18, "CHARGES_INTERETS", "CHARGES_INTERETS", ["CPC"]),
        (19, "RESULTAT_NET", "RESULTAT_NET", ["CPC", "BILAN_PASSIF"]),
    ]

    alias_rows = list(rows)
    detail_alias_map = {
        "ACHATS_REVENDUS_TOTAL": "ACHATS_REVENDUS",
        "ACHATS_CONSOMMES_TOTAL": "ACHATS_CONSOMMES",
        "AUTRES_CHARGES_EXTERNES_TOTAL": "AUTRES_CHARGES_EXTERNES",
    }
    for r in list(rows):
        if r.field_code in detail_alias_map:
            alias_rows.append(EvidenceRow(**{**asdict(r), "field_code": detail_alias_map[r.field_code]}))

    records = []
    detail_sensitive = {"ACHATS_REVENDUS", "ACHATS_CONSOMMES", "AUTRES_CHARGES_EXTERNES"}

    for num, out_code, evidence_code, pref in spec:
        if out_code in detail_sensitive:
            detail_rows = [r for r in alias_rows if r.field_code == evidence_code and r.page_type == "DETAIL_CPC"]
            cpc_rows = [r for r in alias_rows if r.field_code == evidence_code and r.page_type == "CPC"]
            detail_values = {value_from_row(r) for r in detail_rows if value_from_row(r) is not None}
            cpc_values = {value_from_row(r) for r in cpc_rows if value_from_row(r) is not None}
            detail_blank_rows = [r for r in detail_rows if row_present_but_current_blank(r)]

            if len(detail_values) > 1 or len(cpc_values) > 1:
                records.append({"number": num, "code": out_code, "value": None, "status": "conflicting", "page": None, "source": None, "raw_label": None, "confidence": 0.0, "note": "Multiple distinct explicit values found within CPC/Detail CPC."})
                continue

            if detail_values:
                dv = next(iter(detail_values))
                dr = next(r for r in detail_rows if value_from_row(r) == dv)
                if cpc_values and next(iter(cpc_values)) != dv:
                    records.append({"number": num, "code": out_code, "value": None, "status": "conflicting", "page": None, "source": None, "raw_label": None, "confidence": 0.0, "note": f"Detail CPC value {dv} disagrees with CPC value {next(iter(cpc_values))}; not auto-selected."})
                    continue
                status = "cross_validated" if cpc_values else "confirmed"
                note = "Same explicit value appears in CPC and Detail CPC." if cpc_values else "Resolved from the more specific Detail CPC total row."
                records.append({"number": num, "code": out_code, "value": amount_str(dv), "status": status, "page": dr.page, "source": dr.source, "raw_label": dr.raw_label, "confidence": dr.confidence, "note": note})
                continue

            if detail_blank_rows:
                br = detail_blank_rows[0]
                if cpc_values:
                    records.append({"number": num, "code": out_code, "value": None, "status": "conflicting_blank_vs_value", "page": f"{br.page}," + ",".join(str(r.page) for r in cpc_rows if value_from_row(r) is not None), "source": "DETAIL_CPC+CPC", "raw_label": br.raw_label, "confidence": min(br.confidence, max((r.confidence for r in cpc_rows), default=0.0)), "note": "Detail CPC row is present but current value is blank while CPC produced a number; blocked pending review."})
                else:
                    records.append({"number": num, "code": out_code, "value": None, "status": "blank_on_form", "page": br.page, "source": br.source, "raw_label": br.raw_label, "confidence": br.confidence, "note": "Target row is visibly present but the current-exercise cell is blank. Blank was not converted to zero."})
                continue

        row, conflicts = (
            choose_consensus_row(alias_rows, evidence_code, pref)
            if out_code == "RESULTAT_NET"
            else choose_row(alias_rows, evidence_code, pref)
        )
        if row is not None:
            value = value_from_row(row)
            status = "confirmed" if row.confidence >= 0.8 else "low_confidence"
            note = None
            if out_code == "DETTES_BANCAIRES_MLT":
                status = "proxy"
                note = "Proxy: extracted from 'Dettes de financement'; confirm business definition if MLT must be bank-only."
            if out_code == "RESULTAT_NET" and len({value_from_row(r) for r in conflicts if value_from_row(r) is not None}) > 1:
                status = "cross_validated"
                note = "Selected only because the same explicit value is corroborated across independent statement pages; conflicting one-off value retained in evidence."
            records.append({"number": num, "code": out_code, "value": amount_str(value), "status": status, "page": row.page, "source": row.source, "raw_label": row.raw_label, "confidence": row.confidence, "note": note})
        elif conflicts:
            records.append({"number": num, "code": out_code, "value": None, "status": "conflicting", "page": None, "source": None, "raw_label": None, "confidence": 0.0, "note": "Distinct explicit values found; not auto-selected."})
        else:
            blank_rows = [r for r in alias_rows if r.field_code == evidence_code and row_present_but_current_blank(r)]
            if blank_rows:
                br = sorted(blank_rows, key=lambda r: (-r.confidence, r.page))[0]
                records.append({"number": num, "code": out_code, "value": None, "status": "blank_on_form", "page": br.page, "source": br.source, "raw_label": br.raw_label, "confidence": br.confidence, "note": "Row is present but the current-exercise value is blank; previous-year values, if any, were not substituted."})
            else:
                records.append({"number": num, "code": out_code, "value": None, "status": "missing", "page": None, "source": None, "raw_label": None, "confidence": 0.0, "note": None})

    ca = next(r for r in records if r["code"] == "CHIFFRE_AFFAIRES")
    if ca["value"] is None and ca["status"] == "missing":
        for proxy_code in ("VENTES_BIENS_SERVICES", "VENTES_MARCHANDISES"):
            row, conflicts = choose_row(alias_rows, proxy_code, ["CPC"])
            if row is not None:
                ca.update(value=amount_str(value_from_row(row)), status="proxy", page=row.page, source=row.source, raw_label=row.raw_label, confidence=row.confidence, note=f"No explicit Chiffre d'affaires value; using explicit {proxy_code} row as proxy, not an invented sum.")
                break

    export_codes = ["EXPORT_MARCHANDISES", "EXPORT_BIENS", "EXPORT_SERVICES"]
    exp_rows = []
    for c in export_codes:
        matched = [r for r in alias_rows if r.field_code == c]
        exp_rows.append(matched[0] if matched else None)
    exp_values = [value_from_row(r) if r else None for r in exp_rows]
    explicit = [v for v in exp_values if v is not None]
    present_count = sum(r is not None for r in exp_rows)

    if explicit:
        complete_numeric = all(r is not None and value_from_row(r) is not None for r in exp_rows)
        records.append({"number": 4, "code": "CA_EXPORT", "value": amount_str(sum(explicit, Decimal("0"))), "status": "derived" if complete_numeric else "partial", "page": ",".join(str(x) for x in sorted({r.page for r in exp_rows if r})), "source": "DETAIL_CPC", "raw_label": "Explicit foreign-sales rows", "confidence": min((r.confidence for r in exp_rows if r), default=0.0), "note": None if complete_numeric else "Only explicitly printed export amounts were summed; blank rows were not treated as zero."})
    elif present_count == 3 and all(row_present_but_current_blank(r) for r in exp_rows if r):
        records.append({"number": 4, "code": "CA_EXPORT", "value": None, "status": "blank_on_form", "page": ",".join(str(x) for x in sorted({r.page for r in exp_rows if r})), "source": "DETAIL_CPC", "raw_label": "Three explicit foreign-sales rows", "confidence": min((r.confidence for r in exp_rows if r), default=0.0), "note": "All three export rows are visibly present and blank. This is not converted to 0 unless business policy explicitly allows that inference."})
    else:
        records.append({"number": 4, "code": "CA_EXPORT", "value": None, "status": "missing", "page": None, "source": None, "raw_label": None, "confidence": 0.0, "note": None})

    rn = next(r for r in records if r["code"] == "RESULTAT_NET")
    if rn["value"] is not None:
        d = Decimal(rn["value"])
        typ = "Bénéficiaire" if d > 0 else "Déficitaire" if d < 0 else "Nul"
        records.append({"number": 20, "code": "TYPE_RESULTAT", "value": typ, "status": "derived", "page": rn["page"], "source": "RESULTAT_NET", "raw_label": None, "confidence": rn["confidence"], "note": "Derived only from sign of extracted RESULTAT_NET."})
    else:
        records.append({"number": 20, "code": "TYPE_RESULTAT", "value": None, "status": "missing", "page": None, "source": None, "raw_label": None, "confidence": 0.0, "note": None})
    return pd.DataFrame(records).sort_values("number").reset_index(drop=True)


def run_controls(rows: list[EvidenceRow], rcc: pd.DataFrame) -> pd.DataFrame:
    checks = []
    # Intra-row active: BRUT - AMORT = NET_N
    for r in rows:
        if r.page_type != "BILAN_ACTIF":
            continue
        b, a, n = cell_value(r, "BRUT"), cell_value(r, "AMORT_PROV"), cell_value(r, "NET_N")
        if b is not None and a is not None and n is not None:
            diff = (b - a) - n
            checks.append({"check": "ACTIF_ROW_BRUT_MINUS_AMORT_EQUALS_NET", "page": r.page, "field": r.field_code, "expected": amount_str(b-a), "observed": amount_str(n), "difference": amount_str(diff), "status": "passed" if abs(diff) <= Decimal("0.02") else "failed"})
    # CPC row: OP_N + OP_PREV = TOTAL_N
    cpc_arithmetic_fields = {
        "CHIFFRE_AFFAIRES", "VENTES_MARCHANDISES", "VENTES_BIENS_SERVICES",
        "ACHATS_REVENDUS", "ACHATS_CONSOMMES", "AUTRES_CHARGES_EXTERNES", "CHARGES_INTERETS"
    }
    for r in rows:
        if r.page_type != "CPC" or r.field_code not in cpc_arithmetic_fields:
            continue
        x, y, t = cell_value(r, "OP_N"), cell_value(r, "OP_PREV"), cell_value(r, "TOTAL_N")
        if x is not None and y is not None and t is not None:
            diff = (x + y) - t
            checks.append({"check": "CPC_OPS_SUM_TO_TOTAL_N", "page": r.page, "field": r.field_code, "expected": amount_str(x+y), "observed": amount_str(t), "difference": amount_str(diff), "status": "passed" if abs(diff) <= Decimal("0.02") else "failed"})
    # Balance equality
    def rcc_val(code: str) -> Decimal | None:
        s = rcc.loc[rcc.code == code, "value"]
        if s.empty:
            return None
        v = s.iloc[0]
        return parse_amount(v)
    ta = rcc_val("TOTAL_BILAN")
    # independent passif total from evidence
    passif_row, _ = choose_row(rows, "TOTAL_PASSIF", ["BILAN_PASSIF"])
    tp = value_from_row(passif_row) if passif_row else None
    if ta is not None and tp is not None:
        diff = ta - tp
        checks.append({"check": "TOTAL_ACTIF_EQUALS_TOTAL_PASSIF", "page": None, "field": "TOTAL_BILAN", "expected": amount_str(ta), "observed": amount_str(tp), "difference": amount_str(diff), "status": "passed" if abs(diff) <= Decimal("0.02") else "failed"})
    # Result net cross-source
    pas_rows = [r for r in rows if r.field_code == "RESULTAT_NET" and r.page_type == "BILAN_PASSIF" and value_from_row(r) is not None]
    resolved_rn = rcc_val("RESULTAT_NET")
    if resolved_rn is not None and pas_rows:
        p = value_from_row(pas_rows[0])
        assert p is not None
        diff = resolved_rn - p
        checks.append({"check": "RESULTAT_NET_RESOLVED_EQUALS_PASSIF", "page": None, "field": "RESULTAT_NET", "expected": amount_str(resolved_rn), "observed": amount_str(p), "difference": amount_str(diff), "status": "passed" if abs(diff) <= Decimal("0.02") else "failed"})
    return pd.DataFrame(checks)

# ---------------------------
# Orchestrator
# ---------------------------
def analyze_pdf(
    pdf_path: str | Path,
    *,
    client: OllamaClient | None = None,
    max_pages: int | None = None,
    use_glm_verification: bool = True,
    use_reasoning_mapper: bool = True,
    use_adjudicator: bool = True,
):
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(path)
    client = client or OllamaClient()
    if use_glm_verification:
        if use_reasoning_mapper:
            client.check_models(require_adjudicator=use_adjudicator)
        else:
            client.check_model()

    doc = pymupdf.open(path)
    n = min(doc.page_count, max_pages or doc.page_count)
    audit: list[dict[str, Any]] = []
    evidence: list[EvidenceRow] = []

    for i in range(n):
        page = doc[i]
        page_no = i + 1
        native_ok, native_text, word_count = native_text_quality(page)
        if native_ok:
            page_type = classify_native(native_text)
            audit.append({"page": page_no, "mode": "native", "native_chars": len(native_text), "words": word_count, "rotation": 0, "page_type": page_type, "layout_confidence": 1.0, "orientation_confidence": 1.0, "type_confidence": 1.0, "extraction_error": None})
            if page_type == "IDENTIFICATION":
                evidence.extend(extract_native_identification(native_text, page_no))
            elif page_type in {"BILAN_ACTIF", "BILAN_PASSIF", "CPC", "DETAIL_CPC"}:
                raw_rows = extract_native_table_rows(page, page_type, page_no)
                evidence.extend(canonicalize_native_rows(raw_rows))
            continue

        im = render_page(page, dpi=RENDER_DPI)
        if not use_glm_verification:
            audit.append({"page": page_no, "mode": "scan", "native_chars": len(native_text), "words": word_count, "rotation": None, "page_type": "UNCLASSIFIED", "layout_confidence": None, "orientation_confidence": None, "type_confidence": None, "extraction_error": None})
            continue

        try:
            layout = scan_layout_agent(client, im)
        except Exception as exc:
            audit.append({"page": page_no, "mode": "scan_glm", "native_chars": len(native_text), "words": word_count, "rotation": None, "page_type": "UNCLASSIFIED", "layout_confidence": 0.0, "orientation_confidence": 0.0, "type_confidence": 0.0, "extraction_error": f"layout: {exc}"})
            print(f"page {page_no}/{n}: LAYOUT ERROR -> {exc}")
            continue

        page_type = layout["page_type"]
        rotation = int(layout["rotation"])
        rec = {"page": page_no, "mode": "scan_glm", "native_chars": len(native_text), "words": word_count, "rotation": rotation, "page_type": page_type, "layout_confidence": layout.get("confidence"), "orientation_confidence": layout.get("orientation_confidence"), "type_confidence": layout.get("type_confidence"), "axis_ratio": layout.get("axis_ratio"), "orientation_source": layout.get("orientation_source"), "glm_rotation": layout.get("glm_rotation"), "qwen_rotation": layout.get("qwen_rotation"), "extraction_error": None}
        audit.append(rec)
        print(f"page {page_no}/{n}: scan -> rotation={rotation}, type={page_type}, orient_conf={layout.get('orientation_confidence')}, type_conf={layout.get('type_confidence')}, orient_source={layout.get('orientation_source')}")
        if page_type in RELEVANT_PAGE_TYPES:
            try:
                page_rows = extract_scan_page(
                    client, im, page_type, page_no, rotation,
                    use_reasoning_mapper=use_reasoning_mapper,
                    use_adjudicator=use_adjudicator,
                )
                evidence.extend(page_rows)
                rec["scan_extraction_mode"] = ",".join(sorted({r.source for r in page_rows})) if page_rows else "none"
                print(f"  extracted {len(page_rows)} evidence rows via {rec['scan_extraction_mode']}")
            except Exception as exc:
                rec["extraction_error"] = str(exc)
                print(f"  EXTRACTION ERROR (page kept in audit, pipeline continues): {exc}")

    doc.close()
    rcc = resolve_rcc(evidence)
    controls = run_controls(evidence, rcc)
    return pd.DataFrame(audit), rows_df(evidence), rcc, controls, evidence


# ============================================================================
# V10 ROBUST OVERRIDES — accuracy-first recovery for degraded scanned PDFs
# ============================================================================
# Design:
#   OpenCV owns geometry.
#   GLM-OCR reads isolated cells.
#   Qwen3-VL independently verifies scan-row OCR.
#   GLM-4.6V is the tie-breaker / difficult-vision fallback.
#   Qwen3.5 maps semantic row meaning and never rewrites amounts.
#   Python enforces arithmetic/accounting constraints and status propagation.
#
# This section intentionally overrides selected V8 functions while keeping the
# proven native-PDF branch and semantic mapper intact.

import difflib
import itertools
from collections import Counter, defaultdict

OCR_MODEL = "glm-ocr:q8_0"
VERIFY_VISION_MODEL = "qwen3-vl:30b"
PIPELINE_VERSION = "v10-robust-grid-ensemble-recovery"

# Accuracy-first by default.  "all" = independently verify every located scan
# row with Qwen3-VL.  Change to "on_failure" only after the corpus is validated
# if throughput becomes more important than maximal OCR assurance.
ROBUST_VERIFY_POLICY = "all"          # "all" | "on_failure"
ROBUST_MAX_TIEBREAK = 1               # GLM-4.6V tie-break per row when needed
ROBUST_LOCATOR_MIN_CONF = 0.78
ROBUST_FUZZY_MIN_SCORE = 0.72
ROBUST_FUZZY_MIN_MARGIN = 0.025
ROBUST_ARITH_TOL = Decimal("0.02")

# Add a passif subtotal used only for verification of TOTAL PASSIF.
if "PASSIF_TOTAL_I" not in FIELD_CODES["BILAN_PASSIF"]:
    FIELD_CODES["BILAN_PASSIF"].append("PASSIF_TOTAL_I")
if not any(aid == "p10" for aid, _ in RAW_ANCHORS["BILAN_PASSIF"]):
    # Place it before TOTAL II so the raw order remains close to the form.
    RAW_ANCHORS["BILAN_PASSIF"].insert(4, ("p10", "TOTAL I (A+B+C+D+E)"))
ANCHOR_HINTS["p04"] = list(dict.fromkeys(ANCHOR_HINTS.get("p04", []) + [
    "credit de tresorerie", "credits de tresorerie",
]))
ANCHOR_HINTS["p10"] = ["total i (a+b+c+d+e)", "total i a+b+c+d+e"]
RULE_MAP["BILAN_PASSIF"]["p10"] = "PASSIF_TOTAL_I"
FIELD_DEFINITIONS["PASSIF_TOTAL_I"] = "Bilan passif TOTAL I (A+B+C+D+E), used as a control subtotal"
ALIASES["PASSIF_TOTAL_I"] = ["total i (a+b+c+d+e)", "total i a+b+c+d+e"]

# Expanded identification fields.  Missing fields must remain missing; they are
# never inferred from other identifiers.
_IDENT_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_code": {"type": "string", "enum": [
                        "RAISON_SOCIALE", "IDENTIFIANT_FISCAL", "ICE",
                        "TAXE_PROFESSIONNELLE", "RC", "ADRESSE", "VILLE",
                        "EXERCICE_DEBUT", "EXERCICE_FIN",
                    ]},
                    "raw_value": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["field_code", "raw_value", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["fields"],
    "additionalProperties": False,
}


# ---------------------------
# Ollama client with OCR/verifier roles
# ---------------------------
_BaseOllamaClientV8 = OllamaClient

class OllamaClient(_BaseOllamaClientV8):
    def __init__(
        self,
        base_url: str = OLLAMA_URL,
        model: str = VISION_MODEL,
        mapper_model: str = MAPPER_MODEL,
        adjudicator_model: str = ADJUDICATOR_MODEL,
        ocr_model: str = OCR_MODEL,
        verify_model: str = VERIFY_VISION_MODEL,
    ):
        super().__init__(base_url, model, mapper_model, adjudicator_model)
        self.ocr_model = ocr_model
        self.verify_model = verify_model

    def check_models(self, *, require_adjudicator: bool = False, require_robust: bool = True) -> dict[str, Any]:
        data = self.tags()
        names = {m.get("name") for m in data.get("models", [])}
        required = [self.model, self.mapper_model]
        if require_robust:
            required += [self.ocr_model, self.verify_model]
        if require_adjudicator:
            required.append(self.adjudicator_model)
        missing = [m for m in required if m not in names]
        if missing:
            raise RuntimeError(f"Required Ollama model(s) missing: {missing}")
        return data


# ---------------------------
# Robust geometry
# ---------------------------
def detect_scan_grid_geometry(im: Image.Image, page_type: str) -> ScanGridGeometry:
    """V10 grid detector.

    V8 correctly solved row/column drift on dense tables, but some older scans
    draw the same vertical separator twice a few pixels apart.  V8 could treat
    that double edge as an extra numeric column.  V10 tries progressively wider
    clustering tolerances and rejects implausibly narrow numeric columns or a
    clipped label column before accepting geometry.
    """
    expected_numeric_cols = {
        "BILAN_ACTIF": 4,
        "BILAN_PASSIF": 2,
        "CPC": 4,
        "DETAIL_CPC": 2,
    }.get(page_type)
    if expected_numeric_cols is None:
        raise ValueError(f"Grid geometry unsupported for page_type={page_type}")

    rgb = np.asarray(im.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 35, 12,
    )

    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, h // 18)))
    ver = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk)
    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(ver, 8)
    vertical_components: list[tuple[float, int, int, int, int, int]] = []
    xs: list[float] = []
    for i in range(1, n):
        x, y, ww, hh, area = [int(v) for v in stats[i]]
        if hh <= 0.12 * h or ww >= 0.03 * w:
            continue
        if x <= 0.02 * w or x + ww >= 0.98 * w:
            continue
        cx = x + ww / 2.0
        xs.append(cx)
        vertical_components.append((cx, x, y, ww, hh, area))

    needed = expected_numeric_cols + 1
    chosen_lines: list[int] | None = None
    column_bounds: list[int] | None = None
    label_x0 = label_x1 = 0

    # Progressive tolerance is the key fix for double-scanned/double-edge rules.
    for scale in (0.005, 0.007, 0.009, 0.012, 0.016):
        x_lines = _cluster_positions(xs, tolerance=max(7, int(w * scale)))
        if len(x_lines) < needed + 1:
            continue
        bounds = x_lines[-needed:]
        gaps = [b - a for a, b in zip(bounds, bounds[1:])]
        preceding = [x for x in x_lines if x < bounds[0] - 10]
        lx0 = preceding[-1] if preceding else max(0, bounds[0] - int(0.40 * w))
        label_width = bounds[0] - lx0
        # Reject geometries where a doubled border created a 10–20 px fake cell,
        # or where the description column was clipped to a narrow strip.
        if not gaps or min(gaps) < max(48, int(0.043 * w)):
            continue
        if max(gaps) / max(min(gaps), 1) > 2.4:
            continue
        # Do not impose a global label-width ratio: some legitimate landscape
        # DGI scans have a compact description column after rotation.
        chosen_lines = x_lines
        column_bounds = [int(x) for x in bounds]
        label_x0, label_x1 = int(lx0), int(bounds[0])
        break

    if column_bounds is None or chosen_lines is None:
        raise RuntimeError(
            f"Could not obtain plausible numeric-column geometry for {page_type}; raw vertical candidates={_cluster_positions(xs, tolerance=max(7, int(w*0.005)))}"
        )

    amount_x0, amount_x1 = column_bounds[0], column_bounds[-1]
    y_spans: list[tuple[int, int]] = []
    for xb in column_bounds:
        for cx, _x, y, _ww, hh, _area in vertical_components:
            if abs(cx - xb) <= max(10, int(w * 0.010)):
                y_spans.append((y, y + hh))
    if not y_spans:
        raise RuntimeError("Could not determine table vertical extent from grid lines")
    y_min = max(0, min(a for a, _ in y_spans) - 8)
    y_max = min(h, max(b for _, b in y_spans) + 8)

    amount_roi = bw[y_min:y_max, amount_x0:amount_x1]
    if amount_roi.size == 0 or amount_roi.shape[1] < 80:
        raise RuntimeError("Numeric table region is too small for row detection")
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, amount_roi.shape[1] // 12), 1))
    hor = cv2.morphologyEx(amount_roi, cv2.MORPH_OPEN, hk)
    projection = (hor > 0).sum(axis=1)
    threshold = max(15.0, 0.10 * amount_roi.shape[1])
    segments = _contiguous_true(projection > threshold)
    centers = [
        y_min + (a + b) // 2
        for a, b in segments
        if 1 <= (b - a) <= max(18, int(h * 0.008))
    ]
    row_bounds = _cluster_positions(centers, tolerance=max(4, int(h * 0.002)))
    clean_bounds: list[int] = []
    for y in row_bounds:
        if not clean_bounds or y - clean_bounds[-1] >= max(7, int(h * 0.002)):
            clean_bounds.append(y)
    row_intervals: list[tuple[int, int]] = []
    max_row_h = max(150, int(h * 0.07))
    for a, b in zip(clean_bounds, clean_bounds[1:]):
        rh = b - a
        if 10 <= rh <= max_row_h:
            row_intervals.append((a, b))
    if len(row_intervals) < 6:
        raise RuntimeError(f"Too few row intervals detected for {page_type}: {len(row_intervals)}")

    return ScanGridGeometry(
        label_x0=label_x0,
        label_x1=label_x1,
        column_bounds=column_bounds,
        row_bounds=[int(y) for y in clean_bounds],
        row_intervals=row_intervals,
        table_box=(label_x0, int(y_min), int(amount_x1), int(y_max)),
    )


# ---------------------------
# Label-inventory fallback locator
# ---------------------------
def _label_inventory_schema(row_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "row_id": {"type": "string", "enum": row_ids},
                        "raw_label": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["row_id", "raw_label", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["labels"],
        "additionalProperties": False,
    }


def _transcribe_label_inventory(
    client: OllamaClient,
    im: Image.Image,
    geom: ScanGridGeometry,
    row_ids: list[str],
) -> dict[str, tuple[str, float]]:
    """Transcribe only label cells. No financial amounts are exposed here."""
    inventory: dict[str, tuple[str, float]] = {}
    chunk_size = 12
    for start in range(0, len(row_ids), chunk_size):
        chunk = row_ids[start:start + chunk_size]
        atlas = make_row_locator_montage(im, geom, chunk)
        prompt = """
This image is a label-only atlas. Each tile is tagged Rxx and contains one physical table-row label.
Transcribe the visible label text for EVERY tagged row. Do not infer missing words and do not output any numbers from financial cells (they are not shown). If a label is unreadable, return an empty raw_label with low confidence.
""".strip()
        try:
            data = client.chat_json(
                prompt=prompt,
                images=[atlas],
                schema=_label_inventory_schema(chunk),
                model=client.ocr_model,
                think=False,
                num_ctx=6000,
                num_predict=1200,
                attempts=2,
            )
        except Exception:
            continue
        for item in data.get("labels", []):
            rid = str(item.get("row_id", ""))
            label = str(item.get("raw_label", "")).strip()
            conf = float(item.get("confidence", 0.0) or 0.0)
            if rid in chunk and label:
                inventory[rid] = (label, conf)
    return inventory


def _token_similarity(a: str, b: str) -> float:
    aa, bb = fold(a), fold(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        return 0.94
    sa, sb = set(aa.split()), set(bb.split())
    jac = len(sa & sb) / max(1, len(sa | sb))
    seq = difflib.SequenceMatcher(None, aa, bb).ratio()
    return 0.58 * seq + 0.42 * jac


def _anchor_inventory_score(anchor_id: str, expected: str, label: str) -> float:
    f = fold(label)
    if not f:
        return 0.0

    # Hard distinctions for totals prevent neighboring TOTAL I/II/III substitution.
    if anchor_id in {"a07", "p09"} and "total general" not in f:
        return 0.0
    if anchor_id in {"a01", "p10"}:
        if "total i" not in f or "total ii" in f or "total iii" in f or "total general" in f:
            return 0.0
    if anchor_id in {"a03", "p05"} and "total ii" not in f:
        return 0.0
    if anchor_id in {"a05", "p08"} and "total iii" not in f:
        return 0.0
    if anchor_id == "p03" and "dettes de financement" not in f:
        return 0.0

    candidates = [expected] + ANCHOR_HINTS.get(anchor_id, [])
    score = max((_token_similarity(c, label) for c in candidates if c), default=0.0)
    # Prefer the exact section heading over a child row such as "Autres dettes...".
    if anchor_id == "p03" and "autres dettes" in f:
        score -= 0.12
    return max(0.0, min(1.0, score))


def _locate_anchor_rows_model(
    client: OllamaClient,
    montage: bytes,
    page_type: str,
    anchors: list[tuple[str, str]],
    row_ids: list[str],
    *,
    model: str,
) -> dict[str, tuple[str, float]]:
    found: dict[str, tuple[str, float]] = {}
    batch_size = 5
    for start in range(0, len(anchors), batch_size):
        batch = anchors[start:start + batch_size]
        ids = [aid for aid, _ in batch]
        wanted = "\n".join(f"- {aid}: {label}" for aid, label in batch)
        prompt = f"""
This is a LABEL-ONLY row atlas from one upright Moroccan DGI {page_type} table. Each tile is tagged Rxx.
Locate these physical labels:
{wanted}
Return ABSENT only if the row really is not on this page. Never substitute a neighboring subtotal. TOTAL I, TOTAL II, TOTAL III and TOTAL GENERAL are different rows.
""".strip()
        try:
            data = client.chat_json(
                prompt=prompt,
                images=[montage],
                schema=_row_locator_schema(ids, row_ids),
                model=model,
                think=False,
                num_ctx=8000,
                num_predict=700,
                attempts=2,
            )
        except Exception:
            continue
        for m in data.get("matches", []):
            aid = str(m.get("anchor_id", ""))
            rid = str(m.get("row_id", "ABSENT"))
            conf = float(m.get("confidence", 0.0) or 0.0)
            if aid in ids and rid in row_ids + ["ABSENT"]:
                found[aid] = (rid, conf)
    return found


def _locate_anchor_rows_robust(
    client: OllamaClient,
    im: Image.Image,
    geom: ScanGridGeometry,
    page_type: str,
    anchors: list[tuple[str, str]],
    row_ids: list[str],
) -> tuple[dict[str, tuple[str, float]], dict[str, tuple[str, float]]]:
    """Fast GLM locator -> OCR label inventory for misses -> Qwen3-VL last fallback."""
    atlas = make_row_locator_montage(im, geom, row_ids)
    found = _locate_anchor_rows_model(client, atlas, page_type, anchors, row_ids, model=client.model)

    unresolved = [
        (aid, label) for aid, label in anchors
        if aid not in found or found[aid][0] == "ABSENT" or found[aid][1] < ROBUST_LOCATOR_MIN_CONF
    ]
    inventory: dict[str, tuple[str, float]] = {}
    if unresolved:
        inventory = _transcribe_label_inventory(client, im, geom, row_ids)
        used = {rid for aid, (rid, _c) in found.items() if rid != "ABSENT" and aid not in {x[0] for x in unresolved}}
        for aid, expected in unresolved:
            scored = sorted(
                [(_anchor_inventory_score(aid, expected, lab), rid, conf, lab) for rid, (lab, conf) in inventory.items() if rid not in used],
                reverse=True,
            )
            if not scored:
                continue
            best = scored[0]
            second = scored[1][0] if len(scored) > 1 else 0.0
            if best[0] >= ROBUST_FUZZY_MIN_SCORE and (best[0] - second) >= ROBUST_FUZZY_MIN_MARGIN:
                found[aid] = (best[1], min(0.99, 0.60 + 0.40 * best[0]))
                used.add(best[1])

    still = [(aid, lab) for aid, lab in anchors if aid not in found or found[aid][0] == "ABSENT"]
    if still:
        qfound = _locate_anchor_rows_model(client, atlas, page_type, still, row_ids, model=client.verify_model)
        for aid, val in qfound.items():
            if val[0] != "ABSENT":
                found[aid] = val
    return found, inventory


# ---------------------------
# Robust cell OCR / ensemble
# ---------------------------
@dataclass
class RobustRowRead:
    raw_label: str
    states: dict[str, str]               # value | blank | uncertain
    raw_values: dict[str, str | None]
    confidences: dict[str, float]
    model: str
    view: str
    label_confidence: float = 0.0


def _robust_row_schema(columns: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "raw_label": {"type": "string"},
            "label_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "cells": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string", "enum": columns},
                        "state": {"type": "string", "enum": ["value", "blank", "uncertain"]},
                        "raw_value": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["column", "state", "raw_value", "confidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["raw_label", "label_confidence", "cells"],
        "additionalProperties": False,
    }


def _enhance_row_bytes(data: bytes, *, binary: bool = False) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if binary:
        arr = np.asarray(im.convert("L"))
        arr = cv2.GaussianBlur(arr, (3, 3), 0)
        th = cv2.adaptiveThreshold(arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12)
        out = Image.fromarray(th).convert("RGB")
    else:
        out = ImageOps.autocontrast(im, cutoff=0.25)
        out = ImageEnhance.Contrast(out).enhance(1.18)
        out = ImageEnhance.Sharpness(out).enhance(1.35)
    out = out.resize((int(out.width * 1.45), int(out.height * 1.45)), Image.Resampling.LANCZOS)
    return image_bytes(out, quality=97, max_side=4300)


def _read_row_candidate(
    client: OllamaClient,
    row_image: bytes,
    page_type: str,
    aid: str,
    expected_label: str,
    *,
    model: str,
    view: str,
    extra_images: list[bytes] | None = None,
) -> RobustRowRead | None:
    columns = _financial_numeric_columns(page_type)
    prompt = f"""
You are transcribing ONE already-isolated physical row from a Moroccan DGI {page_type} table.
The image panels are explicitly named: LABEL and {', '.join(columns)}.
Requested physical row: {aid}: {expected_label}

For EVERY numeric column return exactly one state:
- value: digits are clearly visible in that exact panel; copy them exactly.
- blank: the panel is visibly empty (blank is NOT zero).
- uncertain: there are marks/digits but you cannot read them reliably.
Never calculate, repair, infer, move a value to another column, or use accounting knowledge to guess digits.
Printed 0,00 is a value. Empty white space is blank.
raw_label must copy the visible LABEL panel.
""".strip()
    imgs = [row_image] + list(extra_images or [])
    try:
        data = client.chat_json(
            prompt=prompt,
            images=imgs,
            schema=_robust_row_schema(columns),
            model=model,
            think=False,
            num_ctx=6500,
            num_predict=900,
            attempts=2,
        )
    except Exception:
        return None
    raw_label = str(data.get("raw_label", "")).strip()
    if not raw_label or not _anchor_matches(aid, raw_label, ""):
        return None
    states = {c: "uncertain" for c in columns}
    raw_values: dict[str, str | None] = {c: None for c in columns}
    confs = {c: 0.0 for c in columns}
    for item in data.get("cells", []):
        col = str(item.get("column", ""))
        if col not in columns:
            continue
        state = str(item.get("state", "uncertain"))
        raw = str(item.get("raw_value", "")).strip()
        conf = float(item.get("confidence", 0.0) or 0.0)
        if state == "value":
            if not raw or parse_amount(raw) is None:
                state, raw = "uncertain", ""
            else:
                raw_values[col] = raw
        elif state == "blank":
            raw_values[col] = None
        else:
            state, raw = "uncertain", ""
        states[col] = state
        confs[col] = conf
    return RobustRowRead(
        raw_label=raw_label,
        states=states,
        raw_values=raw_values,
        confidences=confs,
        model=model,
        view=view,
        label_confidence=float(data.get("label_confidence", 0.0) or 0.0),
    )


def _read_value_candidates(reads: list[RobustRowRead], col: str) -> dict[Decimal, dict[str, Any]]:
    out: dict[Decimal, dict[str, Any]] = {}
    for rr in reads:
        if rr.states.get(col) != "value":
            continue
        raw = rr.raw_values.get(col)
        val = parse_amount(raw)
        if val is None:
            continue
        rec = out.setdefault(val, {"votes": 0, "models": set(), "confidence": 0.0, "raw": raw})
        rec["votes"] += 1
        rec["models"].add(rr.model)
        c = float(rr.confidences.get(col, 0.0))
        if c >= rec["confidence"]:
            rec["confidence"] = c
            rec["raw"] = raw
    return out


def _blank_vote_info(reads: list[RobustRowRead], col: str) -> tuple[int, int, float]:
    rs = [r for r in reads if r.states.get(col) == "blank"]
    return len(rs), len({r.model for r in rs}), max((r.confidences.get(col, 0.0) for r in rs), default=0.0)


def _constraint_solve(reads: list[RobustRowRead], page_type: str) -> dict[str, Decimal | None]:
    """Select only candidate combinations that satisfy printed-table arithmetic.

    None here means a *verified blank candidate* only for CPC OP_PREV. It is not
    converted to numeric zero in evidence.
    """
    if page_type == "BILAN_ACTIF":
        cols = ["BRUT", "AMORT_PROV", "NET_N"]
        cand = {c: list(_read_value_candidates(reads, c).keys()) for c in cols}
        if not all(cand[c] for c in cols):
            return {}
        valid = []
        for b, a, n in itertools.product(cand["BRUT"], cand["AMORT_PROV"], cand["NET_N"]):
            if abs((b - a) - n) <= ROBUST_ARITH_TOL:
                score = 0.0
                for c, v in zip(cols, [b, a, n]):
                    info = _read_value_candidates(reads, c)[v]
                    score += 3.0 * len(info["models"]) + info["votes"] + info["confidence"]
                valid.append((score, {"BRUT": b, "AMORT_PROV": a, "NET_N": n}))
        if valid:
            valid.sort(key=lambda x: x[0], reverse=True)
            return valid[0][1]
        return {}

    if page_type == "CPC":
        op = list(_read_value_candidates(reads, "OP_N").keys())
        tot = list(_read_value_candidates(reads, "TOTAL_N").keys())
        prev_vals: list[Decimal | None] = list(_read_value_candidates(reads, "OP_PREV").keys())
        _bv, bmodels, _bc = _blank_vote_info(reads, "OP_PREV")
        if bmodels >= 2:
            prev_vals.append(None)
        if not op or not tot or not prev_vals:
            return {}
        valid = []
        for x, y, t in itertools.product(op, prev_vals, tot):
            expected = x if y is None else x + y
            if abs(expected - t) <= ROBUST_ARITH_TOL:
                score = 0.0
                for c, v in [("OP_N", x), ("TOTAL_N", t)]:
                    info = _read_value_candidates(reads, c)[v]
                    score += 3.0 * len(info["models"]) + info["votes"] + info["confidence"]
                if y is None:
                    score += 3.0 * bmodels
                else:
                    info = _read_value_candidates(reads, "OP_PREV")[y]
                    score += 3.0 * len(info["models"]) + info["votes"] + info["confidence"]
                valid.append((score, {"OP_N": x, "OP_PREV": y, "TOTAL_N": t}))
        if valid:
            valid.sort(key=lambda x: x[0], reverse=True)
            return valid[0][1]
    return {}


def _merge_row_reads(reads: list[RobustRowRead], page_type: str) -> tuple[dict[str, str | None], dict[str, str], float, list[str]]:
    columns = _financial_numeric_columns(page_type)
    cells: dict[str, str | None] = {}
    states: dict[str, str] = {c: "uncertain" for c in columns}
    notes: list[str] = []
    confidences: list[float] = []

    solved = _constraint_solve(reads, page_type)
    if solved:
        notes.append("arithmetic_constrained_consensus=true")

    for col in columns:
        candidates = _read_value_candidates(reads, col)
        blank_votes, blank_models, blank_conf = _blank_vote_info(reads, col)
        chosen: Decimal | None | object = object()
        chosen_info: dict[str, Any] | None = None

        if col in solved:
            sv = solved[col]
            if sv is None:
                states[col] = "blank"
                confidences.append(max(blank_conf, 0.95))
                continue
            chosen = sv
            chosen_info = candidates.get(sv)
        elif candidates:
            ranked = sorted(
                candidates.items(),
                key=lambda kv: (len(kv[1]["models"]), kv[1]["votes"], kv[1]["confidence"]),
                reverse=True,
            )
            v, info = ranked[0]
            # Require two independent models, or at least two repeated reads with
            # no competing value. Otherwise keep the cell uncertain.
            competing = len(ranked) > 1
            if len(info["models"]) >= 2 or (info["votes"] >= 2 and not competing):
                chosen, chosen_info = v, info
        elif blank_models >= 2:
            states[col] = "blank"
            confidences.append(max(blank_conf, 0.94))
            continue

        if isinstance(chosen, Decimal) and chosen_info is not None:
            states[col] = "value"
            raw = chosen_info.get("raw") or amount_str(chosen)
            cells[col] = str(raw)
            distinct_models = len(chosen_info["models"])
            conf = 0.99 if distinct_models >= 2 else 0.95
            confidences.append(conf)
        else:
            states[col] = "uncertain"
            confidences.append(0.45)
            if candidates:
                notes.append(f"conflicting_candidates.{col}=" + "/".join(amount_str(x) or "" for x in candidates))

    row_conf = min(confidences) if confidences else 0.0
    return cells, states, row_conf, notes


def _candidate_to_evidence(
    reads: list[RobustRowRead],
    *,
    page_type: str,
    page_no: int,
    rotation: int,
    aid: str,
    row_id: str,
    locator_conf: float,
    crop_box: tuple[int, int, int, int],
) -> EvidenceRow | None:
    if not reads:
        return None
    cells, states, row_conf, merge_notes = _merge_row_reads(reads, page_type)
    # Keep the most confident visible label among agreeing row readers.
    best_label = max(reads, key=lambda r: r.label_confidence).raw_label
    if not _anchor_matches(aid, best_label, ""):
        return None
    notes = [
        f"anchor_id={aid}", f"row_id={row_id}", f"locator_conf={locator_conf:.3f}",
        "row_present=true", "ocr_ensemble=true",
    ] + merge_notes
    for col, state in states.items():
        notes.append(f"cell_state.{col}={state}")
    models = sorted({r.model for r in reads})
    notes.append("ocr_models=" + ",".join(models))
    if any(s == "uncertain" for s in states.values()):
        notes.append("ocr_uncertain=true")
    if not cells and all(s == "blank" for s in states.values()):
        notes.append("explicit_blank=true")
    return EvidenceRow(
        page_no, page_type, "ensemble_cell_ocr", "UNMAPPED", best_label, cells,
        min(0.99, max(0.40, row_conf)), rotation, crop_box, notes,
        mapping_source=None, mapping_confidence=None, mapping_note=None,
    )


def _single_read_evidence(
    rr: RobustRowRead,
    *, page_type: str, page_no: int, rotation: int, aid: str,
    row_id: str, locator_conf: float, crop_box: tuple[int, int, int, int],
) -> EvidenceRow:
    cells = {c: rr.raw_values[c] for c in rr.states if rr.states[c] == "value" and rr.raw_values.get(c)}
    vals_conf = [rr.confidences.get(c, 0.0) for c in rr.states]
    conf = min(vals_conf) if vals_conf else 0.60
    notes = [f"anchor_id={aid}", f"row_id={row_id}", f"locator_conf={locator_conf:.3f}", "row_present=true"]
    for c, st in rr.states.items():
        notes.append(f"cell_state.{c}={st}")
    if any(s == "uncertain" for s in rr.states.values()):
        notes.append("ocr_uncertain=true")
    if not cells and all(s == "blank" for s in rr.states.values()):
        notes.append("explicit_blank=true")
    return EvidenceRow(page_no, page_type, "glm_ocr_cell_vision", "UNMAPPED", rr.raw_label, cells, min(0.94, max(0.45, conf)), rotation, crop_box, notes)


def _local_row_consistency(row: EvidenceRow) -> bool | None:
    if row.page_type == "BILAN_ACTIF":
        b, a, n = cell_value(row, "BRUT"), cell_value(row, "AMORT_PROV"), cell_value(row, "NET_N")
        if b is None or a is None or n is None:
            return None
        return abs((b - a) - n) <= ROBUST_ARITH_TOL
    if row.page_type == "CPC":
        x, y, t = cell_value(row, "OP_N"), cell_value(row, "OP_PREV"), cell_value(row, "TOTAL_N")
        if x is None or t is None:
            return None
        prev_state = _cell_state(row, "OP_PREV")
        if y is not None:
            return abs((x + y) - t) <= ROBUST_ARITH_TOL
        if prev_state == "blank":
            return abs(x - t) <= ROBUST_ARITH_TOL
        return None
    return None


def _read_isolated_row_robust(
    client: OllamaClient,
    oriented: Image.Image,
    geom: ScanGridGeometry,
    row_index: int,
    page_type: str,
    page_no: int,
    rotation: int,
    aid: str,
    expected_label: str,
    row_id: str,
    locator_confidence: float,
    *,
    force_full_ensemble: bool = False,
) -> EvidenceRow | None:
    original = make_isolated_row_cell_montage(oriented, geom, row_index, page_type)
    enhanced = _enhance_row_bytes(original)

    primary = _read_row_candidate(
        client, original, page_type, aid, expected_label,
        model=client.ocr_model, view="original",
    )
    if primary is None:
        primary = _read_row_candidate(
            client, enhanced, page_type, aid, expected_label,
            model=client.ocr_model, view="enhanced",
        )
    if primary is None:
        return None

    primary_ev = _single_read_evidence(
        primary, page_type=page_type, page_no=page_no, rotation=rotation,
        aid=aid, row_id=row_id, locator_conf=locator_confidence, crop_box=geom.table_box,
    )
    local = _local_row_consistency(primary_ev)
    has_uncertain = "ocr_uncertain=true" in primary_ev.notes
    verify = (
        force_full_ensemble or ROBUST_VERIFY_POLICY == "all" or has_uncertain or local is False
        or locator_confidence < ROBUST_LOCATOR_MIN_CONF
    )
    if not verify:
        return primary_ev

    reads = [primary]
    # Same OCR model, different visual rendering. This is useful on faint scans,
    # but it does not count as an independent model in consensus.
    enhanced_read = _read_row_candidate(
        client, enhanced, page_type, aid, expected_label,
        model=client.ocr_model, view="enhanced",
    )
    if enhanced_read is not None:
        reads.append(enhanced_read)

    qread = _read_row_candidate(
        client, original, page_type, aid, expected_label,
        model=client.verify_model, view="independent_verify",
    )
    if qread is not None:
        reads.append(qread)

    merged = _candidate_to_evidence(
        reads,
        page_type=page_type, page_no=page_no, rotation=rotation,
        aid=aid, row_id=row_id, locator_conf=locator_confidence,
        crop_box=geom.table_box,
    )
    if merged is not None and _local_row_consistency(merged) is not False and "ocr_uncertain=true" not in merged.notes:
        return merged

    # Final visual tie-break: GLM-4.6V sees original + enhanced renderings of the
    # same isolated row. It still only transcribes; Python chooses the candidate.
    if ROBUST_MAX_TIEBREAK > 0:
        tread = _read_row_candidate(
            client, original, page_type, aid, expected_label,
            model=client.model, view="glm46_tiebreak", extra_images=[enhanced],
        )
        if tread is not None:
            reads.append(tread)
        merged = _candidate_to_evidence(
            reads,
            page_type=page_type, page_no=page_no, rotation=rotation,
            aid=aid, row_id=row_id, locator_conf=locator_confidence,
            crop_box=geom.table_box,
        )
    if merged is not None and (_local_row_consistency(merged) is False or "ocr_uncertain=true" in merged.notes):
        merged.notes.append("verification_unresolved=true")
        merged.confidence = min(merged.confidence, 0.69)
    return merged


def _anchor_id(row: EvidenceRow) -> str | None:
    for note in row.notes:
        if note.startswith("anchor_id="):
            return note.split("=", 1)[1]
    return None


def _cell_state(row: EvidenceRow, col: str) -> str | None:
    prefix = f"cell_state.{col}="
    for note in row.notes:
        if note.startswith(prefix):
            return note.split("=", 1)[1]
    return None


def _cross_row_relation(page_type: str, by_aid: dict[str, EvidenceRow]) -> tuple[bool | None, list[str]]:
    """Return (passed?, involved anchor ids) for strong statement identities."""
    if page_type == "BILAN_ACTIF":
        ids = ["a01", "a03", "a05", "a07"]
        if not all(i in by_aid for i in ids):
            return None, ids
        a, b, c, t = [value_from_row(by_aid[i]) for i in ids]
        if any(v is None for v in [a, b, c, t]):
            return None, ids
        return abs((a + b + c) - t) <= ROBUST_ARITH_TOL, ids
    if page_type == "BILAN_PASSIF":
        ids = ["p10", "p05", "p08", "p09"]
        if not all(i in by_aid for i in ids):
            return None, ids
        a, b, c, t = [value_from_row(by_aid[i]) for i in ids]
        if any(v is None for v in [a, b, c, t]):
            return None, ids
        return abs((a + b + c) - t) <= ROBUST_ARITH_TOL, ids
    if page_type == "CPC":
        ids = ["c01", "c02", "c03"]
        if not all(i in by_aid for i in ids):
            return None, ids
        ca, vm, vb = [value_from_row(by_aid[i]) for i in ids]
        if any(v is None for v in [ca, vm, vb]):
            return None, ids
        return abs((vm + vb) - ca) <= ROBUST_ARITH_TOL, ids
    return None, []


def extract_grid_guided_scan_rows(
    client: OllamaClient,
    oriented: Image.Image,
    page_type: str,
    page_no: int,
    rotation: int,
) -> list[EvidenceRow]:
    """V10: robust grid + missing-row recovery + multi-model isolated-cell OCR."""
    if page_type == "DETAIL_CPC":
        raise RuntimeError("grid-guided Detail CPC deferred to contextual extractor")

    geom = detect_scan_grid_geometry(oriented, page_type)
    row_ids = _candidate_row_ids(oriented, geom)
    if len(row_ids) < 5:
        raise RuntimeError(f"Too few visible label rows in row atlas: {len(row_ids)}")
    anchors = RAW_ANCHORS[page_type]
    located, inventory = _locate_anchor_rows_robust(client, oriented, geom, page_type, anchors, row_ids)

    out: list[EvidenceRow] = []
    row_by_aid: dict[str, EvidenceRow] = {}
    used_rows: set[str] = set()
    anchor_expected = dict(anchors)

    for aid, expected_label in anchors:
        rid, lconf = located.get(aid, ("ABSENT", 0.0))
        if rid == "ABSENT" or rid not in row_ids or rid in used_rows:
            continue
        row_index = int(rid[1:])
        row = _read_isolated_row_robust(
            client, oriented, geom, row_index, page_type, page_no, rotation,
            aid, expected_label, rid, lconf,
        )
        if row is None:
            # If direct locator selected a bad row, use inventory's next best
            # physical label rather than copying a neighboring amount.
            if inventory:
                alternatives = sorted(
                    [(_anchor_inventory_score(aid, expected_label, lab), arid) for arid, (lab, _cf) in inventory.items() if arid not in used_rows and arid != rid],
                    reverse=True,
                )
                for score, arid in alternatives[:3]:
                    if score < ROBUST_FUZZY_MIN_SCORE:
                        break
                    row = _read_isolated_row_robust(
                        client, oriented, geom, int(arid[1:]), page_type, page_no, rotation,
                        aid, expected_label, arid, score, force_full_ensemble=True,
                    )
                    if row is not None:
                        rid = arid
                        break
        if row is None:
            continue
        used_rows.add(rid)
        row_by_aid[aid] = row

    # Strong cross-row identities are a recovery trigger, not just a final report.
    relation, involved = _cross_row_relation(page_type, row_by_aid)
    if relation is False:
        # Re-read the total first, then the components if required.
        priority = {
            "BILAN_ACTIF": ["a07", "a01", "a03", "a05"],
            "BILAN_PASSIF": ["p09", "p10", "p05", "p08"],
            "CPC": ["c01", "c02", "c03"],
        }.get(page_type, involved)
        for aid in priority:
            rid, lconf = located.get(aid, ("ABSENT", 0.0))
            if rid == "ABSENT" or rid not in row_ids:
                continue
            reread = _read_isolated_row_robust(
                client, oriented, geom, int(rid[1:]), page_type, page_no, rotation,
                aid, anchor_expected[aid], rid, lconf, force_full_ensemble=True,
            )
            if reread is not None:
                row_by_aid[aid] = reread
            relation2, _ = _cross_row_relation(page_type, row_by_aid)
            if relation2 is True:
                break
        final_relation, _ = _cross_row_relation(page_type, row_by_aid)
        if final_relation is False:
            for aid in involved:
                if aid in row_by_aid:
                    row_by_aid[aid].notes.append("cross_row_verification_failed=true")
                    row_by_aid[aid].confidence = min(row_by_aid[aid].confidence, 0.69)

    out = list(row_by_aid.values())
    order = {aid: i for i, (aid, _l) in enumerate(anchors)}
    out.sort(key=lambda r: order.get(_anchor_id(r) or "", 999))
    return out


# ---------------------------
# Blank semantics — uncertain is NOT blank
# ---------------------------
def row_present_but_current_blank(row: EvidenceRow) -> bool:
    if row.field_code in {"UNMAPPED", "AMBIGUOUS"}:
        return False
    if "row_present=true" not in row.notes and not row.source.startswith("native"):
        return False
    if value_from_row(row) is not None:
        return False
    if row.source.startswith("native"):
        return True
    current_cols = {
        "BILAN_ACTIF": ["NET_N"],
        "BILAN_PASSIF": ["EXERCICE_N"],
        "CPC": ["TOTAL_N", "OP_N"],
        "DETAIL_CPC": ["EXERCICE_N"],
    }.get(row.page_type, [])
    return bool(current_cols) and all(_cell_state(row, c) == "blank" for c in current_cols)


# ---------------------------
# Improved controls
# ---------------------------
def run_controls(rows: list[EvidenceRow], rcc: pd.DataFrame) -> pd.DataFrame:
    checks: list[dict[str, Any]] = []

    for r in rows:
        if r.page_type != "BILAN_ACTIF":
            continue
        b, a, n = cell_value(r, "BRUT"), cell_value(r, "AMORT_PROV"), cell_value(r, "NET_N")
        if b is not None and a is not None and n is not None:
            diff = (b - a) - n
            checks.append({"check": "ACTIF_ROW_BRUT_MINUS_AMORT_EQUALS_NET", "page": r.page, "field": r.field_code, "expected": amount_str(b-a), "observed": amount_str(n), "difference": amount_str(diff), "status": "passed" if abs(diff) <= ROBUST_ARITH_TOL else "failed"})

    cpc_arithmetic_fields = {
        "CHIFFRE_AFFAIRES", "VENTES_MARCHANDISES", "VENTES_BIENS_SERVICES",
        "ACHATS_REVENDUS", "ACHATS_CONSOMMES", "AUTRES_CHARGES_EXTERNES", "CHARGES_INTERETS"
    }
    for r in rows:
        if r.page_type != "CPC" or r.field_code not in cpc_arithmetic_fields:
            continue
        x, y, t = cell_value(r, "OP_N"), cell_value(r, "OP_PREV"), cell_value(r, "TOTAL_N")
        expected = None
        if x is not None and t is not None:
            if y is not None:
                expected = x + y
            elif _cell_state(r, "OP_PREV") == "blank":
                expected = x  # blank remains None in evidence; neutral only inside this formula
        if expected is not None:
            diff = expected - t
            checks.append({"check": "CPC_OPS_SUM_TO_TOTAL_N", "page": r.page, "field": r.field_code, "expected": amount_str(expected), "observed": amount_str(t), "difference": amount_str(diff), "status": "passed" if abs(diff) <= ROBUST_ARITH_TOL else "failed"})

    def ev(code: str, ptype: str) -> Decimal | None:
        c = [r for r in rows if r.field_code == code and r.page_type == ptype and value_from_row(r) is not None]
        return value_from_row(c[0]) if c else None

    # Bilan Actif total = I + II + III
    ai, ac, ta, tg = ev("ACTIFS_IMMOBILISES", "BILAN_ACTIF"), ev("ACTIF_CIRCULANT", "BILAN_ACTIF"), ev("TRESORERIE_ACTIF", "BILAN_ACTIF"), ev("TOTAL_ACTIF", "BILAN_ACTIF")
    if all(v is not None for v in [ai, ac, ta, tg]):
        expected = ai + ac + ta
        diff = expected - tg
        checks.append({"check": "BILAN_ACTIF_I_PLUS_II_PLUS_III_EQUALS_TOTAL", "page": None, "field": "TOTAL_ACTIF", "expected": amount_str(expected), "observed": amount_str(tg), "difference": amount_str(diff), "status": "passed" if abs(diff) <= ROBUST_ARITH_TOL else "failed"})

    # Bilan Passif total = I + II + III
    p1, p2, p3, pg = ev("PASSIF_TOTAL_I", "BILAN_PASSIF"), ev("PASSIF_CIRCULANT", "BILAN_PASSIF"), ev("TRESORERIE_PASSIF", "BILAN_PASSIF"), ev("TOTAL_PASSIF", "BILAN_PASSIF")
    if all(v is not None for v in [p1, p2, p3, pg]):
        expected = p1 + p2 + p3
        diff = expected - pg
        checks.append({"check": "BILAN_PASSIF_I_PLUS_II_PLUS_III_EQUALS_TOTAL", "page": None, "field": "TOTAL_PASSIF", "expected": amount_str(expected), "observed": amount_str(pg), "difference": amount_str(diff), "status": "passed" if abs(diff) <= ROBUST_ARITH_TOL else "failed"})

    # CA = ventes marchandises + ventes biens/services when all three explicit rows exist.
    ca, vm, vb = ev("CHIFFRE_AFFAIRES", "CPC"), ev("VENTES_MARCHANDISES", "CPC"), ev("VENTES_BIENS_SERVICES", "CPC")
    if all(v is not None for v in [ca, vm, vb]):
        expected = vm + vb
        diff = expected - ca
        checks.append({"check": "CPC_VENTES_SUM_EQUALS_CHIFFRE_AFFAIRES", "page": None, "field": "CHIFFRE_AFFAIRES", "expected": amount_str(expected), "observed": amount_str(ca), "difference": amount_str(diff), "status": "passed" if abs(diff) <= ROBUST_ARITH_TOL else "failed"})

    def rcc_val(code: str) -> Decimal | None:
        s = rcc.loc[rcc.code == code, "value"]
        if s.empty:
            return None
        return parse_amount(s.iloc[0])

    total_actif = rcc_val("TOTAL_BILAN")
    total_passif = ev("TOTAL_PASSIF", "BILAN_PASSIF")
    if total_actif is not None and total_passif is not None:
        diff = total_actif - total_passif
        checks.append({"check": "TOTAL_ACTIF_EQUALS_TOTAL_PASSIF", "page": None, "field": "TOTAL_BILAN", "expected": amount_str(total_actif), "observed": amount_str(total_passif), "difference": amount_str(diff), "status": "passed" if abs(diff) <= ROBUST_ARITH_TOL else "failed"})

    pas_rows = [r for r in rows if r.field_code == "RESULTAT_NET" and r.page_type == "BILAN_PASSIF" and value_from_row(r) is not None]
    resolved_rn = rcc_val("RESULTAT_NET")
    if resolved_rn is not None and pas_rows:
        p = value_from_row(pas_rows[0])
        if p is not None:
            diff = resolved_rn - p
            checks.append({"check": "RESULTAT_NET_RESOLVED_EQUALS_PASSIF", "page": None, "field": "RESULTAT_NET", "expected": amount_str(resolved_rn), "observed": amount_str(p), "difference": amount_str(diff), "status": "passed" if abs(diff) <= ROBUST_ARITH_TOL else "failed"})
    return pd.DataFrame(checks)


def _propagate_validation_status(rcc: pd.DataFrame, controls: pd.DataFrame, rows: list[EvidenceRow]) -> pd.DataFrame:
    out = rcc.copy()
    if out.empty:
        return out
    evidence_to_rcc = {
        "TOTAL_ACTIF": "TOTAL_BILAN",
        "ACTIFS_IMMOBILISES": "ACTIFS_IMMOBILISES",
        "ACTIF_CIRCULANT": "ACTIF_CIRCULANT",
        "CLIENTS": "CREANCES_CLIENTS",
        "TRESORERIE_ACTIF": "TRESORERIE_ACTIF",
        "CAISSE": "CAISSE",
        "DETTES_FINANCEMENT": "DETTES_BANCAIRES_MLT",
        "DETTES_BANCAIRES_CT": "DETTES_BANCAIRES_CT",
        "PASSIF_CIRCULANT": "PASSIF_CIRCULANT",
        "FOURNISSEURS": "DETTES_FOURNISSEURS",
        "COMPTE_COURANT_ASSOCIES": "COMPTE_COURANT_ASSOCIES",
        "TRESORERIE_PASSIF": "TRESORERIE_PASSIF",
        "CHIFFRE_AFFAIRES": "CHIFFRE_AFFAIRES",
        "ACHATS_REVENDUS": "ACHATS_REVENDUS",
        "ACHATS_CONSOMMES": "ACHATS_CONSOMMES",
        "AUTRES_CHARGES_EXTERNES": "AUTRES_CHARGES_EXTERNES",
        "CHARGES_INTERETS": "CHARGES_INTERETS",
        "RESULTAT_NET": "RESULTAT_NET",
    }
    bad_codes: set[str] = set()
    if controls is not None and not controls.empty:
        for _idx, c in controls[controls.status == "failed"].iterrows():
            field = str(c.get("field", ""))
            bad_codes.add(evidence_to_rcc.get(field, field))
    for r in rows:
        if "verification_unresolved=true" in r.notes or "cross_row_verification_failed=true" in r.notes:
            code = evidence_to_rcc.get(r.field_code)
            if code:
                bad_codes.add(code)
    for code in bad_codes:
        mask = out.code == code
        if not mask.any():
            continue
        # Preserve explicit missing/blank/proxy semantics. Numeric values that fail
        # verification must never remain 'confirmed'.
        for idx in out.index[mask]:
            # pandas stores a missing amount as NaN, which is not Python None.
            current = out.at[idx, "value"]
            if pd.isna(current) or out.at[idx, "status"] in {"proxy", "conflicting", "conflicting_blank_vs_value", "missing", "blank_on_form"}:
                continue
            out.at[idx, "status"] = "needs_review"
            old = out.at[idx, "note"]
            suffix = "Automated verification did not resolve all OCR/accounting checks."
            out.at[idx, "note"] = f"{old} {suffix}".strip() if isinstance(old, str) and old else suffix
    return out


# ---------------------------
# Robust scan page wrapper
# ---------------------------
def extract_scan_page(
    client: OllamaClient,
    im: Image.Image,
    page_type: str,
    page_no: int,
    rotation: int,
    *,
    use_reasoning_mapper: bool = True,
    use_adjudicator: bool = True,
) -> list[EvidenceRow]:
    oriented = im if rotation == 0 else im.rotate(-rotation, expand=True)
    if page_type == "IDENTIFICATION":
        prepared = image_bytes(crop_to_visible_content(oriented), quality=96, max_side=3000)
        prompt = """
Extract only taxpayer-identification information that is visibly printed on this page.
Allowed fields: Raison sociale, Identifiant fiscal, ICE, Taxe professionnelle, R.C./RC, Adresse, Ville, exercise start date and exercise end date.
Copy identifiers and dates exactly. Never infer an ICE/RC/date from another number. Omit a field if it is not printed or unreadable.
""".strip()
        data = client.chat_json(prompt=prompt, images=[prepared], schema=_IDENT_SCHEMA, model=client.model, think=False, num_predict=1000)
        out = []
        for f in data.get("fields", []):
            val = str(f.get("raw_value", "")).strip()
            if not val:
                continue
            out.append(EvidenceRow(page_no, page_type, "glm_vision", f["field_code"], f["field_code"], {"TEXT": val}, float(f.get("confidence", 0.7)), rotation, mapping_source="direct_identification", mapping_confidence=1.0))
        return out

    raw_rows: list[EvidenceRow]
    if GRID_GUIDED_SCAN and page_type in {"BILAN_ACTIF", "BILAN_PASSIF", "CPC"}:
        try:
            raw_rows = extract_grid_guided_scan_rows(client, oriented, page_type, page_no, rotation)
        except Exception as grid_exc:
            if not GRID_FALLBACK_TO_FULL_TABLE:
                raise
            crop, crop_box = detect_grid_crop(oriented)
            crop = ImageOps.autocontrast(crop.convert("RGB"), cutoff=0.5)
            crop = ImageEnhance.Sharpness(crop).enhance(1.08)
            prepared = image_bytes(crop, quality=94, max_side=EXTRACT_MAX_SIDE)
            raw_rows = extract_raw_scan_rows(client, prepared, page_type, page_no, rotation, crop_box)
            for r in raw_rows:
                r.notes.append(f"grid_fallback={type(grid_exc).__name__}:{grid_exc}")
    else:
        crop, crop_box = detect_grid_crop(oriented)
        crop = ImageOps.autocontrast(crop.convert("RGB"), cutoff=0.5)
        crop = ImageEnhance.Sharpness(crop).enhance(1.08)
        prepared = image_bytes(crop, quality=94, max_side=EXTRACT_MAX_SIDE)
        raw_rows = extract_raw_scan_rows(client, prepared, page_type, page_no, rotation, crop_box)

    if not use_reasoning_mapper:
        for row in raw_rows:
            code = _rule_map_from_anchor(page_type, row)
            if code:
                row.field_code = code
                row.mapping_source = "rule_only"
                row.mapping_confidence = 1.0
        return raw_rows
    return reasoner_map_rows(client, raw_rows, page_type, use_adjudicator=use_adjudicator)


# ---------------------------
# Orchestrator override
# ---------------------------
def analyze_pdf(
    pdf_path: str | Path,
    *,
    client: OllamaClient | None = None,
    max_pages: int | None = None,
    use_glm_verification: bool = True,
    use_reasoning_mapper: bool = True,
    use_adjudicator: bool = True,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
):
    def emit(event: str, **data: Any) -> None:
        if progress is not None:
            progress(event, data)

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(path)
    client = client or OllamaClient()
    if use_glm_verification:
        if use_reasoning_mapper:
            client.check_models(require_adjudicator=use_adjudicator, require_robust=True)
        else:
            client.check_models(require_adjudicator=False, require_robust=True)

    doc = pymupdf.open(path)
    n = min(doc.page_count, max_pages or doc.page_count)
    audit: list[dict[str, Any]] = []
    evidence: list[EvidenceRow] = []
    emit("pages_rendered", count=n, pages_total=doc.page_count)

    for i in range(n):
        page = doc[i]
        page_no = i + 1
        native_ok, native_text, word_count = native_text_quality(page)
        if native_ok:
            page_type = classify_native(native_text)
            audit.append({"page": page_no, "mode": "native", "native_chars": len(native_text), "words": word_count, "rotation": 0, "page_type": page_type, "layout_confidence": 1.0, "orientation_confidence": 1.0, "type_confidence": 1.0, "scan_extraction_mode": "native", "extraction_error": None})
            emit("page_classified", page=page_no, pages_total=n, page_type=page_type, orientation=0, mode="native")
            if page_type == "IDENTIFICATION":
                evidence.extend(extract_native_identification(native_text, page_no))
            elif page_type in {"BILAN_ACTIF", "BILAN_PASSIF", "CPC", "DETAIL_CPC"}:
                raw_rows = extract_native_table_rows(page, page_type, page_no)
                evidence.extend(canonicalize_native_rows(raw_rows))
            emit("page_extracted", page=page_no, pages_total=n, page_type=page_type, mode="native")
            continue

        im = render_page(page, dpi=RENDER_DPI)
        if not use_glm_verification:
            audit.append({"page": page_no, "mode": "scan", "native_chars": len(native_text), "words": word_count, "rotation": None, "page_type": "UNCLASSIFIED", "layout_confidence": None, "orientation_confidence": None, "type_confidence": None, "scan_extraction_mode": None, "extraction_error": None})
            emit("page_skipped", page=page_no, pages_total=n, page_type="UNCLASSIFIED")
            continue

        try:
            layout = scan_layout_agent(client, im)
        except Exception as exc:
            audit.append({"page": page_no, "mode": "scan_glm", "native_chars": len(native_text), "words": word_count, "rotation": None, "page_type": "UNCLASSIFIED", "layout_confidence": 0.0, "orientation_confidence": 0.0, "type_confidence": 0.0, "scan_extraction_mode": None, "extraction_error": f"layout: {exc}"})
            logger.warning("page %s/%s: LAYOUT ERROR -> %s", page_no, n, exc)
            emit("page_failed", page=page_no, pages_total=n, page_type="UNCLASSIFIED", error=f"layout: {exc}")
            continue

        page_type = layout["page_type"]
        rotation = int(layout["rotation"])
        rec = {"page": page_no, "mode": "scan_glm", "native_chars": len(native_text), "words": word_count, "rotation": rotation, "page_type": page_type, "layout_confidence": layout.get("confidence"), "orientation_confidence": layout.get("orientation_confidence"), "type_confidence": layout.get("type_confidence"), "axis_ratio": layout.get("axis_ratio"), "orientation_source": layout.get("orientation_source"), "glm_rotation": layout.get("glm_rotation"), "qwen_rotation": layout.get("qwen_rotation"), "scan_extraction_mode": None, "extraction_error": None}
        audit.append(rec)
        logger.info(
            "page %s/%s: scan -> rotation=%s, type=%s, orient_conf=%s, type_conf=%s, orient_source=%s",
            page_no, n, rotation, page_type,
            layout.get("orientation_confidence"), layout.get("type_confidence"), layout.get("orientation_source"),
        )
        emit("page_classified", page=page_no, pages_total=n, page_type=page_type, orientation=rotation, mode="scan_glm")
        if page_type in RELEVANT_PAGE_TYPES:
            started = time.time()
            try:
                page_rows = extract_scan_page(
                    client, im, page_type, page_no, rotation,
                    use_reasoning_mapper=use_reasoning_mapper,
                    use_adjudicator=use_adjudicator,
                )
                evidence.extend(page_rows)
                rec["scan_extraction_mode"] = ",".join(sorted({r.source for r in page_rows})) if page_rows else "none"
                logger.info("  extracted %s evidence rows via %s", len(page_rows), rec["scan_extraction_mode"])
                emit(
                    "page_extracted", page=page_no, pages_total=n, page_type=page_type,
                    candidates=len(page_rows), mode=rec["scan_extraction_mode"],
                    latency_ms=int((time.time() - started) * 1000),
                )
            except Exception as exc:
                rec["extraction_error"] = str(exc)
                logger.warning("  EXTRACTION ERROR (page kept in audit, pipeline continues): %s", exc)
                emit("page_failed", page=page_no, pages_total=n, page_type=page_type, error=str(exc))
        else:
            emit("page_skipped", page=page_no, pages_total=n, page_type=page_type)

    doc.close()
    emit("resolving_fields", rows=len(evidence))
    rcc = resolve_rcc(evidence)
    emit("running_controls")
    controls = run_controls(evidence, rcc)
    rcc = _propagate_validation_status(rcc, controls, evidence)
    return pd.DataFrame(audit), rows_df(evidence), rcc, controls, evidence
