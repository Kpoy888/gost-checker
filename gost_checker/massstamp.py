"""Массовая замена штампа в папке или zip.

Сначала атрибуты блока, затем текст внутри штампа, затем старый шифр,
и только если он встретился в штампе один раз. Пустое поле не угадывается.
DWG не пишется: эта запись отложена. PDF только проверяется.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from .stampcard import load_card

STAMP_W = 185.0
STAMP_H = 55.0
TAG_MAP = {
    "OBOZN": "обозначение",
    "NAIMEN": "наименование",
    "STADIYA": "стадия",
    "LIST": "лист",
    "LISTOV": "листов",
    "ORG": "организация",
}
DWG_ERROR = "DWG не изменён: запись DWG относится к отложенной части пункта 12."


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stamp_box(doc) -> tuple[float, float, float, float] | None:
    for entity in doc.modelspace().query("INSERT"):
        name = str(entity.dxf.name or "").upper()
        layer = str(entity.dxf.layer or "").upper()
        if "SPDS_F" in name or "STAMP" in layer:
            ins = entity.dxf.insert
            return (float(ins.x), float(ins.y), float(ins.x) + STAMP_W, float(ins.y) + STAMP_H)
    return None


def _inside(point, box) -> bool:
    if box is None:
        return False
    x, y = point
    return box[0] - 1 <= x <= box[2] + 1 and box[1] - 1 <= y <= box[3] + 1


def _provided(card: dict) -> dict:
    return {key: str(value) for key, value in card.items() if str(value or "").strip()}


def update_one(src: Path, dest: Path | None, card: dict, *, old_cipher: str = "", renumber: tuple[str, str] | None = None, dry_run: bool = True) -> dict:
    src = Path(src)
    before = _sha(src)
    suffix = src.suffix.lower()
    if suffix == ".dwg":
        return {"file": src.name, "ok": False, "written": False, "error": DWG_ERROR, "original_unchanged": True}
    if suffix == ".pdf":
        import fitz

        text = ""
        document = fitz.open(str(src))
        try:
            text = "\n".join(page.get_text() for page in document)
        finally:
            document.close()
        cipher = str(card.get("обозначение") or old_cipher)
        return {
            "file": src.name,
            "ok": True,
            "written": False,
            "check_only": True,
            "found": bool(cipher and cipher in text),
            "error": "",
            "original_unchanged": _sha(src) == before,
            "note": "PDF только проверяется, файл не изменяется.",
        }
    import ezdxf

    doc = ezdxf.readfile(str(src))
    given = _provided(card)
    if renumber:
        given["лист"] = renumber[0]
        given["листов"] = renumber[1]
    warnings = []
    status = "атрибуты"
    inserts = [
        entity
        for entity in doc.modelspace().query("INSERT")
        if "SPDS_F" in str(entity.dxf.name or "").upper() or "STAMP" in str(entity.dxf.layer or "").upper()
    ]
    tagged = []
    for insert in inserts:
        for attrib in insert.attribs:
            tag = str(attrib.dxf.tag or "").upper()
            if tag in TAG_MAP:
                tagged.append(attrib)
    ambiguous = False
    changed = False
    if tagged:
        by_tag: dict[str, list] = {}
        for attrib in tagged:
            by_tag.setdefault(str(attrib.dxf.tag or "").upper(), []).append(attrib)
        if any(len(items) > 1 for items in by_tag.values()):
            ambiguous = True
            status = "неоднозначно"
        else:
            for attrib in tagged:
                key = TAG_MAP[str(attrib.dxf.tag or "").upper()]
                if key not in given:
                    continue
                value = given[key]
                height = float(attrib.dxf.height or 2.5)
                if len(value) * 0.6 * height > 80:
                    warnings.append(f"Поле {key} длинное и может не влезть в графу.")
                    value = value[:40]
                if not dry_run:
                    attrib.dxf.text = value
                changed = True
    else:
        box = _stamp_box(doc)
        texts = [entity for entity in doc.modelspace().query("TEXT") if _inside((entity.dxf.insert.x, entity.dxf.insert.y), box)]
        matches = [entity for entity in texts if old_cipher and str(entity.dxf.text or "") == old_cipher]
        outside = [
            entity
            for entity in doc.modelspace().query("TEXT")
            if old_cipher and str(entity.dxf.text or "") == old_cipher and entity not in matches
        ]
        if not old_cipher:
            status = "нет атрибутов"
        elif len(matches) != 1:
            ambiguous = True
            status = "неоднозначно" if len(matches) > 1 else "не найден"
        else:
            status = "шифр внутри штампа"
            changed = True
            if not dry_run:
                matches[0].dxf.text = str(card.get("обозначение") or matches[0].dxf.text)
            del outside
    written = False
    checker = None
    if not dry_run and changed and not ambiguous and dest is not None:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(str(dest))
        written = True
        try:
            from .engine import process_file

            result = process_file(dest, dest.parent / "check", fix=False)
            checker = {"errors": result.summary.get("error", 0), "ok": True}
        except Exception as exc:
            checker = {"ok": False, "error": str(exc)}
    return {
        "file": src.name,
        "ok": not ambiguous and status != "не найден",
        "written": written,
        "status": status,
        "warnings": warnings,
        "ambiguous": ambiguous,
        "checker": checker,
        "original_unchanged": _sha(src) == before,
        "error": "",
    }


def _collect(src: Path, work: Path) -> list[Path]:
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as archive:
            archive.extractall(work)
        return sorted(path for path in work.rglob("*") if path.suffix.lower() in {".dxf", ".dwg", ".pdf"} and path.is_file())
    if src.is_dir():
        return sorted(path for path in src.iterdir() if path.suffix.lower() in {".dxf", ".dwg", ".pdf"})
    return [src]


def update_set(src: Path, out_dir: Path, card, *, old_cipher: str = "", renumber: bool = False, dry_run: bool = True) -> dict:
    import tempfile

    card = load_card(card)
    out_dir = Path(out_dir)
    with tempfile.TemporaryDirectory() as tmp:
        files = _collect(Path(src), Path(tmp))
        rows = []
        total = len([path for path in files if path.suffix.lower() == ".dxf"])
        dxf_index = 0
        for path in files:
            numbers = None
            if renumber and path.suffix.lower() == ".dxf":
                dxf_index += 1
                numbers = (str(dxf_index), str(total))
            dest = None if dry_run else out_dir / path.name
            rows.append(update_one(path, dest, card, old_cipher=old_cipher, renumber=numbers, dry_run=dry_run))
    return {"dry_run": dry_run, "files": rows, "note": "Пустое поле не заполняется наугад. Формы 4 и 5 остаются практической сеткой."}


def stamps_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check stamps")
    parser.add_argument("input")
    parser.add_argument("--card", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--old-cipher", default="")
    parser.add_argument("--renumber", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    result = update_set(Path(args.input), Path(args.out), args.card, old_cipher=args.old_cipher, renumber=args.renumber, dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False))
    return 0
