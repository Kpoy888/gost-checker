from __future__ import annotations

import math
from pathlib import Path

import ezdxf
import numpy as np
from gost_checker.cli import main as cli_main
from gost_checker.engine import process_file
from gost_checker.sheet_detect import is_sheet_edge
from gost_checker.web import create_app


def _frame(msp, to_mm: float, w_mm: float = 420.0, h_mm: float = 297.0) -> None:
    def u(mm: float) -> float:
        return mm / to_mm

    msp.add_lwpolyline([(0, 0), (u(w_mm), 0), (u(w_mm), u(h_mm)), (0, u(h_mm))], close=True)
    msp.add_lwpolyline(
        [(u(20), u(5)), (u(w_mm - 5), u(5)), (u(w_mm - 5), u(h_mm - 5)), (u(20), u(h_mm - 5))],
        close=True,
    )


def _new(insunits: int = 4):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = insunits
    return doc


def make_rotated_frame(path: Path) -> Path:
    doc = _new(4)
    msp = doc.modelspace()
    ang = math.radians(8)
    cx, cy = math.cos(ang), math.sin(ang)

    def rot(x, y):
        return (x * cx - y * cy + 40, x * cy + y * cx + 30)

    def box(pts):
        msp.add_lwpolyline([rot(*p) for p in pts], close=True)

    box([(0, 0), (420, 0), (420, 297), (0, 297)])
    box([(20, 5), (415, 5), (415, 292), (20, 292)])
    doc.saveas(str(path))
    return path


def make_paperspace_frame(path: Path) -> Path:
    doc = _new(4)
    doc.modelspace().add_line((0, 0), (30, 10))
    try:
        psp = doc.paperspace()
    except Exception:
        psp = doc.layout("Layout1")
    _frame(psp, 1.0)
    doc.saveas(str(path))
    return path


def make_units_sheet(path: Path, insunits: int, to_mm: float, w=420.0, h=297.0) -> Path:
    doc = _new(insunits)
    _frame(doc.modelspace(), to_mm, w, h)
    doc.saveas(str(path))
    return path


def make_nested_block_frame(path: Path) -> Path:
    doc = _new(4)
    inner = doc.blocks.new("INNER_FR")
    inner.add_lwpolyline([(20, 5), (415, 5), (415, 292), (20, 292)], close=True)
    sheet = doc.blocks.new("SHEET_FR")
    sheet.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True)
    sheet.add_blockref("INNER_FR", (0, 0))
    doc.modelspace().add_blockref("SHEET_FR", (12, 8))
    doc.saveas(str(path))
    return path


def make_special_entities(path: Path) -> Path:
    doc = _new(4)
    msp = doc.modelspace()
    _frame(msp, 1.0)
    msp.add_mtext(r"{\H11.0;Крупный} {\fArial|b0;Arial} текст", dxfattribs={"char_height": 3.5, "insert": (40, 80)})
    hatch = msp.add_hatch(color=7)
    hatch.paths.add_polyline_path([(50, 80), (90, 80), (90, 120), (50, 120)], is_closed=True)
    msp.add_spline([(60, 140), (100, 180), (150, 130), (210, 190)])
    msp.add_ellipse(center=(160, 160), major_axis=(35, 0), ratio=0.45)
    doc.saveas(str(path))
    return path


def make_huge(path: Path) -> Path:
    doc = _new(4)
    msp = doc.modelspace()
    _frame(msp, 1.0)
    for i in range(1800):
        y = 40 + i * 0.12
        msp.add_line((40, y), (200, y))
    doc.saveas(str(path))
    return path


def make_raster_only_pdf(src_pdf: Path, dest: Path) -> Path:
    import fitz

    src = fitz.open(str(src_pdf))
    pix = src[0].get_pixmap(matrix=fitz.Matrix(1.1, 1.1), alpha=False)
    src.close()
    mm = 72.0 / 25.4
    pdf = fitz.open()
    page = pdf.new_page(width=420 * mm, height=297 * mm)
    page.insert_image(page.rect, pixmap=pix)
    pdf.save(str(dest))
    pdf.close()
    return dest


def make_multipage_pdf(src_pdf: Path, dest: Path) -> Path:
    import fitz

    src = fitz.open(str(src_pdf))
    pdf = fitz.open()
    pdf.insert_pdf(src)
    page = pdf.new_page(width=src[0].rect.width, height=src[0].rect.height)
    page.insert_text((72, 72), "second page", fontsize=14)
    src.close()
    pdf.save(str(dest))
    pdf.close()
    return dest


