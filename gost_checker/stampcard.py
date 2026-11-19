"""Карточка проекта и заполнение основной надписи.

Форма 3 — 185×55 мм, первый лист чертежей. Форма 6 — 185×15 мм, последующие листы.
Формы 4 и 5 вставляются по явному виду документа; сетка граф — практическая реконструкция, не калька рисунка ГОСТ.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .titleblock import insert_form3, normalize_title_meta

DATE_RE = re.compile(r"^\d{2}\.\d{2}$")


def _date_ok(value: str) -> bool:
    if not DATE_RE.match(value):
        return False
    month = int(value[:2])
    return 1 <= month <= 12
STAGES = {"П", "Р", "И"}
REQUIRED = ("обозначение", "наименование", "стадия", "лист", "листов")
FORM_SIZE = {"3": (185.0, 55.0), "4": (185.0, 55.0), "5": (185.0, 40.0), "6": (185.0, 15.0)}


def load_card(payload) -> dict:
    if isinstance(payload, (str, Path)):
        text = Path(payload).read_text(encoding="utf-8")
        payload = json.loads(text)
    card = normalize_title_meta(payload or {})
    extra = payload or {}
    for key in ("марка", "гип", "изменение", "дата", "форма"):
        if extra.get(key) is not None:
            card[key] = str(extra.get(key))
    return card


def empty_fields(card: dict, required=REQUIRED) -> list[str]:
    return [name for name in required if not str(card.get(name) or "").strip()]


def validate_set(cards: list[dict]) -> list[str]:
    notes = []
    ciphers = {str(card.get("обозначение") or "") for card in cards}
    ciphers.discard("")
    if len(ciphers) > 1:
        notes.append("Шифр различается между листами: " + ", ".join(sorted(ciphers)))
    numbers = []
    for card in cards:
        raw = str(card.get("лист") or "").strip()
        if raw.isdigit():
            numbers.append(int(raw))
        stage = str(card.get("стадия") or "").strip()
        if stage and stage not in STAGES:
            notes.append(f"Недопустимая стадия «{stage}».")
        date = str(card.get("дата") or "").strip()
        if date and not _date_ok(date):
            notes.append(f"Дата «{date}» не в формате ММ.ГГ.")
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        notes.append("Номера листов имеют пропуски или дубли: " + ", ".join(str(n) for n in numbers))
    expected = str(len(cards))
    for card in cards:
        stated = str(card.get("листов") or "").strip()
        if stated and stated != expected:
            notes.append(f"Поле «Листов» равно {stated}, в комплекте {expected}.")
            break
    return notes


def _insert_small(doc, insert, form: str, card: dict, layout=None):
    width, height = FORM_SIZE[form]
    name = f"SPDS_F{form}"
    if name not in doc.blocks:
        block = doc.blocks.new(name)
        block.add_lwpolyline([(0, 0), (width, 0), (width, height), (0, height)], close=True)
        block.add_attdef("OBOZN", insert=(2, height - 6), height=2.5)
        block.add_attdef("LIST", insert=(width - 30, 2), height=2.5)
        block.add_attdef("LISTOV", insert=(width - 15, 2), height=2.5)
    target = layout if layout is not None else doc.modelspace()
    ref = target.add_blockref(name, insert=insert, dxfattribs={"layer": "SPDS_STAMP"})
    ref.add_auto_attribs({
        "OBOZN": card.get("обозначение", ""),
        "LIST": card.get("лист", ""),
        "LISTOV": card.get("листов", ""),
    })
    return ref


def ensure_stamp(doc, card: dict, *, first: bool, layout=None):
    form = str(card.get("форма") or ("3" if first else "6"))
    target = layout if layout is not None else doc.modelspace()
    has_stamp = any(entity.dxftype() == "INSERT" and "SPDS_F" in str(entity.dxf.name or "").upper() for entity in target)
    if has_stamp:
        return fill_attribs(doc, card, layout=layout)
    if form == "3":
        return insert_form3(doc, (20, 5), card, layout=layout)
    return _insert_small(doc, (20, 5), form, card, layout=layout)


def fill_attribs(doc, card: dict, layout=None) -> list[str]:
    warnings = []
    target = layout if layout is not None else doc.modelspace()
    mapping = {
        "OBOZN": "обозначение",
        "NAIMEN": "наименование",
        "STADIYA": "стадия",
        "LIST": "лист",
        "LISTOV": "листов",
        "ORG": "организация",
    }
    for entity in target.query("INSERT"):
        if "SPDS_F" not in str(entity.dxf.name or "").upper() and "STAMP" not in str(entity.dxf.layer or "").upper():
            continue
        for attrib in entity.attribs:
            key = mapping.get(str(attrib.dxf.tag or "").upper())
            if not key:
                continue
            value = str(card.get(key) or "")
            height = float(attrib.dxf.height or 2.5)
            if len(value) * 0.6 * height > 80 and value:
                warnings.append(f"Поле {key} длинное и может не влезть в графу.")
                value = value[:40]
            attrib.dxf.text = value
    return warnings


def apply_card(src: Path, dest: Path, card: dict, *, first: bool = True, draft: bool = False) -> dict:
    import ezdxf

    doc = ezdxf.readfile(str(src))
    if draft:
        card = dict(card)
        card["наименование"] = ("ЧЕРНОВИК " + str(card.get("наименование") or "")).strip()
    warnings = []
    missing_before = empty_fields(card)
    if not any(entity.dxftype() == "INSERT" and "SPDS" in str(entity.dxf.name or "").upper() for entity in doc.modelspace()):
        ensure_stamp(doc, card, first=first)
    else:
        warnings.extend(fill_attribs(doc, card))
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(dest))
    try:
        doc.saveas(str(dest.with_suffix(".cp1251.dxf")), encoding="cp1251")
    except Exception:
        pass
    auditor = ezdxf.readfile(str(dest)).audit()
    return {
        "warnings": warnings,
        "empty": missing_before,
        "blocked": bool(missing_before) and not draft,
        "audit_errors": int(bool(auditor.has_errors)),
    }


def stamp_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check stamp")
    parser.add_argument("input")
    parser.add_argument("--card", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args(argv)
    card = load_card(args.card)
    result = apply_card(Path(args.input), Path(args.out), card, draft=args.draft)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["blocked"]:
        return 2
    return 0
