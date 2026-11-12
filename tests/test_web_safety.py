from __future__ import annotations

import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from gost_checker.web import (
    JOBS,
    MAX_JOBS,
    active_job_count,
    cleanup_jobs,
    create_app,
    safe_download_name,
    safe_upload_name,
)


def test_safe_names_reject_traversal():
    assert safe_upload_name("../../etc/passwd.dxf") == "passwd.dxf"
    assert safe_upload_name("..\\secret.png") == "secret.png"
    assert "/" not in safe_upload_name("a/b/c.dxf")
    try:
        from fastapi import HTTPException
    except Exception as exc:  # pragma: no cover
        raise AssertionError(exc)
    try:
        safe_download_name("../etc/passwd")
        raise AssertionError("expected reject")
    except HTTPException as exc:
        assert exc.status_code == 400
    try:
        safe_download_name("a/../report.html")
        raise AssertionError("expected reject")
    except HTTPException as exc:
        assert exc.status_code == 400


def test_web_wrong_extension_does_not_crash():
    client = TestClient(create_app())
    response = client.post(
        "/api/check",
        files={"file": ("readme.txt", b"hello world", "text/plain")},
        data={"fix": "false", "sheet_format": "A3"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "unknown"
    assert any("неподдерживаемое" in note.lower() for note in payload.get("notes") or [])


def test_web_rejects_large_upload(monkeypatch):
    from gost_checker import engine

    monkeypatch.setattr(engine, "MAX_INPUT_BYTES", 1024)
    client = TestClient(create_app())
    before = set(JOBS)
    response = client.post(
        "/api/check",
        files={"file": ("big.dxf", b"A" * 5000, "application/dxf")},
        data={"fix": "false", "sheet_format": "A3"},
    )
    assert response.status_code == 413
    assert not (set(JOBS) - before)


def test_web_path_traversal_download(samples_dir):
    import gost_checker.web as web

    client = TestClient(create_app())
    data = (samples_dir / "good.dxf").read_bytes()
    response = client.post(
        "/api/check",
        files={"file": ("../../etc/passwd.dxf", data, "application/dxf")},
        data={"fix": "false", "sheet_format": "A3"},
    )
    assert response.status_code == 200, response.text[:400]
    job_id = response.json()["job_id"]
    work, _ts = web.JOBS[job_id]
    assert (work / "passwd.dxf").exists()
    assert not any("etc" in str(path) for path in work.rglob("*") if path.is_file() and path.parent != work and "passwd" in path.name)
    ok = client.get(f"/api/download/{job_id}/report.html")
    assert ok.status_code == 200
    assert client.get(f"/api/download/{job_id}/../../../etc/passwd").status_code in {400, 404}
    assert client.get(f"/api/download/{job_id}/%2e%2e%2f%2e%2e%2fetc%2fpasswd").status_code in {400, 404}
    assert client.get(f"/api/download/{job_id}/..%2freport.html").status_code in {400, 404}


def test_web_concurrent_requests(samples_dir):
    client = TestClient(create_app())
    data = (samples_dir / "good.dxf").read_bytes()

    def one(index: int):
        return client.post(
            "/api/check",
            files={"file": (f"good{index}.dxf", data, "application/dxf")},
            data={"fix": "false", "sheet_format": "A3"},
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, range(6)))
    assert all(item.status_code == 200 for item in results), [item.status_code for item in results]
    assert all(item.json().get("kind") for item in results)


def test_web_temp_cleanup(monkeypatch):
    import gost_checker.web as web

    monkeypatch.setattr(web, "MAX_JOBS", 2)
    monkeypatch.setattr(web, "JOB_TTL_SEC", 10_000)
    web.JOBS.clear()
    paths = []
    for name in ("a", "b", "c"):
        folder = Path(tempfile.mkdtemp(prefix=f"gost_test_{name}_"))
        (folder / "keep.txt").write_text(name, encoding="utf-8")
        paths.append(folder)
        web.JOBS[name] = (folder, time.time())
        time.sleep(0.02)
    removed = cleanup_jobs()
    assert removed >= 1
    assert len(web.JOBS) <= 2
    assert "a" not in web.JOBS
    assert not paths[0].exists()

    monkeypatch.setattr(web, "JOB_TTL_SEC", 0.01)
    time.sleep(0.03)
    cleanup_jobs()
    for folder in list(web.JOBS.values()):
        pass
    leftover = [path for path, _ts in web.JOBS.values()]
    assert leftover == [] or all((time.time() - web.JOBS[job][1]) <= web.JOB_TTL_SEC for job in web.JOBS)
    # TTL=0.01: всё старше 10 мс должно быть снято
    assert web.JOBS == {}
    assert MAX_JOBS >= 1



def test_cleanup_keeps_active_job(monkeypatch):
    import gost_checker.web as web

    monkeypatch.setattr(web, "MAX_JOBS", 2)
    monkeypatch.setattr(web, "JOB_TTL_SEC", 10_000)
    web.JOBS.clear()
    web._ACTIVE_JOBS.clear()
    folders = []
    try:
        for name in ("active", "old", "new"):
            folder = Path(tempfile.mkdtemp(prefix=f"gost_active_{name}_"))
            folders.append(folder)
            web.JOBS[name] = (folder, time.time())
            time.sleep(0.02)
        web._ACTIVE_JOBS.add("active")
        # active старше остальных и всё равно должен пережить очистку
        web.JOBS["active"] = (folders[0], time.time() - 50)
        cleanup_jobs()
        assert "active" in web.JOBS
        assert folders[0].exists()
        assert active_job_count() == 1
    finally:
        import shutil

        web._ACTIVE_JOBS.clear()
        for _job_id, (folder, _ts) in list(web.JOBS.items()):
            shutil.rmtree(folder, ignore_errors=True)
        web.JOBS.clear()


def test_web_rejects_when_all_slots_are_busy(monkeypatch):
    import gost_checker.web as web

    monkeypatch.setattr(web, "MAX_JOBS", 1)
    web.JOBS.clear()
    web._ACTIVE_JOBS.clear()
    folder = Path(tempfile.mkdtemp(prefix="gost_busy_"))
    try:
        web.JOBS["busy"] = (folder, time.time())
        web._ACTIVE_JOBS.add("busy")
        client = TestClient(create_app())
        response = client.post(
            "/api/check",
            files={"file": ("a.dxf", b"0\nEOF\n", "application/dxf")},
            data={"fix": "false"},
        )
        assert response.status_code == 429
        assert folder.exists()
        assert "busy" in web.JOBS
    finally:
        web._ACTIVE_JOBS.clear()
        web.JOBS.clear()
        import shutil

        shutil.rmtree(folder, ignore_errors=True)


def test_index_file_input_is_visible_to_validation():
    client = TestClient(create_app())
    body = client.get("/").text
    tag = body.split('id="file"', 1)[1].split(">", 1)[0]
    assert "required" not in tag
    assert "detailText" in body
    assert "res.statusText" in body
    assert "Number.isFinite" in body


def test_index_and_health():
    client = TestClient(create_app())
    home = client.get("/")
    assert home.status_code == 200
    body = home.text
    assert "Пащенко Владимир" in body
    assert "Faringeits0" in body
    assert "Перетащите" in body
    assert "kpoy888.github.io/reviews-miniapp" in body
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["ttl_sec"] >= 60
