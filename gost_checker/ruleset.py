"""Пакет правил: числовые параметры вынесены в данные, в отчёте фиксируется версия."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from . import constants as C

ROOT = Path(__file__).resolve().parent.parent / "data" / "norms" / "rules" / "spds-eskd-2026.1.json"


def rules_path() -> Path:
    raw = (os.environ.get("GOST_RULESET") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return ROOT


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    file = Path(path)
    if not file.is_file():
        return {}
    return json.loads(file.read_text(encoding="utf-8"))


def clear_cache() -> None:
    _load.cache_clear()


def current() -> dict:
    data = _load(str(rules_path()))
    if not data:
        return {
            "id": "builtin",
            "version": "builtin",
            "scales_reduction": list(C.SCALES_REDUCTION),
            "scales_enlargement": list(C.SCALES_ENLARGEMENT),
            "font_heights_mm": list(C.FONT_HEIGHTS_MM),
            "main_line_s_min_mm": C.MAIN_LINE_S_MIN_MM,
            "main_line_s_max_mm": C.MAIN_LINE_S_MAX_MM,
            "frame_left_mm": C.FRAME_LEFT_MM,
            "frame_other_mm": C.FRAME_OTHER_MM,
            "title_w_mm": C.TITLE_BLOCK_W_MM,
            "title_h_mm": C.TITLE_BLOCK_H_MM,
            "dim_tick_min_mm": C.DIM_TICK_MIN_MM,
            "dim_tick_max_mm": C.DIM_TICK_MAX_MM,
        }
    return data


def version_label() -> str:
    data = current()
    return f"{data.get('id', 'rules')}@{data.get('version', '?')}"


def scale_labels() -> tuple[list[str], list[str]]:
    data = current()
    red = list(data.get("scales_reduction") or C.SCALES_REDUCTION)
    enl = list(data.get("scales_enlargement") or C.SCALES_ENLARGEMENT)
    return red, enl
