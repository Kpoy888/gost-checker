from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from samples.generate import make_bad_drawing, make_good_drawing, make_pdf_from_drawing, make_synthetic_a3_pdf


@pytest.fixture(scope="session")
def samples_dir(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("samples")
    make_bad_drawing(folder / "bad.dxf")
    make_good_drawing(folder / "good.dxf")
    make_pdf_from_drawing(folder / "bad.dxf", folder / "bad_from_dxf.pdf")
    make_synthetic_a3_pdf(folder / "a3_title_sample.pdf")
    return folder
