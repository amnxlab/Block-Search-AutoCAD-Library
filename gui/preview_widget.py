"""
Preview Widget — renders a block preview using:
  1. AutoCAD COM automation (primary, requires AutoCAD on the machine)
  2. ezdxf drawing addon + matplotlib (fallback, always available)

The result is displayed in a resizable dialog with a zoom-to-fit image label.
"""
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.database import BlockRecord

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Preview generation workers
# ---------------------------------------------------------------------------

class PreviewWorker(QThread):
    preview_ready = Signal(str)   # path to PNG file
    failed        = Signal(str)   # error message

    def __init__(
        self,
        rec: BlockRecord,
        config: Dict[str, Any],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._rec = rec
        self._config = config

    def run(self) -> None:
        prefer_acad: bool = self._config.get("preview_prefer_acad", True)

        if prefer_acad:
            png = self._try_acad_preview()
            if png:
                self.preview_ready.emit(png)
                return

        png = self._try_ezdxf_preview()
        if png:
            self.preview_ready.emit(png)
        else:
            self.failed.emit("Could not generate a preview for this block.")

    # ------------------------------------------------------------------
    # Method 1 — AutoCAD COM
    # ------------------------------------------------------------------

    def _try_acad_preview(self) -> Optional[str]:
        try:
            import win32com.client as win32  # type: ignore[import]
        except ImportError:
            log.debug("pywin32 not available — skipping AutoCAD COM preview")
            return None

        dwg_path = self._rec.file_path
        block_name = self._rec.block_name

        if not os.path.isfile(dwg_path):
            return None

        tmp_png = tempfile.mktemp(suffix=".png", prefix="blkprev_")

        try:
            acad = win32.Dispatch("AutoCAD.Application")
            acad.Visible = False  # try to stay headless

            # Open the DWG silently
            doc = acad.Documents.Open(dwg_path, True)  # True = read-only

            # Check the block exists
            try:
                blk_def = doc.Blocks.Item(block_name)
            except Exception:
                doc.Close(False)
                log.warning("Block %r not found in COM blocks collection", block_name)
                return None

            # Insert block at 0,0,0 in model space
            model = doc.ModelSpace
            import win32com.client
            origin = win32com.client.VARIANT(
                win32com.client.pythoncom.VT_ARRAY | win32com.client.pythoncom.VT_R8,
                [0.0, 0.0, 0.0],
            )
            ref = model.InsertBlock(origin, block_name, 1, 1, 1, 0)

            # Zoom to the extents of the new insert
            acad.ActiveDocument.SendCommand("ZOOM\nE\n")

            # Export the current view as PNG via EXPORT command (limited support)
            # A more reliable method: use doc.Export for certain formats
            try:
                acad.ActiveDocument.SendCommand(
                    f'-EXPORT\nP\n"{tmp_png}"\n'
                )
            except Exception:
                pass

            # Cleanup
            try:
                ref.Delete()
            except Exception:
                pass
            doc.Close(False)

            if os.path.isfile(tmp_png):
                return tmp_png

        except Exception as exc:
            log.debug("AutoCAD COM preview failed: %s", exc)

        return None

    # ------------------------------------------------------------------
    # Method 2 — ezdxf drawing addon
    # ------------------------------------------------------------------

    def _try_ezdxf_preview(self) -> Optional[str]:
        try:
            import ezdxf  # type: ignore[import]
            from ezdxf.addons.drawing import Frontend, RenderContext  # type: ignore[import]
            from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # type: ignore[import]
            import matplotlib  # type: ignore[import]
            matplotlib.use("Agg")  # non-interactive backend
            import matplotlib.pyplot as plt  # type: ignore[import]
        except ImportError as exc:
            log.debug("ezdxf drawing addon or matplotlib missing: %s", exc)
            return self._try_ezdxf_preview_simple()

        dwg_path = self._rec.file_path
        block_name = self._rec.block_name

        # Try to read the file (DXF or DWG via ezdxf)
        try:
            doc = ezdxf.readfile(dwg_path)
        except Exception as exc:
            log.debug("ezdxf cannot read %s: %s", dwg_path, exc)
            return None

        if block_name not in doc.blocks:
            log.debug("Block %r not in ezdxf doc.blocks", block_name)
            return None

        try:
            fig = plt.figure(figsize=(6, 6), facecolor="#1e1e1e")
            ax = fig.add_axes([0, 0, 1, 1])
            ax.set_facecolor("#1e1e1e")
            ax.set_aspect("equal")
            ax.axis("off")

            ctx = RenderContext(doc)
            backend = MatplotlibBackend(ax)
            frontend = Frontend(ctx, backend)

            # Render just the block layout
            blk_layout = doc.blocks[block_name]
            frontend.draw_layout(blk_layout, finalize=True)

            tmp_png = tempfile.mktemp(suffix=".png", prefix="blkprev_")
            fig.savefig(tmp_png, dpi=150, bbox_inches="tight",
                        facecolor="#1e1e1e", edgecolor="none")
            plt.close(fig)

            if os.path.isfile(tmp_png):
                return tmp_png

        except Exception as exc:
            log.debug("ezdxf drawing addon render failed: %s", exc)

        return None

    def _try_ezdxf_preview_simple(self) -> Optional[str]:
        """Minimal matplotlib render of block entities without the full Frontend."""
        try:
            import ezdxf  # type: ignore[import]
            import matplotlib  # type: ignore[import]
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt  # type: ignore[import]
            import matplotlib.patches as mpatches  # type: ignore[import]
        except ImportError:
            return None

        dwg_path = self._rec.file_path
        block_name = self._rec.block_name

        try:
            doc = ezdxf.readfile(dwg_path)
        except Exception:
            return None

        if block_name not in doc.blocks:
            return None

        blk = doc.blocks[block_name]

        fig, ax = plt.subplots(figsize=(6, 6), facecolor="#1e1e1e")
        ax.set_facecolor("#252526")
        ax.set_aspect("equal")
        ax.axis("off")

        drawn = 0
        for entity in blk:
            try:
                etype = entity.dxftype()
                color = "#4ec9b0"

                if etype == "LINE":
                    s = entity.dxf.start
                    e = entity.dxf.end
                    ax.plot([s.x, e.x], [s.y, e.y], color=color, linewidth=0.8)
                    drawn += 1

                elif etype == "CIRCLE":
                    c = entity.dxf.center
                    r = entity.dxf.radius
                    circle = plt.Circle((c.x, c.y), r, fill=False, color=color, linewidth=0.8)
                    ax.add_patch(circle)
                    drawn += 1

                elif etype == "ARC":
                    import math
                    c = entity.dxf.center
                    r = entity.dxf.radius
                    sa = math.radians(entity.dxf.start_angle)
                    ea = math.radians(entity.dxf.end_angle)
                    theta = []
                    angle = sa
                    while angle <= ea:
                        theta.append(angle)
                        angle += 0.05
                    theta.append(ea)
                    xs = [c.x + r * math.cos(a) for a in theta]
                    ys = [c.y + r * math.sin(a) for a in theta]
                    ax.plot(xs, ys, color=color, linewidth=0.8)
                    drawn += 1

                elif etype in ("LWPOLYLINE", "POLYLINE"):
                    try:
                        pts = list(entity.get_points())
                        if pts:
                            xs = [p[0] for p in pts]
                            ys = [p[1] for p in pts]
                            if entity.is_closed:
                                xs.append(xs[0])
                                ys.append(ys[0])
                            ax.plot(xs, ys, color=color, linewidth=0.8)
                            drawn += 1
                    except Exception:
                        pass

            except Exception:
                pass

        if drawn == 0:
            ax.text(
                0.5, 0.5,
                f"Block: {block_name}\n(no renderable geometry)",
                ha="center", va="center",
                color="#9d9d9d", fontsize=9,
                transform=ax.transAxes,
            )
        else:
            ax.autoscale()

        tmp_png = tempfile.mktemp(suffix=".png", prefix="blkprev_")
        fig.savefig(tmp_png, dpi=150, bbox_inches="tight",
                    facecolor="#1e1e1e", edgecolor="none")
        plt.close(fig)

        return tmp_png if os.path.isfile(tmp_png) else None


# ---------------------------------------------------------------------------
# Preview Dialog
# ---------------------------------------------------------------------------

class PreviewDialog(QDialog):
    def __init__(
        self,
        rec: BlockRecord,
        config: Dict[str, Any],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._rec = rec
        self._config = config
        self._tmp_png: Optional[str] = None

        self.setWindowTitle(f"Preview — {rec.block_name}")
        self.setMinimumSize(500, 500)
        self.resize(600, 600)
        self.setModal(True)

        self._setup_ui()
        self._start_render()

    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Title
        title = QLabel(f"<b>{self._rec.block_name}</b>")
        title.setObjectName("sectionHeader")
        layout.addWidget(title)

        sub = QLabel(
            f"{self._rec.filename}  ·  {os.path.basename(self._rec.folder)}"
        )
        sub.setObjectName("subLabel")
        layout.addWidget(sub)

        # Image label
        self._img_label = QLabel("Generating preview…")
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setMinimumSize(460, 400)
        self._img_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(self._img_label, stretch=1)

        # Progress bar (shown while generating)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setFixedHeight(6)
        layout.addWidget(self._progress)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setObjectName("accentButton")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _start_render(self) -> None:
        self._worker = PreviewWorker(self._rec, self._config, self)
        self._worker.preview_ready.connect(self._on_preview_ready)
        self._worker.failed.connect(self._on_preview_failed)
        self._worker.finished.connect(lambda: self._progress.setVisible(False))
        self._worker.start()

    def _on_preview_ready(self, png_path: str) -> None:
        self._tmp_png = png_path
        pixmap = QPixmap(png_path)
        if pixmap.isNull():
            self._img_label.setText("Failed to load rendered image.")
            return
        # Scale to fit the label while preserving aspect ratio
        scaled = pixmap.scaled(
            self._img_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._img_label.setPixmap(scaled)

    def _on_preview_failed(self, msg: str) -> None:
        self._img_label.setText(
            f"<center><span style='color:#9d9d9d'>{msg}</span><br><br>"
            f"<span style='color:#5a5a5a; font-size:8pt'>Ensure ezdxf and matplotlib are installed,<br>"
            f"or AutoCAD is installed for COM preview.</span></center>"
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Re-scale the pixmap if already loaded
        if self._img_label.pixmap() and not self._img_label.pixmap().isNull():
            if self._tmp_png:
                pixmap = QPixmap(self._tmp_png)
                scaled = pixmap.scaled(
                    self._img_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._img_label.setPixmap(scaled)

    def closeEvent(self, event) -> None:
        if hasattr(self, "_worker") and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)
        # Clean up temp file
        if self._tmp_png and os.path.isfile(self._tmp_png):
            try:
                os.remove(self._tmp_png)
            except OSError:
                pass
        event.accept()
