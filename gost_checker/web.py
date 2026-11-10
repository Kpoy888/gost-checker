from __future__ import annotations

import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool

from . import engine as engine_mod
from .engine import process_file
from .report import DISCLAIMER

JOBS: dict[str, tuple[Path, float]] = {}
_JOBS_LOCK = threading.Lock()
JOB_TTL_SEC = 3600
MAX_JOBS = 24
_UPLOAD_CHUNK = 1024 * 1024
_SAFE_STEM = re.compile(r"[^\w.\-а-яА-ЯёЁ]+", re.UNICODE)
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "index.html"


def safe_upload_name(filename: Optional[str]) -> str:
    raw = (filename or "drawing.bin").replace("\x00", "").replace(chr(92), "/")
    name = Path(raw).name
    if name in {"", ".", ".."}:
        name = "drawing.bin"
    stem = _SAFE_STEM.sub("_", Path(name).stem)[:80].strip("._") or "drawing"
    suffix = Path(name).suffix.lower()[:12]
    if suffix in {".", ""}:
        suffix = ".bin"
    return f"{stem}{suffix}"


def safe_download_name(filename: str) -> str:
    raw = (filename or "").replace("\x00", "")
    if not raw or raw in {".", ".."} or "/" in raw or "\\" in raw or ".." in raw:
        raise HTTPException(status_code=400, detail="Недопустимое имя файла")
    name = Path(raw).name
    if name != raw or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Недопустимое имя файла")
    return name


def cleanup_jobs(now: Optional[float] = None) -> int:
    """Удалить просроченные и лишние временные каталоги заданий."""
    now = time.time() if now is None else now
    removed = 0
    with _JOBS_LOCK:
        stale = [job_id for job_id, (_path, ts) in JOBS.items() if now - ts > JOB_TTL_SEC]
        keep = [(job_id, ts) for job_id, (_path, ts) in JOBS.items() if job_id not in stale]
        if len(keep) >= MAX_JOBS:
            keep.sort(key=lambda item: item[1])
            overflow = len(keep) - (MAX_JOBS - 1)
            stale.extend(job_id for job_id, _ts in keep[:overflow])
        for job_id in stale:
            path, _ts = JOBS.pop(job_id, (None, 0.0))
            if path:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    return removed


def _register_job(job_id: str, work: Path) -> None:
    with _JOBS_LOCK:
        JOBS[job_id] = (work, time.time())


def _job_dir(job_id: str) -> Optional[Path]:
    with _JOBS_LOCK:
        item = JOBS.get(job_id)
    return item[0] if item else None


async def save_upload_limited(upload: UploadFile, dest: Path, limit: int) -> int:
    written = 0
    with dest.open("wb") as handle:
        while True:
            chunk = await upload.read(_UPLOAD_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                handle.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"Файл слишком большой. Для этой программы предел — {limit / (1024 * 1024):.0f} МБ.",
                )
            handle.write(chunk)
    return written


