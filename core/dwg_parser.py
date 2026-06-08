"""
DWG / DXF parser.

Strategy:
  1. If the file is .dxf — open directly with ezdxf.
  2. If the file is .dwg — convert to a temp .dxf using ODA File Converter
     (via ezdxf.addons.odafc), then open the result.
  3. Iterate doc.blocks, skipping anonymous / model-space entries.
  4. Extract block names, descriptions, and ATTDEF tag names.
  5. Delete the temp .dxf when done.
"""
import logging
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)
logging.getLogger("ezdxf").setLevel(logging.ERROR)

# Anonymous block patterns to skip
_ANON_PATTERNS = re.compile(
    r"^\*(Model_Space|Paper_Space|PAPER_SPACE|MODEL_SPACE|Block\d+|D\d+|A\d+|U\d+|T\d+)",
    re.IGNORECASE,
)


@dataclass
class ParsedBlock:
    name: str
    description: str = ""
    attribute_tags: List[str] = field(default_factory=list)
    entities: List[Dict[str, Any]] = field(default_factory=list)
    bounds: Dict[str, float] = field(default_factory=dict)


@dataclass
class ParseResult:
    blocks: List[ParsedBlock] = field(default_factory=list)
    error: str = ""
    source: str = ""  # "dxf_direct" | "oda_converted" | "failed"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_dwg(
    file_path: str,
    oda_exe: Optional[str] = None,
    skip_anonymous: bool = True,
    temp_dir: Optional[str] = None,
) -> ParseResult:
    """
    Parse a DWG or DXF file and return a ParseResult with all block definitions.

    Parameters
    ----------
    file_path  : Absolute path to the .dwg or .dxf file.
    oda_exe    : Path to ODAFileConverter.exe. Required for .dwg files if
                 ezdxf cannot open them directly.
    skip_anonymous : If True, skip blocks whose names start with '*'.
    temp_dir   : Directory to place temp conversion files. Uses system temp if None.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".dxf":
        return _parse_dxf(file_path, skip_anonymous)

    if ext in (".dwg", ".dwt"):
        return _parse_dwg(file_path, oda_exe, skip_anonymous, temp_dir)

    return ParseResult(error=f"Unsupported file type: {ext}", source="failed")


# ---------------------------------------------------------------------------
# DXF parsing (ezdxf)
# ---------------------------------------------------------------------------

def _parse_dxf(dxf_path: str, skip_anonymous: bool) -> ParseResult:
    try:
        import ezdxf  # type: ignore[import]
    except ImportError:
        return ParseResult(error="ezdxf not installed", source="failed")

    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as exc:  # ezdxf.DXFError, IOError, etc.
        return ParseResult(error=f"ezdxf.readfile failed: {exc}", source="failed")

    blocks: List[ParsedBlock] = []
    for blk in doc.blocks:
        name: str = blk.name

        if skip_anonymous and _is_anonymous(name):
            continue

        description = _get_block_description(blk)
        attdef_tags = _extract_attdef_tags(blk)
        entities, bounds = _extract_geometry(blk)

        blocks.append(ParsedBlock(
            name=name,
            description=description,
            attribute_tags=attdef_tags,
            entities=entities,
            bounds=bounds,
        ))

    return ParseResult(blocks=blocks, source="dxf_direct")


# ---------------------------------------------------------------------------
# DWG parsing — ODA conversion path
# ---------------------------------------------------------------------------

def _parse_dwg(
    dwg_path: str,
    oda_exe: Optional[str],
    skip_anonymous: bool,
    temp_dir: Optional[str],
) -> ParseResult:
    """Try ODA conversion, then fall back to direct ezdxf DWG reading."""

    # --- Attempt 1: ODA File Converter ---
    if oda_exe and Path(oda_exe).is_file():
        result = _try_oda_conversion(dwg_path, oda_exe, skip_anonymous, temp_dir)
        if not result.error:
            return result
        log.warning("ODA conversion failed for %s: %s — trying direct", dwg_path, result.error)
    else:
        # ODA is not configured — return a distinct sentinel so the caller
        # can count these separately without logging each file as an error.
        reason = "path not set" if not oda_exe else f"exe not found: {oda_exe}"
        log.debug("ODA not available for %s (%s)", dwg_path, reason)
        return ParseResult(
            error=f"ODA_NOT_CONFIGURED: {reason}",
            source="oda_missing",
        )

    # --- Attempt 2: ezdxf direct DWG reading (limited support) ---
    result = _try_ezdxf_direct(dwg_path, skip_anonymous)
    if not result.error:
        return result

    log.error("Parse failed for %s: %s", dwg_path, result.error)
    return result


def _try_oda_conversion(
    dwg_path: str,
    oda_exe: str,
    skip_anonymous: bool,
    temp_dir: Optional[str],
) -> ParseResult:
    """Convert DWG→DXF with ODA CLI, parse with ezdxf, clean up temp."""
    tmp_base = temp_dir or tempfile.gettempdir()
    tmp_folder = tempfile.mkdtemp(prefix="blksearch_", dir=tmp_base)

    try:
        output_dxf = _run_oda_converter(dwg_path, oda_exe, tmp_folder)
        if not output_dxf:
            return ParseResult(error="ODA produced no output file", source="failed")

        result = _parse_dxf(output_dxf, skip_anonymous)
        result.source = "oda_converted"
        return result

    finally:
        shutil.rmtree(tmp_folder, ignore_errors=True)


def _run_oda_converter(dwg_path: str, oda_exe: str, out_dir: str) -> Optional[str]:
    """
    Run ODAFileConverter CLI to convert one DWG file to DXF.

    CLI signature:
      ODAFileConverter <input_folder> <output_folder> <version> <type>
                       <recurse> <audit> [filter]

    We copy the single file into a temp input folder to isolate the conversion.
    """
    import subprocess

    inp_dir = tempfile.mkdtemp(prefix="oda_in_", dir=out_dir)
    src_name = Path(dwg_path).name
    dst_src = os.path.join(inp_dir, src_name)
    shutil.copy2(dwg_path, dst_src)

    cmd = [
        oda_exe,
        inp_dir,        # input folder
        out_dir,         # output folder
        "ACAD2018",      # output version (DXF R2018)
        "DXF",           # output type
        "0",             # recurse (0=no)
        "1",             # audit (1=yes)
    ]

    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        # Use DEVNULL instead of capture_output — ODA is a GUI app;
        # creating stdin/stdout pipes causes a deadlock.
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            startupinfo=si,
        )
        try:
            ret = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            log.warning("ODA conversion timed out for %s", dwg_path)
            return None
        if ret != 0:
            log.debug("ODA exit %d for %s", ret, dwg_path)
    except subprocess.TimeoutExpired:
        log.warning("ODA conversion timed out for %s", dwg_path)
        return None
    except OSError as exc:
        log.warning("ODA launch failed: %s", exc)
        return None

    # ODA outputs the DXF with the same base name
    expected = os.path.join(out_dir, Path(dwg_path).stem + ".dxf")
    if os.path.isfile(expected):
        return expected

    # Search for any .dxf in out_dir
    for fname in os.listdir(out_dir):
        if fname.lower().endswith(".dxf"):
            return os.path.join(out_dir, fname)

    return None


def _try_ezdxf_direct(dwg_path: str, skip_anonymous: bool) -> ParseResult:
    """ezdxf can sometimes open .dwg files natively (limited versions)."""
    try:
        import ezdxf  # type: ignore[import]
        doc = ezdxf.readfile(dwg_path)
    except Exception as exc:
        return ParseResult(error=f"ezdxf direct DWG read failed: {exc}", source="failed")

    blocks: List[ParsedBlock] = []
    for blk in doc.blocks:
        name: str = blk.name
        if skip_anonymous and _is_anonymous(name):
            continue
        entities, bounds = _extract_geometry(blk)
        blocks.append(ParsedBlock(
            name=name,
            description=_get_block_description(blk),
            attribute_tags=_extract_attdef_tags(blk),
            entities=entities,
            bounds=bounds,
        ))
    return ParseResult(blocks=blocks, source="dxf_direct")


# ---------------------------------------------------------------------------
# ezdxf helpers
# ---------------------------------------------------------------------------

def _is_anonymous(name: str) -> bool:
    return bool(_ANON_PATTERNS.match(name))


def _get_block_description(blk: Any) -> str:
    try:
        return blk.dxf.description or ""
    except Exception:
        return ""


def _extract_attdef_tags(blk: Any) -> List[str]:
    """Extract ATTDEF tag names from a block definition."""
    tags: List[str] = []
    try:
        for entity in blk:
            if entity.dxftype() == "ATTDEF":
                try:
                    tag = entity.dxf.tag.strip()
                    if tag:
                        tags.append(tag)
                except Exception:
                    pass
    except Exception:
        pass
    return tags


def _extract_geometry(blk: Any) -> tuple[List[Dict[str, Any]], Dict[str, float]]:
    entities: List[Dict[str, Any]] = []
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")

    def update_bounds(x: float, y: float) -> None:
        nonlocal min_x, min_y, max_x, max_y
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x)
        max_y = max(max_y, y)

    def add_point(x: Any, y: Any) -> tuple[float, float]:
        fx = float(x)
        fy = float(y)
        update_bounds(fx, fy)
        return fx, fy

    def process_entity(entity: Any, default_layer: str = "0") -> None:
        layer = getattr(entity.dxf, "layer", default_layer) or default_layer
        etype = entity.dxftype()

        if etype == "LINE":
            s = entity.dxf.start
            e = entity.dxf.end
            x1, y1 = add_point(s.x, s.y)
            x2, y2 = add_point(e.x, e.y)
            entities.append({
                "type": "LINE",
                "coords": [[x1, y1], [x2, y2]],
                "layer": layer,
            })

        elif etype == "CIRCLE":
            c = entity.dxf.center
            r = float(entity.dxf.radius)
            cx, cy = add_point(c.x, c.y)
            update_bounds(cx - r, cy - r)
            update_bounds(cx + r, cy + r)
            entities.append({
                "type": "CIRCLE",
                "center": [cx, cy],
                "radius": r,
                "layer": layer,
            })

        elif etype == "ARC":
            c = entity.dxf.center
            r = float(entity.dxf.radius)
            cx, cy = add_point(c.x, c.y)
            sa = float(entity.dxf.start_angle)
            ea = float(entity.dxf.end_angle)
            # Include full circle extents as a safe bounds envelope.
            update_bounds(cx - r, cy - r)
            update_bounds(cx + r, cy + r)
            entities.append({
                "type": "ARC",
                "center": [cx, cy],
                "radius": r,
                "start_angle": sa,
                "end_angle": ea,
                "layer": layer,
            })

        elif etype in ("LWPOLYLINE", "POLYLINE"):
            pts: List[List[float]] = []
            closed = bool(getattr(entity, "is_closed", False))
            try:
                raw = list(entity.get_points())
                for p in raw:
                    x, y = add_point(p[0], p[1])
                    pts.append([x, y])
            except Exception:
                pts = []
            if len(pts) >= 2:
                entities.append({
                    "type": "POLYLINE",
                    "coords": pts,
                    "closed": closed,
                    "layer": layer,
                })

        elif etype in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
            text_value = ""
            x = 0.0
            y = 0.0
            height = 2.5
            rotation = 0.0

            try:
                if etype == "MTEXT":
                    text_value = (entity.plain_text() or "").strip()
                    ins = entity.dxf.insert
                    height = float(getattr(entity.dxf, "char_height", 2.5) or 2.5)
                    rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)
                else:
                    text_value = str(getattr(entity.dxf, "text", "") or "").strip()
                    ins = entity.dxf.insert
                    height = float(getattr(entity.dxf, "height", 2.5) or 2.5)
                    rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)

                if text_value:
                    x, y = add_point(ins.x, ins.y)
                    # Approximate text bbox for fit-to-view in fallback renderer.
                    txt_w = max(height * 0.6, len(text_value) * height * 0.6)
                    txt_h = max(height, 1.0)
                    update_bounds(x + txt_w, y + txt_h)
                    update_bounds(x - txt_w * 0.1, y - txt_h * 0.2)
                    entities.append({
                        "type": "TEXT",
                        "text": text_value,
                        "position": [x, y],
                        "height": height,
                        "rotation": rotation,
                        "layer": layer,
                    })
            except Exception:
                pass

    try:
        for entity in blk:
            try:
                if entity.dxftype() == "INSERT":
                    try:
                        for sub in entity.virtual_entities():
                            try:
                                process_entity(sub, default_layer=getattr(entity.dxf, "layer", "0") or "0")
                            except Exception:
                                continue
                    except Exception:
                        continue
                else:
                    process_entity(entity)

            except Exception:
                continue
    except Exception:
        pass

    if not entities:
        return [], {}

    # Keep bounds finite and non-zero to simplify viewport fit calculations.
    if not math.isfinite(min_x) or not math.isfinite(min_y) or not math.isfinite(max_x) or not math.isfinite(max_y):
        return entities, {}

    if abs(max_x - min_x) < 1e-9:
        max_x = min_x + 1.0
    if abs(max_y - min_y) < 1e-9:
        max_y = min_y + 1.0

    return entities, {
        "min_x": float(min_x),
        "min_y": float(min_y),
        "max_x": float(max_x),
        "max_y": float(max_y),
    }
