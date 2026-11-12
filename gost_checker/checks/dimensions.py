from __future__ import annotations

from ezdxf.render.arrows import ARROWS

from ..constants import (
    DIM_EXE_MAX_MM,
    DIM_EXE_MIN_MM,
    DIM_TICK_MAX_MM,
    DIM_TICK_MIN_MM,
    DIM_TXT_PREFERRED_MM,
    GOST_STYLE_NAME,
    SPDS_DIMSTYLE,
)
from ..titleblock import ensure_gost_style
from ..units import du_to_mm, mm_to_du
from .base import BaseCheck, CheckContext


SPDS_ARROW_NAMES = {
    "OBLIQUE",
    "_OBLIQUE",
    "ARCHTICK",
    "_ARCHTICK",
    "ARCHITECTURAL_TICK",
    "TICK",
    "",
}
# 0/1 — линейные (засечки по СПДС); 2–5 — угол, диаметр, радиус (стрелки по ГОСТ 2.307).
LINEAR_DIMTYPES = {0, 1}
ARROW_DIMTYPES = {2, 3, 4, 5}


def _get(ds, name: str, header, default):
    try:
        if ds.dxf.hasattr(name):
            value = ds.dxf.get(name)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        header_val = header.get("$" + name.upper())
        if header_val is not None:
            return header_val
    except Exception:
        pass
    return default


def _arrow_ok(ds, header) -> tuple[bool, str]:
    dimtsz = float(_get(ds, "dimtsz", header, 0) or 0)
    if dimtsz > 0:
        return True, f"засечки через DIMTSZ={dimtsz:g}"
    names = []
    for key in ("dimblk", "dimblk1", "dimblk2"):
        raw = str(_get(ds, key, header, "") or "")
        names.append(raw)
    joined = ",".join(names)
    upper = {n.upper() for n in names}
    if any(n in SPDS_ARROW_NAMES or "OBLIQUE" in n or "ARCH" in n or "TICK" in n for n in upper):
        # пустые имена при dimtsz=0 обычно означают стрелку ClosedFilled по умолчанию
        if upper <= {"", "NONE"} and dimtsz == 0:
            return False, "стрелки по умолчанию (как правило, замкнутые заполненные), не засечки"
        if "OBLIQUE" in ",".join(upper) or "ARCH" in ",".join(upper) or "TICK" in ",".join(upper):
            return True, f"ограничители: {joined or 'засечка'}"
    if any(x in ",".join(upper) for x in ("CLOSED", "DOT", "ORIGIN", "BOX", "DATUM", "OPEN")):
        return False, f"ограничители «{joined}» не являются засечками СПДС"
    return False, f"ограничители «{joined or 'по умолчанию'}» не распознаны как засечки"


def _is_tick_arrow(names: tuple, dimtsz: float) -> bool:
    if dimtsz > 1e-6:
        return True
    upper = " ".join(str(name or "").upper() for name in names)
    return any(token in upper for token in ("OBLIQUE", "ARCH", "TICK"))


def _dim_arrow_state(dimension) -> tuple[bool, float]:
    override = dimension.override()
    try:
        names = override.get_arrow_names()
    except Exception:
        names = ("", "")
    try:
        dimtsz = float(override.get("dimtsz") or 0)
    except Exception:
        dimtsz = 0.0
    return _is_tick_arrow(names, dimtsz), dimtsz


def _restore_arrows_on_radial(doc) -> int:
    """Радиус, диаметр и угол оставляют стрелку, даже если стиль листа переведён на засечки."""
    restored = 0
    seen = set()
    for layout in [doc.modelspace(), *list(doc.layouts)]:
        if id(layout) in seen:
            continue
        seen.add(id(layout))
        try:
            dimensions = list(layout.query("DIMENSION"))
        except Exception:
            continue
        for dimension in dimensions:
            try:
                kind = int(dimension.dimtype)
            except Exception:
                continue
            if kind not in ARROW_DIMTYPES:
                continue
            is_tick, _dimtsz = _dim_arrow_state(dimension)
            if not is_tick:
                continue
            try:
                override = dimension.override()
                override["dimblk"] = ARROWS.closed_filled
                override["dimtsz"] = 0
                override.commit()
                restored += 1
            except Exception:
                continue
    return restored