def create_app() -> FastAPI:
    app = FastAPI(title="Проверка оформления чертежа", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return TEMPLATE.read_text(encoding="utf-8")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "service": "gost-checker", "ttl_sec": JOB_TTL_SEC}

    @app.post("/api/check")
    async def api_check(
        file: UploadFile = File(...),
        fix: str = Form("true"),
        sheet_format: str = Form("A3"),
        assign_layers: str = Form("false"),
        обозначение: str = Form(""),
        наименование: str = Form(""),
        объект: str = Form(""),
        здание: str = Form(""),
        стадия: str = Form(""),
        лист: str = Form(""),
        листов: str = Form(""),
        разраб: str = Form(""),
        пров: str = Form(""),
        н_контр: str = Form(""),
        утв: str = Form(""),
        организация: str = Form(""),
        preview: str = Form("false"),
    ):
        cleanup_jobs()
        job_id = uuid.uuid4().hex
        work = Path(tempfile.mkdtemp(prefix=f"gost_{job_id}_"))
        raw_name = safe_upload_name(file.filename)
        src = work / raw_name
        try:
            await save_upload_limited(upload=file, dest=src, limit=engine_mod.MAX_INPUT_BYTES)
        except HTTPException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(work, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"Не удалось сохранить файл: {exc}") from exc
        _register_job(job_id, work)
        meta = {
            "обозначение": обозначение,
            "наименование": наименование,
            "объект": объект,
            "здание": здание,
            "стадия": стадия,
            "лист": лист,
            "листов": листов,
            "разраб": разраб,
            "пров": пров,
            "н_контр": н_контр,
            "утв": утв,
            "организация": организация,
        }
        do_fix = str(fix).lower() not in {"0", "false", "no", "off"}
        do_assign = str(assign_layers).lower() not in {"0", "false", "no", "off"}
        do_preview = str(preview).lower() not in {"0", "false", "no", "off"}
        try:
            result = await run_in_threadpool(
                process_file,
                src,
                work / "out",
                do_fix,
                meta,
                sheet_format,
                do_preview,
                do_assign,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        downloads = []
        mapping = [
            ("Отчёт HTML", result.report_html, "report.html"),
            ("Отчёт Markdown", result.report_md, "report.md"),
            ("Отчёт JSON", result.report_json, "report.json"),
            ("Исправленный DXF", result.output_dxf, Path(result.output_dxf).name if result.output_dxf else ""),
            ("Исправленный DWG", result.output_dwg, Path(result.output_dwg).name if result.output_dwg else ""),
            ("Просмотр до", result.render_before, "before.png"),
            ("Просмотр после", result.render_after, "after.png"),
            ("Схема линий", result.render_overlay, "lines_overlay.png"),
        ]
        for title, path, filename in mapping:
            if path and Path(path).exists() and filename:
                downloads.append({"title": title, "url": f"/api/download/{job_id}/{filename}", "filename": filename})

        extra = result.extra or {}
        overlay_name = Path(result.render_overlay).name if result.render_overlay else "lines_overlay.png"
        checks = []
        for check in result.checks:
            details = list(check.details or [])
            if len(details) > 16:
                details = details[:16] + [f"… и ещё {len(check.details) - 16}"]
            checks.append(
                {
                    "id": check.check_id,
                    "title": check.title,
                    "gost": check.gost,
                    "severity": check.severity,
                    "message": check.message,
                    "details": details,
                    "can_autofix": check.can_autofix,
                    "auto_fixed": check.auto_fixed,
                }
            )
        return {
            "job_id": job_id,
            "kind": result.kind,
            "summary": result.summary,
            "disclaimer": DISCLAIMER,
            "notes": result.notes,
            "checks": checks,
            "downloads": downloads,
            "report_url": f"/api/download/{job_id}/report.html" if result.report_html else None,
            "margins_table": extra.get("margins_table") or [],
            "lines": extra.get("lines") or {},
            "overlay_url": f"/api/download/{job_id}/{overlay_name}" if result.render_overlay else None,
            "before_url": f"/api/download/{job_id}/before.png" if result.render_before else None,
            "after_url": f"/api/download/{job_id}/after.png" if result.render_after else None,
            "ttl_sec": JOB_TTL_SEC,
            "privacy": (
                "Загруженный файл и результаты проверки хранятся только на этом сервере "
                f"и автоматически удаляются через {JOB_TTL_SEC // 60} мин. "
                "Мы не передаём чертежи третьим лицам и не используем их ни для чего, кроме этой проверки."
            ),
        }

    @app.get("/api/download/{job_id}/{filename}")
    def api_download(job_id: str, filename: str):
        name = safe_download_name(filename)
        work = _job_dir(job_id)
        if not work:
            raise HTTPException(status_code=404, detail="Результат уже недоступен")
        try:
            root = work.resolve()
        except OSError as exc:
            raise HTTPException(status_code=404, detail="Результат уже недоступен") from exc
        candidates = [root / name, root / "out" / name]
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved.is_file() and resolved.is_relative_to(root):
                return FileResponse(resolved, filename=name)
        for found in root.rglob("*"):
            if not found.is_file() or found.name != name:
                continue
            try:
                resolved = found.resolve()
            except OSError:
                continue
            if resolved.is_relative_to(root):
                return FileResponse(resolved, filename=name)
        raise HTTPException(status_code=404, detail="Файл не найден")

    return app


app = create_app()


def main() -> None:
    import os
    import uvicorn

    port = int(os.environ.get("PORT", "7860"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("gost_checker.web:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
