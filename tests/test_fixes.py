from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from gost_checker.checks.layout import _entity_rect, interiors_overlap, stamp_zone
from gost_checker.checks.linetypes import suggest_spds_layer
from gost_checker.convert import dwg_to_dxf, dxf_to_dwg, find_libredwg_dwg2dxf, find_libredwg_dxf2dwg
from gost_checker.engine import process_file
from gost_checker.models import Rect


def _stamp_from_doc(doc) -> Rect:
    from gost_checker.checks.base import CheckContext

    ctx = CheckContext(doc=doc, path=Path("x.dxf"), to_mm=1.0)
    stamp = stamp_zone(ctx)
    assert stamp is not None
    return stamp


def test_layout_moves_note_out_of_title_block(samples_dir, tmp_path):
    result = process_file(
        samples_dir / "bad.dxf",
        tmp_path / "layout",
        fix=True,
        title_meta={"обозначение": "КЖ-01", "разраб": "Иванов"},
        assumed_format="A3",
    )
    layout = next(c for c in result.checks if c.check_id == "layout_overlap")
    assert layout.auto_fixed
    blob = layout.message + " " + " ".join(layout.details)
    assert "Примечание" in blob
    assert "Перемещено" in blob or "Сдвинуты" in blob or "перенесено" in blob.lower()

    doc = ezdxf.readfile(result.output_dxf)
    stamp = _stamp_from_doc(doc)
    notes = [e for e in doc.modelspace().query("TEXT") if "Примечание" in (e.dxf.text or "")]
    assert notes, "надпись «Примечание» должна остаться на чертеже"
    for entity in notes:
        rect = _entity_rect(entity)
        assert rect is not None
        assert not interiors_overlap(rect, stamp), (rect, stamp)

    again = process_file(Path(result.output_dxf), tmp_path / "layout-recheck", fix=False)
    again_layout = next(c for c in again.checks if c.check_id == "layout_overlap")
    assert again_layout.severity == "ok"


def test_assign_layers_optional(samples_dir, tmp_path):
    off = process_file(samples_dir / "bad.dxf", tmp_path / "layers-off", fix=True, assign_layers=False)
    doc_off = ezdxf.readfile(off.output_dxf)
    user_poly = [e for e in doc_off.modelspace().query("LWPOLYLINE") if e.dxf.layer == "Layer1"]
    assert user_poly, "без --assign-layers пользовательский слой Layer1 должен сохраниться"

    on = process_file(samples_dir / "bad.dxf", tmp_path / "layers-on", fix=True, assign_layers=True)
    assert "--assign-layers" in " ".join(on.checks[0].details) or any(
        "SPDS" in " ".join(c.details) for c in on.checks if c.check_id == "linetypes"
    )
    doc_on = ezdxf.readfile(on.output_dxf)
    texts = list(doc_on.modelspace().query("TEXT"))
    assert texts
    assert any(entity.dxf.layer == "SPDS_TEXT" for entity in texts)
    dims = list(doc_on.modelspace().query("DIMENSION"))
    if dims:
        assert all(entity.dxf.layer == "SPDS_DIM" or entity.dxf.layer.startswith("SPDS") for entity in dims)


def test_suggest_spds_layer_heuristics():
    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    text = msp.add_text("А", height=3.5, dxfattribs={"layer": "0"})
    assert suggest_spds_layer(text, doc) == "SPDS_TEXT"
    line = msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "0", "linetype": "CENTER"})
    assert suggest_spds_layer(line, doc) == "SPDS_OS"


def test_scale_inferred_from_dimension(samples_dir, tmp_path):
    bad = process_file(samples_dir / "bad.dxf", tmp_path / "scale-bad", fix=False)
    scales = next(c for c in bad.checks if c.check_id == "scales")
    blob = scales.message + " " + " ".join(scales.details)
    assert "1:1" in blob
    assert "геометр" in blob.lower() or "размер:" in blob

    good = process_file(samples_dir / "good.dxf", tmp_path / "scale-good", fix=False)
    scales = next(c for c in good.checks if c.check_id == "scales")
    blob = scales.message + " " + " ".join(scales.details)
    assert "1:100" in blob
    assert "1:1" in blob
    assert scales.severity in {"warning", "ok", "info"}
    if scales.severity == "warning":
        assert "различа" in scales.message.lower() or "не совпад" in " ".join(scales.details).lower()


def test_dwg_roundtrip(samples_dir, tmp_path):
    if not find_libredwg_dxf2dwg() or not find_libredwg_dwg2dxf():
        pytest.skip("LibreDWG (dxf2dwg/dwg2dxf) не установлен")
    # Простой R2000 без блоков с атрибутами: LibreDWG надёжнее кодирует такую геометрию.
    simple = tmp_path / "roundtrip.dxf"
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_line((0, 0), (120, 0))
    msp.add_lwpolyline([(10, 10), (80, 10), (80, 50), (10, 50)], close=True)
    msp.add_text("HELLO", height=5, dxfattribs={"insert": (15, 20)})
    msp.add_circle((40, 30), 8)
    doc.saveas(str(simple))

    dwg, message = dxf_to_dwg(simple, tmp_path)
    assert dwg is not None and Path(dwg).exists(), message
    dxf, message2 = dwg_to_dxf(Path(dwg), tmp_path)
    assert dxf is not None and Path(dxf).exists(), message2
    try:
        back = ezdxf.readfile(str(dxf))
    except Exception:
        from ezdxf import recover

        back, _auditor = recover.readfile(str(dxf))
    types = {entity.dxftype() for entity in back.modelspace()}
    assert types >= {"LINE", "LWPOLYLINE", "TEXT", "CIRCLE"}, (types, message2)
    texts = [entity.dxf.text for entity in back.modelspace().query("TEXT")]
    assert "HELLO" in texts


def test_fix_is_idempotent(samples_dir, tmp_path):
    first = process_file(
        samples_dir / "bad.dxf",
        tmp_path / "fix1",
        fix=True,
        title_meta={"обозначение": "КЖ-01", "разраб": "Иванов", "стадия": "Р", "наименование": "План"},
        assumed_format="A3",
    )
    first_doc = ezdxf.readfile(first.output_dxf)
    assert not first_doc.audit().has_errors
    note1 = next(entity for entity in first_doc.modelspace().query("TEXT") if "Примечание" in (entity.dxf.text or ""))
    pos1 = (round(float(note1.dxf.insert.x), 3), round(float(note1.dxf.insert.y), 3), round(float(note1.dxf.height), 3))
    lw1 = []
    for entity in first_doc.modelspace().query("LWPOLYLINE"):
        if entity.dxf.hasattr("lineweight"):
            lw1.append((entity.dxf.layer, int(entity.dxf.lineweight)))
    count1 = sum(1 for _ in first_doc.modelspace())

    second = process_file(Path(first.output_dxf), tmp_path / "fix2", fix=True, assumed_format="A3")
    second_doc = ezdxf.readfile(second.output_dxf)
    assert not second_doc.audit().has_errors
    note2 = next(entity for entity in second_doc.modelspace().query("TEXT") if "Примечание" in (entity.dxf.text or ""))
    pos2 = (round(float(note2.dxf.insert.x), 3), round(float(note2.dxf.insert.y), 3), round(float(note2.dxf.height), 3))
    lw2 = []
    for entity in second_doc.modelspace().query("LWPOLYLINE"):
        if entity.dxf.hasattr("lineweight"):
            lw2.append((entity.dxf.layer, int(entity.dxf.lineweight)))
    count2 = sum(1 for _ in second_doc.modelspace())
    assert pos1 == pos2
    assert lw1 == lw2
    assert count1 == count2
