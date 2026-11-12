"""Проверка растровых и PDF-чертежей: только отчёт, без исправления файла."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .checks.sheet_frame import match_format
from .constants import (
    FRAME_LEFT_MM,
    FRAME_OTHER_MM,
    TITLE_BLOCK_H_MM,
    TITLE_BLOCK_W_MM,
    TITLE_KEYWORDS,
)
from .line_classify import classify_pdf_drawings, details_from_summary
from .margin_report import format_margin_lines, margins_as_table_rows, measure_margins
from .models import CheckResult
from .stamp_cells import field_details
from .sheet_detect import detect_sheet_frames, is_sheet_edge
from .runtime_limits import max_image_side, pdf_raster_zoom
from .vision import classify_raster_lines, choose_sheet_rect, deskew_image, detect_rects_cv, expand_inner_quad, infer_sheet_mm, ocr_stamp_cells, warp_quad_to_rect


def find_tesseract() -> Optional[str]:
    env = os.environ.get("TESSERACT_CMD")
    if env and Path(env).exists():
        return env
    found = shutil.which("tesseract")
    if found:
        return found
    extras = [
        Path.home() / ".local/bin/tesseract",
        Path("/usr/bin/tesseract"),
        Path("/tmp/tess-dl/squashfs-root/usr/bin/tesseract"),
    ]
    for path in extras:
        if path.exists():
            return str(path)
    return None


def _ensure_tessdata() -> None:
    if os.environ.get("TESSDATA_PREFIX"):
        return
    candidates = [
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/share/tesseract-ocr/4.00/tessdata"),
        Path("/usr/share/tessdata"),
        Path("/tmp/tess-dl/squashfs-root/usr/share/tesseract-ocr/5/tessdata"),
    ]
    for path in candidates:
        if (path / "rus.traineddata").exists():
            os.environ["TESSDATA_PREFIX"] = str(path)
            return



def read_raster_bgr(path: Path):
    """Прочитать картинку без cv2.imread: тот не открывает часть путей с кириллицей."""
    import cv2

    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError as exc:
        raise RuntimeError(f"Не удалось прочитать изображение: {exc}") from exc
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def limit_raster_size(image, max_side: Optional[int] = None):
    """Уменьшить слишком большой скан, чтобы проверка не съела всю память."""
    import cv2

    if image is None or image.size == 0:
        return image, 1.0
    if max_side is None:
        raw = os.environ.get("GOST_RASTER_MAX_SIDE", "").strip()
        if raw.isdigit():
            max_side = max(256, min(8000, int(raw)))
        else:
            max_side = max(max_image_side() * 2, 4096)
    height, width = image.shape[:2]
    long_side = max(height, width)
    if long_side <= max_side or long_side <= 0:
        return image, 1.0
    scale = max_side / float(long_side)
    resized = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    return resized, scale


def read_image_dpi(path: Path) -> Optional[tuple[float, float]]:
    try:
        from PIL import Image

        with Image.open(str(path)) as image:
            dpi = image.info.get("dpi")
            if dpi and dpi[0] and dpi[1]:
                return float(dpi[0]), float(dpi[1])
    except Exception:
        return None
    return None


def ocr_image(image, lang: str = "rus+eng", psm: int = 6, prepared: bool = False, with_conf: bool = False):
    cmd = find_tesseract()
    if not cmd:
        msg = "Tesseract не найден. Установите пакеты tesseract-ocr и tesseract-ocr-rus."
        return ("", msg, 0.0) if with_conf else ("", msg)
    try:
        import cv2
        import pytesseract
        from PIL import Image

        _ensure_tessdata()
        pytesseract.pytesseract.tesseract_cmd = cmd
        if hasattr(image, "mode"):
            arr = np.array(image)
        else:
            arr = image
        if arr.ndim == 3:
            gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if arr.shape[2] == 3 else arr[:, :, 0]
        else:
            gray = arr
        if not prepared:
            # чуть увеличиваем и бинаризуем — тонкие линии штампа иначе «съедают» буквы
            gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        pil = Image.fromarray(gray)
        config = f"--psm {int(psm)}"
        data = pytesseract.image_to_data(pil, lang=lang, config=config, output_type=pytesseract.Output.DICT)
        words = []
        confs = []
        for word, conf in zip(data.get("text", []), data.get("conf", [])):
            word = (word or "").strip()
            try:
                conf_f = float(conf)
            except (TypeError, ValueError):
                conf_f = -1.0
            if not word or conf_f < 0:
                continue
            words.append(word)
            confs.append(conf_f)
        text = " ".join(words).strip()
        if not text:
            text = (pytesseract.image_to_string(pil, lang=lang, config=config) or "").strip()
        mean = sum(confs) / len(confs) if confs else 0.0
        return (text, None, mean) if with_conf else (text, None)
    except Exception as exc:
        return ("", f"OCR не выполнен: {exc}", 0.0) if with_conf else ("", f"OCR не выполнен: {exc}")


def _page_format_from_size_mm(width_mm: float, height_mm: float) -> Optional[tuple[str, str, float]]:
    hit = match_format(width_mm, height_mm, tol=6.0)
    if not hit:
        return None
    return hit[0], hit[1], 0.0


def _title_roi(image_bgr, frame_rect=None, page_w_mm: Optional[float] = None, page_h_mm: Optional[float] = None):
    h, w = image_bgr.shape[:2]
    pad = 12
    if page_w_mm and page_h_mm and page_w_mm > 20 and page_h_mm > 20:
        sx, sy = w / page_w_mm, h / page_h_mm
        x1 = w - FRAME_OTHER_MM * sx
        y1 = h - FRAME_OTHER_MM * sy
        x0 = x1 - TITLE_BLOCK_W_MM * sx
        y0 = y1 - TITLE_BLOCK_H_MM * sy
        return image_bgr[max(int(y0) - pad, 0): min(int(y1) + pad, h), max(int(x0) - pad, 0): min(int(x1) + pad, w)]
    if frame_rect:
        x, y, rw, rh = frame_rect
        # 185/395 ≈ 0.47 ширины внутренней рамки А3, 55/287 ≈ 0.19 высоты
        tw = max(int(0.50 * rw), 40)
        th = max(int(0.24 * rh), 30)
        x0 = x + rw - tw
        y0 = y + rh - th
        return image_bgr[max(y0 - pad, 0): min(y + rh + pad, h), max(x0 - pad, 0): min(x + rw + pad, w)]
    tw, th = int(0.50 * w), int(0.26 * h)
    return image_bgr[h - th: h, w - tw: w]


def extract_pdf_frame_margins(page) -> list[dict]:
    """Прямоугольники из векторных путей PDF, поля в мм по осевой линии штриха."""
    page_w = float(page.rect.width) * 25.4 / 72.0
    page_h = float(page.rect.height) * 25.4 / 72.0
    pt = 25.4 / 72.0
    found: list[dict] = []
    horiz: list[tuple[float, float, float]] = []
    vert: list[tuple[float, float, float]] = []

    def add_rect_mm(x0, y0, x1, y1, source: str) -> None:
        xa, xb = sorted((float(x0), float(x1)))
        ya, yb = sorted((float(y0), float(y1)))
        width, height = xb - xa, yb - ya
        if width < 40 or height < 40:
            return
        found.append(
            {
                "left": xa,
                "right": page_w - xb,
                "top": ya,
                "bottom": page_h - yb,
                "width": width,
                "height": height,
                "source": source,
            }
        )

    def add_line_mm(x0, y0, x1, y1) -> None:
        if abs(y1 - y0) <= 0.4 and abs(x1 - x0) >= 20:
            horiz.append(((y0 + y1) / 2.0, min(x0, x1), max(x0, x1)))
        elif abs(x1 - x0) <= 0.4 and abs(y1 - y0) >= 20:
            vert.append(((x0 + x1) / 2.0, min(y0, y1), max(y0, y1)))

    try:
        drawings = page.get_drawings()
    except Exception:
        return found

    for drawing in drawings:
        items = drawing.get("items") or []
        xs: list[float] = []
        ys: list[float] = []
        for item in items:
            if not item:
                continue
            kind = item[0]
            if kind == "re" and len(item) >= 2:
                rect = item[1]
                x0, y0, x1, y1 = (
                    float(rect.x0) * pt,
                    float(rect.y0) * pt,
                    float(rect.x1) * pt,
                    float(rect.y1) * pt,
                )
                add_rect_mm(x0, y0, x1, y1, "vector-rect")
                add_line_mm(x0, y0, x1, y0)
                add_line_mm(x0, y1, x1, y1)
                add_line_mm(x0, y0, x0, y1)
                add_line_mm(x1, y0, x1, y1)
            elif kind == "l" and len(item) >= 3:
                p1, p2 = item[1], item[2]
                x0, y0 = float(p1.x) * pt, float(p1.y) * pt
                x1, y1 = float(p2.x) * pt, float(p2.y) * pt
                add_line_mm(x0, y0, x1, y1)
                xs.extend([x0, x1])
                ys.extend([y0, y1])
            elif kind in {"c", "qu"}:
                for point in item[1:]:
                    if hasattr(point, "x"):
                        xs.append(float(point.x) * pt)
                        ys.append(float(point.y) * pt)
        if len(xs) >= 4 and len(ys) >= 4:
            add_rect_mm(min(xs), min(ys), max(xs), max(ys), "vector-path")

    def cluster(values: list[float], tol: float = 0.8) -> list[float]:
        if not values:
            return []
        ordered = sorted(values)
        groups = [[ordered[0]]]
        for value in ordered[1:]:
            if abs(value - groups[-1][-1]) <= tol:
                groups[-1].append(value)
            else:
                groups.append([value])
        return [sum(group) / len(group) for group in groups]

    long_h = [item for item in horiz if (item[2] - item[1]) >= page_w * 0.45]
    long_v = [item for item in vert if (item[2] - item[1]) >= page_h * 0.45]
    ys = cluster([item[0] for item in long_h])
    xs = cluster([item[0] for item in long_v])
    for i, x0 in enumerate(xs):
        for x1 in xs[i + 1 :]:
            for j, y0 in enumerate(ys):
                for y1 in ys[j + 1 :]:
                    if abs(x1 - x0) >= 40 and abs(y1 - y0) >= 40:
                        add_rect_mm(x0, y0, x1, y1, "vector-lines")
    return found


def _margin_score(item: dict) -> float:
    return (
        abs(item["left"] - 20)
        + abs(item["right"] - 5)
        + abs(item["top"] - 5)
        + abs(item["bottom"] - 5)
    )


def margins_within_tolerance(item: dict, tol: float = 0.5) -> bool:
    return (
        abs(item["left"] - 20) <= tol
        and abs(item["right"] - 5) <= tol
        and abs(item["top"] - 5) <= tol
        and abs(item["bottom"] - 5) <= tol
    )


def analyze_raster_image(
    image_bgr,
    page_w_mm: Optional[float],
    page_h_mm: Optional[float],
    source: str,
    vector_frames: Optional[list] = None,
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    details_fmt = [f"Источник изображения: {source}."]
    fmt_name = None
    orientation = None
    if page_w_mm and page_h_mm:
        details_fmt.append(f"Размер страницы/изображения: {page_w_mm:.1f}×{page_h_mm:.1f} мм.")
        hit = match_format(page_w_mm, page_h_mm, tol=6.0)
        if hit:
            fmt_name, orientation, kind = hit
            details_fmt.append(f"По размеру это похоже на {fmt_name} ({orientation}, {kind}).")
            checks.append(
                CheckResult(
                    check_id="sheet_frame",
                    title="Формат листа и рамка",
                    gost="ГОСТ 2.301-68",
                    severity="ok" if kind in {"outer", "inner"} else "info",
                    message=f"Формат листа определён по размеру страницы как {fmt_name} ({orientation}).",
                    details=details_fmt,
                    can_autofix=False,
                )
            )
        else:
            details_fmt.append("Размер страницы не совпал с форматами А0–А4 с допуском 6 мм.")
            checks.append(
                CheckResult(
                    check_id="sheet_frame",
                    title="Формат листа и рамка",
                    gost="ГОСТ 2.301-68",
                    severity="warning",
                    message="Формат листа по размеру страницы однозначно не определён.",
                    details=details_fmt,
                    can_autofix=False,
                    findings_count=1,
                )
            )
    else:
        h, w = image_bgr.shape[:2]
        details_fmt.append(f"Пикселей: {w}×{h}, отношение сторон {w / h:.3f}.")
        # оценка формата только по пропорции
        ratio = max(w, h) / max(min(w, h), 1)
        target = (2 ** 0.5)
        if abs(ratio - target) < 0.08:
            details_fmt.append("Отношение сторон близко к √2 — как у форматов ряда А.")
            checks.append(
                CheckResult(
                    check_id="sheet_frame",
                    title="Формат листа и рамка",
                    gost="ГОСТ 2.301-68",
                    severity="info",
                    message="По пропорции кадр похож на формат ряда А, точный размер в мм неизвестен.",
                    details=details_fmt,
                    can_autofix=False,
                )
            )
        else:
            checks.append(
                CheckResult(
                    check_id="sheet_frame",
                    title="Формат листа и рамка",
                    gost="ГОСТ 2.301-68",
                    severity="warning",
                    message="Пропорции изображения не похожи на формат ряда А.",
                    details=details_fmt,
                    can_autofix=False,
                    findings_count=1,
                )
            )

    vector_best = None
    if vector_frames:
        vector_best = min(vector_frames, key=_margin_score)
        if checks and checks[0].check_id == "sheet_frame":
            checks[0].details.append(
                "По векторным контурам PDF (осевая линия штриха, не внешний край растра): "
                f"слева {vector_best['left']:.2f}, справа {vector_best['right']:.2f}, "
                f"сверху {vector_best['top']:.2f}, снизу {vector_best['bottom']:.2f} мм "
                "(ожидается 20 / 5 / 5 / 5, допуск ±0,5 мм)."
            )
            margins = measure_margins(
                vector_best["left"], vector_best["right"], vector_best["top"], vector_best["bottom"]
            )
            checks[0].details.extend(format_margin_lines(margins))
            checks[0].extra = {
                "margins": margins,
                "margins_table": margins_as_table_rows(margins),
            }
            if margins_within_tolerance(vector_best, 0.5):
                checks[0].severity = "ok"
                checks[0].findings_count = 0
                checks[0].message = (
                    checks[0].message.split(".")[0]
                    + ". Поля внутренней рамки совпадают с 20/5/5/5 мм в пределах ±0,5 мм."
                )
            else:
                checks[0].severity = "info"
                checks[0].message += " Векторные поля рамки отличаются от 20/5/5/5 мм больше чем на 0,5 мм."

    rects = detect_rects_cv(image_bgr)
    frame_details = [f"Контуров, похожих на крупные прямоугольники (растр): {len(rects)}."]
    frame_rect = None
    looks_like_frame = False
    found_any = bool(rects)
    if page_w_mm and page_h_mm:
        sheet_info = detect_sheet_frames(image_bgr, page_w_mm, page_h_mm)
        found_any = True
        if sheet_info.get("rects"):
            frame_details.append(
                f"После шумоподавления, замыкания линий и поиска вложенных прямоугольников "
                f"кандидатов: {len(sheet_info['rects'])} (метод: {sheet_info.get('method') or 'контуры/Хаф'})."
            )
        if sheet_info.get("inner") and sheet_info.get("margins") and not is_sheet_edge(sheet_info["margins"]):
            x, y, rw, rh = sheet_info["inner"][:4]
            frame_rect = (x, y, rw, rh)
            raster_margins = sheet_info["margins"]
            frame_details.extend(format_margin_lines(raster_margins))
            looks_like_frame = bool(raster_margins.get("all_ok")) or (
                abs(raster_margins["left"]["value"] - 20) <= 1.0
                and abs(raster_margins["right"]["value"] - 5) <= 1.0
                and abs(raster_margins["top"]["value"] - 5) <= 1.0
                and abs(raster_margins["bottom"]["value"] - 5) <= 1.0
            )
            if vector_best is not None:
                looks_like_frame = margins_within_tolerance(vector_best, 0.5) or looks_like_frame
            if checks and checks[0].check_id == "sheet_frame" and not checks[0].extra.get("margins"):
                checks[0].extra = {
                    "margins": raster_margins,
                    "margins_table": margins_as_table_rows(raster_margins),
                }
        else:
            frame_details.append(
                "Внутренняя рамка на изображении не найдена. "
                "Край листа за внутреннюю рамку не принимается."
            )
            if sheet_info.get("outer"):
                frame_rect = tuple(sheet_info["outer"][:4])
            looks_like_frame = vector_best is not None and margins_within_tolerance(vector_best, 0.5)
    elif rects:
        h, w = image_bgr.shape[:2]
        frame_rect = rects[0][:4]
        x, y, rw, rh = frame_rect
        left = x / w
        right = (w - (x + rw)) / w
        top = y / h
        bottom = (h - (y + rh)) / h
        frame_details.append(
            f"Крупнейший прямоугольник: {rw}×{rh} пкс; поля кадра "
            f"слева {left:.2%}, справа {right:.2%}, сверху {top:.2%}, снизу {bottom:.2%}."
        )
        looks_like_frame = left > right * 1.3 and left < 0.15
    if found_any:
        sev = "ok" if looks_like_frame else "info"
        msg = "На изображении есть крупный прямоугольный контур, похожий на рамку."
        if not looks_like_frame:
            msg = "Крупный прямоугольник найден, но внутренняя рамка 20/5/5/5 мм не подтверждена."
        if checks and checks[0].check_id == "sheet_frame":
            checks[0].details.extend(frame_details)
            vector_ok = vector_best is not None and margins_within_tolerance(vector_best, 0.5)
            if checks[0].severity == "ok" and sev != "ok" and not vector_ok:
                checks[0].severity = "info"
                checks[0].message += " " + msg
            elif checks[0].severity != "ok" and looks_like_frame and not vector_best:
                checks[0].severity = "ok"
                checks[0].findings_count = 0
                checks[0].message = (
                    checks[0].message.split(".")[0]
                    + ". Поля внутренней рамки совпадают с 20/5/5/5 мм в пределах ±1 мм (растр)."
                )
        else:
            checks.append(
                CheckResult(
                    check_id="sheet_frame_contour",
                    title="Контур рамки на изображении",
                    gost="ГОСТ 2.301-68",
                    severity=sev,
                    message=msg,
                    details=frame_details,
                    can_autofix=False,
                )
            )
    else:
        frame_details.append("Устойчивый прямоугольный контур рамки не обнаружен (эвристика OpenCV).")
        if checks and checks[0].check_id == "sheet_frame":
            checks[0].details.extend(frame_details)
            if checks[0].severity == "ok":
                checks[0].severity = "warning"
                checks[0].message += " Контур рамки на изображении не найден."
                checks[0].findings_count = max(checks[0].findings_count, 1)
        else:
            checks.append(
                CheckResult(
                    check_id="sheet_frame",
                    title="Формат листа и рамка",
                    gost="ГОСТ 2.301-68",
                    severity="warning",
                    message="Контур рамки на изображении не найден.",
                    details=frame_details,
                    can_autofix=False,
                    findings_count=1,
                )
            )

    roi = _title_roi(image_bgr, frame_rect, page_w_mm=page_w_mm, page_h_mm=page_h_mm)
    # поиск маленького прямоугольника 185x55 в правом нижнем
    stamp_details = ["Область поиска штампа: правый нижний угол рамки или кадра."]
    if frame_rect and page_w_mm and page_h_mm:
        x, y, rw, rh = frame_rect
        # ожидаемый размер штампа в пикселях
        exp_w = TITLE_BLOCK_W_MM * (rw / max((page_w_mm - FRAME_LEFT_MM - FRAME_OTHER_MM) if page_w_mm > 40 else page_w_mm, 1))
        exp_h = TITLE_BLOCK_H_MM * (rh / max((page_h_mm - 2 * FRAME_OTHER_MM) if page_h_mm > 40 else page_h_mm, 1))
        stamp_details.append(f"Ожидаемый размер штампа на изображении примерно {exp_w:.0f}×{exp_h:.0f} пкс.")

    text, ocr_err = ocr_image(roi)
    if ocr_err:
        stamp_details.append(ocr_err)
        checks.append(
            CheckResult(
                check_id="title_block",
                title="Основная надпись (штамп)",
                gost="ГОСТ Р 21.101-2020, форма 3 (185×55 мм)",
                severity="warning",
                message="Область штампа выделена, но OCR недоступен.",
                details=stamp_details,
                can_autofix=False,
                findings_count=1,
            )
        )
    else:
        cleaned = " ".join(text.split())
        stamp_details.append("Распознанный текст области штампа:")
        stamp_details.append(cleaned[:800] or "(пусто)")
        low = cleaned.lower()
        found = [k for k in TITLE_KEYWORDS if k in low]
        filled = []
        for line in text.splitlines():
            line_s = line.strip()
            if not line_s:
                continue
            low_line = line_s.lower()
            if "разраб" in low_line:
                rest = low_line.split("разраб", 1)[-1]
                rest = rest.replace(".", " ").strip()
                tokens = [t for t in rest.split() if t not in {"пров", "дата", "подп"}]
                if tokens:
                    filled.append("разраб.")
            if "стадия" in low_line:
                rest = low_line.split("стадия", 1)[-1].strip(" .:")
                if rest:
                    filled.append("стадия")
        if found:
            msg = "В области штампа распознаны характерные подписи граф: " + ", ".join(found) + "."
            if filled:
                msg += " Похоже, часть граф заполнена (" + ", ".join(filled) + ")."
            else:
                msg += " По заполненности граф уверенно судить нельзя: OCR часто ошибается на тонких линиях сетки."
            checks.append(
                CheckResult(
                    check_id="title_block",
                    title="Основная надпись (штамп)",
                    gost="ГОСТ Р 21.101-2020, форма 3 (185×55 мм)",
                    severity="info" if found else "warning",
                    message=msg,
                    details=stamp_details,
                    can_autofix=False,
                )
            )
        else:
            checks.append(
                CheckResult(
                    check_id="title_block",
                    title="Основная надпись (штамп)",
                    gost="ГОСТ Р 21.101-2020, форма 3 (185×55 мм)",
                    severity="warning",
                    message="В правом нижнем углу не распознаны подписи граф основной надписи.",
                    details=stamp_details,
                    can_autofix=False,
                    findings_count=1,
                )
            )

    # На растре остальные ГОСТ-проверки не выполняются полноценно
    checks.append(
        CheckResult(
            check_id="linetypes",
            title="Типы и толщины линий",
            gost="ГОСТ 2.303-68",
            severity="info",
            message="Для растра и скана толщины линий по ГОСТ 2.303 автоматически не проверяются (нет однозначных векторных слоёв).",
            details=["Если PDF получен из CAD, дополнительные сведения берутся из векторного содержимого страницы."],
            can_autofix=False,
        )
    )
    checks.append(
        CheckResult(
            check_id="fonts",
            title="Шрифты и высоты текста",
            gost="ГОСТ 2.304-81",
            severity="info",
            message="Для растра шрифт и точная высота букв не определяются. OCR читает только текст области штампа.",
            details=[],
            can_autofix=False,
        )
    )
    checks.append(
        CheckResult(
            check_id="scales",
            title="Масштабы изображений",
            gost="ГОСТ 2.302-68",
            severity="info",
            message="Масштаб на растре отдельно не вычисляется. Ищите в отчёте OCR указание вида 1:100, если оно попало в область штампа.",
            details=[],
            can_autofix=False,
        )
    )
    checks.append(
        CheckResult(
            check_id="dimensions",
            title="Размеры (размерные стили)",
            gost="ГОСТ 2.307-68; ГОСТ Р 21.101-2020 п. 5.4.2",
            severity="info",
            message="Тип ограничителей размеров (засечки или стрелки) на растре автоматически не распознаётся.",
            details=[],
            can_autofix=False,
        )
    )
    return checks


def analyze_vector_pdf_page(page) -> list[str]:
    notes = []
    try:
        fonts = page.get_fonts()
        names = []
        for item in fonts:
            # (xref, ext, type, basefont, name, encoding) — состав зависит от версии
            if len(item) >= 4:
                names.append(str(item[3]))
        if names:
            notes.append("Шрифты PDF: " + ", ".join(sorted(set(names))[:20]))
            gostish = [n for n in names if any(k in n.lower() for k in ("gost", "isocp"))]
            nongost = [n for n in names if any(k in n.lower() for k in ("arial", "times", "calibri"))]
            if gostish:
                notes.append("Есть имена, похожие на ГОСТ/ISOCPEUR: " + ", ".join(sorted(set(gostish))))
            if nongost:
                notes.append("Есть офисные шрифты: " + ", ".join(sorted(set(nongost))))
    except Exception as exc:
        notes.append(f"Не удалось прочитать шрифты PDF: {exc}")
    try:
        drawings = page.get_drawings()
        widths = []
        for drawing in drawings:
            width = drawing.get("width")
            if width:
                widths.append(float(width) * 25.4 / 72.0)
        if widths:
            widths.sort()
            notes.append(
                f"Векторных штрихов: {len(drawings)}; толщина (мм): мин {widths[0]:.2f}, "
                f"медиана {widths[len(widths)//2]:.2f}, макс {widths[-1]:.2f}."
            )
        else:
            notes.append(f"Векторных штрихов на странице: {len(drawings)} (толщина указана не у всех).")
    except Exception as exc:
        notes.append(f"Не удалось разобрать векторные линии PDF: {exc}")
    try:
        text = page.get_text("text") or ""
        if text.strip():
            notes.append("Извлечённый текстовый слой (начало): " + " ".join(text.split())[:400])
    except Exception:
        pass
    return notes


def rasterize_pdf_page(page, zoom: float = 2.0):
    import fitz
    import numpy as np

    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if arr.shape[2] >= 3:
        bgr = np.ascontiguousarray(arr[:, :, 2::-1])
    else:
        bgr = arr.copy()
    width, height = pix.width, pix.height
    del pix, arr
    return bgr, width, height


def _pdf_page_score(page) -> int:
    score = 0
    try:
        score += len(page.get_drawings()) * 5
    except Exception:
        pass
    try:
        text = (page.get_text("text") or "").strip()
        score += min(len(text), 800)
    except Exception:
        pass
    return score


def _select_pdf_page(doc) -> int:
    """Первая страница часто бывает обложкой. Берём самую «чертёжную» из первых восьми."""
    limit = min(int(doc.page_count or 0), 8)
    best_index = 0
    best_score = -1
    for index in range(limit):
        try:
            score = _pdf_page_score(doc[index])
        except Exception:
            continue
        if score > best_score:
            best_score = score
            best_index = index
    return best_index


def process_pdf(path: Path) -> tuple[list[CheckResult], list[str], dict]:
    import fitz

    notes: list[str] = []
    extra: dict[str, Any] = {}
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise RuntimeError("PDF повреждён или не является PDF.") from exc
    try:
        if getattr(doc, "needs_pass", False):
            raise RuntimeError("PDF закрыт паролем. Снимите защиту и загрузите файл снова.")
        if int(doc.page_count or 0) < 1:
            raise RuntimeError("В PDF нет страниц.")
        return _process_open_pdf(doc, notes, extra)
    finally:
        doc.close()


def _process_open_pdf(doc, notes: list[str], extra: dict[str, Any]) -> tuple[list[CheckResult], list[str], dict]:
    extra["pages"] = doc.page_count
    page_index = _select_pdf_page(doc)
    if doc.page_count > 1 and page_index:
        notes.append(
            f"Страниц в PDF: {doc.page_count}. Первая страница без содержимого чертежа, "
            f"проверяется страница {page_index + 1}."
        )
    else:
        notes.append(f"Страниц в PDF: {doc.page_count}. Проверяется страница {page_index + 1}.")
    page = doc[page_index]
    try:
        rotation = int(page.rotation or 0)
    except Exception:
        rotation = 0
    if rotation:
        notes.append(f"Страница повёрнута на {rotation}°. Векторные контуры читаются в системе координат страницы.")
    # размер в мм
    w_mm = page.rect.width * 25.4 / 72.0
    h_mm = page.rect.height * 25.4 / 72.0
    vector_notes = analyze_vector_pdf_page(page)
    if vector_notes:
        notes.append("Дополнительно из векторного содержимого PDF (если файл выгружен из CAD):")
        notes.extend(vector_notes)
    zoom = pdf_raster_zoom(page.rect.width, page.rect.height)
    image, _pw, _ph = rasterize_pdf_page(page, zoom=zoom)
    if abs(zoom - 2.0) > 0.05:
        notes.append(
            f"Страница растеризована с масштабом {zoom:.2f} "
            f"(лимит длинной стороны {max_image_side()} px)."
        )
    vector_frames = extract_pdf_frame_margins(page)
    if vector_frames:
        extra["vector_frames"] = vector_frames
        notes.append(f"Векторных прямоугольников рамки на странице: {len(vector_frames)}.")
    checks = analyze_raster_image(
        image,
        w_mm,
        h_mm,
        source=f"PDF, стр. {page_index + 1}, {w_mm:.1f}×{h_mm:.1f} мм",
        vector_frames=vector_frames,
    )
    if vector_notes:
        for check in checks:
            if check.check_id == "fonts":
                check.details.extend(vector_notes)
                if any("офисные шрифты" in n.lower() or "Arial" in n or "arial" in n for n in vector_notes):
                    check.severity = "warning"
                    check.message = "В PDF встретились шрифты, не похожие на ГОСТ 2.304."
                    check.findings_count = 1
            if check.check_id == "linetypes":
                check.details.extend([n for n in vector_notes if "толщина" in n.lower() or "штрих" in n.lower()])
    try:
        line_summary = classify_pdf_drawings(page, w_mm, h_mm)
        extra["lines_full"] = line_summary
        extra["lines"] = {key: value for key, value in line_summary.items() if key != "items"}
        checks.append(
            CheckResult(
                check_id="lines",
                title="Распознавание линий",
                gost="ГОСТ 2.303-68",
                severity="ok" if not line_summary.get("issue_count") else "warning",
                message=(
                    f"По векторным штрихам PDF распознано линий: {line_summary.get('total', 0)}."
                ),
                details=details_from_summary(line_summary),
                extra={"kinds": line_summary.get("kinds") or [], "s_main_mm": line_summary.get("s_main_mm")},
            )
        )
    except Exception as exc:
        notes.append(f"Классификация линий PDF не выполнена: {exc}")
    try:
        cells = ocr_stamp_cells(image, w_mm, h_mm, ocr_image)
        if cells:
            lines, empty_req, stamp_extra = field_details(cells)
            extra["stamp_fields"] = stamp_extra
            for check in checks:
                if check.check_id == "title_block":
                    check.details.extend(lines)
                    if empty_req and check.severity == "ok":
                        check.severity = "warning"
                        check.message += " Пустые обязательные графы: " + ", ".join(empty_req) + "."
                        check.findings_count = max(check.findings_count, len(empty_req))
                    check.extra = {**(check.extra or {}), **stamp_extra}
    except Exception as exc:
        notes.append(f"OCR граф штампа не выполнен: {exc}")
    extra["preview_image"] = image
    extra["page_size_mm"] = (w_mm, h_mm)
    for check in checks:
        if check.check_id == "sheet_frame" and check.extra.get("margins"):
            extra["margins"] = check.extra["margins"]
            extra["margins_table"] = check.extra.get("margins_table")
    return checks, notes, extra



def refine_sheet_by_inner(image_bgr, page_w_mm: float, page_h_mm: float):
    """Если внутренняя рамка найдена со сдвигом, выправить кадр так, чтобы поля стали 20/5/5/5."""
    info = detect_sheet_frames(image_bgr, page_w_mm, page_h_mm)
    inner = info.get("inner")
    margins = info.get("margins")
    if not inner or not margins:
        return image_bgr, info, False
    if (
        abs(margins["left"]["value"] - FRAME_LEFT_MM) <= 1.0
        and abs(margins["right"]["value"] - FRAME_OTHER_MM) <= 1.0
        and abs(margins["top"]["value"] - FRAME_OTHER_MM) <= 1.0
        and abs(margins["bottom"]["value"] - FRAME_OTHER_MM) <= 1.0
    ):
        return image_bgr, info, False
    ix, iy, iw, ih = inner[:4]
    h, w = image_bgr.shape[:2]
    sx, sy = w / max(page_w_mm, 1e-6), h / max(page_h_mm, 1e-6)
    pts = [
        (ix - FRAME_LEFT_MM * sx, iy - FRAME_OTHER_MM * sy),
        (ix + iw + FRAME_OTHER_MM * sx, iy - FRAME_OTHER_MM * sy),
        (ix + iw + FRAME_OTHER_MM * sx, iy + ih + FRAME_OTHER_MM * sy),
        (ix - FRAME_LEFT_MM * sx, iy + ih + FRAME_OTHER_MM * sy),
    ]
    try:
        refined = warp_quad_to_rect(image_bgr, pts, w, h)
    except Exception:
        return image_bgr, info, False
    if refined is None or refined.size == 0:
        return image_bgr, info, False
    new_info = detect_sheet_frames(refined, page_w_mm, page_h_mm)
    return refined, new_info, True


def process_image(path: Path) -> tuple[list[CheckResult], list[str], dict]:
    image = read_raster_bgr(path)
    if image is None:
        raise RuntimeError(f"Не удалось открыть изображение: {path}")
    notes = [
        "Это растровый файл (скан или фото). Если DPI неизвестен, формат оценивается по пропорции "
        "обнаруженного контура листа и таблице ГОСТ 2.301 (включая кратные форматы)."
    ]
    declared_w = declared_h = None
    dpi = read_image_dpi(path)
    if dpi:
        declared_w = image.shape[1] / dpi[0] * 25.4
        declared_h = image.shape[0] / dpi[1] * 25.4
    image, raster_scale = limit_raster_size(image)
    if raster_scale < 0.999:
        notes.append(
            "Скан уменьшен перед проверкой: исходная сторона была слишком большой для разбора в памяти."
        )
    if dpi:
        notes.append(
            f"DPI из метаданных файла: {dpi[0]:.0f}×{dpi[1]:.0f}, "
            f"размер кадра {declared_w:.1f}×{declared_h:.1f} мм."
        )
        hit = match_format(declared_w, declared_h, tol=15.0)
        if hit:
            notes.append(f"По DPI кадр совпадает с форматом {hit[0]} ({hit[1]}).")
        else:
            notes.append("DPI не дал формат из таблицы ГОСТ 2.301 — оценка по контуру листа.")
            declared_w = declared_h = None
    deskewed, angle = deskew_image(image)
    if abs(angle) >= 0.35:
        notes.append(f"Лист выровнен: обнаружен перекос {angle:.2f}°.")
        image = deskewed
    else:
        notes.append("Заметного перекоса листа не обнаружено.")
    frame_rects = detect_rects_cv(image)
    chosen = choose_sheet_rect(frame_rects, image.shape) if frame_rects else None
    frame_rect = chosen[:4] if chosen is not None else None
    w_mm, h_mm, how, ppmx, ppmy = infer_sheet_mm(image, declared_w, declared_h, frame_rect)
    extra = {"pixels": [int(image.shape[1]), int(image.shape[0])], "deskew_deg": angle, "scale_source": how}
    if frame_rect and w_mm and h_mm and str(how) not in {"unknown", "page"}:
        x, y, rw, rh = frame_rect
        warped = None
        pts = chosen[5] if chosen is not None and len(chosen) > 5 else None
        inner_mode = str(how).startswith("inner-")
        warp_pts = pts
        if inner_mode and pts is not None:
            try:
                warp_pts = expand_inner_quad(pts, w_mm, h_mm)
            except Exception:
                warp_pts = pts
        elif inner_mode:
            sx = ppmx or (rw / max(w_mm - FRAME_LEFT_MM - FRAME_OTHER_MM, 1))
            sy = ppmy or (rh / max(h_mm - 2 * FRAME_OTHER_MM, 1))
            warp_pts = [
                (x - FRAME_LEFT_MM * sx, y - FRAME_OTHER_MM * sy),
                (x + rw + FRAME_OTHER_MM * sx, y - FRAME_OTHER_MM * sy),
                (x + rw + FRAME_OTHER_MM * sx, y + rh + FRAME_OTHER_MM * sy),
                (x - FRAME_LEFT_MM * sx, y + rh + FRAME_OTHER_MM * sy),
            ]
        if warp_pts is not None:
            try:
                scale = ppmx or (rw / max((w_mm - FRAME_LEFT_MM - FRAME_OTHER_MM) if inner_mode else w_mm, 1))
                out_w = max(int(w_mm * scale), 200)
                out_h = max(int(h_mm * (ppmy or scale)), 200)
                out_h = max(int(out_w * h_mm / max(w_mm, 1)), 200)
                cap = max_image_side()
                if max(out_w, out_h) > cap:
                    k = cap / max(out_w, out_h)
                    out_w, out_h = int(out_w * k), int(out_h * k)
                candidate = warp_quad_to_rect(image, warp_pts, out_w, out_h)
                if candidate.size and candidate.shape[0] > 40 and candidate.shape[1] > 40:
                    warped = candidate
            except Exception:
                warped = None
        if warped is not None:
            image = warped
            if inner_mode:
                notes.append("Найдена внутренняя рамка; кадр восстановлен с полями 20/5/5/5 мм и выправлен.")
            else:
                notes.append("Лист выправлен по четырём углам обнаруженного контура.")
            ppmx = image.shape[1] / w_mm
        else:
            pad = 3
            if inner_mode and ppmx:
                sx = ppmx
                sy = ppmy or ppmx
                x0 = int(x - FRAME_LEFT_MM * sx) - pad
                y0 = int(y - FRAME_OTHER_MM * sy) - pad
                x1 = int(x + rw + FRAME_OTHER_MM * sx) + pad
                y1 = int(y + rh + FRAME_OTHER_MM * sy) + pad
                cropped = image[max(y0, 0): min(y1, image.shape[0]), max(x0, 0): min(x1, image.shape[1])]
            else:
                cropped = image[max(y - pad, 0): min(y + rh + pad, image.shape[0]), max(x - pad, 0): min(x + rw + pad, image.shape[1])]
            if cropped.size and cropped.shape[0] > 40 and cropped.shape[1] > 40:
                image = cropped
                notes.append("Кадр обрезан по обнаруженному контуру листа.")
                ppmx = image.shape[1] / w_mm
    if w_mm and h_mm:
        try:
            image, _refined_info, did_refine = refine_sheet_by_inner(image, w_mm, h_mm)
            if did_refine:
                notes.append("Кадр уточнён по найденной внутренней рамке (поля приведены к 20/5/5/5 мм).")
                ppmx = image.shape[1] / w_mm
        except Exception:
            pass
        notes.append(
            f"Оценка размера листа: {w_mm:.1f}×{h_mm:.1f} мм ({how})."
        )
        extra["page_size_mm"] = (w_mm, h_mm)
    extra["pixels"] = [int(image.shape[1]), int(image.shape[0])]
    checks = analyze_raster_image(image, w_mm, h_mm, source=str(path.name))
    try:
        line_summary = classify_raster_lines(image, ppmx, page_w_mm=w_mm, page_h_mm=h_mm)
        extra["lines_full"] = line_summary
        extra["lines"] = {key: value for key, value in line_summary.items() if key != "items"}
        checks.append(
            CheckResult(
                check_id="lines",
                title="Распознавание линий",
                gost="ГОСТ 2.303-68",
                severity="info" if line_summary.get("total") else "warning",
                message=f"По растру распознано отрезков: {line_summary.get('total', 0)}.",
                details=details_from_summary(line_summary),
                extra={"kinds": line_summary.get("kinds") or [], "s_main_mm": line_summary.get("s_main_mm")},
            )
        )
    except Exception as exc:
        notes.append(f"Растровые линии не классифицированы: {exc}")
    if w_mm and h_mm:
        try:
            cells = ocr_stamp_cells(image, w_mm, h_mm, ocr_image)
            if cells:
                lines, empty_req, stamp_extra = field_details(cells)
                extra["stamp_fields"] = stamp_extra
                for check in checks:
                    if check.check_id == "title_block":
                        check.details.extend(lines)
                        check.extra = {**(check.extra or {}), **stamp_extra}
        except Exception as exc:
            notes.append(f"OCR граф штампа: {exc}")
    extra["preview_image"] = image
    for check in checks:
        if check.check_id == "sheet_frame" and check.extra.get("margins"):
            extra["margins"] = check.extra["margins"]
            extra["margins_table"] = check.extra.get("margins_table")
    return checks, notes, extra
