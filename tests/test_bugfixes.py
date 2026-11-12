"""Регрессии по найденным ошибкам: единицы, старый DXF, PDF, конвертер, веб."""

from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
import pytest

from gost_checker.checks.title_block import _collect_text, layout_or_model
from gost_checker.convert import dxf_to_dwg, dwg_to_dxf, run_converter
from gost_checker.engine import process_file
from gost_checker.titleblock import normalize_title_meta
from gost_checker.units import INSUNITS_TO_MM, doc_to_mm


def _frame(msp, to_mm: float, width_mm: float = 420.0, height_mm: float = 297.0) -> None:
    def u(mm: float) -> float:
        return mm / to_mm

    msp.add_lwpolyline(
        [(0, 0), (u(width_mm), 0), (u(width_mm), u(height_mm)), (0, u(height_mm))],
        close=True,
    )
    msp.add_lwpolyline(
        [
            (u(20), u(5)),
            (u(width_mm - 5), u(5)),
            (u(width_mm - 5), u(height_mm - 5)),
            (u(20), u(height_mm - 5)),
        ],
        close=True,
    )


def test_insunits_kilometers_and_decimeters_match_millimetres():
    assert INSUNITS_TO_MM[7] == 1_000_000.0
    assert INSUNITS_TO_MM[14] == 100.0
    assert INSUNITS_TO_MM[15] == 10_000.0
    km = ezdxf.new("R2010")
    km.header["$INSUNITS"] = 7
    assert doc_to_mm(km) == 1_000_000.0
    dm = ezdxf.new("R2010")
    dm.header["$INSUNITS"] = 14
    assert doc_to_mm(dm) == 100.0


def test_sheet_in_kilometers_decimeters_and_unknown_units(tmp_path):
    cases = [
        (7, 1_000_000.0, "km"),
        (14, 100.0, "dm"),
        (15, 10_000.0, "dam"),
    ]
    for code, factor, name in cases:
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = code
        _frame(doc.modelspace(), factor)
        path = tmp_path / f"{name}.dxf"
        doc.saveas(str(path))
        result = process_file(path, tmp_path / name, fix=False)
        frame = next(check for check in result.checks if check.check_id == "sheet_frame")
        assert frame.severity in {"ok", "info"}, (name, frame.message, frame.details)
        assert "A3" in frame.message or any("A3" in line for line in frame.details)

    unknown = ezdxf.new("R2010")
    unknown.header["$INSUNITS"] = 99
    _frame(unknown.modelspace(), 1.0)
    path = tmp_path / "unknown.dxf"
    unknown.saveas(str(path))
    result = process_file(path, tmp_path / "unknown", fix=False)
    assert any("не входит" in note for note in result.notes), result.notes
    assert doc_to_mm(unknown) == 1.0


def test_legacy_r12_fix_does_not_abort(tmp_path):
    doc = ezdxf.new("R12")
    doc.modelspace().add_line((0, 0), (80, 0))
    doc.modelspace().add_text("Привет", dxfattribs={"insert": (10, 10), "height": 2.5})
    path = tmp_path / "r12.dxf"
    doc.saveas(str(path))
    result = process_file(path, tmp_path / "out", fix=True, assumed_format="A3")
    assert result.kind == "dxf", result.notes
    assert result.output_dxf and Path(result.output_dxf).exists()
    assert any("AC1015" in note or "2000" in note for note in result.notes)
    fixed = ezdxf.readfile(result.output_dxf)
    assert fixed.dxfversion == "AC1015"
    assert any(entity.dxftype() == "LINE" for entity in fixed.modelspace())
    assert not any("LWPOLYLINE requires" in note for note in result.notes)


def test_dimension_styles_follow_drawing_units(tmp_path):
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    _frame(msp, 1000.0)
    style = doc.dimstyles.get("Standard")
    style.dxf.dimtxt = 2.5
    style.dxf.dimasz = 2.5
    style.dxf.dimexe = 1.25
    msp.add_linear_dim(
        base=(0.05, 0.08),
        p1=(0.05, 0.05),
        p2=(0.20, 0.05),
        dimstyle="Standard",
    ).render()
    path = tmp_path / "meters.dxf"
    doc.saveas(str(path))
    plain = process_file(path, tmp_path / "count", fix=False)
    counted = next(check for check in plain.checks if check.check_id == "dimensions")
    assert "чертеже: 1." in " ".join(counted.details)

    fixed = process_file(path, tmp_path / "fix", fix=True)
    saved = ezdxf.readfile(fixed.output_dxf)
    standard = saved.dimstyles.get("Standard")
    assert float(standard.dxf.dimtxt) == pytest.approx(0.0035, rel=1e-6)
    assert float(standard.dxf.dimasz) == pytest.approx(0.0025, rel=1e-6)
    assert float(standard.dxf.dimexe) == pytest.approx(0.002, rel=1e-6)
    assert float(standard.dxf.dimtxt) < 1.0


