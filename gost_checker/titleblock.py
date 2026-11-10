"""Вставка основной надписи формы 3 (185 x 55 мм) по ГОСТ Р 21.101-2020.

Внутренняя сетка граф — практическая реконструкция характерных размеров формы 3
(185×55, графы обозначения, наименований, стадии, листа, организации и подписей).
Это не сертифицированная калька официального рисунка приложения Ж.
"""

from __future__ import annotations

from typing import Any, Optional

from .constants import (
    GOST_FONT_FILE,
    GOST_STYLE_NAME,
    TITLE_BLOCK_H_MM,
    TITLE_BLOCK_NAME,
    TITLE_BLOCK_W_MM,
)
from .units import mm_to_du


def default_title_meta() -> dict[str, str]:
    return {
        "обозначение": "",
        "объект": "",
        "здание": "",
        "наименование": "",
        "стадия": "",
        "лист": "",
        "листов": "",
        "разраб": "",
        "пров": "",
        "н_контр": "",
        "утв": "",
        "организация": "",
    }


def normalize_title_meta(raw: Optional[dict]) -> dict[str, str]:
    meta = default_title_meta()
    if not raw:
        return meta
    aliases = {
        "designation": "обозначение",
        "ozn": "обозначение",
        "name": "наименование",
        "title": "наименование",
        "изображение": "наименование",
        "object": "объект",
        "object_name": "объект",
        "building": "здание",
        "stage": "стадия",
        "sheet": "лист",
        "sheets": "листов",
        "developed": "разраб",
        "developer": "разраб",
        "checked": "пров",
        "ncontrol": "н_контр",
        "normocontrol": "н_контр",
        "approved": "утв",
        "org": "организация",
        "organization": "организация",
        "organisation": "организация",
    }
    for key, value in raw.items():
        if value is None:
            continue
        lk = str(key).strip().lower()
        dest = aliases.get(lk, str(key).strip().lower())
        dest = {
            "н.контр": "н_контр",
            "н контр": "н_контр",
            "наименование изделий": "наименование",
        }.get(dest, dest)
        if dest in meta:
            meta[dest] = str(value)
    return meta


def ensure_gost_style(doc: Any) -> str:
    names = {s.dxf.name for s in doc.styles}
    if GOST_STYLE_NAME not in names:
        doc.styles.add(GOST_STYLE_NAME, font=GOST_FONT_FILE)
    return GOST_STYLE_NAME


def _line(blk, p1, p2, lw: int) -> None:
    blk.add_line(p1, p2, dxfattribs={"layer": "0", "lineweight": lw})


def _text(blk, text: str, insert, height: float, style: str, align: str = "LEFT") -> None:
    entity = blk.add_text(
        text,
        height=height,
        dxfattribs={"style": style, "layer": "0", "insert": insert},
    )
    if align != "LEFT":
        try:
            entity.set_placement(insert, align=align)
        except Exception:
            entity.dxf.insert = insert


