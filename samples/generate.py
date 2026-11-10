"""Программная генерация учебных примеров чертежей."""

from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
from ezdxf import units

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gost_checker.checks.dimensions import _apply_spds_dimstyle
from gost_checker.checks.sheet_frame import add_sheet_frame
from gost_checker.constants import STANDARD_LAYERS
from gost_checker.titleblock import ensure_gost_style, insert_form3, title_block_insert_point


def _new_doc():
    doc = ezdxf.new("R2013", setup=True)
    doc.units = units.MM
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    return doc


def make_bad_drawing(path: Path) -> Path:
    doc = _new_doc()
    msp = doc.modelspace()
    doc.layers.add("Layer1", linetype="Continuous", lineweight=211)
    doc.layers.add("MyFancyLayer", linetype="Continuous", lineweight=13)
    doc.layers.add("Sketch", linetype="Continuous")
    if "Arial" not in {s.dxf.name for s in doc.styles}:
        doc.styles.add("Arial", font="arial.ttf")

    msp.add_lwpolyline(
        [(40, 60), (260, 60), (260, 200), (40, 200)],
        close=True,
        dxfattribs={"layer": "Layer1", "lineweight": 211},
    )
    msp.add_line((40, 130), (260, 130), dxfattribs={"layer": "MyFancyLayer"})
    msp.add_circle((150, 130), 22, dxfattribs={"layer": "Sketch"})
    msp.add_text(
        "План подземной части",
        height=4.2,
        dxfattribs={"style": "Arial", "insert": (50, 210), "layer": "Sketch"},
    )
    msp.add_text(
        "Сечение 1-1",
        height=6.1,
        dxfattribs={"style": "Arial", "insert": (50, 40), "layer": "Sketch"},
    )
    msp.add_text(
        "Примечание",
        height=11,
        dxfattribs={"style": "Standard", "insert": (200, 40), "layer": "Sketch"},
    )
    dim = msp.add_linear_dim(base=(150, 30), p1=(40, 60), p2=(260, 60), dimstyle="Standard")
    dim.render()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(path))
    return path


def make_good_drawing(path: Path) -> Path:
    doc = _new_doc()
    for name, spec in STANDARD_LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, linetype=spec["linetype"], lineweight=spec["lineweight"], color=spec["color"])
    ensure_gost_style(doc)
    outer, inner = add_sheet_frame(doc, to_mm=1.0, fmt="A3", orientation="landscape")
    insert = title_block_insert_point(inner.xmin, inner.ymin, inner.xmax, 1.0)
    insert_form3(
        doc,
        insert,
        {
            "обозначение": "КЖ-01",
            "объект": "СПбГАСУ",
            "здание": "Учебный корпус",
            "наименование": "План подземной части",
            "стадия": "Р",
            "лист": "1",
            "листов": "1",
            "разраб": "Иванов",
            "организация": "СПбГАСУ",
        },
        to_mm=1.0,
    )
    _apply_spds_dimstyle(doc)
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(80, 90), (280, 90), (280, 220), (80, 220)],
        close=True,
        dxfattribs={"layer": "SPDS_OSN"},
    )
    msp.add_line((80, 155), (280, 155), dxfattribs={"layer": "SPDS_OS"})
    msp.add_text(
        "План на отм. -3.000",
        height=5.0,
        dxfattribs={"style": "GOST_A", "insert": (90, 230), "layer": "SPDS_TEXT"},
    )
    msp.add_text(
        "М 1:100",
        height=3.5,
        dxfattribs={"style": "GOST_A", "insert": (90, 80), "layer": "SPDS_TEXT"},
    )
    dim = msp.add_linear_dim(base=(180, 70), p1=(80, 90), p2=(280, 90), dimstyle="SPDS")
    dim.render()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(path))
    return path


def make_pdf_from_drawing(dxf_path: Path, pdf_path: Path) -> Path:
    """Собрать PDF формата A3 из растра чертежа — для проверки растрового маршрута."""
    import ezdxf
    import fitz

    from gost_checker.render import render_dxf_to_png

    dxf_path = Path(dxf_path)
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_png = pdf_path.with_suffix(".preview.png")
    doc = ezdxf.readfile(str(dxf_path))
    render_dxf_to_png(doc, tmp_png, size=(16.54, 11.69), dpi=100)

    # A3 альбом, мм -> пункты
    mm = 72.0 / 25.4
    page_w, page_h = 420 * mm, 297 * mm
    pdf = fitz.open()
    page = pdf.new_page(width=page_w, height=page_h)
    # тонкая «рамка» для эвристик, даже если растр чертежа её не содержит
    # (сам плохой DXF рамки не имеет — так и задумано)
    pix = fitz.Pixmap(str(tmp_png)) if tmp_png.exists() else None
    if pix:
        rect = fitz.Rect(30, 20, page_w - 20, page_h - 20)
        page.insert_image(rect, pixmap=pix)
    pdf.save(str(pdf_path))
    pdf.close()
    return pdf_path


