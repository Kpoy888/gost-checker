"""Стандартизация слоёв, типов и толщин линий.

Имена слоёв — «профиль по умолчанию». Это не ISO 13567 и не сверенный перечень ГОСТ.
Толщины берутся из ряда ГОСТ 2.303, который уже использует проверка линий.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .checks.linetypes import mm_to_lw
from .constants import STANDARD_LAYERS

PROFILE_NAME = "профиль по умолчанию"
PROFILE_NOTE = (
    "Имена слоёв — профиль по умолчанию. Это не проверенный ISO 13567 и не пункт ГОСТ. "
    "Толщины взяты из ряда ГОСТ 2.303, уже используемого проверкой линий."
)

# Ключ ищется в имени слоя без учёта регистра. Сопоставление — эвристика.
ALIASES = {
    "стен": "SPDS_OSN",
    "wall": "SPDS_OSN",
    "ось": "SPDS_OS",
    "оси": "SPDS_OS",
    "axis": "SPDS_OS",
    "размер": "SPDS_DIM",
    "dim": "SPDS_DIM",
    "текст": "SPDS_TEXT",
    "text": "SPDS_TEXT",
    "скрыт": "SPDS_NEVID",
    "невид": "SPDS_NEVID",
    "рамк": "SPDS_FRAME",
    "штамп": "SPDS_STAMP",
    "тонк": "SPDS_TON",
}

PROTECTED = {"DEFPOINTS"}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_profile(source) -> dict:
    if source is None:
        return {"name": PROFILE_NAME, "layers": _default_layers()}
    if isinstance(source, dict):
        data = dict(source)
    else:
        text = Path(source).read_text(encoding="utf-8")
        data = json.loads(text) if text.lstrip().startswith("{") else _parse_yaml(text)
    layers = data.get("layers") or _default_layers()
    return {"name": str(data.get("name") or PROFILE_NAME), "layers": layers}


def _default_layers() -> dict:
    layers = {}
    for key, target in ALIASES.items():
        spec = STANDARD_LAYERS[target]
        layers[key] = {
            "target": target,
            "linetype": spec["linetype"],
            "lineweight_mm": spec["lineweight"] / 100.0,
        }
    return layers


def _parse_yaml(text: str) -> dict:
    data: dict = {"layers": {}}
    current = None
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0 and line.startswith("name:"):
            data["name"] = line.split(":", 1)[1].strip()
            continue
        if line == "layers:":
            continue
        if line.endswith(":") and ":" == line[-1]:
            current = line[:-1].strip()
            data["layers"][current] = {}
            continue
        if current and ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "lineweight_mm":
                data["layers"][current][key] = float(value.replace(",", "."))
            else:
                data["layers"][current][key] = value
    return data


def _match(layer_name: str, layers: dict) -> dict | None:
    folded = layer_name.casefold()
    best = None
    best_len = -1
    for key, spec in layers.items():
        token = str(key).casefold()
        if token and token in folded and len(token) > best_len:
            best = spec
            best_len = len(token)
    return best


def _ensure_linetype(doc, name: str) -> str:
    wanted = name or "Continuous"
    existing = {item.dxf.name.upper() for item in doc.linetypes}
    if wanted.upper() in existing:
        return wanted
    try:
        pattern = [1.25, -0.25, 0.25, -0.25] if "CENTER" in wanted.upper() else [0.75, -0.25]
        doc.linetypes.add(wanted, pattern=pattern, description=wanted)
        return wanted
    except Exception:
        return "Continuous"


def standardize(src: Path, dest: Path | None = None, profile=None, dry_run: bool = True) -> dict:
    import ezdxf

    src = Path(src)
    before = file_sha256(src)
    loaded = load_profile(profile)
    doc = ezdxf.readfile(str(src))
    plan = []
    unmatched = []
    for layer in doc.layers:
        name = str(layer.dxf.name)
        if name.upper() in PROTECTED or name.startswith("*"):
            continue
        spec = _match(name, loaded["layers"])
        if not spec or not spec.get("target"):
            unmatched.append(name)
            continue
        if name == spec["target"]:
            continue
        plan.append(
            {
                "from": name,
                "to": spec["target"],
                "linetype": spec.get("linetype") or "Continuous",
                "lineweight_mm": float(spec.get("lineweight_mm") or 0.25),
            }
        )
    written = False
    if not dry_run and dest is not None:
        by_source = {item["from"]: item for item in plan}
        for item in plan:
            linetype = _ensure_linetype(doc, item["linetype"])
            if item["to"] not in doc.layers:
                doc.layers.add(
                    item["to"],
                    linetype=linetype,
                    lineweight=mm_to_lw(item["lineweight_mm"]),
                )
            else:
                target = doc.layers.get(item["to"])
                target.dxf.linetype = linetype
                target.dxf.lineweight = mm_to_lw(item["lineweight_mm"])
        for entity in doc.modelspace():
            item = by_source.get(str(entity.dxf.layer))
            if not item:
                continue
            entity.dxf.layer = item["to"]
            if entity.dxf.hasattr("linetype"):
                entity.dxf.linetype = "BYLAYER"
            if entity.dxf.hasattr("lineweight"):
                entity.dxf.lineweight = -1
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(str(dest))
        written = True
    return {
        "profile": loaded["name"],
        "note": PROFILE_NOTE,
        "plan": plan,
        "unmatched": unmatched,
        "dry_run": dry_run,
        "written": written,
        "original_unchanged": file_sha256(src) == before,
    }


def layers_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check layers")
    parser.add_argument("input")
    parser.add_argument("--out")
    parser.add_argument("--profile")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    result = standardize(Path(args.input), Path(args.out) if args.out else None, args.profile, dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False))
    return 0
