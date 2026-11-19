"""Общие величины листа: единицы, масштаб видового экрана, отрезки."""

from __future__ import annotations

from typing import Any

# ГОСТ 2.307: минимумы на чертеже. Номер пункта здесь не указан — он не сверен по тексту редакции.
CONTOUR_GAP_MM = 10.0
PARALLEL_GAP_MM = 7.0


def open_doc(path):
    import ezdxf

    try:
        return ezdxf.readfile(str(path))
    except Exception:
        from ezdxf import recover

        doc, _auditor = recover.readfile(str(path))
        return doc


def to_mm(doc) -> float:
    from .units import resolve_insunits

    factor, _warning = resolve_insunits(doc)
    return float(factor or 1.0)


def scale_denom(doc) -> float:
    """Сколько миллиметров модели приходится на 1 мм листа. Без видового экрана — 1."""
    denom = 1.0
    try:
        layouts = list(doc.layouts)
    except Exception:
        return denom
    for layout in layouts:
        if str(getattr(layout, "name", "")).lower() == "model":
            continue
        try:
            viewports = list(layout.query("VIEWPORT"))
        except Exception:
            continue
        for vp in viewports:
            try:
                paper = float(vp.dxf.height or 0)
                view = float(vp.dxf.view_height or 0)
            except Exception:
                continue
            if paper > 1e-9 and view > 1e-9 and int(getattr(vp.dxf, "status", 1) or 1) != 0:
                # id 1 часто служебный viewport листа
                if abs(view / paper - 1.0) < 1e-6 and paper > 1000:
                    continue
                denom = view / paper
    return denom if denom > 1e-9 else 1.0


def paper_mm(distance_du: float, unit_mm: float, denom: float) -> float:
    return abs(float(distance_du)) * unit_mm / max(denom, 1e-9)


def model_du(distance_paper_mm: float, unit_mm: float, denom: float) -> float:
    return float(distance_paper_mm) * max(denom, 1e-9) / max(unit_mm, 1e-9)


def header_dim(doc, name: str, default: float) -> float:
    try:
        value = doc.header.get(name, default)
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def text_height_paper_mm(entity, doc, unit_mm: float, denom: float) -> float:
    del entity
    dimtxt = header_dim(doc, "$DIMTXT", 2.5) or 2.5
    dimscale = header_dim(doc, "$DIMSCALE", 1.0) or 1.0
    return paper_mm(dimtxt * dimscale, unit_mm, denom)
