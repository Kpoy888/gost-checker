from __future__ import annotations

import re

from ..constants import SCALES_ENLARGEMENT, SCALES_REDUCTION
from .base import BaseCheck, CheckContext

SCALE_RE = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*:\s*(\d+(?:[.,]\d+)?)(?!\d)")


def _canon(a: str, b: str) -> str:
    def norm(part: str) -> str:
        part = part.replace(",", ".")
        if part.endswith(".0"):
            part = part[:-2]
        return part
    return f"{norm(a)}:{norm(b)}"


def _is_standard(label: str) -> bool:
    return label in SCALES_REDUCTION or label in SCALES_ENLARGEMENT


def _ratio_to_label(model_per_paper: float) -> str | None:
    if model_per_paper <= 0:
        return None
    candidates = []
    for label in (*SCALES_REDUCTION, *SCALES_ENLARGEMENT):
        a, b = label.split(":")
        inv = float(b) / float(a)
        candidates.append((abs(model_per_paper - inv) / max(inv, 1e-9), label))
    candidates.sort()
    if candidates and candidates[0][0] < 0.08:
        return candidates[0][1]
    return None


def nearest_standard_scale(ratio: float) -> tuple[str | None, float]:
    """ratio = «натуральная длина / длина на чертеже» для масштаба 1:N."""
    if ratio <= 0:
        return None, 1.0
    candidates = []
    for label in (*SCALES_REDUCTION, *SCALES_ENLARGEMENT):
        a, b = (float(x) for x in label.split(":"))
        inv = b / a
        candidates.append((abs(inv - ratio) / max(inv, 1e-9), label))
    candidates.sort()
    if candidates and candidates[0][0] < 0.08:
        return candidates[0][1], candidates[0][0]
    return None, candidates[0][0] if candidates else 1.0


def _linear_geom_length(dim) -> float | None:
    pairs = (("defpoint2", "defpoint3"), ("defpoint", "defpoint2"), ("defpoint", "defpoint3"))
    for first, second in pairs:
        try:
            if not (dim.dxf.hasattr(first) and dim.dxf.hasattr(second)):
                continue
            a, b = dim.dxf.get(first), dim.dxf.get(second)
            dist = ((float(a.x) - float(b.x)) ** 2 + (float(a.y) - float(b.y)) ** 2) ** 0.5
            if dist > 1e-6:
                return dist
        except Exception:
            continue
    return None


def infer_from_dimensions(doc, to_mm: float) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    layouts = [doc.modelspace(), *list(doc.layouts)]
    seen_ids = set()
    for layout in layouts:
        ident = id(layout)
        if ident in seen_ids:
            continue
        seen_ids.add(ident)
        for dim in layout.query("DIMENSION"):
            geom = _linear_geom_length(dim)
            measured = None
            try:
                measured = float(dim.get_measurement())
            except Exception:
                try:
                    measured = float(dim.dxf.get("actual_measurement"))
                except Exception:
                    measured = None
            if not geom or geom < 1e-6 or not measured:
                continue
            geom_mm = geom * to_mm
            candidates = [abs(measured) / geom]
            if geom_mm > 1e-6:
                candidates.append(abs(measured) * 1000.0 / geom_mm)
            picked = None
            for ratio in candidates:
                label, err = nearest_standard_scale(ratio)
                if label:
                    picked = (label, ratio, geom_mm, measured)
                    break
            if picked:
                label, ratio, geom_mm, measured = picked
                found.append(
                    (
                        label,
                        f"размер: геометрия {geom_mm:.1f} мм, измерение {measured:g} → {label}",
                    )
                )
            else:
                ratio = abs(measured) / geom
                found.append(
                    (
                        f"≈ 1:{ratio:.3g}",
                        f"размер: геометрия {geom_mm:.1f} мм, измерение {measured:g} (нет точного совпадения с таблицей ГОСТ 2.302)",
                    )
                )
    return found


def _collect_text(doc) -> list[str]:
    parts: list[str] = []
    layouts = [doc.modelspace(), *list(doc.layouts)]
    seen = set()
    for layout in layouts:
        ident = id(layout)
        if ident in seen:
            continue
        seen.add(ident)
        for entity in layout:
            dtype = entity.dxftype()
            if dtype == "TEXT":
                parts.append(str(entity.dxf.text or ""))
            elif dtype == "MTEXT":
                try:
                    parts.append(entity.plain_text())
                except Exception:
                    parts.append(str(entity.dxf.text or ""))
            elif dtype == "INSERT":
                for attrib in getattr(entity, "attribs", []):
                    parts.append(str(attrib.dxf.text or ""))
                    parts.append(str(attrib.dxf.tag or ""))
    return parts


