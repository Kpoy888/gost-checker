from __future__ import annotations

from gost_checker.engine import process_file


def test_pdf_from_bad_drawing(samples_dir, tmp_path):
    result = process_file(samples_dir / "bad_from_dxf.pdf", tmp_path / "pdf-bad", fix=True)
    assert result.kind == "pdf"
    assert result.output_dxf is None  # растр не исправляется
    assert result.report_html
    ids = {c.check_id for c in result.checks}
    assert "sheet_frame" in ids
    assert "title_block" in ids


def test_synthetic_a3_pdf(samples_dir, tmp_path):
    result = process_file(samples_dir / "a3_title_sample.pdf", tmp_path / "pdf-a3", fix=False)
    assert result.kind == "pdf"
    by_id = {c.check_id: c for c in result.checks}
    # формат должен определиться по размеру страницы
    assert "A3" in by_id["sheet_frame"].message or any("A3" in d for d in by_id["sheet_frame"].details)
    blob = " ".join(by_id["title_block"].details).lower()
    assert "разраб" in blob or "петров" in blob or "кж-01" in blob or "стадия" in blob


def test_revit_message(tmp_path):
    fake = tmp_path / "model.rvt"
    fake.write_bytes(b"not-a-real-revit-file")
    result = process_file(fake, tmp_path / "rvt", fix=False)
    assert result.kind == "Revit"
    assert "DXF" in result.checks[0].message


def test_pdf_frame_margins_within_half_mm(samples_dir, tmp_path):
    import re

    import fitz

    from gost_checker.raster import extract_pdf_frame_margins, margins_within_tolerance

    pdf = fitz.open(str(samples_dir / "a3_title_sample.pdf"))
    frames = extract_pdf_frame_margins(pdf[0])
    pdf.close()
    assert frames, "векторные контуры рамки должны находиться в учебном PDF"
    best = min(
        frames,
        key=lambda item: abs(item["left"] - 20)
        + abs(item["right"] - 5)
        + abs(item["top"] - 5)
        + abs(item["bottom"] - 5),
    )
    assert margins_within_tolerance(best, 0.5), best

    result = process_file(samples_dir / "a3_title_sample.pdf", tmp_path / "pdf-margins", fix=False)
    frame = next(c for c in result.checks if c.check_id == "sheet_frame")
    blob = frame.message + " " + " ".join(frame.details)
    assert frame.severity == "ok"
    assert re.search(r"слева\s+20[.,]\d{2}", blob)
    assert re.search(r"справа\s+4[.,]\d{2}|справа\s+5[.,]\d{2}", blob)
    left = float(re.search(r"слева\s+(20[.,]\d+)", blob).group(1).replace(",", "."))
    assert abs(left - 20.0) <= 0.5
