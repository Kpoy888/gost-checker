"""Пакетная проверка, сравнение двух файлов и история. Не меняет /api/check."""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from .compare import diff_checks, diff_entities, load_raster_or_pdf, render_entity_diff_png, align_and_diff_images
from .engine import process_file
from .history import delete_check, get_check, list_checks, stored_file
from .report import DISCLAIMER
from .web import (
    MAX_JOBS,
    _job_dir,
    _register_job,
    _set_active,
    active_job_count,
    cleanup_jobs,
    safe_download_name,
    safe_upload_name,
    save_upload_limited,
)
from . import engine as engine_mod

MAX_BATCH_FILES = 8
MAX_BATCH_BYTES = 80 * 1024 * 1024


def _bool_form(value: str) -> bool:
    return str(value).lower() not in {"0", "false", "no", "off"}


def _zip_tree(folder: Path, dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            arc = path.relative_to(folder).as_posix()
            if ".." in arc.split("/"):
                continue
            archive.write(path, arc)


def register_extra(app) -> None:
    @app.post("/api/check-batch")
    async def api_check_batch(
        files: list[UploadFile] = File(...),
        fix: str = Form("true"),
        sheet_format: str = Form("A3"),
        assign_layers: str = Form("false"),
        telegram_chat_id: str = Form(""),
    ):
        cleanup_jobs()
        if not files:
            raise HTTPException(status_code=400, detail="Выберите хотя бы один файл")
        if len(files) > MAX_BATCH_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"Слишком много файлов. За один раз можно проверить не больше {MAX_BATCH_FILES}.",
            )
        if active_job_count() >= MAX_JOBS:
            raise HTTPException(status_code=429, detail="Слишком много одновременных проверок. Подождите и повторите.")
        job_id = uuid.uuid4().hex
        work = Path(tempfile.mkdtemp(prefix=f"gost_{job_id}_"))
        saved: list[Path] = []
        total = 0
        try:
            for upload in files:
                name = safe_upload_name(upload.filename)
                dest = work / f"{len(saved):02d}_{name}"
                written = await save_upload_limited(upload, dest, engine_mod.MAX_INPUT_BYTES)
                total += written
                if total > MAX_BATCH_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Суммарный размер файлов больше 80 МБ.",
                    )
                saved.append(dest)
        except HTTPException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(work, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"Не удалось сохранить файлы: {exc}") from exc
        _register_job(job_id, work)
        _set_active(job_id, True)
        do_fix = _bool_form(fix)
        do_assign = _bool_form(assign_layers)
        sheets = []
        try:
            for index, src in enumerate(saved):
                out = work / "out" / f"{index:02d}"
                result = await run_in_threadpool(
                    process_file,
                    src,
                    out,
                    do_fix,
                    {},
                    sheet_format,
                    False,
                    do_assign,
                    True,
                    True,
                    True,
                    True,
                    telegram_chat_id if index == 0 else "",
                )
                if result.report_html and Path(result.report_html).is_file():
                    shutil.copy2(result.report_html, work / f"report_{index}.html")
                sheets.append(
                    {
                        "filename": src.name.split("_", 1)[-1],
                        "kind": result.kind,
                        "summary": result.summary,
                        "issues": (result.extra or {}).get("issues") or [],
                        "report": f"/api/download/{job_id}/report_{index}.html" if result.report_html else None,
                    }
                )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            _set_active(job_id, False)
        bundle = work / "batch"
        bundle.mkdir(parents=True, exist_ok=True)
        for index, src in enumerate(saved):
            produced = work / "out" / f"{index:02d}"
            target = bundle / f"{index:02d}_{src.stem}"
            if produced.is_dir():
                shutil.copytree(produced, target, dirs_exist_ok=True)
        (bundle / "summary.json").write_text(json.dumps({"sheets": sheets}, ensure_ascii=False, indent=2), encoding="utf-8")
        zip_path = work / "batch.zip"
        _zip_tree(bundle, zip_path)
        totals = {"files": len(sheets), "error": 0, "warning": 0, "ok": 0}
        for sheet in sheets:
            summary = sheet.get("summary") or {}
            totals["error"] += int(summary.get("error") or 0)
            totals["warning"] += int(summary.get("warning") or 0)
            totals["ok"] += int(summary.get("ok") or 0)
        return {
            "job_id": job_id,
            "batch": True,
            "sheets": sheets,
            "totals": totals,
            "zip_url": f"/api/download/{job_id}/batch.zip",
            "disclaimer": DISCLAIMER,
            "downloads": [{"title": "Все отчёты и файлы (zip)", "url": f"/api/download/{job_id}/batch.zip", "filename": "batch.zip"}],
        }

    @app.post("/api/compare")
    async def api_compare(
        file_a: UploadFile = File(...),
        file_b: UploadFile = File(...),
    ):
        cleanup_jobs()
        if active_job_count() >= MAX_JOBS:
            raise HTTPException(status_code=429, detail="Слишком много одновременных проверок. Подождите и повторите.")
        job_id = uuid.uuid4().hex
        work = Path(tempfile.mkdtemp(prefix=f"gost_{job_id}_"))
        path_a = work / ("a_" + safe_upload_name(file_a.filename))
        path_b = work / ("b_" + safe_upload_name(file_b.filename))
        try:
            await save_upload_limited(file_a, path_a, engine_mod.MAX_INPUT_BYTES)
            await save_upload_limited(file_b, path_b, engine_mod.MAX_INPUT_BYTES)
        except HTTPException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        _register_job(job_id, work)
        _set_active(job_id, True)
        try:
            result_a = await run_in_threadpool(process_file, path_a, work / "a", False)
            result_b = await run_in_threadpool(process_file, path_b, work / "b", False)
            payload = await run_in_threadpool(_compare_paths, path_a, path_b, result_a, result_b, work / "diff.png")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            _set_active(job_id, False)
        payload["job_id"] = job_id
        if Path(work / "diff.png").is_file():
            payload["diff_url"] = f"/api/download/{job_id}/diff.png"
        payload["disclaimer"] = DISCLAIMER
        return payload

    @app.get("/api/history")
    def api_history(filename: str = "", sha256: str = ""):
        return {"items": list_checks(filename=filename, sha256=sha256)}

    @app.get("/api/history/{row_id}")
    def api_history_one(row_id: int):
        item = get_check(row_id)
        if not item:
            raise HTTPException(status_code=404, detail="Запись истории не найдена")
        return item

    @app.delete("/api/history/{row_id}")
    def api_history_delete(row_id: int):
        if not delete_check(row_id):
            raise HTTPException(status_code=404, detail="Запись истории не найдена")
        return {"ok": True}

    @app.get("/api/history/{row_id}/file/{filename}")
    def api_history_file(row_id: int, filename: str):
        safe_download_name(filename)
        path = stored_file(row_id, filename)
        if not path:
            raise HTTPException(status_code=404, detail="Файл истории не найден")
        return FileResponse(path, filename=path.name)


