"""Справочные величины из ГОСТ, используемые проверками (не полный текст стандартов)."""

from __future__ import annotations

# ГОСТ 2.301-68. Форматы листов: размеры сторон внешней рамки, мм.
SHEET_FORMATS: dict[str, tuple[float, float]] = {
    "A0": (841.0, 1189.0),
    "A1": (594.0, 841.0),
    "A2": (420.0, 594.0),
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
}

# Дополнительные форматы (ГОСТ 2.301-68, табл. 2) — только для распознавания.
ADDITIONAL_FORMATS: dict[str, tuple[float, float]] = {
    "A0x2": (1189.0, 1682.0),
    "A0x3": (1189.0, 2523.0),
    "A1x3": (841.0, 1783.0),
    "A1x4": (841.0, 2378.0),
    "A2x3": (594.0, 1261.0),
    "A2x4": (594.0, 1682.0),
    "A2x5": (594.0, 2102.0),
    "A3x3": (420.0, 891.0),
    "A3x4": (420.0, 1189.0),
    "A3x5": (420.0, 1486.0),
    "A3x6": (420.0, 1783.0),
    "A3x7": (420.0, 2080.0),
    "A4x3": (297.0, 630.0),
    "A4x4": (297.0, 841.0),
    "A4x5": (297.0, 1051.0),
    "A4x6": (297.0, 1261.0),
    "A4x7": (297.0, 1471.0),
    "A4x8": (297.0, 1682.0),
    "A4x9": (297.0, 1892.0),
}

# Поля внутренней рамки: слева поле подшивки 20 мм, с остальных сторон 5 мм.
# (общепринятое оформление по ГОСТ 2.301 / ГОСТ Р 21.101 приложение И).
FRAME_LEFT_MM = 20.0
FRAME_OTHER_MM = 5.0
FORMAT_TOLERANCE_MM = 4.0

# ГОСТ Р 21.101-2020, форма 3: основная надпись 185 x 55 мм.
TITLE_BLOCK_W_MM = 185.0
TITLE_BLOCK_H_MM = 55.0
TITLE_BLOCK_TOLERANCE_MM = 4.0
TITLE_BLOCK_NAME = "SPDS_F3"

# ГОСТ 2.304-81. Ряд высот шрифта, мм (основные значения).
FONT_HEIGHTS_MM = (1.8, 2.5, 3.5, 5.0, 7.0, 10.0, 14.0, 20.0, 28.0, 40.0)
FONT_HEIGHTS_PRIMARY_MM = (1.8, 2.5, 3.5, 5.0, 7.0, 10.0, 14.0, 20.0)

GOST_FONT_MARKERS = (
    "gost",
    "gost type a",
    "gost type b",
    "gost_a",
    "gost_b",
    "gost a",
    "gost b",
    "gost2.304",
    "gost 2.304",
    "gost2304",
    "cs_gost",
    "isocpeur",
    "isocp.ttf",
    "isocp.shx",
    "isocp ",
    "gostui",
)

NON_GOST_FONT_MARKERS = (
    "arial",
    "times",
    "calibri",
    "cambria",
    "tahoma",
    "verdana",
    "comic",
    "courier",
    "georgia",
    "helvetica",
    "opensans",
    "liberation",
    "dejavu",
    "roboto",
    "segoe",
    "noto",
)

GOST_STYLE_NAME = "GOST_A"
GOST_FONT_FILE = "GOST type A"

# ГОСТ 2.303-68. Предпочтительные толщины, мм.
PREFERRED_LINEWEIGHTS_MM = (0.18, 0.25, 0.35, 0.50, 0.70, 1.00, 1.40, 2.00)
MAIN_LINE_S_MIN_MM = 0.50
MAIN_LINE_S_MAX_MM = 1.40

