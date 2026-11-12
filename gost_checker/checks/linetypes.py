from __future__ import annotations

from ..constants import (
    ALLOWED_LINETYPES,
    MAIN_LINE_S_MAX_MM,
    MAIN_LINE_S_MIN_MM,
    PREFERRED_LINEWEIGHTS_MM,
    STANDARD_LAYERS,
)
from ..units import du_to_mm
from .base import BaseCheck, CheckContext

# ГОСТ 2.303-68: центровые штрихпунктирные заменяют сплошной тонкой, если размер фигуры меньше 12 мм.
SMALL_CENTER_DIAMETER_MM = 12.0


def _fix_small_center_marks(ctx: CheckContext) -> list[str]:
    notes: list[str] = []
    seen = set()
    for layout in [ctx.doc.modelspace(), *list(ctx.doc.layouts)]:
        if id(layout) in seen:
            continue
        seen.add(id(layout))
        try:
            circles = list(layout.query("CIRCLE"))
        except Exception:
            continue
        for entity in circles:
            try:
                diameter = du_to_mm(float(entity.dxf.radius) * 2.0, ctx.to_mm)
            except Exception:
                continue
            if diameter <= 0 or diameter >= SMALL_CENTER_DIAMETER_MM - 0.05:
                continue
            linetype = _entity_linetype(entity, ctx.doc)
            if linetype not in AXIS_LINETYPES and "CENTER" not in linetype and "DASHDOT" not in linetype:
                continue
            notes.append(f"окружность ⌀{diameter:.1f} мм, тип «{linetype}»")
            if ctx.fix:
                try:
                    entity.dxf.linetype = "Continuous"
                except Exception:
                    continue
    return notes


def nearest_lineweight_mm(value_mm: float) -> float:
    # толще максимальной основной S (1,4 мм) приводим к S, а не к 2,0 мм ряда
    if value_mm > MAIN_LINE_S_MAX_MM + 0.05:
        return MAIN_LINE_S_MAX_MM
    return min(PREFERRED_LINEWEIGHTS_MM, key=lambda item: abs(item - value_mm))


def lw_to_mm(code: int) -> float | None:
    # DXF: сотые доли мм. Особые: -1 ByLayer, -2 ByBlock, -3 Default, 0 Default.
    if code is None or int(code) <= 0:
        return None
    return int(code) / 100.0


def mm_to_lw(value_mm: float) -> int:
    return int(round(value_mm * 100))


PROTECTED_ASSIGN = {"SPDS_FRAME", "SPDS_STAMP", "DEFPOINTS"}
AXIS_LINETYPES = {"CENTER", "CENTER2", "CENTERX2", "DASHDOT", "DASHDOT2", "DASHDOTX2"}
HIDDEN_LINETYPES = {"DASHED", "DASHED2", "DASHEDX2", "HIDDEN", "HIDDEN2", "HIDDENX2"}


def _entity_linetype(entity, doc) -> str:
    raw = str(entity.dxf.linetype or "BYLAYER").upper()
    if raw in {"BYLAYER", "BYBLOCK"}:
        try:
            layer = doc.layers.get(entity.dxf.layer)
            return str(layer.dxf.linetype or "Continuous").upper()
        except Exception:
            return "CONTINUOUS"
    return raw


def _entity_lw_mm(entity, doc) -> float | None:
    try:
        code = int(entity.dxf.lineweight) if entity.dxf.hasattr("lineweight") else -1
    except Exception:
        code = -1
    value = lw_to_mm(code)
    if value is not None:
        return value
    try:
        layer = doc.layers.get(entity.dxf.layer)
        return lw_to_mm(int(layer.dxf.lineweight))
    except Exception:
        return None


def suggest_spds_layer(entity, doc) -> str | None:
    dtype = entity.dxftype()
    layer = str(entity.dxf.layer or "")
    if layer.upper() in PROTECTED_ASSIGN or layer.startswith("SPDS_"):
        return None
    if dtype in {"TEXT", "MTEXT"}:
        return "SPDS_TEXT"
    if dtype == "DIMENSION":
        return "SPDS_DIM"
    if dtype in {"INSERT", "ATTDEF", "ATTRIB", "VIEWPORT"}:
        return None
    linetype = _entity_linetype(entity, doc)
    if linetype in AXIS_LINETYPES:
        return "SPDS_OS"
    if linetype in HIDDEN_LINETYPES:
        return "SPDS_NEVID"
    weight = _entity_lw_mm(entity, doc)
    if weight is not None and weight >= MAIN_LINE_S_MIN_MM:
        return "SPDS_OSN"
    return "SPDS_TON"


