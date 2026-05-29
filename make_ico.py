"""
Convert resources/icon.svg → resources/icon.ico using PySide6.
Run before PyInstaller:  python make_ico.py
"""
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).parent


def svg_to_png_bytes(svg_path: Path, size: int) -> bytes:
    """Render an SVG to a PNG bytes object at the given pixel size."""
    from PySide6.QtCore import Qt, QBuffer, QIODevice
    from PySide6.QtGui import QImage, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    # QApplication needed for Qt to initialise
    app = QApplication.instance() or QApplication(sys.argv)

    renderer = QSvgRenderer(str(svg_path))
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(0)  # transparent
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(buf.data())


def build_ico(png_list: list[tuple[int, bytes]], dest: Path) -> None:
    """
    Write a .ico file from a list of (size, png_bytes) tuples.
    Uses PNG compression inside the ICO container (supported by Windows Vista+).
    """
    n = len(png_list)
    # ICO header: 6 bytes
    header = struct.pack("<HHH", 0, 1, n)

    # Each directory entry: 16 bytes
    # Image data starts after header + n * 16 bytes
    offset = 6 + n * 16
    dir_entries = b""
    image_data = b""

    for size, png_bytes in png_list:
        sz = len(png_bytes)
        w = size if size < 256 else 0  # 0 means 256 in ICO spec
        h = w
        dir_entries += struct.pack(
            "<BBBBHHII",
            w, h,      # width, height (0 = 256)
            0,         # color count (0 = no palette)
            0,         # reserved
            1,         # planes
            32,        # bit count
            sz,        # size of image data
            offset,    # offset from start of file
        )
        image_data += png_bytes
        offset += sz

    dest.write_bytes(header + dir_entries + image_data)
    print(f"[OK] Written {dest}  ({len(png_list)} sizes)")


def main():
    svg = ROOT / "resources" / "icon.svg"
    ico = ROOT / "resources" / "icon.ico"

    if not svg.is_file():
        print(f"[ERROR] SVG not found: {svg}")
        sys.exit(1)

    sizes = [16, 24, 32, 48, 64, 128, 256]
    print(f"Rendering {svg.name} at sizes: {sizes}")

    png_list = []
    for s in sizes:
        png = svg_to_png_bytes(svg, s)
        print(f"  {s}x{s}  →  {len(png)} bytes PNG")
        png_list.append((s, png))

    build_ico(png_list, ico)


if __name__ == "__main__":
    main()
