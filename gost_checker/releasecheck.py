"""Финальная проверка комплекта. Коллизии систем и линии совмещения не входят: эти пункты отменены."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .clearance import check_file as clearance_check
from .dimconflict import check_file as dim_check
from .electrical import check_file as gap_check
from .engine import process_file
from .stampcard import empty_fields, load_card


def _safe(name: str, func):
    try:
        return func(), {"check": name, "ok": True}
    except Exception as exc:
        return [{"priority": "blocking", "type": "checker_error", "sheet": "", "text": f"{name}: {exc}"}], {"check": name, "ok": False}


def _from_check(result, sheet: str) -> list[dict]:
    items = []
    priority = {"error": "blocking", "warning": "warning", "info": "info", "ok": "info"}.get(result.severity, "info")
    if result.severity == "ok":
        return []
    items.append({
        "priority": priority,
        "type": result.check_id,
        "sheet": sheet,
        "text": result.message,
        "gost": result.gost,
    })
    return items


def release_check(paths: list[Path], *, card: dict | None = None, rules: dict | None = None, extra_checkers: list | None = None) -> dict:
    findings = []
    progress = []
    jobs = []
    for path in paths:
        sheet = Path(path).name

        def gost(path=path, sheet=sheet):
            result = process_file(path, Path(path).parent / f"rel-{Path(path).stem}", fix=False, highlight=False)
            rows = []
            for check in result.checks:
                if check.severity in {"error", "warning"} and check.check_id in {"sheet_frame", "title_block", "fonts", "dimensions"}:
                    rows.extend(_from_check(check, sheet))
            return rows

        def clearance(path=path, sheet=sheet):
            return _from_check(clearance_check(path), sheet)

        def dims(path=path, sheet=sheet):
            return _from_check(dim_check(path), sheet)

        def gaps(path=path, sheet=sheet):
            return _from_check(gap_check(path, rules), sheet)

        jobs.append(("gost", gost))
        jobs.append(("clearance", clearance))
        jobs.append(("dim_conflict", dims))
        jobs.append(("electrical_gap", gaps))
    if card is not None:
        def stamp():
            missing = empty_fields(card)
            if not missing:
                return []
            return [{"priority": "blocking", "type": "stamp", "sheet": "", "text": "Пустые поля штампа: " + ", ".join(missing)}]

        jobs.append(("stamp", stamp))
    for checker in extra_checkers or []:
        jobs.append((checker["name"], checker["func"]))

    def run(job):
        name, func = job
        return _safe(name, func)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for rows, event in pool.map(run, jobs):
            findings.extend(rows)
            progress.append(event)
    unique = []
    seen = set()
    for item in findings:
        key = (item.get("type"), item.get("sheet"), item.get("text"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    unique.sort(key=lambda item: ({"blocking": 0, "warning": 1, "info": 2}.get(item["priority"], 3), item.get("sheet") or "", item.get("type") or ""))
    blocking = [item for item in unique if item["priority"] == "blocking"]
    return {"can_release": not blocking, "findings": unique, "progress": progress}


def release_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check release-check")
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("--card")
    parser.add_argument("--rules")
    args = parser.parse_args(argv)
    card = load_card(args.card) if args.card else None
    rules = json.loads(Path(args.rules).read_text(encoding="utf-8")) if args.rules else None
    payload = release_check([Path(item) for item in args.inputs], card=card, rules=rules)
    print(json.dumps({"can_release": payload["can_release"], "count": len(payload["findings"])}, ensure_ascii=False))
    return 0 if payload["can_release"] else 2