class LineTypesCheck(BaseCheck):
    id = "linetypes"
    title = "Типы и толщины линий"
    gost = "ГОСТ 2.303-68 (сплошная основная S = 0,5…1,4 мм; тонкая S/3…S/2; штриховая; штрихпунктирная)"
    can_autofix = True

    def run(self, ctx: CheckContext):
        details: list[str] = []
        unknown_lt = []
        odd_lw = []
        created = []
        snapped = []

        for layer in ctx.doc.layers:
            name = layer.dxf.name
            if name.upper() in {"DEFPOINTS"}:
                continue
            linetype = str(layer.dxf.linetype or "Continuous")
            if linetype.upper() not in ALLOWED_LINETYPES:
                unknown_lt.append(f"слой «{name}»: тип линии «{linetype}»")
            lw_mm = lw_to_mm(int(layer.dxf.lineweight)) if layer.dxf.hasattr("lineweight") else None
            if lw_mm is not None:
                pref = nearest_lineweight_mm(lw_mm)
                if abs(pref - lw_mm) > 0.02:
                    odd_lw.append((name, lw_mm, pref, layer))

        if unknown_lt:
            details.append("Типы линий вне обычного набора Continuous / DASHED / CENTER / DASHDOT / Hidden / ISO:")
            details.extend(f"  {item}" for item in unknown_lt[:12])
            details.append("Автоматически тип линии слоя не менялся: это может испортить смысл объектов.")
        else:
            details.append("Типы линий на слоях входят в обычный набор, применяемый для сплошной, штриховой и штрихпунктирной.")

        if odd_lw:
            details.append("Толщины линий слоёв, не совпадающие с предпочтительным рядом 0,18…1,4 (2,0) мм:")
            for name, lw_mm, pref, _layer in odd_lw[:12]:
                details.append(f"  слой «{name}»: {lw_mm:.2f} мм → ближайшая {pref:.2f} мм")
        else:
            details.append("Заданные толщины слоёв совпадают с предпочтительным рядом либо оставлены по умолчанию (ByLayer/Default).")

        details.append(
            f"Для основной сплошной ГОСТ 2.303 задаёт S = {MAIN_LINE_S_MIN_MM:.1f}…{MAIN_LINE_S_MAX_MM:.1f} мм; "
            "тонкая — примерно S/3…S/2. Без флага --assign-layers объекты с пользовательских слоёв не переносятся."
        )

        auto = False
        if ctx.fix:
            for key, spec in STANDARD_LAYERS.items():
                if key not in ctx.doc.layers:
                    ctx.doc.layers.add(
                        key,
                        linetype=spec["linetype"],
                        lineweight=spec["lineweight"],
                        color=spec["color"],
                    )
                    created.append(f"{key} ({spec['title']}, {spec['lineweight'] / 100:.2f} мм, {spec['linetype']})")
            for name, lw_mm, pref, layer in odd_lw:
                # безопасная нормализация только толщины слоя
                layer.dxf.lineweight = mm_to_lw(pref)
                snapped.append(f"слой «{name}»: {lw_mm:.2f} → {pref:.2f} мм")
            if created:
                details.append("Созданы стандартные слои (объекты на них не переносились):")
                details.extend(f"  {item}" for item in created)
            if snapped:
                details.append("Нормализованы толщины линий слоёв:")
                details.extend(f"  {item}" for item in snapped)
            entity_snapped = []
            seen_owners = set()
            owners = [ctx.doc.modelspace(), *list(ctx.doc.layouts)]
            try:
                owners.extend(ctx.doc.blocks)
            except Exception:
                pass
            for owner in owners:
                if id(owner) in seen_owners:
                    continue
                seen_owners.add(id(owner))
                try:
                    entities = list(owner)
                except Exception:
                    continue
                for entity in entities:
                    if not entity.dxf.hasattr("lineweight"):
                        continue
                    try:
                        code = int(entity.dxf.lineweight)
                    except Exception:
                        continue
                    value = lw_to_mm(code)
                    if value is None:
                        continue
                    pref = nearest_lineweight_mm(value)
                    if abs(pref - value) <= 0.02:
                        continue
                    entity.dxf.lineweight = mm_to_lw(pref)
                    entity_snapped.append(f"{entity.dxftype()} на слое «{entity.dxf.layer}»: {value:.2f} → {pref:.2f} мм")
            if entity_snapped:
                details.append("Нормализованы явные толщины объектов (не только слоёв):")
                details.extend(f"  {item}" for item in entity_snapped[:20])
                if len(entity_snapped) > 20:
                    details.append(f"  … и ещё {len(entity_snapped) - 20}")
            auto = bool(created or snapped or entity_snapped)

        assigned = []
        proposals = []
        layouts = [ctx.doc.modelspace(), *list(ctx.doc.layouts)]
        seen_layouts = set()
        for layout in layouts:
            ident = id(layout)
            if ident in seen_layouts:
                continue
            seen_layouts.add(ident)
            for entity in layout:
                target = suggest_spds_layer(entity, ctx.doc)
                if not target or entity.dxf.layer == target:
                    continue
                note = f"{entity.dxftype()} со слоя «{entity.dxf.layer}» → {target}"
                proposals.append(note)
                if ctx.assign_layers and ctx.fix:
                    entity.dxf.layer = target
                    assigned.append(note)
        if ctx.assign_layers:
            if ctx.fix and assigned:
                details.append("Объекты перенесены на слои СПДС по эвристике (--assign-layers):")
                details.extend(f"  {item}" for item in assigned[:25])
                if len(assigned) > 25:
                    details.append(f"  … и ещё {len(assigned) - 25}")
                auto = True
            elif proposals:
                details.append("Предложение --assign-layers (нужен также --fix), сейчас не применялось:")
                details.extend(f"  {item}" for item in proposals[:15])
        elif proposals:
            details.append(
                f"Есть {len(proposals)} объектов на пользовательских слоях. "
                "Чтобы перенести их на слои СПДС по типу линии/толщине/тексту, запустите с --assign-layers и --fix."
            )

        small_centers = _fix_small_center_marks(ctx)
        if small_centers:
            details.append(
                "ГОСТ 2.303-68: штрихпунктирную центровую заменяют сплошной тонкой, "
                f"если диаметр меньше {SMALL_CENTER_DIAMETER_MM:.0f} мм. Таких окружностей: {len(small_centers)}."
            )
            if ctx.fix:
                details.append("Тип линии этих окружностей заменён на Continuous.")
                auto = True
            else:
                details.append("Замена выполняется с --fix.")

        findings = len(unknown_lt) + len(odd_lw) + len(small_centers)
        if findings == 0 and not created and not assigned:
            return self.result("ok", "Типы и толщины линий на слоях выглядят допустимо.", details)
        if assigned and findings == 0:
            return self.result(
                "info",
                "Объекты распределены по стандартным слоям СПДС (--assign-layers).",
                details,
                auto_fixed=True,
            )
        if unknown_lt and not odd_lw:
            return self.result(
                "warning",
                "Есть слои с нетипичным типом линии; автоматически они не переназначались.",
                details,
                auto_fixed=auto,
                findings_count=findings,
            )
        if odd_lw:
            msg = "Часть толщин линий слоёв не из предпочтительного ряда ГОСТ 2.303."
            if auto:
                msg = "Толщины линий слоёв нормализованы к ближайшему значению ряда ГОСТ 2.303."
            return self.result("warning", msg, details, auto_fixed=auto, findings_count=findings)
        if small_centers:
            msg = (
                "На окружностях диаметром меньше 12 мм центровые штрихпунктирные линии "
                "по ГОСТ 2.303-68 заменяют сплошной тонкой."
            )
            if ctx.fix:
                msg = "Мелкие центровые окружности переведены на сплошную тонкую линию (ГОСТ 2.303-68)."
            return self.result("warning", msg, details, auto_fixed=bool(ctx.fix and small_centers), findings_count=findings)
        return self.result(
            "info",
            "Добавлены стандартные слои оформления СПДС (без переноса существующих объектов).",
            details,
            auto_fixed=auto,
        )
