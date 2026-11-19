"""Экспликация, штамп и выпуск комплекта."""

from __future__ import annotations

import hashlib
from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient


def _room_block(doc):
    if "ROOM" in doc.blocks:
        return
    block = doc.blocks.new("ROOM")
    block.add_attdef("ROOM_NUMBER", insert=(0, 0), height=2.5)
    block.add_attdef("ROOM_NAME", insert=(12, 0), height=2.5)


def _sheet(path: Path, number: str, name: str) -> None:
    doc = ezdxf.new("R2010")
    _room_block(doc)
    ref = doc.modelspace().add_blockref("ROOM", (10, 10), dxfattribs={"layer": "ПОМЕЩЕНИЯ"})
    ref.add_auto_attribs({"ROOM_NUMBER": number, "ROOM_NAME": name})
    doc.saveas(str(path))


def test_table_sources_and_sync(tmp_path):
    from gost_checker.rooms import parse_table_rows, read_acad_table, read_csv, read_line_grid, read_xlsx, sync_drawing

    assert parse_table_rows([["Номер", "Наименование", "Площадь"], ["101", "Кухня", "12.5"]])[0]["name"] == "Кухня"
    dxf = """0
SECTION
2
ENTITIES
0
ACAD_TABLE
5
2A1
100
AcDbEntity
8
0
100
AcDbBlockReference
2
*T1
10
0.0
20
0.0
30
0.0
100
AcDbTable
280
0
91
2
92
2
171
1
1
Номер
171
1
1
Наименование
171
1
1
101
171
1
1
Кухня
0
ENDSEC
0
EOF
"""
    table_path = tmp_path / "table.dxf"
    table_path.write_bytes(dxf.encode("cp1251"))
    assert read_acad_table(ezdxf.readfile(str(table_path), encoding="cp1251"))[0]["name"] == "Кухня"

    grid = ezdxf.new("R2010")
    msp = grid.modelspace()
    for x in (0, 30, 80):
        msp.add_line((x, 0), (x, 20))
    for y in (0, 10, 20):
        msp.add_line((0, y), (80, y))
    msp.add_text("Номер", dxfattribs={"insert": (2, 12), "height": 2.5})
    msp.add_text("Наименование", dxfattribs={"insert": (32, 12), "height": 2.5})
    msp.add_text("101", dxfattribs={"insert": (2, 2), "height": 2.5})
    msp.add_text("Кухня", dxfattribs={"insert": (32, 2), "height": 2.5})
    assert read_line_grid(grid)[0]["name"] == "Кухня"

    csv_path = tmp_path / "rooms.csv"
    csv_path.write_text("Номер,Наименование\n101,Кухня\n", encoding="utf-8")
    assert read_csv(csv_path)[0]["number"] == "101"
    from openpyxl import Workbook

    book = Workbook()
    book.active.append(["Номер", "Наименование"])
    book.active.append(["101", "Столовая"])
    xlsx = tmp_path / "rooms.xlsx"
    book.save(xlsx)
    assert read_xlsx(xlsx)[0]["name"] == "Столовая"

    src = tmp_path / "a.dxf"
    _sheet(src, "101", "Кухня")
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    preview = sync_drawing(src, [{"number": "101", "name": "Столовая", "area": "", "category": ""}], tmp_path / "out.dxf", apply=False)
    assert preview["changes"]
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    assert not (tmp_path / "out.dxf").exists()
    applied = sync_drawing(src, [{"number": "101", "name": "Столовая", "area": "", "category": ""}], tmp_path / "out.dxf", apply=True)
    assert applied["audit_errors"] == 0
    again = sync_drawing(tmp_path / "out.dxf", [{"number": "101", "name": "Столовая", "area": "", "category": ""}], tmp_path / "out2.dxf", apply=True)
    assert again["remaining_renames"] == 0
    doc = ezdxf.readfile(str(tmp_path / "out.dxf"))
    attrib = list(doc.modelspace().query("INSERT"))[0].attribs
    names = {a.dxf.tag: a.dxf.text for a in attrib}
    assert names["ROOM_NAME"] == "Столовая"
    assert names["ROOM_NUMBER"] == "101"


def test_stamp_fill_block_and_release_codes(tmp_path):
    from gost_checker.stampcard import apply_card, empty_fields, validate_set
    from gost_checker.web import create_app

    src = tmp_path / "blank.dxf"
    ezdxf.new("R2010").saveas(str(src))
    original = hashlib.sha256(src.read_bytes()).hexdigest()
    card = {"обозначение": "КЖ-01", "наименование": "План", "стадия": "Р", "лист": "1", "листов": "1", "организация": "СПбГАСУ"}
    result = apply_card(src, tmp_path / "filled.dxf", card, first=True)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == original
    assert result["audit_errors"] == 0
    doc = ezdxf.readfile(str(tmp_path / "filled.dxf"))
    assert any(entity.dxftype() == "INSERT" for entity in doc.modelspace())
    blocked = apply_card(src, tmp_path / "draft.dxf", {"обозначение": "", "наименование": "", "стадия": "", "лист": "", "листов": ""}, draft=True)
    assert blocked["blocked"] is False
    assert "ЧЕРНОВИК" in " ".join(a.dxf.text or "" for e in ezdxf.readfile(str(tmp_path / "draft.dxf")).modelspace().query("INSERT") for a in e.attribs)
    assert empty_fields({"обозначение": ""})
    notes = validate_set([
        {"обозначение": "А", "стадия": "Р", "лист": "1", "листов": "1", "дата": "13.25"},
        {"обозначение": "Б", "стадия": "Х", "лист": "3", "листов": "9", "дата": "11.26"},
    ])
    assert any("Шифр" in note for note in notes)
    assert any("стадия" in note for note in notes)
    assert any("ММ.ГГ" in note for note in notes)
    long_card = dict(card)
    long_card["наименование"] = "Очень длинное наименование которое не помещается в графу штампа формы три"
    warned = apply_card(tmp_path / "filled.dxf", tmp_path / "long.dxf", long_card)
    assert warned["warnings"]
    client = TestClient(create_app())
    response = client.post(
        "/api/release-check",
        files=[("files", ("a.dxf", src.read_bytes(), "application/dxf"))],
        data={"card": '{"обозначение":"","наименование":"","стадия":"","лист":"","листов":""}'},
    )
    assert response.status_code == 200
    assert response.json()["can_release"] is False


def test_release_isolates_crash_and_cli(tmp_path, samples_dir):
    from gost_checker.releasecheck import release_check, release_cli

    src = samples_dir / "good.dxf"
    clean = release_check([src], card={"обозначение": "КЖ", "наименование": "План", "стадия": "Р", "лист": "1", "листов": "1"})
    assert clean["can_release"] is True
    crashed = release_check([src], extra_checkers=[{"name": "boom", "func": lambda: (_ for _ in ()).throw(RuntimeError("сломалось"))}])
    assert crashed["can_release"] is False
    assert any(item["type"] == "checker_error" for item in crashed["findings"])
    assert any(event["ok"] is False for event in crashed["progress"])
    code = release_cli([str(src), "--card", str(_card(tmp_path))])
    assert code == 0


def _card(tmp_path: Path) -> Path:
    path = tmp_path / "card.json"
    path.write_text('{"обозначение":"КЖ","наименование":"План","стадия":"Р","лист":"1","листов":"1"}', encoding="utf-8")
    return path
