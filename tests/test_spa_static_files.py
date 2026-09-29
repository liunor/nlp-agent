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


def test_existing_static_asset_is_served_unchanged(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<html>Nova</html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/assets/app.js", headers={"accept": "*/*"})

    assert response.status_code == 200
    assert response.text == "console.log('ok')"
    assert response.headers["content-type"].split(";", 1)[0] in {
        "application/javascript",
        "text/javascript",
    }


def test_head_browser_navigation_gets_spa_shell_without_body(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<html>Nova</html>", encoding="utf-8")

    with TestClient(_app(tmp_path)) as client:
        response = client.head("/admin/users", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert response.text == ""
    assert response.headers["content-type"].startswith("text/html")


def test_non_html_request_is_not_masked_even_for_get(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<html>Nova</html>", encoding="utf-8")

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/admin/users", headers={"accept": "application/json"})

    assert response.status_code == 404


def test_html_accept_does_not_mask_missing_static_asset(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<html>Nova</html>", encoding="utf-8")

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/assets/missing.js", headers={"accept": "text/html"})

    assert response.status_code == 404


def test_html_accept_does_not_mask_missing_websocket_path(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<html>Nova</html>", encoding="utf-8")

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/ws/missing", headers={"accept": "text/html"})

    assert response.status_code == 404


def test_html_accept_does_not_mask_file_like_route(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<html>Nova</html>", encoding="utf-8")

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/admin/users.js", headers={"accept": "text/html"})

    assert response.status_code == 404
