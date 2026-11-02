"""Contracts for the runtime LLM config layer: env inheritance, source metadata and reset."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import runtime_store
from core.config.settings import LLMSettings, _apply_runtime_llm_overrides


def _prepare_store(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_store, "DB_DIR", tmp_path)
    monkeypatch.setattr(runtime_store, "DB_PATH", tmp_path / "runtime.db")


def test_invalid_log_level_falls_back_to_info(monkeypatch):
    from core.config.settings import Settings

    monkeypatch.setenv("LOG_LEVEL", "bogus-level")
    assert Settings(_env_file=None).app.log_level == "INFO"

    monkeypatch.setenv("LOG_LEVEL", "warn")
    assert Settings(_env_file=None).app.log_level == "WARNING"


def test_blank_runtime_text_fields_fall_back_to_env(tmp_path, monkeypatch):
    _prepare_store(tmp_path, monkeypatch)
    runtime_store.upsert_runtime_llm_config(
        {"provider": "", "base_url": "  ", "model_name": None},
        secret="test",
    )

    settings = SimpleNamespace(
        llm=LLMSettings(
            _env_file=None,
            provider="vllm",
            base_url="http://env.example/v1",
            model_name="env-model",
        ),
        llm_config_secret="test",
    )
    _apply_runtime_llm_overrides(settings)

    assert settings.llm.provider == "vllm"
    assert settings.llm.base_url == "http://env.example/v1"
    assert settings.llm.model_name == "env-model"


def test_reset_clears_console_config_and_restores_env(tmp_path, monkeypatch):
    from web.backend.app.api.routes import config
    from core.agents.base import model_config
    from web.backend.app.permissions import require_admin

    _prepare_store(tmp_path, monkeypatch)
    llm = LLMSettings(
        _env_file=None,
        provider="vllm",
        base_url="http://env.example/v1",
        model_name="env-model",
        context_window=None,
    )
    settings = SimpleNamespace(llm=llm, llm_config_secret="test")

    def refresh():
        llm.base_url = "http://env.example/v1"
        _apply_runtime_llm_overrides(settings)
        return settings

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "refresh_settings", refresh)
    monkeypatch.setattr(model_config, "refresh_model", lambda: None)

    app = FastAPI()
    app.include_router(config.router)
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(username="tester")

    with TestClient(app) as client:
        saved = client.put("/config/llm", json={"base_url": "http://console.example/v1"})
        assert saved.status_code == 200
        assert saved.json()["base_url"] == "http://console.example/v1"
        assert saved.json()["runtime_updated_at"]

        current = client.get("/config/llm")
        assert current.json()["base_url"] == "http://console.example/v1"
        assert current.json()["runtime_updated_by"] == "tester"

        reset = client.delete("/config/llm")
        assert reset.status_code == 200
        body = reset.json()
        assert body["base_url"] == "http://env.example/v1"
        assert body["runtime_updated_at"] is None
        assert body["runtime_updated_by"] is None
        assert runtime_store.get_runtime_llm_config("test") is None


def _app_with_stubbed_settings(monkeypatch, settings, env_window=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.backend.app.api.routes import config
    from web.backend.app.permissions import require_admin
    from core.agents.base import model_config

    def refresh():
        _apply_runtime_llm_overrides(settings)
        return settings

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "refresh_settings", refresh)
    monkeypatch.setattr(config, "env_llm_context_window", lambda: env_window)
    monkeypatch.setattr(model_config, "refresh_model", lambda: None)

    app = FastAPI()
    app.include_router(config.router)
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(username="tester")
    return TestClient(app)


def test_identity_change_inherits_env_context_window(tmp_path, monkeypatch):
    _prepare_store(tmp_path, monkeypatch)
    settings = SimpleNamespace(
        llm=LLMSettings(
            _env_file=None,
            provider="vllm",
            base_url="http://env.example/v1",
            model_name="env-model",
            context_window=131072,
        ),
        llm_config_secret="test",
    )

    with _app_with_stubbed_settings(monkeypatch, settings, env_window=131072) as client:
        response = client.put("/config/llm", json={"model_name": "new-model"})
        assert response.status_code == 200
        assert response.json()["context_window"] == 131072
        stored = runtime_store.get_runtime_llm_config("test")
        assert stored.get("context_window") is None
        assert stored["model_name"] == "new-model"


def test_provider_switch_clears_stale_api_key(tmp_path, monkeypatch):
    from core.agents.base.model_config import get_provider_config_for

    _prepare_store(tmp_path, monkeypatch)
    settings = SimpleNamespace(
        llm=LLMSettings(
            _env_file=None,
            provider="vllm",
            base_url="http://env.example/v1",
            model_name="env-model",
        ),
        llm_config_secret="test",
    )

    with _app_with_stubbed_settings(monkeypatch, settings) as client:
        saved = client.put(
            "/config/llm",
            json={"provider": "vllm", "api_key": "sk-old", "model_name": "vllm-model"},
        )
        assert saved.status_code == 200
        assert runtime_store.get_runtime_llm_config("test")["api_key"] == "sk-old"

        switched = client.put("/config/llm", json={"provider": "gemini", "model_name": "gemini-pro"})
        assert switched.status_code == 200
        stored = runtime_store.get_runtime_llm_config("test")
        assert stored["provider"] == "gemini"
        assert stored["api_key"] is None
        assert stored["base_url"] == get_provider_config_for("gemini")["default_base_url"]


def test_timeout_is_bounded(tmp_path, monkeypatch):
    _prepare_store(tmp_path, monkeypatch)
    settings = SimpleNamespace(
        llm=LLMSettings(_env_file=None, provider="vllm", base_url="http://env.example/v1"),
        llm_config_secret="test",
    )

    with _app_with_stubbed_settings(monkeypatch, settings) as client:
        assert client.put("/config/llm", json={"timeout": 0}).status_code == 422
        assert client.put("/config/llm", json={"timeout": 10**6}).status_code == 422
        assert client.put("/config/llm", json={"timeout": "abc"}).status_code == 422
        assert client.put("/config/llm", json={"timeout": 300}).status_code == 200


def test_blank_base_url_clears_runtime_override(tmp_path, monkeypatch):
    _prepare_store(tmp_path, monkeypatch)
    settings = SimpleNamespace(
        llm=LLMSettings(
            _env_file=None,
            provider="vllm",
            base_url="http://env.example/v1",
            model_name="env-model",
        ),
        llm_config_secret="test",
    )

    with _app_with_stubbed_settings(monkeypatch, settings) as client:
        saved = client.put("/config/llm", json={"base_url": "http://console.example/v1"})
        assert saved.status_code == 200
        assert runtime_store.get_runtime_llm_config("test")["base_url"] == "http://console.example/v1"

        cleared = client.put("/config/llm", json={"base_url": "   "})
        assert cleared.status_code == 200
        stored = runtime_store.get_runtime_llm_config("test")
        assert stored["base_url"] is None


def test_config_response_reports_only_fields_actually_overridden(tmp_path, monkeypatch):
    from web.backend.app.api.routes import config

    _prepare_store(tmp_path, monkeypatch)
    runtime_store.upsert_runtime_llm_config(
        {"provider": "vllm", "base_url": "", "model_name": "m", "timeout": 300},
        secret="test",
    )
    settings = SimpleNamespace(
        llm=LLMSettings(
            _env_file=None,
            provider="vllm",
            base_url="http://env.example/v1",
            model_name="m",
            timeout=300,
        ),
        llm_config_secret="test",
    )
    response = config._llm_config_response(settings)

    assert "provider" in response.runtime_source_fields
    assert "model_name" in response.runtime_source_fields
    assert "timeout" in response.runtime_source_fields
    # Blank base_url is ignored by the override layer, so it must not claim console source.
    assert "base_url" not in response.runtime_source_fields
    assert response.runtime_updated_at is not None
    assert response.runtime_updated_at.endswith("Z")


def test_schema_setup_is_cached_per_database(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setattr(runtime_store, "DB_DIR", tmp_path)
    monkeypatch.setattr(runtime_store, "DB_PATH", tmp_path / "once.db")
    monkeypatch.setattr(runtime_store, "_initialized_paths", set())

    legacy = sqlite3.connect(str(tmp_path / "once.db"))
    legacy.execute(
        "CREATE TABLE llm_runtime_config (id INTEGER PRIMARY KEY CHECK (id = 1),"
        " provider TEXT, base_url TEXT, api_key_encrypted TEXT, model_name TEXT,"
        " timeout INTEGER, is_vision_model INTEGER)"
    )
    legacy.commit()
    legacy.close()

    first = runtime_store._connect()
    columns = {row[1] for row in first.execute("PRAGMA table_info(llm_runtime_config)")}
    first.close()

    assert "context_window" in columns
    assert "context_window_overridden" in columns
    assert str(runtime_store.DB_PATH) in runtime_store._initialized_paths

    second = runtime_store._connect()
    second.close()
