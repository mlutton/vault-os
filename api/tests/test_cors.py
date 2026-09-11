import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaultos.config import ConfigError, cors_allowed_origins_from_env
from vaultos.main import configure_cors


def _app(allowed_origins: tuple[str, ...]) -> FastAPI:
    app = FastAPI()
    configure_cors(app, allowed_origins)

    @app.get("/resource")
    def read_resource():
        return {"ok": True}

    @app.post("/resource")
    def write_resource():
        return {"ok": True}

    return app


def test_cors_allows_configured_origin_get_and_json_post_preflight():
    origin = "https://web.example.test"
    with TestClient(_app((origin,))) as client:
        response = client.get("/resource", headers={"Origin": origin})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin

        preflight = client.options(
            "/resource",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == origin
        assert preflight.headers["access-control-allow-methods"] == "GET, POST"
        assert "content-type" in preflight.headers["access-control-allow-headers"].lower()


def test_cors_denies_unconfigured_origin():
    denied_origin = "https://other.example.test"
    with TestClient(_app(("https://web.example.test",))) as client:
        response = client.get("/resource", headers={"Origin": denied_origin})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers

        preflight = client.options(
            "/resource",
            headers={
                "Origin": denied_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 400
        assert "access-control-allow-origin" not in preflight.headers


def test_cors_denies_unconfigured_method_and_header():
    origin = "https://web.example.test"
    with TestClient(_app((origin,))) as client:
        method_preflight = client.options(
            "/resource",
            headers={"Origin": origin, "Access-Control-Request-Method": "PUT"},
        )
        assert method_preflight.status_code == 400

        header_preflight = client.options(
            "/resource",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert header_preflight.status_code == 400


def test_cors_origins_are_configurable_and_wildcard_is_rejected(monkeypatch):
    monkeypatch.delenv("VAULTOS_CORS_ALLOWED_ORIGINS", raising=False)
    assert cors_allowed_origins_from_env() == ()

    monkeypatch.setenv(
        "VAULTOS_CORS_ALLOWED_ORIGINS",
        " https://web.example.test,https://backup.example.test ",
    )
    assert cors_allowed_origins_from_env() == (
        "https://web.example.test",
        "https://backup.example.test",
    )

    monkeypatch.setenv("VAULTOS_CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(ConfigError, match="explicit origins"):
        cors_allowed_origins_from_env()


@pytest.mark.parametrize(
    "origin",
    ["https://good.test:bad", "http://good.test:", "https://bad host.test"],
)
def test_cors_rejects_malformed_origins(monkeypatch, origin):
    monkeypatch.setenv("VAULTOS_CORS_ALLOWED_ORIGINS", origin)
    with pytest.raises(ConfigError, match=r"exact http\(s\) origins"):
        cors_allowed_origins_from_env()