def _apply_spds_dimstyle(doc, name: str = SPDS_DIMSTYLE, to_mm: float = 1.0):
    ensure_gost_style(doc)
    if name not in {s.dxf.name for s in doc.dimstyles}:
        try:
            doc.dimstyles.duplicate_entry("Standard", name)
        except Exception:
            doc.dimstyles.new(name)
    ds = doc.dimstyles.get(name)
    try:
        ds.set_arrows(blk=ARROWS.oblique)
    except Exception:
        try:
            ds.dxf.dimblk = ARROWS.oblique
            ds.dxf.dimblk1 = ARROWS.oblique
            ds.dxf.dimblk2 = ARROWS.oblique
        except Exception:
            pass
    try:
        ds.dxf.dimtsz = 0
    except Exception:
        pass
    # DIMTXT/DIMASZ/DIMEXE хранятся в единицах чертежа, а не всегда в миллиметрах.
    ds.dxf.dimasz = mm_to_du(2.5, to_mm)
    ds.dxf.dimtxt = mm_to_du(3.5, to_mm)
    ds.dxf.dimexe = mm_to_du(2.0, to_mm)
    ds.dxf.dimexo = 0
    ds.dxf.dimgap = mm_to_du(1.0, to_mm)
    ds.dxf.dimtad = 1
    ds.dxf.dimtih = 0
    ds.dxf.dimtoh = 0
    try:
        ds.dxf.dimtxsty = GOST_STYLE_NAME
    except Exception:
        pass
    return ds