class ScalesCheck(BaseCheck):
    id = "scales"
    title = "Масштабы изображений"
    gost = "ГОСТ 2.302-68 (масштабы уменьшения и увеличения)"
    can_autofix = False

    def run(self, ctx: CheckContext):
        details: list[str] = []
        found: list[tuple[str, str]] = []

        header = ctx.doc.header
        for key, title in (("$DIMSCALE", "DIMSCALE"), ("$LTSCALE", "LTSCALE")):
            try:
                value = header.get(key)
            except Exception:
                value = None
            if value:
                details.append(f"{title} = {value}")

        try:
            cannoscale = header.get("$CANNOSCALE")
            if cannoscale:
                found.append((str(cannoscale), "аннотативный масштаб чертежа"))
        except Exception:
            pass

        for layout in ctx.doc.layouts:
            if layout.name.lower() == "model":
                continue
            for vp in layout.query("VIEWPORT"):
                try:
                    if int(getattr(vp.dxf, "id", 0) or 0) == 1:
                        continue
                except Exception:
                    pass
                try:
                    paper_h = float(vp.dxf.height)
                    view_h = float(vp.dxf.view_height)
                    if paper_h > 0:
                        ratio = view_h / paper_h
                        label = _ratio_to_label(ratio) or f"≈ 1:{ratio:.3g}"
                        found.append((label, f"видовой экран на листе «{layout.name}»"))
                        details.append(
                            f"Видовой экран «{layout.name}»: высота на листе {paper_h:.3f}, "
                            f"высота вида {view_h:.3f}, отношение {ratio:.4g} → {label}."
                        )
                except Exception as exc:
                    details.append(f"Не удалось прочитать видовой экран на «{layout.name}»: {exc}")

        blob = "\n".join(_collect_text(ctx.doc))
        for match in SCALE_RE.finditer(blob):
            label = _canon(match.group(1), match.group(2))
            try:
                a, b = (float(x.replace(",", ".")) for x in label.split(":"))
                if a <= 0 or b <= 0 or max(a, b) > 100000:
                    continue
            except Exception:
                continue
            found.append((label, f"текст «{match.group(0)}»"))

        found.extend(infer_from_dimensions(ctx.doc, ctx.to_mm))

        if not found:
            details.append(
                "Масштаб не удалось определить: нет видовых экранов, надписи вида 1:100 "
                "и нет размеров, по которым можно сравнить измерение с геометрией."
            )
            return self.result(
                "info",
                "Масштаб по ГОСТ 2.302 определить не удалось (только отчёт, без исправления).",
                details,
            )

        unique = []
        seen = set()
        for label, src in found:
            key = (label, src)
            if key in seen:
                continue
            seen.add(key)
            unique.append((label, src))

        details.append("Найденные указания и оценки масштаба:")
        for label, src in unique:
            mark = "входит в ГОСТ 2.302" if _is_standard(label) else "нет в таблице ГОСТ 2.302 (или прочитано приближённо)"
            details.append(f"  {label} — {src}; {mark}")

        text_like = [lab for lab, src in unique if src.startswith("текст") or "видовой" in src or "аннотативн" in src]
        dim_like = [lab for lab, src in unique if src.startswith("размер")]
        std_text = [lab for lab in text_like if _is_standard(lab)]
        std_dim = [lab for lab in dim_like if _is_standard(lab)]
        if std_text and std_dim and set(std_text) != set(std_dim):
            details.append(
                "Указанный на чертеже масштаб не совпадает с тем, что получается из сравнения "
                "размерного числа с длиной геометрии. Так бывает, если чертёж выполнен в модели 1:1, "
                "а надпись «М 1:100» относится к будущему виду на листе."
            )
            return self.result(
                "warning",
                "Масштаб в надписи и масштаб, выведенный из размеров, различаются. Исправление не выполняется.",
                details,
                findings_count=1,
            )

        nonstd = [(lab, src) for lab, src in unique if not _is_standard(lab)]
        if nonstd and not any(_is_standard(lab) for lab, _src in unique):
            return self.result(
                "warning",
                "Часть масштабов не входит в таблицу ГОСТ 2.302 или прочитана только приближённо. Исправление не выполняется.",
                details,
                findings_count=len(nonstd),
            )
        return self.result("ok", "Найденные масштабы входят в таблицу ГОСТ 2.302.", details)