def make_synthetic_a3_pdf(path: Path) -> Path:
    """PDF формата A3 с рамкой, штампом и русскими подписями — удобен для OCR."""
    import fitz

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mm = 72.0 / 25.4
    w, h = 420 * mm, 297 * mm
    pdf = fitz.open()
    page = pdf.new_page(width=w, height=h)
    fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    page.insert_font(fontname="deja", fontfile=fontfile)
    # внешняя рамка по краю
    page.draw_rect(fitz.Rect(0.5, 0.5, w - 0.5, h - 0.5), color=(0, 0, 0), width=0.4)
    # внутренняя: 20 мм слева, 5 мм остальные
    inner = fitz.Rect(20 * mm, 5 * mm, w - 5 * mm, h - 5 * mm)
    page.draw_rect(inner, color=(0, 0, 0), width=1.2)
    stamp = fitz.Rect(inner.x1 - 185 * mm, inner.y1 - 55 * mm, inner.x1, inner.y1)
    page.draw_rect(stamp, color=(0, 0, 0), width=1.0)
    # сетка штампа упрощённо
    page.draw_line(fitz.Point(stamp.x0 + 65 * mm, stamp.y0), fitz.Point(stamp.x0 + 65 * mm, stamp.y1), width=0.6)

    def put(x_mm, y_mm, text, size=10):
        page.insert_text(
            fitz.Point(stamp.x0 + x_mm * mm, stamp.y0 + y_mm * mm),
            text,
            fontsize=size,
            fontname="deja",
        )

    put(3, 12, "Разраб. Петров", 11)
    put(3, 20, "Пров.", 11)
    put(3, 28, "Н.контр.", 11)
    put(70, 14, "КЖ-01", 14)
    put(70, 28, "План подземной части", 12)
    put(138, 16, "Стадия Р", 11)
    put(138, 26, "Лист 1   Листов 1", 11)
    put(70, 48, "СПбГАСУ", 12)
    page.insert_text(
        fitz.Point(inner.x0 + 20 * mm, inner.y0 + 40 * mm),
        "Учебный чертёж",
        fontsize=16,
        fontname="deja",
    )
    pdf.save(str(path))
    pdf.close()
    return path



def make_scan_demo(src_pdf: Path, dest: Path, angle_deg: float = 3.0) -> Path:
    """Растрировать учебный PDF, слегка повернуть и зашумить — как неровный скан."""
    import cv2
    import fitz
    import numpy as np

    src_pdf = Path(src_pdf)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pdf = fitz.open(str(src_pdf))
    page = pdf[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    pdf.close()
    if arr.shape[2] == 4:
        arr = arr[:, :, :3]
    bgr = arr[:, :, ::-1].copy()
    h, w = bgr.shape[:2]
    pad = 40
    canvas = np.full((h + 2 * pad, w + 2 * pad, 3), 255, dtype=np.uint8)
    canvas[pad : pad + h, pad : pad + w] = bgr
    center = (canvas.shape[1] / 2.0, canvas.shape[0] / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    rotated = cv2.warpAffine(canvas, matrix, (canvas.shape[1], canvas.shape[0]), borderValue=(255, 255, 255))
    rng = np.random.default_rng(7)
    noisy = np.clip(rotated.astype(np.float32) + rng.normal(0, 5, rotated.shape), 0, 255).astype(np.uint8)
    speck = rng.random(rotated.shape[:2]) < 0.0015
    noisy[speck] = rng.integers(0, 50, size=(int(speck.sum()), 3), dtype=np.uint8)
    cv2.imwrite(str(dest), noisy, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    return dest


def main() -> None:
    out = ROOT / "samples"
    bad = make_bad_drawing(out / "bad.dxf")
    good = make_good_drawing(out / "good.dxf")
    pdf_from_bad = make_pdf_from_drawing(bad, out / "bad_from_dxf.pdf")
    synth = make_synthetic_a3_pdf(out / "a3_title_sample.pdf")
    scan = make_scan_demo(synth, out / "scan_demo.png", angle_deg=3.0)
    print(bad)
    print(good)
    print(pdf_from_bad)
    print(synth)
    print(scan)


if __name__ == "__main__":
    main()
