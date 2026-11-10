"""Проверка типов и толщин линий по фактической геометрии (ГОСТ 2.303-68)."""

from __future__ import annotations

from ..line_classify import classify_dxf, details_from_summary
from .base import BaseCheck, CheckContext


class LinesCheck(BaseCheck):
    id = "lines"
    title = "Распознавание линий"
    gost = "ГОСТ 2.303-68 (основная сплошная S, тонкая S/3…S/2, штриховая 2–8/1–2 мм, штрихпунктирная 5–30 мм)"
    can_autofix = False

    def run(self, ctx: CheckContext):
        try:
            summary = classify_dxf(ctx.doc, ctx.to_mm)
        except Exception as exc:
            return self.result(
                "info",
                f"Линии распознать не удалось: {exc}",
                [str(exc)],
            )
        details = details_from_summary(summary)
        ctx.extras["lines"] = {k: v for k, v in summary.items() if k != "items"}
        ctx.extras["line_items"] = summary.get("items") or []
        extra = {
            "s_main_mm": summary.get("s_main_mm"),
            "kinds": summary.get("kinds") or [],
            "total": summary.get("total", 0),
            "issue_count": summary.get("issue_count", 0),
        }
        findings = int(summary.get("issue_count") or 0)
        if summary.get("total", 0) == 0:
            return self.result(
                "info",
                "Графических линий на чертеже не найдено.",
                details,
                extra=extra,
            )
        if findings:
            return self.result(
                "warning",
                f"Линии классифицированы; замечаний к начертанию: {findings}.",
                details,
                findings_count=findings,
                extra=extra,
            )
        return self.result(
            "ok",
            f"Распознаны линии {len(summary.get('kinds') or [])} типов; отношения толщин и шаблоны в допустимых пределах.",
            details,
            extra=extra,
        )
