"""Уклоны, оси и контуры помещений."""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gost_checker.slopes import analyze_segments, segments_from_dxf, slope_value


@given(
    dz=st.floats(min_value=-5, max_value=5, allow_nan=False, allow_infinity=False),
    length=st.floats(min_value=0.01, max_value=100, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=30)
def test_slope_formula(dz, length):
    assert slope_value(dz, length) == pytest.approx(dz / length)


def test_slope_cases(tmp_path):
    rules = {"sewer": {"min": 0.01, "max": 0.05}, "water": {"min": 0.002, "max": 0.01, "warn_zero": True}}
    normal = analyze_segments([{"kind": "sewer", "length": 10, "z1": 1.2, "z2": 1.0, "labeled": 0.02}], rules)
    assert normal[0]["status"] == "ok"
    low = analyze_segments([{"kind": "sewer", "length": 10, "z1": 1.05, "z2": 1.0, "labeled": None}], rules)
    assert "минимума" in low[0]["message"]
    reverse = analyze_segments([{"kind": "sewer", "length": 10, "z1": 1.0, "z2": 1.2, "labeled": None}], rules)
    assert "Обратный" in reverse[0]["message"]
    zero = analyze_segments([{"kind": "sewer", "length": 10, "z1": 1, "z2": 1, "labeled": None}], rules)
    assert "Нулевой" in zero[0]["message"]
    edge = analyze_segments([{"kind": "sewer", "length": 10, "z1": 1.1, "z2": 1.0, "labeled": None}], rules)
    assert edge[0]["status"] == "ok"
    missing = analyze_segments([{"kind": "water", "length": 5, "z1": None, "z2": None, "labeled": None}], rules)
    assert missing[0]["status"] == "недостаточно данных"
    mismatch = analyze_segments([{"kind": "water", "length": 10, "z1": 0.02, "z2": 0, "labeled": 0.005}], rules)
    assert "не совпадает" in mismatch[0]["message"]

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_line((0, 0, 1250), (10000, 0, 1050), dxfattribs={"layer": "К1"})
    doc.modelspace().add_text("+1.250", dxfattribs={"insert": (0, 10), "height": 2.5})
    doc.modelspace().add_text("−0,450", dxfattribs={"insert": (0, 30), "height": 2.5})
    path = tmp_path / "pipe.dxf"
    doc.saveas(str(path))
    loaded = ezdxf.readfile(str(path))
    rows = segments_from_dxf(loaded, unit_to_m=0.001)
    assert rows and rows[0]["slope"] if False else rows[0]["z1"] == pytest.approx(1250)
    meters = ezdxf.new("R2010")
    meters.header["$INSUNITS"] = 6
    meters.modelspace().add_line((0, 0, 1.25), (10, 0, 1.05), dxfattribs={"layer": "К1"})
    meters.saveas(str(tmp_path / "m.dxf"))
    mrows = segments_from_dxf(ezdxf.readfile(str(tmp_path / "m.dxf")), unit_to_m=1.0)
    found = analyze_segments(mrows, rules)
    assert found[0]["slope"] == pytest.approx(0.02)


def test_axes_grid(tmp_path):
    from gost_checker.axes import EXCLUDED, build_axes

    doc = ezdxf.new("R2010")
    block = doc.blocks.new("КОЛОННА")
    block.add_circle((0, 0), 20)
    msp = doc.modelspace()
    for x in (0, 6000, 12000):
        for y in (0, 4000, 8000, 12000):
            msp.add_blockref("КОЛОННА", (x, y), dxfattribs={"layer": "Колонны"})
    src = tmp_path / "grid.dxf"
    doc.saveas(str(src))
    result = build_axes(src, tmp_path / "axes.dxf")
    assert result["ok"]
    assert result["numbers"] == ["1", "2", "3"]
    assert result["letters"][0] == "А"
    assert not set(result["letters"]) & EXCLUDED
    assert result["chain"] == pytest.approx(result["overall"])
    assert result["original_unchanged"]
    empty = ezdxf.new("R2010")
    empty.saveas(str(tmp_path / "empty.dxf"))
    assert build_axes(tmp_path / "empty.dxf", tmp_path / "no.dxf")["ok"] is False


def test_room_plan(tmp_path):
    from gost_checker.roomplan import write_plan

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (5000, 0), dxfattribs={"layer": "Стены"})
    msp.add_line((5000, 0), (5000, 4000), dxfattribs={"layer": "Стены"})
    msp.add_line((5000, 4000), (0, 4000), dxfattribs={"layer": "Стены"})
    msp.add_line((0, 4000), (0, 900), dxfattribs={"layer": "Стены"})
    msp.add_line((0, 0), (0, 200), dxfattribs={"layer": "Стены"})
    src = tmp_path / "plan.dxf"
    doc.saveas(str(src))
    result = write_plan(src, tmp_path / "rooms.dxf", texts=[{"x": 2500, "y": 2000, "name": "Кухня", "number": "101"}])
    assert result["original_unchanged"]
    assert result["rooms"]
    assert result["note"]
    assert result["total"] == pytest.approx(sum(item["area_m2"] for item in result["rooms"]))
