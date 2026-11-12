from __future__ import annotations

from typing import Any

# Коды $INSUNITS (DXF) -> множитель «единица чертежа в миллиметры».
# Таблица AutoCAD: 7 — километры (1e6 мм, не 1e7), 14 — дециметры (100 мм, не 10).
INSUNITS_TO_MM = {
    0: 1.0,        # безразмерные: для студенческих чертежей чаще всего мм
    1: 25.4,       # дюймы
    2: 304.8,      # футы
    3: 1609344.0,  # мили
    4: 1.0,        # миллиметры
    5: 10.0,       # сантиметры
    6: 1000.0,     # метры
    7: 1_000_000.0,  # километры
    8: 25.4e-6,    # микродюймы
    9: 25.4e-3,    # милы
    10: 914.4,     # ярды
    11: 1e-7,      # ангстремы
    12: 1e-6,      # нанометры
    13: 1e-3,      # микроны
    14: 100.0,     # дециметры
    15: 10_000.0,  # декаметры
    16: 100_000.0,  # гектометры
    17: 1e12,       # гигаметры
    18: 1.495978707e14,  # астрономическая единица
    19: 9.4607304725808e18,  # световой год
    20: 3.085677581e19,  # парсек
}


def resolve_insunits(doc: Any) -> tuple[float, str | None]:
    """Множитель в миллиметры и предупреждение, если код единиц неизвестен.

    Отсутствующий $INSUNITS по-прежнему считается миллиметрами: так устроены
    старые DXF без этой переменной.
    """
    try:
        raw = doc.header.get("$INSUNITS", None)
    except Exception:
        return 1.0, "Не удалось прочитать $INSUNITS, единицы приняты за миллиметры."
    if raw is None or raw == "":
        return 1.0, None
    try:
        code = int(raw)
    except (TypeError, ValueError):
        return 1.0, f"Код единиц $INSUNITS={raw!r} не является числом. Размеры приняты за миллиметры."
    factor = INSUNITS_TO_MM.get(code)
    if factor is None:
        return 1.0, (
            f"Код единиц $INSUNITS={code} не входит в таблицу DXF (0–20). "
            "Размеры приняты за миллиметры; проверьте единицы в CAD."
        )
    return factor, None


def doc_to_mm(doc: Any) -> float:
    """Сколько миллиметров в одной единице чертежа."""
    factor, _warning = resolve_insunits(doc)
    return factor


def mm_to_du(mm: float, to_mm: float) -> float:
    if not to_mm:
        return mm
    return mm / to_mm


def du_to_mm(value: float, to_mm: float) -> float:
    return value * to_mm
