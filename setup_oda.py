"""
ODA File Converter setup helper.

Downloads the ODA File Converter installer for Windows from the official
Open Design Alliance guest files page, or guides the user through manual
installation. Copies the extracted executable into vendor/ODAFileConverter/.

Usage:
    python setup_oda.py
"""
import os
import subprocess
import sys
import zipfile
from pathlib import Path

VENDOR_DIR = Path(__file__).parent / "vendor" / "ODAFileConverter"
ODA_EXE    = VENDOR_DIR / "ODAFileConverter.exe"

# Official ODA guest download — Windows x64 ZIP build
# Check https://www.opendesign.com/guestfiles/oda_file_converter for the latest URL
ODA_DOWNLOAD_URL = (
    "https://download.opendesign.com/guestfiles/ODAFileConverter/"
    "ODAFileConverter_QT5_lnxX64_8.3dll_23.6.zip"
)
ODA_WIN_URL = (
    "https://download.opendesign.com/guestfiles/ODAFileConverter/"
    "ODAFileConverter_QT5_win64_vc17dll_23.11.zip"
)


def check_already_installed() -> bool:
    if ODA_EXE.is_file():
        print(f"[OK] ODA File Converter already present at:\n     {ODA_EXE}")
        return True
    return False


def try_auto_download() -> bool:
    """Attempt to download and unpack the Windows ODA zip."""
    try:
        import urllib.request
    except ImportError:
        return False

    print(f"\nDownloading ODA File Converter from:\n  {ODA_WIN_URL}\n")
    zip_path = VENDOR_DIR.parent / "oda_temp.zip"
    VENDOR_DIR.parent.mkdir(parents=True, exist_ok=True)

    try:
        urllib.request.urlretrieve(ODA_WIN_URL, zip_path, _show_progress)
        print()
    except Exception as exc:
        print(f"\n[WARN] Auto-download failed: {exc}")
        return False

    # Unpack
    print("Unpacking…")
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(VENDOR_DIR)
        zip_path.unlink(missing_ok=True)
    except Exception as exc:
        print(f"[ERROR] Unpack failed: {exc}")
        return False

    # Find the exe
    for candidate in VENDOR_DIR.rglob("ODAFileConverter*.exe"):
        if not candidate.name.startswith("unins"):
            target = VENDOR_DIR / "ODAFileConverter.exe"
            if candidate != target:
                candidate.rename(target)
            print(f"[OK] ODA File Converter installed at:\n     {target}")
            return True

    print("[WARN] ODAFileConverter.exe not found in the downloaded archive.")
    return False


def _show_progress(block_num, block_size, total_size):
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100, int(downloaded * 100 / total_size))
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"\r  [{bar}] {pct:3d}%  {downloaded // 1024} KB / {total_size // 1024} KB", end="")
    else:
        print(f"\r  Downloaded {downloaded // 1024} KB…", end="")


def print_manual_instructions() -> None:
    print("""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  MANUAL INSTALLATION — ODA File Converter
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Open your browser and go to:
     https://www.opendesign.com/guestfiles/oda_file_converter

  2. Download the Windows (64-bit) ZIP version.

  3. Extract the ZIP and copy ODAFileConverter.exe to:
     {dest}

  4. Run this script again to verify:
     python setup_oda.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  NOTE: Without ODA File Converter, the tool will attempt
  to read .dwg files directly via ezdxf (limited support).
  DXF files are always supported without ODA.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".format(dest=ODA_EXE))


def update_config(exe_path: Path) -> None:
    """Update config.json with the confirmed ODA path."""
    config_path = Path(__file__).parent / "config.json"
    if not config_path.exists():
        return
    import json
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["oda_converter_path"] = str(exe_path)
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    print(f"[OK] config.json updated with ODA path.")


def main() -> None:
    print("=" * 60)
    print("  AutoCAD Block Search Tool — ODA File Converter Setup")
    print("=" * 60)

    if check_already_installed():
        update_config(ODA_EXE)
        return

    print("\nODA File Converter not found. Attempting automatic download…")
    if try_auto_download():
        if ODA_EXE.is_file():
            update_config(ODA_EXE)
            return

    # Try to find it anywhere in the vendor dir
    for candidate in VENDOR_DIR.rglob("*.exe"):
        if "odafileconverter" in candidate.name.lower():
            target = VENDOR_DIR / "ODAFileConverter.exe"
            candidate.rename(target)
            update_config(target)
            print(f"[OK] Found and linked: {target}")
            return

    print_manual_instructions()
    sys.exit(1)


if __name__ == "__main__":
    main()