# Допустимые имена типов линий (без учёта регистра).
ALLOWED_LINETYPES = {
    "BYLAYER",
    "BYBLOCK",
    "CONTINUOUS",
    "DASHED",
    "DASHED2",
    "DASHEDX2",
    "HIDDEN",
    "HIDDEN2",
    "HIDDENX2",
    "CENTER",
    "CENTER2",
    "CENTERX2",
    "DASHDOT",
    "DASHDOT2",
    "DASHDOTX2",
    "PHANTOM",
    "PHANTOM2",
    "PHANTOMX2",
    "DOT",
    "DOT2",
    "DOTX2",
    "DIVIDE",
    "DIVIDE2",
    "DIVIDEX2",
    "BORDER",
    "BORDER2",
    "BORDERX2",
    "ACAD_ISO02W100",
    "ACAD_ISO03W100",
    "ACAD_ISO04W100",
    "ACAD_ISO05W100",
    "ACAD_ISO06W100",
    "ACAD_ISO07W100",
    "ACAD_ISO08W100",
    "ACAD_ISO09W100",
    "ACAD_ISO10W100",
    "ACAD_ISO11W100",
    "ACAD_ISO12W100",
    "ACAD_ISO13W100",
    "ACAD_ISO14W100",
    "ACAD_ISO15W100",
}

# Стандартные слои, которые инструмент может безопасно создать (не переназначая объекты).
STANDARD_LAYERS: dict[str, dict] = {
    "SPDS_OSN": {"linetype": "Continuous", "lineweight": 70, "color": 7, "title": "Основная сплошная"},
    "SPDS_TON": {"linetype": "Continuous", "lineweight": 25, "color": 7, "title": "Тонкая сплошная"},
    "SPDS_OS": {"linetype": "CENTER", "lineweight": 25, "color": 1, "title": "Осевая (штрихпунктирная)"},
    "SPDS_NEVID": {"linetype": "DASHED", "lineweight": 25, "color": 8, "title": "Штриховая (невидимый контур)"},
    "SPDS_TEXT": {"linetype": "Continuous", "lineweight": 25, "color": 7, "title": "Текст"},
    "SPDS_DIM": {"linetype": "Continuous", "lineweight": 25, "color": 7, "title": "Размеры"},
    "SPDS_FRAME": {"linetype": "Continuous", "lineweight": 70, "color": 7, "title": "Рамка листа"},
    "SPDS_STAMP": {"linetype": "Continuous", "lineweight": 50, "color": 7, "title": "Основная надпись"},
}

# ГОСТ 2.302-68. Масштабы уменьшения и увеличения.
SCALES_REDUCTION = (
    "1:1",
    "1:2",
    "1:2.5",
    "1:4",
    "1:5",
    "1:10",
    "1:15",
    "1:20",
    "1:25",
    "1:40",
    "1:50",
    "1:75",
    "1:100",
    "1:200",
    "1:400",
    "1:500",
    "1:800",
    "1:1000",
    "1:2000",
    "1:5000",
    "1:10000",
    "1:20000",
    "1:25000",
    "1:50000",
)
SCALES_ENLARGEMENT = (
    "2:1",
    "2.5:1",
    "4:1",
    "5:1",
    "10:1",
    "20:1",
    "40:1",
    "50:1",
    "100:1",
)

# ГОСТ 2.307 / ГОСТ Р 21.101-2020 п. 5.4.2: засечки 2–4 мм.
# Высота размерных чисел — из ряда ГОСТ 2.304, но не ниже 2,5 мм (ГОСТ Р 21.101).
DIM_TICK_MIN_MM = 2.0
DIM_TICK_MAX_MM = 4.0
DIM_TXT_PREFERRED_MM = (2.5, 3.5, 5.0, 7.0, 10.0, 14.0, 20.0)
DIM_EXE_MIN_MM = 1.0
DIM_EXE_MAX_MM = 5.0
SPDS_DIMSTYLE = "SPDS"

REVIT_EXTS = {".rvt", ".rfa", ".rte", ".rft"}
RENGA_EXTS = {".rnp", ".rnt", ".renga"}
RASTER_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
CAD_EXTS = {".dxf", ".dwg"}
PDF_EXTS = {".pdf"}
IFC_EXTS = {".ifc", ".ifczip"}

TITLE_KEYWORDS = (
    "разраб",
    "пров",
    "стадия",
    "листов",
    "н.контр",
    "н контр",
    "нормоконтр",
    "обознач",
    "наименован",
)
