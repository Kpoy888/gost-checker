from __future__ import annotations

from typing import Any

# Коды $INSUNITS (DXF) -> множитель «единица чертежа в миллиметры».
INSUNITS_TO_MM = {
    0: 1.0,       # безразмерные: для студенческих чертежей чаще всего мм
    1: 25.4,      # дюймы
    2: 304.8,     # футы
    3: 1609344.0, # мили
    4: 1.0,       # миллиметры
    5: 10.0,      # сантиметры
    6: 1000.0,    # метры
    7: 1e7,       # километры
    8: 25.4e-6,   # микродюймы
    9: 25.4e-3,   # милы
    10: 914.4,    # ярды
    11: 1e-7,     # ангстремы
    12: 1e-6,     # нанометры
    13: 1e-3,     # микроны
    14: 10.0,     # дециметры (редко)
}


def doc_to_mm(doc: Any) -> float:
    """Сколько миллиметров в одной единице чертежа."""
    try:
        code = int(doc.header.get("$INSUNITS", 4))
    except Exception:
        code = 4
    return INSUNITS_TO_MM.get(code, 1.0)


def mm_to_du(mm: float, to_mm: float) -> float:
    if not to_mm:
        return mm
    return mm / to_mm


def du_to_mm(value: float, to_mm: float) -> float:
    return value * to_mm
