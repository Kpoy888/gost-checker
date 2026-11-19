from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .constants import CAD_EXTS, IFC_EXTS, PDF_EXTS, RASTER_EXTS, RENGA_EXTS, REVIT_EXTS
from .engine import process_file

_INPUT_EXTS = CAD_EXTS | PDF_EXTS | RASTER_EXTS | IFC_EXTS | RENGA_EXTS | REVIT_EXTS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gost-check",
        description=(
            "Проверка оформления строительного чертежа по отдельным правилам СПДС/ЕСКД. "
            "Не является полным нормоконтролем."
        ),
    )
    parser.add_argument("input", nargs="+", help="Файлы или папка с чертежами")
    parser.add_argument("--fix", action="store_true", help="Внести безопасные автоисправления (только DXF/DWG)")
    parser.add_argument("--out", default="gost-out", help="Каталог для отчёта и исправленного файла")
    parser.add_argument("--titleblock", help="JSON с полями основной надписи")
    parser.add_argument(
        "--format",
        dest="sheet_format",
        default="A3",
        help="Формат листа, если рамка не найдена (A0…A4). По умолчанию A3",
    )
    parser.add_argument("--render", action="store_true", help="Сохранить PNG-просмотр до/после")
    parser.add_argument(
        "--assign-layers",
        action="store_true",
        help="Перенести объекты на слои СПДС по эвристике (только вместе с --fix)",
    )
    parser.add_argument("--highlight", action="store_true", help="PNG с номерами замечаний")
    parser.add_argument("--pdf", action="store_true", help="Дополнительно сохранить report.pdf")
    parser.add_argument("--xlsx", action="store_true", help="Дополнительно сохранить report.xlsx")
    parser.add_argument("--compare", help="Второй файл: сравнить сущности и результаты проверки")
    parser.add_argument("--telegram-chat", default="", help="Необязательный chat_id для уведомления")
    return parser


def collect_inputs(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.is_file() and child.suffix.lower() in _INPUT_EXTS:
                    found.append(child)
            continue
        found.append(path)
    return found


def load_titleblock(path: str | None) -> dict:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON основной надписи должен быть объектом")
    return data


def _print_result(result) -> None:
    print(f"Отчёт: {result.report_html}")
    print(f"Текст: {result.report_md}")
    if result.output_dxf:
        print(f"DXF:  {result.output_dxf}")
    if result.output_dwg:
        print(f"DWG:  {result.output_dwg}")
    if getattr(result, "render_issues", None):
        print(f"Подсветка: {result.render_issues}")
    extra = result.extra or {}
    if extra.get("report_pdf"):
        print(f"PDF:  {extra['report_pdf']}")
    if extra.get("report_xlsx"):
        print(f"Excel: {extra['report_xlsx']}")
    print(
        "Итог: "
        f"ок={result.summary.get('ok', 0)}, "
        f"сведения={result.summary.get('info', 0)}, "
        f"замечания={result.summary.get('warning', 0)}, "
        f"ошибки={result.summary.get('error', 0)}, "
        f"исправлено={result.summary.get('fixed', 0)}"
    )


def main(argv: list[str] | None = None) -> int:
    args_list = list(__import__("sys").argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "quantities":
        from .quantities import quantities_cli

        return quantities_cli(args_list[1:])
    if args_list and args_list[0] == "clearance":
        from .clearance import clearance_cli

        return clearance_cli(args_list[1:])
    if args_list and args_list[0] == "gaps":
        from .electrical import gaps_cli

        return gaps_cli(args_list[1:])
    if args_list and args_list[0] == "rooms":
        from .rooms import rooms_cli

        return rooms_cli(args_list[1:])
    if args_list and args_list[0] == "stamp":
        from .stampcard import stamp_cli

        return stamp_cli(args_list[1:])
    if args_list and args_list[0] == "release-check":
        from .releasecheck import release_cli

        return release_cli(args_list[1:])
    if args_list and args_list[0] == "slopes":
        from .slopes import slopes_cli

        return slopes_cli(args_list[1:])
    if args_list and args_list[0] == "axes":
        from .axes import axes_cli

        return axes_cli(args_list[1:])
    if args_list and args_list[0] == "roomplan":
        from .roomplan import roomplan_cli

        return roomplan_cli(args_list[1:])
    if args_list and args_list[0] == "layers":
        from .layerstd import layers_cli

        return layers_cli(args_list[1:])
    if args_list and args_list[0] == "fonts":
        from .fontfix import fonts_cli

        return fonts_cli(args_list[1:])
    if args_list and args_list[0] == "equipment":
        from .equipment import equipment_cli

        return equipment_cli(args_list[1:])
    if args_list and args_list[0] == "plans":
        from .floorplans import plans_cli

        return plans_cli(args_list[1:])
    if args_list and args_list[0] == "materials":
        from .materials import materials_cli

        return materials_cli(args_list[1:])
    parser = build_parser()
    args = parser.parse_args(args_list)
    raw_inputs = list(args.input)
    inputs = collect_inputs(raw_inputs)
    if not inputs:
        parser.error("В папке нет поддерживаемых файлов")
    batch_layout = len(inputs) > 1 or any(Path(item).is_dir() for item in raw_inputs)
    missing = [path for path in inputs if not path.exists()]
    if missing:
        parser.error(f"Файл не найден: {missing[0]}")
    try:
        meta = load_titleblock(args.titleblock)
    except Exception as exc:
        parser.error(f"Не удалось прочитать --titleblock: {exc}")

    if args.compare:
        other = Path(args.compare)
        if not other.exists():
            parser.error(f"Файл не найден: {other}")
        from .web_extra import _compare_paths

        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        left = process_file(inputs[0], out / "a", fix=False, assumed_format=args.sheet_format)
        right = process_file(other, out / "b", fix=False, assumed_format=args.sheet_format)
        diff = _compare_paths(inputs[0], other, left, right, out / "diff.png")
        print(
            "Сравнение: "
            f"добавлено={diff['entities']['added']}, "
            f"удалено={diff['entities']['removed']}, "
            f"изменено={diff['entities']['changed']}"
        )
        print(f"Пропавшие замечания: {len(diff['checks']['disappeared'])}, новые: {len(diff['checks']['appeared'])}")
        if (out / "diff.png").is_file():
            print(f"Картинка: {out / 'diff.png'}")
        return 0

    worst = 0
    for path in inputs:
        target = Path(args.out) / path.stem if batch_layout else Path(args.out)
        result = process_file(
            path,
            target,
            fix=args.fix,
            title_meta=meta,
            assumed_format=args.sheet_format,
            render=args.render,
            assign_layers=args.assign_layers,
            highlight=args.highlight,
            export_pdf=args.pdf,
            export_xlsx=args.xlsx,
            telegram_chat_id=args.telegram_chat,
        )
        if batch_layout:
            print(f"== {path.name}")
        _print_result(result)
        if result.summary.get("error", 0):
            worst = 2
    print("Это не полный нормоконтроль: проверены только правила из отчёта.")
    return worst


if __name__ == "__main__":
    sys.exit(main())