def make_rotated_pdf(src_pdf: Path, dest: Path) -> Path:
    import fitz

    src = fitz.open(str(src_pdf))
    pdf = fitz.open()
    pdf.insert_pdf(src)
    src.close()
    pdf[0].set_rotation(90)
    pdf.save(str(dest))
    pdf.close()
    return dest


def make_png_with_dpi(dest: Path) -> Path:
    import cv2
    from PIL import Image

    ppm = 150 / 25.4
    w, h = int(420 * ppm), int(297 * ppm)
    image = np.full((h, w, 3), 255, dtype=np.uint8)
    def rect(x0, y0, x1, y1, t=3):
        cv2.rectangle(image, (int(x0 * ppm), int(y0 * ppm)), (int(x1 * ppm), int(y1 * ppm)), (0, 0, 0), t)
    rect(0.4, 0.4, 419.6, 296.6, 2)
    rect(20, 5, 415, 292, 4)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    Image.fromarray(rgb).save(dest, dpi=(150, 150))
    return dest


def make_png_no_dpi(dest: Path) -> Path:
    import cv2

    image = np.full((600, 848, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (8, 8), (840, 592), (0, 0, 0), 2)
    cv2.rectangle(image, (48, 18), (830, 582), (0, 0, 0), 3)
    cv2.imwrite(str(dest), image)
    return dest


def make_lowres(dest: Path) -> Path:
    import cv2

    image = np.full((220, 310, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (3, 3), (307, 217), (0, 0, 0), 1)
    cv2.rectangle(image, (18, 6), (302, 212), (0, 0, 0), 1)
    cv2.imwrite(str(dest), image)
    return dest


def make_noisy_jpg(src_png: Path, dest: Path) -> Path:
    import cv2

    image = cv2.imread(str(src_png), cv2.IMREAD_COLOR)
    rng = np.random.default_rng(3)
    noisy = np.clip(image.astype(np.float32) + rng.normal(0, 12, image.shape), 0, 255).astype(np.uint8)
    cv2.imwrite(str(dest), noisy, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return dest


def _collect_cases(tmp: Path, samples_dir: Path) -> list[tuple[str, Path]]:
    from samples.generate import make_scan_demo, make_synthetic_a3_pdf

    tmp.mkdir(parents=True, exist_ok=True)
    cases = []
    cases.append(("rotated_frame", make_rotated_frame(tmp / "rotated_frame.dxf")))
    cases.append(("paperspace", make_paperspace_frame(tmp / "paperspace.dxf")))
    cases.append(("inches", make_units_sheet(tmp / "inches.dxf", 1, 25.4)))
    cases.append(("cm", make_units_sheet(tmp / "cm.dxf", 5, 10.0)))
    cases.append(("meters", make_units_sheet(tmp / "meters.dxf", 6, 1000.0)))
    cases.append(("a4_portrait", make_units_sheet(tmp / "a4.dxf", 4, 1.0, 210, 297)))
    cases.append(("a1", make_units_sheet(tmp / "a1.dxf", 4, 1.0, 841, 594)))
    cases.append(("a3x3", make_units_sheet(tmp / "a3x3.dxf", 4, 1.0, 891, 420)))
    cases.append(("nested_blocks", make_nested_block_frame(tmp / "nested.dxf")))
    cases.append(("specials", make_special_entities(tmp / "specials.dxf")))
    cases.append(("huge", make_huge(tmp / "huge.dxf")))
    empty = tmp / "empty.dxf"
    _new(4).saveas(str(empty))
    cases.append(("empty", empty))
    zero = tmp / "zero.dxf"
    zero.write_bytes(b"")
    cases.append(("zero", zero))
    corrupt = tmp / "corrupt.dxf"
    corrupt.write_text("это не DXF", encoding="utf-8")
    cases.append(("corrupt", corrupt))
    cyr = tmp / "чертёж_рамка.dxf"
    make_units_sheet(cyr, 4, 1.0)
    cases.append(("cyrillic", cyr))

    a3 = samples_dir / "a3_title_sample.pdf"
    if not a3.exists():
        a3 = make_synthetic_a3_pdf(tmp / "a3_title_sample.pdf")
    cases.append(("pdf_vector", a3))
    cases.append(("pdf_raster", make_raster_only_pdf(a3, tmp / "raster_only.pdf")))
    cases.append(("pdf_multipage", make_multipage_pdf(a3, tmp / "multi.pdf")))
    cases.append(("pdf_rotated", make_rotated_pdf(a3, tmp / "rotated.pdf")))
    cases.append(("scan_skew", make_scan_demo(a3, tmp / "scan_skew.png", angle_deg=3.0)))
    dpi_png = make_png_with_dpi(tmp / "with_dpi.png")
    cases.append(("png_dpi", dpi_png))
    cases.append(("png_nodpi", make_png_no_dpi(tmp / "no_dpi.png")))
    cases.append(("png_lowres", make_lowres(tmp / "lowres.png")))
    cases.append(("jpg_noisy", make_noisy_jpg(dpi_png, tmp / "noisy.jpg")))
    return cases


def _assert_sane(label: str, result) -> None:
    assert result is not None, label
    assert result.report_html and Path(result.report_html).exists(), label
    blob = " ".join(result.notes) + " " + " ".join(
        check.message + " " + " ".join(check.details) for check in result.checks
    )
    margins = (result.extra or {}).get("margins")
    if margins:
        assert not is_sheet_edge(margins), (label, margins)
    assert "Traceback" not in blob
    assert "internal error" not in blob.lower()


def test_stress_cli_and_web(tmp_path, samples_dir):
    from fastapi.testclient import TestClient

    cases = _collect_cases(tmp_path / "files", samples_dir)
    client = TestClient(create_app())
    mime = {
        ".dxf": "application/dxf",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    seen = []
    for label, path in cases:
        out = tmp_path / f"cli-{label}"
        rc = cli_main([str(path), "--out", str(out)])
        assert rc in {0, 2}, (label, rc)
        result = process_file(path, tmp_path / f"eng-{label}", fix=False)
        _assert_sane(label, result)
        if label == "empty":
            assert any("не содержит объектов" in note for note in result.notes)
        if label == "zero":
            assert result.kind == "error"
            assert any("пуст" in check.message.lower() for check in result.checks)
        if label == "corrupt":
            assert result.kind == "error" or any(check.severity == "error" for check in result.checks)
        if label in {"inches", "cm", "meters", "a4_portrait", "a1", "a3x3", "nested_blocks", "paperspace", "rotated_frame"}:
            frame = next(check for check in result.checks if check.check_id == "sheet_frame")
            assert frame.severity in {"ok", "info"}, (label, frame.message, frame.details)
        if label == "scan_skew":
            margins = (result.extra or {}).get("margins")
            if margins:
                assert abs(margins["left"]["value"] - 20) <= 1.0
                assert abs(margins["right"]["value"] - 5) <= 1.0
                assert abs(margins["top"]["value"] - 5) <= 1.0
                assert abs(margins["bottom"]["value"] - 5) <= 1.0
            else:
                frame = next(check for check in result.checks if check.check_id == "sheet_frame")
                assert "не найден" in (frame.message + " " + " ".join(frame.details)).lower()
        with path.open("rb") as handle:
            response = client.post(
                "/api/check",
                files={"file": (path.name, handle, mime.get(path.suffix.lower(), "application/octet-stream"))},
                data={"fix": "false", "sheet_format": "A3"},
            )
        assert response.status_code == 200, (label, response.status_code, response.text[:400])
        payload = response.json()
        assert "summary" in payload, label
        assert payload.get("kind")
        seen.append(label)
    assert len(seen) == len(cases)


def test_scan_demo_margins_not_sheet_edge(samples_dir, tmp_path):
    from samples.generate import make_scan_demo

    scan = make_scan_demo(samples_dir / "a3_title_sample.pdf", tmp_path / "scan_demo.png", angle_deg=3.0)
    result = process_file(scan, tmp_path / "scan-out", fix=False)
    frame = next(check for check in result.checks if check.check_id == "sheet_frame")
    margins = (result.extra or {}).get("margins") or frame.extra.get("margins")
    assert margins, frame.message + " " + " ".join(frame.details)
    assert not is_sheet_edge(margins)
    assert abs(margins["left"]["value"] - 20) <= 1.0
    assert abs(margins["right"]["value"] - 5) <= 1.0
    assert abs(margins["top"]["value"] - 5) <= 1.0
    assert abs(margins["bottom"]["value"] - 5) <= 1.0


def test_sheet_edge_is_not_reported_as_inner():
    from gost_checker.sheet_detect import detect_sheet_frames

    image = np.full((400, 560, 3), 255, dtype=np.uint8)
    import cv2

    cv2.rectangle(image, (1, 1), (558, 398), (0, 0, 0), 2)
    info = detect_sheet_frames(image, 420.0, 297.0)
    assert info.get("inner") is None
    if info.get("margins"):
        assert not is_sheet_edge(info["margins"]) or info.get("inner") is None