def _compare_paths(path_a: Path, path_b: Path, result_a, result_b, image_dest: Path) -> dict:
    import ezdxf

    def open_dxf(path: Path):
        if path.suffix.lower() != ".dxf":
            return None
        try:
            return ezdxf.readfile(str(path))
        except Exception:
            from ezdxf import recover

            doc, _auditor = recover.readfile(str(path))
            return doc

    doc_a = open_dxf(path_a)
    doc_b = open_dxf(path_b)
    entity_diff = None
    if doc_a is not None and doc_b is not None:
        entity_diff = diff_entities(doc_a, doc_b)
        render_entity_diff_png(doc_a, doc_b, entity_diff, image_dest)
    elif path_a.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"} and path_b.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}:
        image_a = load_raster_or_pdf(path_a)
        image_b = load_raster_or_pdf(path_b)
        if image_a is not None and image_b is not None:
            align_and_diff_images(image_a, image_b, image_dest)
            entity_diff = {"mode": "raster"}
    checks = diff_checks(result_a.checks, result_b.checks)
    return {
        "file_a": path_a.name,
        "file_b": path_b.name,
        "entities": {
            "added": (entity_diff or {}).get("added_count", 0),
            "removed": (entity_diff or {}).get("removed_count", 0),
            "changed": (entity_diff or {}).get("changed_count", 0),
        },
        "checks": checks,
        "summary_a": result_a.summary,
        "summary_b": result_b.summary,
    }
