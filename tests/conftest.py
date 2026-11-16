from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from samples.generate import make_bad_drawing, make_good_drawing, make_pdf_from_drawing, make_synthetic_a3_pdf


@pytest.fixture(autouse=True)
def _disable_llm_by_default(monkeypatch):
    """Существующие тесты не должны ходить в сеть за пояснениями."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)


@pytest.fixture(scope="session")
def samples_dir(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("samples")
    make_bad_drawing(folder / "bad.dxf")
    make_good_drawing(folder / "good.dxf")
    make_pdf_from_drawing(folder / "bad.dxf", folder / "bad_from_dxf.pdf")
    make_synthetic_a3_pdf(folder / "a3_title_sample.pdf")
    return folder
