"""Конвертация DWG <-> DXF только через бесплатные внешние утилиты."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]


LIBREDWG_DXF_NAMES = ("dwg2dxf",)
LIBREDWG_DWG_NAMES = ("dxf2dwg",)
ODA_NAMES = (
    "ODAFileConverter",
    "odafileconverter",
    "ODAFileConverter.sh",
)


def which_any(names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _bin_dirs() -> list[Path]:
    dirs = [
        PROJECT_ROOT / "vendor" / "libredwg" / "bin",
        Path.home() / ".local" / "bin",
        Path("/usr/local/bin"),
    ]
    extra = os.environ.get("LIBREDWG_PREFIX")
    if extra:
        dirs.insert(0, Path(extra) / "bin")
    return dirs


def _look_in_dirs(filename: str) -> Optional[str]:
    for directory in _bin_dirs():
        candidate = directory / filename
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def find_libredwg_dwg2dxf() -> Optional[str]:
    return which_any(LIBREDWG_DXF_NAMES) or _look_in_dirs("dwg2dxf")


def find_libredwg_dxf2dwg() -> Optional[str]:
    return which_any(LIBREDWG_DWG_NAMES) or _look_in_dirs("dxf2dwg")


def _libredwg_env(binary: str) -> dict:
    """Подхватить libredwg.so рядом с vendor/.../bin, даже если RPATH абсолютный."""
    env = os.environ.copy()
    libdir = Path(binary).resolve().parent.parent / "lib"
    if libdir.is_dir():
        prev = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = str(libdir) + ((os.pathsep + prev) if prev else "")
    return env


def run_converter(cmd: list[str], timeout: int, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """Запуск внешней утилиты. stderr в cp1251 не должен ронять уже успешную конвертацию."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=env,
    )


def _prepare_dxf_for_libredwg(src: Path, out_dir: Path) -> Path:
    """LibreDWG dxf2dwg устойчиво пишет только r12–r2000."""
    try:
        import ezdxf

        doc = ezdxf.readfile(str(src))
        version = str(getattr(doc, "dxfversion", "") or "")
        if version and version > "AC1015":
            tmp = out_dir / f"{src.stem}.r2000.dxf"
            doc.dxfversion = "AC1015"
            doc.saveas(str(tmp))
            return tmp
    except Exception:
        pass
    return src


def _next_dxf_handle(used: set[str]) -> str:
    value = 1
    for item in used:
        try:
            value = max(value, int(item, 16) + 1)
        except ValueError:
            continue
    while True:
        handle = f"{value:X}"
        if handle not in used and handle != "0":
            return handle
        value += 1


def sanitize_libredwg_dxf(path: Path) -> bool:
    """LibreDWG иногда пишет ENDBLK с handle 0 и повторяющиеся handle — ezdxf такое не открывает."""
    path = Path(path)
    try:
        lines = path.read_text(encoding="latin-1").splitlines()
    except OSError:
        return False
    if len(lines) < 4:
        return False
    used: set[str] = set()
    for index in range(0, len(lines) - 1, 2):
        if lines[index].strip() == "5":
            used.add(lines[index + 1].strip().upper())
    seen: set[str] = set()
    changed = False
    out = list(lines)
    for index in range(0, len(out) - 1, 2):
        if out[index].strip() != "5":
            continue
        handle = out[index + 1].strip().upper()
        invalid = (not handle.lstrip("0")) or handle in seen
        if invalid:
            fresh = _next_dxf_handle(used | seen)
            out[index + 1] = fresh
            used.add(fresh)
            seen.add(fresh)
            changed = True
        else:
            seen.add(handle)
    if changed:
        path.write_text("\n".join(out) + "\n", encoding="latin-1")
    return changed


def _dxf_entity_count(path: Path) -> int:
    try:
        import ezdxf
        from ezdxf import recover

        try:
            doc = ezdxf.readfile(str(path))
        except Exception:
            doc, _auditor = recover.readfile(str(path))
        return sum(1 for _entity in doc.modelspace())
    except Exception:
        return -1


def find_oda() -> Optional[str]:
    found = which_any(ODA_NAMES)
    if found:
        return found
    extra = [
        Path("/usr/bin/ODAFileConverter"),
        Path("/usr/local/bin/ODAFileConverter"),
        Path("/opt/oda/odafileconverter/ODAFileConverter"),
        Path.home() / "ODAFileConverter",
    ]
    for path in extra:
        if path.exists() and path.is_file():
            return str(path)
    return None


def converter_status() -> dict[str, Optional[str]]:
    return {
        "dwg2dxf": find_libredwg_dwg2dxf(),
        "dxf2dwg": find_libredwg_dxf2dwg(),
        "oda": find_oda(),
    }