class DimensionsCheck(BaseCheck):
    id = "dimensions"
    title = "Размеры (размерные стили)"
    gost = "ГОСТ 2.307-68; для строительных чертежей — засечки 2–4 мм под 45° по ГОСТ Р 21.101-2020 п. 5.4.2"
    can_autofix = True

    def run(self, ctx: CheckContext):
        details: list[str] = []
        header = ctx.doc.header
        issues = []
        styles_ok = 0

        used = set()
        dim_count = 0
        style_kinds: dict[str, set[int]] = {}
        radial_with_ticks = 0
        seen_layouts = set()
        for layout in [ctx.doc.modelspace(), *list(ctx.doc.layouts)]:
            if id(layout) in seen_layouts:
                continue
            seen_layouts.add(id(layout))
            for entity in layout.query("DIMENSION"):
                dim_count += 1
                try:
                    style_name = str(entity.dxf.dimstyle)
                except Exception:
                    style_name = ""
                if style_name:
                    used.add(style_name)
                try:
                    kind = int(entity.dimtype)
                except Exception:
                    kind = 0
                style_kinds.setdefault(style_name, set()).add(kind)
                if kind in ARROW_DIMTYPES and _dim_arrow_state(entity)[0]:
                    radial_with_ticks += 1
        if dim_count == 0:
            details.append("Размерных объектов нет — готовые стили шаблона CAD не проверялись.")
        else:
            details.append("Проверяются размерные стили, которыми проставлены размеры: " + ", ".join(sorted(used)) + ".")

        for ds in ctx.doc.dimstyles:
            name = ds.dxf.name
            if name not in used:
                continue
            arrows_ok, arrow_desc = _arrow_ok(ds, header)
            kinds = style_kinds.get(name, set())
            dimtxt = du_to_mm(float(_get(ds, "dimtxt", header, 2.5) or 2.5), ctx.to_mm)
            dimasz = du_to_mm(float(_get(ds, "dimasz", header, 2.5) or 2.5), ctx.to_mm)
            dimexe = du_to_mm(float(_get(ds, "dimexe", header, 1.25) or 1.25), ctx.to_mm)
            dimexo = du_to_mm(float(_get(ds, "dimexo", header, 0.625) or 0.625), ctx.to_mm)
            allowed_text = {round(value, 2) for value in DIM_TXT_PREFERRED_MM}
            style_issues = []
            if kinds & LINEAR_DIMTYPES and not arrows_ok:
                style_issues.append(arrow_desc)
            elif kinds & LINEAR_DIMTYPES and arrows_ok:
                if not (DIM_TICK_MIN_MM - 0.2 <= dimasz <= DIM_TICK_MAX_MM + 0.2) and float(_get(ds, "dimtsz", header, 0) or 0) == 0:
                    style_issues.append(f"длина засечки DIMASZ={dimasz:g} мм (ожидается 2–4 мм)")
            if round(dimtxt, 2) not in allowed_text:
                style_issues.append(
                    f"высота размерного текста {dimtxt:g} мм "
                    "(ряд ГОСТ 2.304 от 2,5 мм: 2,5; 3,5; 5; 7; 10; 14; 20)"
                )
            if not (DIM_EXE_MIN_MM - 0.3 <= dimexe <= DIM_EXE_MAX_MM + 0.3):
                style_issues.append(f"выступ выносной за размерную DIMEXE={dimexe:g} мм (1–5 мм)")
            if dimexo < -0.1 or dimexo > 3.1:
                style_issues.append(f"отступ выносной от контура DIMEXO={dimexo:g} мм (обычно 0–2 мм)")

            if style_issues:
                issues.append((name, style_issues, ds))
                details.append(f"Стиль «{name}»: " + "; ".join(style_issues) + f". Сейчас: {arrow_desc}.")
            else:
                styles_ok += 1
                details.append(f"Стиль «{name}»: {arrow_desc}; текст {dimtxt:g} мм; засечка/стрелка {dimasz:g} мм; DIMEXE {dimexe:g}.")

        if radial_with_ticks:
            details.append(
                f"Засечки стоят на радиусах, диаметрах или углах: {radial_with_ticks}. "
                "По ГОСТ 2.307 и ГОСТ Р 21.101 там оставляют стрелки."
            )
            issues.append(("__radial__", [f"засечки на {radial_with_ticks} размерах радиуса/диаметра/угла"], None))
        details.append(f"Объектов DIMENSION в чертеже: {dim_count}.")
        details.append(
            "Для стройчертежей СПДС линейный размер ограничивают засечками 2–4 мм, "
            "а радиус, диаметр и угол — стрелками. Уже годная высота текста и выступ выносной не переписываются."
        )

        auto = False
        if ctx.fix and issues:
            _apply_spds_dimstyle(ctx.doc, SPDS_DIMSTYLE, ctx.to_mm)
            details.append(
                f"Создан или обновлён размерный стиль «{SPDS_DIMSTYLE}»: засечки OBLIQUE 2,5 мм, "
                "текст 3,5 мм, DIMEXE=2, DIMEXO=0, текст над размерной линией."
            )
            # Обновляем только используемые проблемные стили.
            for name, _msgs, ds in issues:
                if ds is None:
                    continue
                kinds = style_kinds.get(name, set())
                if kinds & LINEAR_DIMTYPES:
                    try:
                        ds.set_arrows(blk=ARROWS.oblique)
                    except Exception:
                        pass
                    dimasz_now = du_to_mm(float(_get(ds, "dimasz", header, 2.5) or 2.5), ctx.to_mm)
                    if not (DIM_TICK_MIN_MM - 0.2 <= dimasz_now <= DIM_TICK_MAX_MM + 0.2):
                        ds.dxf.dimasz = mm_to_du(2.5, ctx.to_mm)
                dimtxt_now = du_to_mm(float(_get(ds, "dimtxt", header, 2.5) or 2.5), ctx.to_mm)
                if round(dimtxt_now, 2) not in {round(value, 2) for value in DIM_TXT_PREFERRED_MM}:
                    ds.dxf.dimtxt = mm_to_du(3.5, ctx.to_mm)
                dimexe_now = du_to_mm(float(_get(ds, "dimexe", header, 1.25) or 1.25), ctx.to_mm)
                if not (DIM_EXE_MIN_MM - 0.3 <= dimexe_now <= DIM_EXE_MAX_MM + 0.3):
                    ds.dxf.dimexe = mm_to_du(2.0, ctx.to_mm)
                dimexo_now = du_to_mm(float(_get(ds, "dimexo", header, 0.625) or 0.625), ctx.to_mm)
                if dimexo_now < -0.1 or dimexo_now > 3.1:
                    ds.dxf.dimexo = 0
                ds.dxf.dimtad = 1
                ds.dxf.dimtih = 0
                ds.dxf.dimtoh = 0
                try:
                    ds.dxf.dimtxsty = GOST_STYLE_NAME
                except Exception:
                    pass
                details.append(f"Стиль «{name}» приведён к параметрам СПДС там, где значения были вне ГОСТ.")
            restored = _restore_arrows_on_radial(ctx.doc)
            if restored:
                details.append(f"На радиусах, диаметрах и углах оставлены стрелки: {restored}.")
            try:
                ctx.doc.header["$DIMSTYLE"] = SPDS_DIMSTYLE
            except Exception:
                pass
            auto = True

        if dim_count == 0:
            return self.result("info", "Размерных объектов нет — стили шаблона CAD не проверялись.", details)
        if not issues:
            return self.result("ok", "Размерные стили соответствуют проверенным правилам засечек и высот.", details)
        msg = "Размерные стили не соответствуют правилам СПДС для засечек и/или высот текста."
        if auto:
            msg = "Размерные стили приведены к засечкам СПДС и стандартным высотам."
        return self.result("warning", msg, details, auto_fixed=auto, findings_count=len(issues))
