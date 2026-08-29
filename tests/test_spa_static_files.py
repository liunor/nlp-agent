from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.web.app import SpaStaticFiles


def _app(static_dir) -> FastAPI:
    app = FastAPI()
    app.mount("/", SpaStaticFiles(directory=static_dir, html=True), name="webui")
    return app


def test_browser_navigation_to_unknown_route_gets_spa_shell(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<html>Nova</html>", encoding="utf-8")

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/profile", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert response.text == "<html>Nova</html>"


def test_api_404_is_not_masked_by_spa_fallback(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<html>Nova</html>", encoding="utf-8")

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/api/does-not-exist", headers={"accept": "text/html"})

    assert response.status_code == 404


def test_missing_asset_is_not_masked_without_html_accept(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<html>Nova</html>", encoding="utf-8")

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/assets/app.js", headers={"accept": "*/*"})

    assert response.status_code == 404