def dwg_to_dxf(src: Path, out_dir: Path) -> tuple[Optional[Path], str]:
    """Попытаться получить DXF из DWG. Возвращает (путь, сообщение)."""
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / (src.stem + ".converted.dxf")

    dwg2dxf = find_libredwg_dwg2dxf()
    if dwg2dxf:
        try:
            proc = run_converter(
                [dwg2dxf, "-y", "-o", str(dest), str(src)],
                timeout=120,
                env=_libredwg_env(dwg2dxf),
            )
            produced = dest if dest.exists() and dest.stat().st_size > 0 else None
            if produced is None:
                sidecar = src.with_suffix(".dxf")
                if sidecar.exists() and sidecar.stat().st_size > 0:
                    shutil.copy2(sidecar, dest)
                    produced = dest
            if produced is not None:
                extra = ""
                if sanitize_libredwg_dxf(produced):
                    extra = " Исправлены нулевые или повторяющиеся handle, которые LibreDWG иногда записывает."
                count = _dxf_entity_count(produced)
                if count == 0 or count == -1:
                    minimal = out_dir / (src.stem + ".converted.min.dxf")
                    run_converter(
                        [dwg2dxf, "-y", "-m", "-o", str(minimal), str(src)],
                        timeout=120,
                        env=_libredwg_env(dwg2dxf),
                    )
                    if minimal.exists() and _dxf_entity_count(minimal) > 0:
                        return minimal, (
                            f"DWG преобразован в DXF утилитой LibreDWG ({dwg2dxf}, режим --minimal): "
                            "полный DXF был структурно повреждён, сохранены сущности модели без таблиц слоёв."
                        )
                    minimal.unlink(missing_ok=True)
                    if count > 0:
                        return produced, f"DWG преобразован в DXF утилитой LibreDWG ({dwg2dxf}).{extra}"
                    if count == 0:
                        return produced, (
                            f"DWG преобразован в DXF утилитой LibreDWG ({dwg2dxf}), "
                            f"но в пространстве модели не видно сущностей.{extra}"
                        )
                return produced, f"DWG преобразован в DXF утилитой LibreDWG ({dwg2dxf}).{extra}"
            return None, (
                "LibreDWG dwg2dxf найден, но не смог прочитать файл "
                f"(код {proc.returncode}): {(proc.stderr or proc.stdout or '')[:400]}"
            )
        except Exception as exc:
            return None, f"Ошибка запуска LibreDWG dwg2dxf: {exc}"

    oda = find_oda()
    if oda:
        tmp_in = out_dir / "_oda_in"
        tmp_out = out_dir / "_oda_out"
        try:
            tmp_in.mkdir(exist_ok=True)
            tmp_out.mkdir(exist_ok=True)
            shutil.copy2(src, tmp_in / src.name)
            proc = run_converter(
                [oda, str(tmp_in), str(tmp_out), "DWG", "ACAD2018", "DXF", "0", "0"],
                timeout=180,
            )
            produced = list(tmp_out.glob("*.dxf")) + list(tmp_out.glob("*.DXF"))
            if produced:
                shutil.copy2(produced[0], dest)
                return dest, f"DWG преобразован в DXF через ODA File Converter ({oda})."
            return None, (
                "ODA File Converter найден, но DXF не появился "
                f"(код {proc.returncode}): {(proc.stderr or proc.stdout or '')[:400]}"
            )
        except Exception as exc:
            return None, f"Ошибка запуска ODA File Converter: {exc}"
        finally:
            shutil.rmtree(tmp_in, ignore_errors=True)
            shutil.rmtree(tmp_out, ignore_errors=True)

    return None, (
        "Конвертер DWG не найден. Запустите scripts/install_dwg.sh "
        "(соберёт GNU LibreDWG в vendor/libredwg) или установите пакет libredwg-tools. "
        "Запасной вариант — ODA File Converter "
        "(https://www.opendesign.com/guestfiles/oda_file_converter). "
        "Пока можно экспортировать чертёж в DXF из nanoCAD, Renga или Revit."
    )


def dxf_to_dwg(src: Path, out_dir: Path) -> tuple[Optional[Path], str]:
    src = Path(src)
    out_dir = Path(out_dir)
    dest = out_dir / (src.stem + ".dwg")

    dxf2dwg = find_libredwg_dxf2dwg()
    if dxf2dwg:
        work = _prepare_dxf_for_libredwg(src, out_dir)
        try:
            proc = run_converter(
                [dxf2dwg, "-y", "--as", "r2000", "-o", str(dest), str(work)],
                timeout=120,
                env=_libredwg_env(dxf2dwg),
            )
            if dest.exists() and dest.stat().st_size > 0:
                extra = ""
                log = (proc.stderr or proc.stdout or "")
                if "ERROR" in log:
                    extra = " LibreDWG сообщил об ошибках кодирования; надёжнее открывать исправленный DXF."
                return dest, (
                    f"Исправленный файл также сохранён в DWG через LibreDWG ({dxf2dwg}, r2000).{extra}"
                )
            return None, (
                f"LibreDWG dxf2dwg не создал DWG (код {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '')[:400]}"
            )
        except Exception as exc:
            return None, f"Ошибка запуска LibreDWG dxf2dwg: {exc}"
        finally:
            if work != src:
                try:
                    work.unlink()
                except OSError:
                    pass

    oda = find_oda()
    if oda:
        tmp_in = out_dir / "_oda_dxf_in"
        tmp_out = out_dir / "_oda_dwg_out"
        try:
            tmp_in.mkdir(exist_ok=True)
            tmp_out.mkdir(exist_ok=True)
            shutil.copy2(src, tmp_in / src.name)
            proc = run_converter(
                [oda, str(tmp_in), str(tmp_out), "DXF", "ACAD2018", "DWG", "0", "0"],
                timeout=180,
            )
            produced = list(tmp_out.glob("*.dwg")) + list(tmp_out.glob("*.DWG"))
            if produced:
                shutil.copy2(produced[0], dest)
                return dest, f"Исправленный файл также сохранён в DWG через ODA File Converter ({oda})."
            return None, f"ODA File Converter не создал DWG (код {proc.returncode})."
        except Exception as exc:
            return None, f"Ошибка запуска ODA File Converter: {exc}"
        finally:
            shutil.rmtree(tmp_in, ignore_errors=True)
            shutil.rmtree(tmp_out, ignore_errors=True)

    return None, (
        "Запись DWG недоступна: нет LibreDWG `dxf2dwg` (см. scripts/install_dwg.sh) и нет ODA File Converter. "
        "Выдан исправленный DXF — его открывают nanoCAD, Renga и Revit."
    )