def test_cp1251_russian_text_is_not_duplicated(tmp_path):
    body = (
        "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1015\n9\n$DWGCODEPAGE\n3\nANSI_1251\n9\n$INSUNITS\n70\n4\n0\nENDSEC\n"
        "0\nSECTION\n2\nENTITIES\n0\nTEXT\n8\n0\n10\n10\n20\n10\n40\n2.5\n1\n"
    ).encode("latin-1") + "Разраб. Иванов".encode("cp1251") + b"\n0\nENDSEC\n0\nEOF\n"
    path = tmp_path / "cp1251.dxf"
    path.write_bytes(body)
    doc = ezdxf.readfile(str(path))
    texts = _collect_text(doc)
    assert texts.count("Разраб. Иванов") == 1
    result = process_file(path, tmp_path / "out", fix=False)
    blob = " ".join(
        check.message + " " + " ".join(check.details) for check in result.checks
    ).lower()
    assert "разраб" in blob


def test_missing_layout_name_does_not_raise():
    doc = ezdxf.new("R2010")
    layout = layout_or_model(doc, "НетТакогоЛиста")
    assert str(layout.name).lower() == "model"


def test_outdir_that_is_a_file_is_reported(tmp_path):
    src = tmp_path / "a.dxf"
    ezdxf.new("R2010").saveas(str(src))
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("x", encoding="utf-8")
    result = process_file(src, blocked, fix=False)
    assert result.kind == "error"
    assert "каталог" in result.checks[0].message
    assert result.report_html and Path(result.report_html).exists()


def test_title_fields_drop_nulls_and_huge_values():
    meta = normalize_title_meta({"обозначение": "А\x00Б" + ("Я" * 800)})
    assert "\x00" not in meta["обозначение"]
    assert meta["обозначение"].startswith("АБ")
    assert len(meta["обозначение"]) == 500


def test_bad_ifc_is_reported(monkeypatch, tmp_path):
    class Boom:
        @staticmethod
        def open(path):
            raise RuntimeError("битый ifc")

    monkeypatch.setitem(sys.modules, "ifcopenshell", Boom)
    path = tmp_path / "model.ifc"
    path.write_text("ISO-10303-21;", encoding="utf-8")
    result = process_file(path, tmp_path / "out", fix=False)
    assert result.kind == "ifc"
    blob = " ".join(result.notes)
    assert "не прочитан" in blob
    assert "Traceback" not in blob


def test_converter_keeps_binary_stderr(tmp_path):
    script = tmp_path / "emit.py"
    script.write_text(
        "import sys\nsys.stdout.buffer.write(b'ok\\n')\nsys.stderr.buffer.write(bytes([0xff, 0xfe]))\n",
        encoding="utf-8",
    )
    proc = run_converter([sys.executable, str(script)], timeout=15)
    assert proc.returncode == 0
    assert "ok" in proc.stdout
    assert proc.stderr  # байты заменены, исключение не выброшено


def test_dwg_conversion_survives_non_utf8_log(monkeypatch, tmp_path):
    import gost_checker.convert as convert

    fake = tmp_path / "dwg2dxf"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "out = sys.argv[sys.argv.index('-o') + 1]\n"
        "open(out, 'w', encoding='ascii').write('0\\nSECTION\\n2\\nHEADER\\n0\\nENDSEC\\n0\\nEOF\\n')\n"
        "sys.stderr.buffer.write(bytes([0xff, 0xfe, 0xfd]))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setattr(convert, "find_libredwg_dwg2dxf", lambda: str(fake))
    monkeypatch.setattr(convert, "find_oda", lambda: None)
    src = tmp_path / "sample.dwg"
    src.write_bytes(b"AC1015fake")
    produced, message = dwg_to_dxf(src, tmp_path / "out")
    assert produced is not None and produced.exists(), message
    assert "Ошибка запуска" not in message


