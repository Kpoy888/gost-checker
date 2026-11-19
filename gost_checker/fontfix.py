"""Отдельный режим правки шрифта.

Ряд высот — ГОСТ 2.304-81, тот же, что в проверке шрифтов.
По умолчанию высота округляется вверх. Ближайшее значение — по запросу.
PDF и сканы только проверяются.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .checks.fonts import is_explicit_non_gost, is_gost_font, nearest_height
from .constants import FONT_HEIGHTS_MM, GOST_FONT_FILE, RASTER_EXTS
from .units import du_to_mm, mm_to_du, resolve_insunits

OVERFLOW_NOTE = "Порог переполнения задаёт вызывающий код. Номер пункта ГОСТ для этого порога не сверен."
_CODE_RE = re.compile(r"\\[A-Za-z][^;]*;")


def strip_codes(raw: str) -> str:
    """Убирает inline-коды MTEXT и оставляет сам текст."""
    text = raw or ""
    previous = None
    while previous != text:
        previous = text
        text = _CODE_RE.sub("", text)
    text = text.replace("{", "").replace("}", "")
    text = text.replace("\\P", "\n").replace("\\~", " ")
    return text.strip()


def round_up_height(height_mm: float) -> float:
    for item in FONT_HEIGHTS_MM:
        if height_mm <= item + 0.05:
            return float(item)
    return float(FONT_HEIGHTS_MM[-1])


def _target(height_mm: float, mode: str) -> float:
    if mode == "nearest":
        return float(nearest_height(height_mm))
    return round_up_height(height_mm)


def _plain_width_mm(text: str, height_mm: float) -> float:
    lines = (text or "").splitlines() or [""]
    longest = max(len(line) for line in lines)
    return longest * height_mm * 0.7


def _text_items(doc):
    for entity in doc.modelspace():
        if entity.dxftype() in {"TEXT", "MTEXT", "ATTRIB"}:
            yield entity


def _height_du(entity) -> float:
    if entity.dxftype() == "MTEXT":
        return float(entity.dxf.char_height or 0)
    return float(entity.dxf.height or 0)


def _set_height(entity, value: float) -> None:
    if entity.dxftype() == "MTEXT":
        entity.dxf.char_height = value
    else:
        entity.dxf.height = value


def _alignment(entity) -> dict:
    data = {}
    for name in ("insert", "align_point", "attachment_point", "halign", "valign"):
        if entity.dxf.hasattr(name):
            value = entity.dxf.get(name)
            if hasattr(value, "x"):
                data[name] = (float(value.x), float(value.y))
            else:
                data[name] = value
    return data


def fix_fonts(src: Path, dest: Path | None = None, *, mode: str = "up", limit_mm: float = 185.0, dry_run: bool = True) -> dict:
    src = Path(src)
    suffix = src.suffix.lower()
    if suffix == ".pdf" or suffix in RASTER_EXTS:
        return _check_only(src, limit_mm)
    import ezdxf

    before = hashlib.sha256(src.read_bytes()).hexdigest()
    doc = ezdxf.readfile(str(src))
    to_mm, _warning = resolve_insunits(doc)
    changes = []
    overflows = []
    for entity in _text_items(doc):
        raw = str(entity.dxf.text or "") if entity.dxftype() != "ATTRIB" else str(entity.dxf.text or "")
        plain = strip_codes(raw)
        height = du_to_mm(_height_du(entity), to_mm)
        if height <= 0:
            continue
        target = _target(height, mode)
        align = _alignment(entity)
        if abs(target - height) > 0.05 or plain != raw:
            changes.append({"was": round(height, 3), "now": target, "text": plain, "alignment": align})
        if _plain_width_mm(plain, target) > limit_mm:
            overflows.append(plain[:80])
        if not dry_run:
            entity.dxf.text = plain
            _set_height(entity, mm_to_du(target, to_mm))
            _restore_alignment(entity, align)
    styles = []
    if not dry_run:
        for style in doc.styles:
            font = str(style.dxf.get("font") or "")
            name = str(style.dxf.name)
            if is_gost_font(name) or is_gost_font(font):
                continue
            if is_explicit_non_gost(name) or is_explicit_non_gost(font) or name.lower() == "standard":
                style.dxf.font = GOST_FONT_FILE
                styles.append(name)
    written = False
    if not dry_run and dest is not None:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(str(dest))
        written = True
    return {
        "mode": mode,
        "written": written,
        "changes": changes,
        "overflows": overflows,
        "styles": styles,
        "note": OVERFLOW_NOTE,
        "original_unchanged": hashlib.sha256(src.read_bytes()).hexdigest() == before,
        "check_only": False,
    }


def _restore_alignment(entity, saved: dict) -> None:
    for name, value in saved.items():
        if name in {"insert", "align_point"} and isinstance(value, tuple):
            entity.dxf.set(name, value)
        elif entity.dxf.hasattr(name):
            entity.dxf.set(name, value)


def _check_only(src: Path, limit_mm: float) -> dict:
    warnings = []
    if src.suffix.lower() == ".pdf":
        import fitz

        doc = fitz.open(str(src))
        try:
            for page in doc:
                info = page.get_text("dict")
                for block in info.get("blocks") or []:
                    for line in block.get("lines") or []:
                        for span in line.get("spans") or []:
                            mm = float(span.get("size") or 0) * 25.4 / 72.0
                            target = round_up_height(mm) if mm else 0
                            text = str(span.get("text") or "")
                            if mm and abs(target - mm) > 0.3:
                                warnings.append(f"{text[:40]}: {mm:.2f} мм, вверх было бы {target:g}")
                            if _plain_width_mm(text, mm or target) > limit_mm:
                                warnings.append(f"переполнение: {text[:40]}")
        finally:
            doc.close()
    return {
        "mode": "check-only",
        "written": False,
        "changes": [],
        "overflows": warnings,
        "styles": [],
        "note": "PDF и сканы только проверяются, файл не изменяется. " + OVERFLOW_NOTE,
        "original_unchanged": True,
        "check_only": True,
        "warnings": warnings,
    }


def fonts_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check fonts")
    parser.add_argument("input")
    parser.add_argument("--out")
    parser.add_argument("--nearest", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit-mm", type=float, default=185.0)
    args = parser.parse_args(argv)
    result = fix_fonts(
        Path(args.input),
        Path(args.out) if args.out else None,
        mode="nearest" if args.nearest else "up",
        limit_mm=args.limit_mm,
        dry_run=not args.apply,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0
