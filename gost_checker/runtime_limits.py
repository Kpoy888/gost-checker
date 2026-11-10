"""Лимиты под узкие хосты (Render free: 512 МБ ОЗУ)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional


@lru_cache(maxsize=1)
def total_ram_mb() -> Optional[float]:
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        return None
    return None


def light_memory() -> bool:
    flag = os.environ.get("GOST_LIGHT_MEMORY", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    total = total_ram_mb()
    return total is not None and total <= 768.0


def max_image_side() -> int:
    raw = os.environ.get("GOST_MAX_IMAGE_SIDE", "").strip()
    if raw:
        try:
            return max(800, min(4000, int(raw)))
        except ValueError:
            pass
    return 1600 if light_memory() else 2200


def pdf_raster_zoom(page_width_pts: float, page_height_pts: float) -> float:
    raw = os.environ.get("GOST_PDF_ZOOM", "").strip()
    if raw:
        try:
            return max(0.75, min(3.0, float(raw)))
        except ValueError:
            pass
    long_side = max(float(page_width_pts or 0.0), float(page_height_pts or 0.0))
    cap = 1.5 if light_memory() else 2.0
    if long_side <= 1.0:
        return cap
    zoom = max_image_side() / long_side
    return max(1.0, min(cap, zoom))


def overlay_max_side() -> int:
    return 1200 if light_memory() else 1600


def preview_figure_size_dpi() -> tuple[tuple[float, float], int]:
    if light_memory():
        return (8.27, 5.85), 72
    return (16.54, 11.69), 120
