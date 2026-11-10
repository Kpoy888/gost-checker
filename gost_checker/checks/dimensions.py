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


def _apply_spds_dimstyle(doc, name: str = SPDS_DIMSTYLE):
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
    ds.dxf.dimasz = 2.5
    ds.dxf.dimtxt = 3.5
    ds.dxf.dimexe = 2.0
    ds.dxf.dimexo = 0
    ds.dxf.dimgap = 1.0
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
        for layout in [ctx.doc.modelspace(), *list(ctx.doc.layouts)]:
            for entity in layout.query("DIMENSION"):
                dim_count += 1
                try:
                    used.add(str(entity.dxf.dimstyle))
                except Exception:
                    pass
        if dim_count == 0:
            details.append("Размерных объектов нет — готовые стили шаблона CAD не проверялись.")
        else:
            details.append("Проверяются размерные стили, которыми проставлены размеры: " + ", ".join(sorted(used)) + ".")

        for ds in ctx.doc.dimstyles:
            name = ds.dxf.name
            if name not in used:
                continue
            arrows_ok, arrow_desc = _arrow_ok(ds, header)
            dimtxt = float(_get(ds, "dimtxt", header, 2.5) or 2.5)
            dimasz = float(_get(ds, "dimasz", header, 2.5) or 2.5)
            dimexe = float(_get(ds, "dimexe", header, 1.25) or 1.25)
            dimexo = float(_get(ds, "dimexo", header, 0.625) or 0.625)
            style_issues = []
            if not arrows_ok:
                style_issues.append(arrow_desc)
            else:
                if not (DIM_TICK_MIN_MM - 0.2 <= dimasz <= DIM_TICK_MAX_MM + 0.2) and float(_get(ds, "dimtsz", header, 0) or 0) == 0:
                    style_issues.append(f"длина засечки DIMASZ={dimasz:g} мм (ожидается 2–4 мм)")
            if round(dimtxt, 2) not in {round(x, 2) for x in DIM_TXT_PREFERRED_MM + (1.8, 7.0)}:
                style_issues.append(f"высота размерного текста {dimtxt:g} мм (обычно 2,5 или 3,5 мм)")
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

        details.append(f"Объектов DIMENSION в чертеже: {dim_count}.")
        details.append(
            "Для стройчертежей СПДС размерную линию ограничивают засечками, а не стрелками "
            "(стрелки оставляют у радиусов, диаметров и углов). Инструмент правит размерный стиль, "
            "а не каждую выносную линию по отдельности."
        )

        auto = False
        if ctx.fix and issues:
            _apply_spds_dimstyle(ctx.doc, SPDS_DIMSTYLE)
            details.append(
                f"Создан или обновлён размерный стиль «{SPDS_DIMSTYLE}»: засечки OBLIQUE 2,5 мм, "
                "текст 3,5 мм, DIMEXE=2, DIMEXO=0, текст над размерной линией."
            )
            # Обновляем только используемые проблемные стили.
            for name, _msgs, ds in issues:
                try:
                    ds.set_arrows(blk=ARROWS.oblique)
                except Exception:
                    pass
                ds.dxf.dimasz = 2.5
                ds.dxf.dimtxt = 3.5
                ds.dxf.dimexe = 2.0
                ds.dxf.dimexo = 0
                ds.dxf.dimtad = 1
                ds.dxf.dimtih = 0
                ds.dxf.dimtoh = 0
                try:
                    ds.dxf.dimtxsty = GOST_STYLE_NAME
                except Exception:
                    pass
                details.append(f"Стиль «{name}» приведён к параметрам СПДС (засечки, высоты, отступы).")
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
