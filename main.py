"""
AutoCAD Block Search Tool — Entry Point
"""
import sys
import os

# Ensure the project root is on the path when bundled with PyInstaller
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS  # type: ignore[attr-defined]
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)

# WebEngine must be imported (and on some OSes initialized) before QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont

from utils.config import load_config
from utils.logger import setup_logger
from gui.main_window import MainWindow


# ---------------------------------------------------------------------------
# ODA pre-flight: download in background thread with a progress dialog
# ---------------------------------------------------------------------------

# Candidate URLs in order — tried one at a time until one succeeds
_ODA_URLS = [
    "https://download.opendesign.com/guestfiles/ODAFileConverter/ODAFileConverter_QT5_win64_vc17dll_23.11.zip",
    "https://download.opendesign.com/guestfiles/ODAFileConverter/ODAFileConverter_QT5_win64_vc17dll_24.6.zip",
    "https://download.opendesign.com/guestfiles/ODAFileConverter/ODAFileConverter_QT5_win64_vc17dll_23.6.zip",
]
_ODA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept": "application/zip,application/octet-stream,*/*",
    "Referer": "https://www.opendesign.com/guestfiles/oda_file_converter",
}


class _OdaDownloadThread(QThread):
    """Downloads and unpacks ODA File Converter in a worker thread."""
    progress = Signal(str)   # status text
    finished = Signal(bool, str)  # success flag, error message

    def run(self) -> None:
        import urllib.request
        import urllib.error
        import zipfile
        from setup_oda import update_config, ODA_EXE, VENDOR_DIR

        zip_path = VENDOR_DIR.parent / "oda_temp.zip"
        VENDOR_DIR.parent.mkdir(parents=True, exist_ok=True)
        VENDOR_DIR.mkdir(parents=True, exist_ok=True)

        downloaded = False
        last_error = "All download URLs failed."

        for url in _ODA_URLS:
            try:
                self.progress.emit(f"Trying {url.split('/')[-1]}…")
                req = urllib.request.Request(url, headers=_ODA_HEADERS)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    total = int(resp.headers.get("Content-Length", 0))
                    received = 0
                    with open(zip_path, "wb") as fh:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            fh.write(chunk)
                            received += len(chunk)
                            if total > 0:
                                pct = min(100, int(received * 100 / total))
                                self.progress.emit(f"Downloading… {pct}%  ({received // 1024} / {total // 1024} KB)")
                            else:
                                self.progress.emit(f"Downloading… {received // 1024} KB received")
                downloaded = True
                break
            except Exception as exc:
                last_error = f"{url.split('/')[-1]}: {exc}"
                if zip_path.exists():
                    zip_path.unlink(missing_ok=True)
                continue

        if not downloaded:
            self.finished.emit(False, last_error)
            return

        # Unpack
        try:
            self.progress.emit("Unpacking archive…")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(VENDOR_DIR)
            zip_path.unlink(missing_ok=True)
        except Exception as exc:
            self.finished.emit(False, f"Unpack failed: {exc}")
            return

        # Normalise exe name (may be nested in a subfolder)
        found = False
        for candidate in VENDOR_DIR.rglob("ODAFileConverter*.exe"):
            if not candidate.name.lower().startswith("unins"):
                target = VENDOR_DIR / "ODAFileConverter.exe"
                if candidate != target:
                    candidate.rename(target)
                found = True
                break

        if not found or not ODA_EXE.is_file():
            self.finished.emit(False, "ODAFileConverter.exe not found in downloaded archive.")
            return

        update_config(ODA_EXE)
        self.progress.emit("ODA Converter installed successfully.")
        self.finished.emit(True, "")


def _find_oda_exe(config: dict) -> str:
    """
    Search for ODAFileConverter.exe in priority order:
      1. The path already stored in config
      2. Any system-wide installation under C:\\Program Files\\ODA\\
      3. The project vendor directory
    Returns the first valid path found, or "" if nothing exists.
    """
    import glob as _glob
    from pathlib import Path as _Path

    candidates: list[str] = []

    # 1. Config-stored path
    cfg_path = config.get("oda_converter_path", "")
    if cfg_path:
        candidates.append(cfg_path)

    # 2. System install locations (any version subfolder)
    for base in (
        r"C:\Program Files\ODA",
        r"C:\Program Files (x86)\ODA",
        r"C:\ODA",
    ):
        for match in _glob.glob(
            base + r"\**\ODAFileConverter.exe", recursive=True
        ):
            candidates.append(match)

    # 3. Project vendor directory
    from setup_oda import ODA_EXE
    candidates.append(str(ODA_EXE))

    for path in candidates:
        if path and _Path(path).is_file():
            return path
    return ""


def _ensure_oda(app: "QApplication", config: dict) -> None:
    """
    1. Auto-detect ODA from config / system paths — silently fix config if found.
    2. Only if truly absent: ask the user whether to download now.
    """
    from utils.config import save_config

    found = _find_oda_exe(config)
    if found:
        # If the stored path was wrong / empty, update it silently
        if config.get("oda_converter_path") != found:
            config["oda_converter_path"] = found
            save_config(config)
        return  # ODA is available — nothing more to do

    reply = QMessageBox.question(
        None,
        "ODA File Converter — Required for DWG support",
        "ODA File Converter is not installed.\n\n"
        "Without it, .DWG files cannot be indexed "
        "(only .DXF files will work).\n\n"
        "Download and install it now? (~25 MB)",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )

    if reply != QMessageBox.StandardButton.Yes:
        return  # user chose to skip — app continues without ODA

    # Show a modal progress dialog while the download runs
    dlg = QProgressDialog("Preparing download…", "Skip", 0, 0)
    dlg.setWindowTitle("Installing ODA File Converter")
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setMinimumWidth(420)
    dlg.setCancelButton(None)   # disallow cancel mid-download
    dlg.show()
    app.processEvents()

    thread = _OdaDownloadThread()
    thread.progress.connect(lambda msg: (
        dlg.setLabelText(msg), app.processEvents()
    ))

    _result: list[tuple[bool, str]] = []

    def _on_finished(ok: bool, err: str) -> None:
        _result.append((ok, err))
        dlg.close()

    thread.finished.connect(_on_finished)
    thread.start()

    # Spin the event loop until the thread finishes
    while thread.isRunning():
        app.processEvents()
        thread.wait(50)

    dlg.close()

    if _result and _result[0][0]:
        # Re-run auto-detect so config gets updated with the newly installed path
        found = _find_oda_exe(config)
        if found and config.get("oda_converter_path") != found:
            from utils.config import save_config
            config["oda_converter_path"] = found
            save_config(config)
        QMessageBox.information(
            None,
            "ODA Converter Ready",
            "ODA File Converter was installed successfully.\n"
            "DWG files will be indexed on the next scan.",
        )
    else:
        err_detail = _result[0][1] if _result else "Unknown error"
        QMessageBox.warning(
            None,
            "Download Failed",
            "Could not download ODA File Converter automatically.\n\n"
            f"Reason: {err_detail}\n\n"
            "You can install it manually later via the Settings panel inside the app.",
        )


def main() -> None:
    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Block Search")
    app.setApplicationDisplayName("Block Search — AutoCAD Library")
    app.setOrganizationName("BlockSearch")

    # Load config first so app-level text scale can be applied at startup.
    config = load_config()

    # Base font (scaled from persisted UI text scale)
    text_scale = float(config.get("ui_text_scale", 1.0) or 1.0)
    text_scale = max(0.8, min(2.0, text_scale))
    base_point_size = max(7, min(18, int(round(9 * text_scale))))
    font = QFont("Segoe UI", base_point_size)
    app.setFont(font)

    # Logger
    setup_logger(config)

    # Ensure ODA File Converter is present before opening the main window
    _ensure_oda(app, config)

    # Launch main window
    window = MainWindow(config)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()