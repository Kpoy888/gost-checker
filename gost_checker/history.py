"""История проверок. Живёт отдельно от временных заданий сайта."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_LOCK = threading.Lock()


def history_root() -> Path:
    raw = (os.environ.get("GOST_HISTORY_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.cwd() / "data" / "history"


def db_path() -> Path:
    raw = (os.environ.get("GOST_HISTORY_DB") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return history_root() / "history.sqlite"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            kind TEXT,
            errors INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0,
            infos INTEGER DEFAULT 0,
            oks INTEGER DEFAULT 0,
            fixed INTEGER DEFAULT 0,
            summary_json TEXT,
            ruleset_version TEXT,
            source_relpath TEXT,
            report_relpath TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_checks_sha ON checks(sha256)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_checks_name ON checks(filename)")
    return conn


def _rel(path: Path) -> str:
    root = history_root().resolve()
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(root))
    except ValueError:
        return path.name


def record_check(result, source: Optional[Path] = None) -> Optional[int]:
    """Скопировать отчёт (и исходник, если есть) и записать строку. Ошибки записи не роняют проверку."""
    try:
        summary = dict(getattr(result, "summary", None) or {})
        checks_brief = [
            {
                "id": check.check_id,
                "title": check.title,
                "severity": check.severity,
                "message": check.message,
            }
            for check in (getattr(result, "checks", None) or [])
        ]
        name = Path(getattr(result, "input_path", "") or "drawing").name
        digest = ""
        if source and Path(source).is_file():
            digest = file_sha256(Path(source))
        else:
            digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with _LOCK:
            conn = _connect()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO checks (
                        filename, sha256, created_at, kind, errors, warnings, infos, oks, fixed,
                        summary_json, ruleset_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        digest,
                        created,
                        getattr(result, "kind", ""),
                        int(summary.get("error") or 0),
                        int(summary.get("warning") or 0),
                        int(summary.get("info") or 0),
                        int(summary.get("ok") or 0),
                        int(summary.get("fixed") or 0),
                        json.dumps({"summary": summary, "checks": checks_brief}, ensure_ascii=False),
                        str((getattr(result, "extra", None) or {}).get("ruleset_version") or ""),
                    ),
                )
                row_id = int(cur.lastrowid)
                folder = history_root() / str(row_id)
                folder.mkdir(parents=True, exist_ok=True)
                report_rel = ""
                source_rel = ""
                for label, src in (
                    ("report.html", getattr(result, "report_html", None)),
                    ("report.md", getattr(result, "report_md", None)),
                    ("report.json", getattr(result, "report_json", None)),
                    ("issues.png", getattr(result, "render_issues", None)),
                ):
                    if src and Path(src).is_file():
                        target = folder / label
                        shutil.copy2(src, target)
                        if label == "report.html":
                            report_rel = _rel(target)
                pdf = (getattr(result, "extra", None) or {}).get("report_pdf")
                xlsx = (getattr(result, "extra", None) or {}).get("report_xlsx")
                for src in (pdf, xlsx):
                    if src and Path(src).is_file():
                        shutil.copy2(src, folder / Path(src).name)
                if source and Path(source).is_file() and Path(source).stat().st_size <= 80 * 1024 * 1024:
                    stored = folder / ("source" + Path(source).suffix.lower())
                    shutil.copy2(source, stored)
                    source_rel = _rel(stored)
                conn.execute(
                    "UPDATE checks SET report_relpath = ?, source_relpath = ? WHERE id = ?",
                    (report_rel, source_rel, row_id),
                )
                conn.commit()
                return row_id
            finally:
                conn.close()
    except Exception:
        return None


def list_checks(filename: str = "", sha256: str = "", limit: int = 100) -> list[dict]:
    conn = _connect()
    try:
        query = "SELECT * FROM checks WHERE 1=1"
        args: list = []
        if filename:
            query += " AND filename = ?"
            args.append(filename)
        if sha256:
            query += " AND sha256 = ?"
            args.append(sha256)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        rows = conn.execute(query, args).fetchall()
        return [_public_row(row) for row in rows]
    finally:
        conn.close()


def get_check(row_id: int) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM checks WHERE id = ?", (int(row_id),)).fetchone()
        return _public_row(row) if row else None
    finally:
        conn.close()


def delete_check(row_id: int) -> bool:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT id FROM checks WHERE id = ?", (int(row_id),)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM checks WHERE id = ?", (int(row_id),))
            conn.commit()
        finally:
            conn.close()
    folder = history_root() / str(int(row_id))
    if folder.is_dir():
        resolved = folder.resolve()
        root = history_root().resolve()
        if resolved.is_relative_to(root):
            shutil.rmtree(resolved, ignore_errors=True)
    return True


def stored_file(row_id: int, name: str) -> Optional[Path]:
    """Безопасный путь к файлу истории. Имена только из каталога записи."""
    safe = Path(name).name
    if safe != name or safe in {"", ".", ".."}:
        return None
    folder = (history_root() / str(int(row_id))).resolve()
    root = history_root().resolve()
    if not folder.is_relative_to(root):
        return None
    candidate = (folder / safe).resolve()
    if candidate.is_file() and candidate.is_relative_to(folder):
        return candidate
    return None


def _public_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    try:
        data["summary_json"] = json.loads(data.get("summary_json") or "{}")
    except json.JSONDecodeError:
        data["summary_json"] = {}
    return data
