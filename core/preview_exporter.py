"""
Preview export helpers used by indexing.

This module renders persistent PNG previews from already-extracted block geometry,
so runtime UI paths never need AutoCAD or on-demand rendering work.
"""

import hashlib
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen


def build_preview_relative_path(
    file_path: str,
    block_name: str,
    file_mtime: float,
    file_size: int,
    block_id: int,
) -> str:
    """Return deterministic relative path under data/previews for one block."""
    file_key_src = "|".join([
        os.path.normcase(os.path.normpath(file_path)),
        str(int(file_mtime)),
        str(int(file_size)),
    ])
    file_key = hashlib.sha1(file_key_src.encode("utf-8", errors="ignore")).hexdigest()[:20]

    name_key = hashlib.sha1(block_name.lower().encode("utf-8", errors="ignore")).hexdigest()[:10]
    filename = f"{block_id}_{name_key}.png"
    return str(Path("data") / "previews" / file_key / filename)


def render_preview_from_geometry(
    entities: List[Dict[str, Any]],
    bounds: Dict[str, float],
    output_path: str,
    image_size: int = 700,
) -> bool:
    """Render geometry entities into a PNG file. Returns True on success."""
    if not entities:
        return False

    min_x, min_y, max_x, max_y = _read_bounds(bounds)
    if min_x is None:
        return False

    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        return False

    padding = max(16.0, image_size * 0.06)
    draw_w = image_size - 2.0 * padding
    draw_h = image_size - 2.0 * padding
    if draw_w <= 1 or draw_h <= 1:
        return False

    scale = min(draw_w / width, draw_h / height)

    def map_point(x: float, y: float) -> Tuple[float, float]:
        px = padding + (x - min_x) * scale
        py = image_size - (padding + (y - min_y) * scale)
        return px, py

    img = QImage(image_size, image_size, QImage.Format.Format_ARGB32)
    img.fill(QColor("#1f1f1f"))

    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    pen = QPen(QColor("#58d7c9"))
    pen.setWidthF(max(1.1, image_size / 520.0))
    painter.setPen(pen)

    base_font = QFont("Segoe UI", max(6, int(image_size / 110)))
    painter.setFont(base_font)

    for ent in entities:
        etype = str(ent.get("type", "")).upper()
        try:
            if etype == "LINE":
                coords = ent.get("coords") or []
                if len(coords) >= 2:
                    x1, y1 = _xy(coords[0])
                    x2, y2 = _xy(coords[1])
                    px1, py1 = map_point(x1, y1)
                    px2, py2 = map_point(x2, y2)
                    painter.drawLine(px1, py1, px2, py2)

            elif etype == "CIRCLE":
                cx, cy = _xy(ent.get("center"))
                r = float(ent.get("radius", 0.0))
                if r > 0:
                    left, top = map_point(cx - r, cy + r)
                    right, bottom = map_point(cx + r, cy - r)
                    painter.drawEllipse(left, top, right - left, bottom - top)

            elif etype == "ARC":
                cx, cy = _xy(ent.get("center"))
                r = float(ent.get("radius", 0.0))
                if r > 0:
                    sa = float(ent.get("start_angle", 0.0))
                    ea = float(ent.get("end_angle", sa))
                    pts = _sample_arc(cx, cy, r, sa, ea)
                    if len(pts) >= 2:
                        qpts = [QPointF(*map_point(x, y)) for x, y in pts]
                        for i in range(len(qpts) - 1):
                            painter.drawLine(qpts[i], qpts[i + 1])

            elif etype == "POLYLINE":
                coords = ent.get("coords") or []
                if len(coords) >= 2:
                    qpts = [QPointF(*map_point(*_xy(p))) for p in coords]
                    for i in range(len(qpts) - 1):
                        painter.drawLine(qpts[i], qpts[i + 1])
                    if bool(ent.get("closed", False)) and len(qpts) > 2:
                        painter.drawLine(qpts[-1], qpts[0])

            elif etype == "TEXT":
                pos = ent.get("position")
                text = str(ent.get("text", "")).strip()
                if pos is not None and text:
                    px, py = map_point(*_xy(pos))
                    cad_h = float(ent.get("height", 0.0) or 0.0)
                    pixel_h = max(5, min(14, int(cad_h * scale * 0.9)))
                    txt_font = QFont(base_font)
                    txt_font.setPixelSize(pixel_h)
                    painter.setFont(txt_font)

                    max_chars = max(14, min(56, int(draw_w / max(pixel_h * 0.55, 1.0))))
                    lines = _prepare_text_lines(text, max_chars)
                    line_step = max(pixel_h + 1, int(pixel_h * 1.25))
                    for idx, line in enumerate(lines[:4]):
                        painter.drawText(px + 1, py - 1 + (idx * line_step), line)

                    painter.setFont(base_font)
        except Exception:
            continue

    painter.end()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    return bool(img.save(str(out), "PNG"))


def _read_bounds(bounds: Dict[str, float]) -> Tuple[Any, Any, Any, Any]:
    try:
        min_x = float(bounds.get("min_x"))
        min_y = float(bounds.get("min_y"))
        max_x = float(bounds.get("max_x"))
        max_y = float(bounds.get("max_y"))
    except Exception:
        return None, None, None, None

    if not all(math.isfinite(v) for v in (min_x, min_y, max_x, max_y)):
        return None, None, None, None
    return min_x, min_y, max_x, max_y


def _xy(raw: Any) -> Tuple[float, float]:
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return 0.0, 0.0
    return float(raw[0]), float(raw[1])


def _sample_arc(cx: float, cy: float, radius: float, start_deg: float, end_deg: float) -> List[Tuple[float, float]]:
    span = (end_deg - start_deg) % 360.0
    if span == 0.0:
        span = 360.0
    steps = max(24, min(220, int(span / 3.0)))
    points: List[Tuple[float, float]] = []
    for i in range(steps + 1):
        ang = math.radians(start_deg + (span * i / steps))
        x = cx + radius * math.cos(ang)
        y = cy + radius * math.sin(ang)
        points.append((x, y))
    return points


def _prepare_text_lines(text: str, max_chars: int) -> List[str]:
    clean = text.replace("\\P", "\n").replace("\r", "\n")
    clean = " ".join(clean.split()) if "\n" not in clean else clean
    raw_lines = [ln.strip() for ln in clean.split("\n") if ln.strip()]
    lines: List[str] = []
    for ln in raw_lines:
        if len(ln) <= max_chars:
            lines.append(ln)
            continue
        start = 0
        while start < len(ln):
            lines.append(ln[start:start + max_chars])
            start += max_chars
    return lines or [""]
