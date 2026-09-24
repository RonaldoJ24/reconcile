from pathlib import Path

from fastapi.testclient import TestClient

from reconcile.api.app import create_app


def test_page_shell_is_revalidated_but_hashed_assets_are_not(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Reconcile</title>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log('ok')")
    monkeypatch.setenv("RECONCILE_FRONTEND_DIR", str(tmp_path))
    client = TestClient(create_app())

    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-cache"

    asset = client.get("/assets/index-abc123.js")
    assert asset.status_code == 200
    assert "cache-control" not in asset.headers
