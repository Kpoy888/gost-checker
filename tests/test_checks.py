from __future__ import annotations

from pathlib import Path

import ezdxf

from gost_checker.checks import ALL_CHECKS
from gost_checker.constants import FONT_HEIGHTS_PRIMARY_MM
from gost_checker.engine import process_file


def test_plugins_have_metadata():
    ids = []
    for check in ALL_CHECKS:
        assert check.id
        assert check.title
        assert check.gost
        assert check.id not in ids
        ids.append(check.id)
    assert set(ids) >= {"sheet_frame", "title_block", "layout_overlap", "linetypes", "lines", "fonts", "scales", "dimensions"}


def test_bad_drawing_finds_problems(samples_dir, tmp_path):
    result = process_file(samples_dir / "bad.dxf", tmp_path / "bad-report", fix=False)
    by_id = {c.check_id: c for c in result.checks}
    assert by_id["sheet_frame"].severity == "error"
    assert by_id["title_block"].severity == "error"
    assert by_id["fonts"].findings_count >= 1
    assert by_id["dimensions"].findings_count >= 1
    assert by_id["layout_overlap"].severity in {"warning", "error"}
    assert result.output_dxf is None


def test_fix_bad_drawing_and_audit(samples_dir, tmp_path):
    out = tmp_path / "fixed"
    result = process_file(
        samples_dir / "bad.dxf",
        out,
        fix=True,
        title_meta={"обозначение": "КЖ-01", "разраб": "Иванов", "организация": "СПбГАСУ", "стадия": "Р", "наименование": "План подземной части"},
        assumed_format="A3",
    )
    assert result.output_dxf
    path = Path(result.output_dxf)
    assert path.exists()
    doc = ezdxf.readfile(str(path))
    auditor = doc.audit()
    assert not auditor.has_errors, [str(item) for item in auditor]

    by_id = {c.check_id: c for c in result.checks}
    assert by_id["sheet_frame"].auto_fixed
    assert by_id["title_block"].auto_fixed

    # повторная проверка уже исправленного файла
    again = process_file(path, tmp_path / "recheck", fix=False)
    again_map = {c.check_id: c for c in again.checks}
    assert again_map["sheet_frame"].severity in {"ok", "info"}
    assert again_map["title_block"].severity in {"ok", "info"}

    # высоты текста приведены к ряду
    for entity in doc.modelspace().query("TEXT"):
        height = float(entity.dxf.height)
        nearest = min(FONT_HEIGHTS_PRIMARY_MM, key=lambda item: abs(item - height))
        assert abs(height - nearest) < 0.15

    # рамка примерно A3
    from gost_checker.checks.sheet_frame import collect_rectangles, pick_frame
    from gost_checker.units import doc_to_mm

    info = pick_frame(collect_rectangles(doc, doc_to_mm(doc)), doc_to_mm(doc))
    assert info.format_name == "A3"
    assert info.inner or info.outer


def test_good_drawing_mostly_clean(samples_dir, tmp_path):
    result = process_file(samples_dir / "good.dxf", tmp_path / "good-report", fix=False)
    by_id = {c.check_id: c for c in result.checks}
    assert by_id["sheet_frame"].severity in {"ok", "info"}
    assert by_id["title_block"].severity in {"ok", "info"}
    assert result.summary.get("error", 0) == 0


def test_cli_entrypoint(samples_dir, tmp_path):
    from gost_checker.cli import main

    code = main([str(samples_dir / "good.dxf"), "--out", str(tmp_path / "cli")])
    assert code == 0
    assert (tmp_path / "cli" / "report.html").exists()