def build_form3_block(doc: Any, to_mm: float = 1.0) -> str:
    """Создать или пересоздать блок основной надписи в единицах чертежа."""
    ensure_gost_style(doc)
    style = GOST_STYLE_NAME
    s = mm_to_du(1.0, to_mm)  # масштаб: 1 мм -> единицы чертежа
    name = TITLE_BLOCK_NAME
    if name in doc.blocks:
        try:
            doc.blocks.delete_block(name, safe=False)
        except Exception:
            name = TITLE_BLOCK_NAME + "_NEW"
            if name in doc.blocks:
                doc.blocks.delete_block(name, safe=False)

    blk = doc.blocks.new(name)
    W, H = TITLE_BLOCK_W_MM * s, TITLE_BLOCK_H_MM * s

    def X(*mm):
        return tuple(v * s for v in mm)

    # Внешний контур — основная линия.
    blk.add_lwpolyline([(0, 0), (W, 0), (W, H), (0, H)], close=True, dxfattribs={"layer": "0", "lineweight": 70})

    # Основные вертикали: 65 (левый блок подписей/изменений), 135 (правая колонка 50 мм)
    for x_mm in (65, 135):
        _line(blk, (x_mm * s, 0), (x_mm * s, H), 70)

    # Горизонталь, отделяющая таблицу изменений (сверху 25 мм) от подписей (снизу 30 мм)
    _line(blk, (0, 30 * s), (65 * s, 30 * s), 70)
    # Горизонтали правой части
    _line(blk, (65 * s, 40 * s), (W, 40 * s), 50)  # под обозначением / наименованием объекта
    _line(blk, (65 * s, 25 * s), (135 * s, 25 * s), 25)  # между зданием и изображениями
    _line(blk, (65 * s, 10 * s), (135 * s, 10 * s), 25)
    _line(blk, (135 * s, 20 * s), (W, 20 * s), 50)  # верх организации / низ стадии-листов

    # Таблица изменений (графы 14–19), 5 строк по 5 мм, колонки 10 10 10 15 10 10 = 65
    for y_mm in (35, 40, 45, 50):
        _line(blk, (0, y_mm * s), (65 * s, y_mm * s), 25)
    for x_mm in (10, 20, 30, 45, 55):
        _line(blk, (x_mm * s, 30 * s), (x_mm * s, H), 25)

    # Подписи (графы 10–13): 6 строк по 5 мм. Колонки: должность 20 / фамилия 20 / подп. 15 / дата 10
    for y_mm in (5, 10, 15, 20, 25):
        _line(blk, (0, y_mm * s), (65 * s, y_mm * s), 25)
    for x_mm in (20, 40, 55):
        _line(blk, (x_mm * s, 0), (x_mm * s, 30 * s), 25)

    # Стадия / Лист / Листов: 15 + 15 + 20 = 50
    _line(blk, (150 * s, 20 * s), (150 * s, 40 * s), 25)
    _line(blk, (165 * s, 20 * s), (165 * s, 40 * s), 25)
    _line(blk, (135 * s, 35 * s), (W, 35 * s), 25)  # подписи «Стадия» и т.п.

    h18 = 1.8 * s

    headers = [
        (0.4, 31.3, "Изм."),
        (10.3, 31.3, "Кол."),
        (20.3, 31.3, "Лист"),
        (30.3, 31.3, "N док."),
        (45.3, 31.3, "Подп."),
        (55.3, 31.3, "Дата"),
    ]
    for x, y, t in headers:
        _text(blk, t, (x * s, y * s), h18, style)

    jobs = [
        (0.6, 26.2, "Разраб."),
        (0.6, 21.2, "Пров."),
        (0.6, 16.2, ""),
        (0.6, 11.2, ""),
        (0.6, 6.2, "Н.контр."),
        (0.6, 1.2, "Утв."),
    ]
    for x, y, t in jobs:
        if t:
            _text(blk, t, (x * s, y * s), h18, style)

    _text(blk, "Стадия", (136.2 * s, 36.2 * s), h18, style)
    _text(blk, "Лист", (151.2 * s, 36.2 * s), h18, style)
    _text(blk, "Листов", (166.2 * s, 36.2 * s), h18, style)

    # Атрибуты (заполняемые поля)
    attribs = [
        ("OBOZN", (67.0, 43.0), 3.5, ""),
        ("OBEKT", (137.0, 43.0), 2.5, ""),
        ("ZDANIE", (67.0, 28.5), 2.5, ""),
        ("NAIMEN", (67.0, 14.5), 2.5, ""),
        ("STADIYA", (136.5, 26.0), 3.5, ""),
        ("LIST", (151.5, 26.0), 3.5, ""),
        ("LISTOV", (166.5, 26.0), 3.5, ""),
        ("ORG", (137.0, 6.5), 2.5, ""),
        ("RAZRAB", (21.0, 26.2), 2.5, ""),
        ("PROV", (21.0, 21.2), 2.5, ""),
        ("NKONTR", (21.0, 6.2), 2.5, ""),
        ("UTV", (21.0, 1.2), 2.5, ""),
    ]
    for tag, (x, y), height, default in attribs:
        blk.add_attdef(
            tag,
            insert=(x * s, y * s),
            text=default,
            height=height * s,
            dxfattribs={"style": style, "layer": "0"},
        )
    return name


def insert_form3(doc: Any, insert_xy: tuple[float, float], meta: Optional[dict], to_mm: float = 1.0, layout=None) -> Any:
    name = build_form3_block(doc, to_mm=to_mm)
    meta = normalize_title_meta(meta)
    target = layout if layout is not None else doc.modelspace()
    ref = target.add_blockref(
        name,
        insert=insert_xy,
        dxfattribs={"layer": "SPDS_STAMP"},
    )
    values = {
        "OBOZN": meta.get("обозначение", ""),
        "OBEKT": meta.get("объект", ""),
        "ZDANIE": meta.get("здание", ""),
        "NAIMEN": meta.get("наименование", ""),
        "STADIYA": meta.get("стадия", ""),
        "LIST": meta.get("лист", ""),
        "LISTOV": meta.get("листов", ""),
        "ORG": meta.get("организация", ""),
        "RAZRAB": meta.get("разраб", ""),
        "PROV": meta.get("пров", ""),
        "NKONTR": meta.get("н_контр", ""),
        "UTV": meta.get("утв", ""),
    }
    ref.add_auto_attribs(values)
    return ref


def title_block_insert_point(inner_xmin: float, inner_ymin: float, inner_xmax: float, to_mm: float) -> tuple[float, float]:
    """Нижний правый угол внутренней рамки минус ширина штампа."""
    return (inner_xmax - mm_to_du(TITLE_BLOCK_W_MM, to_mm), inner_ymin)
