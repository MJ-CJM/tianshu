"""The public plugin surface is a fail-closed manifest catalog."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.gateway.providers_api import providers_router
from tianshu.storage import Storage


def _app(storage: Storage) -> FastAPI:
    app = FastAPI()
    app.state.storage = storage
    app.include_router(providers_router, prefix="/api")
    return app


def test_plugin_catalog_never_claims_discovered_code_is_loaded(tmp_path) -> None:
    storage = Storage(str(tmp_path / "plugins.db"))
    storage.init_db()
    storage.save_plugin(
        {
            "name": "sample",
            "version": "1.0.0",
            "manifest": {"entry_point": "sample.plugin:setup"},
            "status": "active",
            "sha256": "declared-only",
        }
    )

    with TestClient(_app(storage)) as client:
        listed = client.get("/api/plugins")
        fetched = client.get("/api/plugins/sample")

    assert listed.status_code == 200
    listed_plugin = listed.json()["data"][0]
    assert listed_plugin["status"] == "manifest_only"
    assert listed_plugin["capability_status"] == "manifest_only"
    assert listed_plugin["loaded"] is False
    assert fetched.status_code == 200
    assert fetched.json()["data"]["status"] == "manifest_only"
    assert fetched.json()["data"]["loaded"] is False
    storage.close()


def test_plugin_install_and_activation_fail_closed(tmp_path) -> None:
    storage = Storage(str(tmp_path / "plugins.db"))
    storage.init_db()

    with TestClient(_app(storage)) as client:
        install = client.post(
            "/api/plugins/install",
            json={"name": "untrusted", "entry_point": "untrusted:setup"},
        )
        activate = client.put("/api/plugins/untrusted/status", json={"status": "active"})

    assert install.status_code == 501
    assert install.json()["detail"]["code"] == "plugin_install_not_supported"
    assert activate.status_code == 501
    assert activate.json()["detail"]["code"] == "plugin_activation_not_supported"
    assert storage.get_plugin("untrusted") is None
    storage.close()
