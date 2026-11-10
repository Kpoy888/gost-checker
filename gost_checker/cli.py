from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import process_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gost-check",
        description=(
            "Проверка оформления строительного чертежа по отдельным правилам СПДС/ЕСКД. "
            "Не является полным нормоконтролем."
        ),
    )
    parser.add_argument("input", help="Файл DXF, DWG, PDF, PNG/JPG или IFC")
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
    return parser


def load_titleblock(path: str | None) -> dict:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON основной надписи должен быть объектом")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_path = Path(args.input)
    if not input_path.exists():
        parser.error(f"Файл не найден: {input_path}")
    try:
        meta = load_titleblock(args.titleblock)
    except Exception as exc:
        parser.error(f"Не удалось прочитать --titleblock: {exc}")

    result = process_file(
        input_path,
        args.out,
        fix=args.fix,
        title_meta=meta,
        assumed_format=args.sheet_format,
        render=args.render,
        assign_layers=args.assign_layers,
    )
    print(f"Отчёт: {result.report_html}")
    print(f"Текст: {result.report_md}")
    if result.output_dxf:
        print(f"DXF:  {result.output_dxf}")
    if result.output_dwg:
        print(f"DWG:  {result.output_dwg}")
    print(
        "Итог: "
        f"ок={result.summary.get('ok', 0)}, "
        f"сведения={result.summary.get('info', 0)}, "
        f"замечания={result.summary.get('warning', 0)}, "
        f"ошибки={result.summary.get('error', 0)}, "
        f"исправлено={result.summary.get('fixed', 0)}"
    )
    print("Это не полный нормоконтроль: проверены только правила из отчёта.")
    if result.summary.get("error", 0):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
