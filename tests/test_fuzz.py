from __future__ import annotations

import random
from pathlib import Path

import ezdxf
import pytest

from gost_checker.engine import process_file


def _fill_random_dxf(doc, rng: random.Random) -> None:
    doc.header["$INSUNITS"] = rng.choice([0, 1, 4, 5, 6])
    msp = doc.modelspace()
    n = rng.randint(0, 25)
    for _ in range(n):
        kind = rng.choice(["line", "circle", "lwpoly", "text", "mtext", "arc", "ellipse", "empty_text"])
        x = rng.uniform(-50, 500)
        y = rng.uniform(-50, 400)
        try:
            if kind == "line":
                msp.add_line((x, y), (x + rng.uniform(-80, 80), y + rng.uniform(-80, 80)))
            elif kind == "circle":
                msp.add_circle((x, y), max(0.5, rng.uniform(1, 40)))
            elif kind == "lwpoly":
                pts = [(x + rng.uniform(-30, 30), y + rng.uniform(-30, 30)) for _ in range(rng.randint(2, 6))]
                msp.add_lwpolyline(pts, close=rng.choice([True, False]))
            elif kind == "text":
                msp.add_text(rng.choice(["Текст", "Note", "Примечание", "КЖ-01", ""]), dxfattribs={"height": rng.choice([0.1, 2.5, 11.0, 25.0]), "insert": (x, y)})
            elif kind == "mtext":
                msp.add_mtext(rng.choice([r"{\H11;Крупный}", "Строка\nдве", ""]), dxfattribs={"char_height": rng.uniform(1, 12), "insert": (x, y)})
            elif kind == "arc":
                msp.add_arc((x, y), max(1.0, rng.uniform(5, 40)), rng.uniform(0, 180), rng.uniform(180, 359))
            elif kind == "ellipse":
                msp.add_ellipse((x, y), major_axis=(rng.uniform(5, 30), 0), ratio=rng.uniform(0.2, 0.9))
            else:
                msp.add_text("", dxfattribs={"height": 2.5, "insert": (x, y)})
        except Exception:
            continue
    if rng.random() < 0.4:
        msp.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True)
        msp.add_lwpolyline([(20, 5), (415, 5), (415, 292), (20, 292)], close=True)


def _assert_no_crash(result, label: str) -> None:
    assert result is not None, label
    assert result.report_html and Path(result.report_html).exists(), label
    blob = " ".join(result.notes) + " " + " ".join(
        check.message + " " + " ".join(check.details) for check in result.checks
    )
    assert "Traceback" not in blob, (label, blob[:400])
    assert "internal error" not in blob.lower(), (label, blob[:400])


def test_random_dxf_fuzz_does_not_crash(tmp_path):
    rng = random.Random(20261110)
    for index in range(16):
        doc = ezdxf.new("R2013")
        _fill_random_dxf(doc, rng)
        path = tmp_path / f"fuzz_{index}.dxf"
        doc.saveas(str(path))
        result = process_file(path, tmp_path / f"out_{index}", fix=bool(index % 2))
        _assert_no_crash(result, f"fuzz_{index}")


try:
    from hypothesis import given, settings, strategies as st
except Exception:  # pragma: no cover
    given = None


@pytest.mark.skipif(given is None, reason="hypothesis не установлен — используется test_random_dxf_fuzz_does_not_crash")
def test_hypothesis_dxf_fuzz(tmp_path):
    @settings(max_examples=8, deadline=None)
    @given(st.integers(min_value=0, max_value=10_000_000))
    def _run(seed: int):
        rng = random.Random(seed)
        doc = ezdxf.new("R2013")
        _fill_random_dxf(doc, rng)
        path = tmp_path / f"hyp_{seed}.dxf"
        doc.saveas(str(path))
        result = process_file(path, tmp_path / f"hyp_out_{seed}", fix=False)
        _assert_no_crash(result, f"hyp_{seed}")

    _run()