def test_r2000_intermediate_removed_when_dwg_fails(monkeypatch, tmp_path):
    import gost_checker.convert as convert

    fake = tmp_path / "dxf2dwg"
    fake.write_text("#!/usr/bin/env python3\nimport sys\nsys.stderr.buffer.write(bytes([0xff]))\nsys.exit(1)\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(convert, "find_libredwg_dxf2dwg", lambda: str(fake))
    monkeypatch.setattr(convert, "find_oda", lambda: None)
    src = tmp_path / "new.dxf"
    doc = ezdxf.new("R2018")
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.saveas(str(src))
    out = tmp_path / "out"
    produced, message = dxf_to_dwg(src, out)
    assert produced is None
    assert "не создал" in message or "Ошибка" in message
    assert list(out.glob("*.r2000.dxf")) == []


def test_oda_workdir_is_removed(monkeypatch, tmp_path):
    import gost_checker.convert as convert

    fake = tmp_path / "ODAFileConverter"
    fake.write_text("#!/usr/bin/env python3\nraise SystemExit(3)\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(convert, "find_libredwg_dwg2dxf", lambda: None)
    monkeypatch.setattr(convert, "find_oda", lambda: str(fake))
    src = tmp_path / "a.dwg"
    src.write_bytes(b"dwg")
    out = tmp_path / "out"
    produced, _message = dwg_to_dxf(src, out)
    assert produced is None
    assert not (out / "_oda_in").exists()
    assert not (out / "_oda_out").exists()


def test_empty_broken_encrypted_and_multipage_pdf(tmp_path):
    import fitz

    empty = tmp_path / "empty.pdf"
    empty.write_bytes(
        b"%PDF-1.4\n1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
        b"2 0 obj<< /Type /Pages /Count 0 /Kids [] >>endobj\n"
        b"trailer<< /Root 1 0 R >>\n%%EOF\n"
    )
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\n%%EOF\n")
    for path, needle in ((empty, "нет страниц"), (broken, "поврежд")):
        result = process_file(path, tmp_path / f"out-{path.stem}", fix=False)
        assert result.kind == "error"
        message = result.checks[0].message
        assert needle in message
        assert "Failed to open" not in message
        assert "page 0" not in message
        assert Path(result.report_html).exists()

    secret = tmp_path / "secret.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "secret")
    pdf.save(str(secret), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="secret")
    pdf.close()
    locked = process_file(secret, tmp_path / "locked", fix=False)
    assert locked.kind == "error"
    assert "парол" in locked.checks[0].message.lower()

    multi = tmp_path / "multi.pdf"
    pdf = fitz.open()
    pdf.new_page(width=200, height=200)
    drawing = pdf.new_page(width=420 * 72 / 25.4, height=297 * 72 / 25.4)
    drawing.insert_text((72, 180), "Разраб. Петров стадия Р", fontsize=16)
    drawing.draw_rect(fitz.Rect(30, 30, 500, 300), color=(0, 0, 0), width=1.2)
    pdf.save(str(multi))
    pdf.close()
    result = process_file(multi, tmp_path / "multi", fix=False)
    blob = " ".join(result.notes) + " " + " ".join(
        " ".join(check.details) for check in result.checks
    )
    assert "страница 2" in blob
    assert "Разраб" in blob or "Петров" in blob


def test_cyrillic_raster_does_not_use_imread(monkeypatch, tmp_path):
    import cv2
    import numpy as np

    def boom(*_args, **_kwargs):
        raise AssertionError("cv2.imread не должен читать путь")

    monkeypatch.setattr(cv2, "imread", boom)
    image = np.full((90, 140, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (4, 4), (130, 80), (0, 0, 0), 2)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    path = tmp_path / "чертёж скана.png"
    path.write_bytes(encoded.tobytes())
    result = process_file(path, tmp_path / "out", fix=False)
    assert result.kind == "raster"
    assert result.report_html and Path(result.report_html).exists()


def test_huge_scan_is_downscaled(monkeypatch, tmp_path):
    import cv2
    import numpy as np

    monkeypatch.setenv("GOST_RASTER_MAX_SIDE", "600")
    image = np.full((40, 2000, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (2, 2), (1990, 36), (0, 0, 0), 1)
    path = tmp_path / "wide.png"
    cv2.imwrite(str(path), image)
    result = process_file(path, tmp_path / "out", fix=False)
    assert any("уменьшен" in note.lower() for note in result.notes)
    pixels = (result.extra or {}).get("pixels") or []
    assert pixels and max(pixels) <= 600
