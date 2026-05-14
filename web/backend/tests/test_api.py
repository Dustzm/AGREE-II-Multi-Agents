"""
Web API 认证相关测试。
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

os.environ.setdefault("AMA_AUTH_ENABLED", "false")

from core.config import get_settings, refresh_settings
from core.config import runtime_store
from web.backend.app.api.routes import batch as batch_route
from web.backend.app.api.routes import chat as chat_route
from web.backend.app.api.routes import config as config_route
from web.backend.app import database as auth_db
from web.backend.app.auth import AUTH_COOKIE_NAME
from web.backend.app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """创建带测试账号文件的同步客户端。"""
    users_file = tmp_path / "users.json"
    users_file.write_text(
        json.dumps(
            {
                "users": [
                    {
                        "username": "admin",
                        "display_name": "管理员",
                        "enabled": True,
                        "password": "secret123",
                        "role": "admin",
                    },
                    {
                        "username": "reviewer",
                        "display_name": "评审员",
                        "enabled": True,
                        "password": "reviewer123",
                        "role": "user",
                    },
                    {
                        "username": "disabled",
                        "display_name": "禁用用户",
                        "enabled": False,
                        "password": "disabled123",
                        "role": "user",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("AMA_AUTH_ENABLED", "true")
    monkeypatch.setenv("AMA_AUTH_SECRET", "test-auth-secret")
    monkeypatch.setenv("AMA_AUTH_SESSION_TTL_HOURS", "12")
    monkeypatch.setenv("AMA_AUTH_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_CONFIG_SECRET", "test-llm-config-secret")

    refresh_settings()
    auth_db.close_db()
    monkeypatch.setattr(auth_db, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth_db, "DB_DIR", tmp_path / "data")
    monkeypatch.setattr(auth_db, "DB_PATH", tmp_path / "data" / "ama.db")
    monkeypatch.setattr(runtime_store, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(runtime_store, "DB_DIR", tmp_path / "data")
    monkeypatch.setattr(runtime_store, "DB_PATH", tmp_path / "data" / "ama.db")

    with TestClient(app) as test_client:
        assert auth_db.DB_PATH.exists()
        yield test_client

    auth_db.close_db()


def test_root_is_public(client: TestClient):
    """根路径无需登录。"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "AMA API"
    assert "version" in data


def test_health_endpoints_are_public(client: TestClient):
    """健康检查接口无需登录。"""
    health_response = client.get("/ama/health")
    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"

    ready_response = client.get("/ama/ready")
    assert ready_response.status_code == 200
    assert ready_response.json()["status"] == "ready"


def test_protected_api_requires_login(client: TestClient):
    """未登录访问受保护 API 返回 401。"""
    response = client.get("/ama/api/files/list")
    assert response.status_code == 401
    assert response.json()["detail"] == "未登录或登录已失效"


def test_docs_redirect_to_login_when_unauthorized(client: TestClient):
    """未登录访问接口文档应跳转到登录页。"""
    response = client.get("/ama/api-docs", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/ama/login"


def test_auth_me_returns_anonymous_state_when_not_logged_in(client: TestClient):
    """未登录时 /auth/me 返回匿名状态。"""
    response = client.get("/ama/api/auth/me")
    assert response.status_code == 200
    assert response.json() == {"authenticated": False, "user": None}


def test_login_success_sets_cookie_and_allows_access(client: TestClient):
    """正确密码可登录并访问受保护接口。"""
    response = client.post(
        "/ama/api/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["user"]["username"] == "admin"
    assert AUTH_COOKIE_NAME in response.cookies

    protected_response = client.get("/ama/api/manual?lang=zh-CN")
    assert protected_response.status_code == 200
    assert protected_response.json()["available"] is True


def test_login_rejects_invalid_password(client: TestClient):
    """错误密码返回 401。"""
    response = client.post(
        "/ama/api/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"


def test_login_rejects_disabled_account(client: TestClient):
    """禁用账号不可登录。"""
    response = client.post(
        "/ama/api/auth/login",
        json={"username": "disabled", "password": "disabled123"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"


def test_logout_clears_cookie_and_revokes_access(client: TestClient):
    """登出后再次访问受保护接口应被拒绝。"""
    login_response = client.post(
        "/ama/api/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert login_response.status_code == 200

    logout_response = client.post("/ama/api/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json() == {"authenticated": False, "user": None}

    protected_response = client.get("/ama/api/files/list")
    assert protected_response.status_code == 401


def test_second_login_revokes_previous_session_and_kicks_websocket(client: TestClient):
    """同账号二次登录应撤销旧会话并踢掉旧 WebSocket。"""
    first_login = client.post(
        "/ama/api/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert first_login.status_code == 200
    old_token = client.cookies.get(AUTH_COOKIE_NAME)
    assert old_token

    with client.websocket_connect("/ama/api/chat/test-session") as websocket:
        with TestClient(app) as second_client:
            second_login = second_client.post(
                "/ama/api/auth/login",
                json={"username": "admin", "password": "secret123"},
            )
            assert second_login.status_code == 200
            new_token = second_client.cookies.get(AUTH_COOKIE_NAME)
            assert new_token
            assert new_token != old_token

        with TestClient(app) as stale_client:
            stale_client.cookies.set(AUTH_COOKIE_NAME, old_token)
            stale_response = stale_client.get("/ama/api/files/list")
            assert stale_response.status_code == 401

        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()
        assert exc_info.value.code == 1008


def test_disabling_user_revokes_existing_session(client: TestClient):
    """管理员禁用用户后，该用户现有会话应立即失效。"""
    admin_login = client.post(
        "/ama/api/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert admin_login.status_code == 200

    with TestClient(app) as reviewer_client:
        reviewer_login = reviewer_client.post(
            "/ama/api/auth/login",
            json={"username": "reviewer", "password": "reviewer123"},
        )
        assert reviewer_login.status_code == 200
        old_token = reviewer_client.cookies.get(AUTH_COOKIE_NAME)
        assert old_token

        with reviewer_client.websocket_connect("/ama/api/chat/reviewer-session") as websocket:
            disable_response = client.put(
                "/ama/api/users/reviewer",
                json={"enabled": False},
            )
            assert disable_response.status_code == 200
            assert disable_response.json()["enabled"] is False

            protected_response = reviewer_client.get("/ama/api/files/list")
            assert protected_response.status_code == 401

            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_json()
            assert exc_info.value.code == 1008

        with TestClient(app) as stale_client:
            stale_client.cookies.set(AUTH_COOKIE_NAME, old_token)
            stale_response = stale_client.get("/ama/api/files/list")
            assert stale_response.status_code == 401


def test_change_my_password_replaces_session_and_invalidates_old_cookie(client: TestClient):
    """用户修改自己的密码后，应签发新会话并让旧 Cookie 失效。"""
    login_response = client.post(
        "/ama/api/auth/login",
        json={"username": "reviewer", "password": "reviewer123"},
    )
    assert login_response.status_code == 200
    old_token = client.cookies.get(AUTH_COOKIE_NAME)
    assert old_token

    change_response = client.put(
        "/ama/api/users/me/password",
        json={"old_password": "reviewer123", "new_password": "reviewer456"},
    )
    assert change_response.status_code == 200
    assert change_response.json()["detail"] == "密码已修改"

    new_token = client.cookies.get(AUTH_COOKIE_NAME)
    assert new_token
    assert new_token != old_token

    protected_response = client.get("/ama/api/manual?lang=zh-CN")
    assert protected_response.status_code == 200

    with TestClient(app) as stale_client:
        stale_client.cookies.set(AUTH_COOKIE_NAME, old_token)
        stale_response = stale_client.get("/ama/api/files/list")
        assert stale_response.status_code == 401

    with TestClient(app) as relogin_client:
        old_password_response = relogin_client.post(
            "/ama/api/auth/login",
            json={"username": "reviewer", "password": "reviewer123"},
        )
        assert old_password_response.status_code == 401

        new_password_response = relogin_client.post(
            "/ama/api/auth/login",
            json={"username": "reviewer", "password": "reviewer456"},
        )
        assert new_password_response.status_code == 200


def test_llm_config_is_persisted_in_sqlite_with_encrypted_api_key(client: TestClient):
    """LLM 配置应持久化到 SQLite，且 API Key 以密文存储。"""
    login_response = client.post(
        "/ama/api/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert login_response.status_code == 200

    response = client.put(
        "/ama/api/config/llm",
        json={
            "provider": "openai_compatible",
            "base_url": "https://llm.example.com/v1",
            "api_key": "secret-api-key",
            "model_name": "gpt-4o-mini",
            "timeout": 45,
            "is_vision_model": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openai_compatible"
    assert payload["base_url"] == "https://llm.example.com/v1"
    assert payload["model_name"] == "gpt-4o-mini"
    assert payload["timeout"] == 45
    assert payload["is_vision_model"] is False
    assert payload["api_key_masked"].startswith("secr...")

    with closing(runtime_store._connect()) as conn:
        row = conn.execute(
            """
            SELECT provider, base_url, api_key_encrypted, model_name, timeout, is_vision_model, updated_by
            FROM llm_runtime_config
            WHERE id = 1
            """
        ).fetchone()

    assert row is not None
    assert row["provider"] == "openai_compatible"
    assert row["base_url"] == "https://llm.example.com/v1"
    assert row["model_name"] == "gpt-4o-mini"
    assert row["timeout"] == 45
    assert row["is_vision_model"] == 0
    assert row["updated_by"] == "admin"
    assert row["api_key_encrypted"] != "secret-api-key"
    assert "secret-api-key" not in row["api_key_encrypted"]
    assert runtime_store.decrypt_api_key(row["api_key_encrypted"], "test-llm-config-secret") == "secret-api-key"


def test_llm_config_can_clear_saved_api_key(client: TestClient):
    """清空 API Key 时应删除已保存密文。"""
    login_response = client.post(
        "/ama/api/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert login_response.status_code == 200

    save_response = client.put(
        "/ama/api/config/llm",
        json={
            "provider": "openai_compatible",
            "base_url": "https://llm.example.com/v1",
            "api_key": "secret-api-key",
            "model_name": "gpt-4o-mini",
            "timeout": 45,
            "is_vision_model": False,
        },
    )
    assert save_response.status_code == 200

    clear_response = client.put(
        "/ama/api/config/llm",
        json={"api_key": ""},
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["api_key_masked"] == ""

    with closing(runtime_store._connect()) as conn:
        row = conn.execute(
            "SELECT api_key_encrypted FROM llm_runtime_config WHERE id = 1"
        ).fetchone()

    assert row is not None
    assert row["api_key_encrypted"] is None


def test_llm_runtime_config_falls_back_when_secret_changes(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """已保存密文在 secret 变化后不应导致配置加载失败。"""
    login_response = client.post(
        "/ama/api/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert login_response.status_code == 200

    save_response = client.put(
        "/ama/api/config/llm",
        json={
            "provider": "openai_compatible",
            "base_url": "https://llm.example.com/v1",
            "api_key": "secret-api-key",
            "model_name": "gpt-4o-mini",
            "timeout": 45,
            "is_vision_model": False,
        },
    )
    assert save_response.status_code == 200

    monkeypatch.setenv("LLM_CONFIG_SECRET", "wrong-secret")
    refresh_settings()

    settings = get_settings()
    assert settings.llm.provider == "openai_compatible"
    assert settings.llm.base_url == "https://llm.example.com/v1"
    assert settings.llm.model_name == "gpt-4o-mini"
    assert settings.llm.api_key == ""

    config_response = client.get("/ama/api/config/llm")
    assert config_response.status_code == 200
    assert config_response.json()["api_key_masked"] == ""


def test_llm_connection_test_uses_unsaved_draft_values(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """测试连接应优先使用当前草稿，而不是已保存配置。"""
    login_response = client.post(
        "/ama/api/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert login_response.status_code == 200

    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, list[dict[str, str]]]:
            return {"data": [{"id": "draft-model"}]}

    class FakeAsyncClient:
        def __init__(self, timeout: float):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers: dict[str, str] | None = None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return FakeResponse()

    monkeypatch.setattr(config_route.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/ama/api/config/llm/test",
        json={
            "provider": "openai_compatible",
            "base_url": "https://draft.example.com/v1",
            "api_key": "draft-key",
            "model_name": "draft-model",
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["url"] == "https://draft.example.com/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer draft-key"}


def test_llm_health_check_uses_unsaved_draft_values(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """健康验证应优先使用当前草稿，而不是已保存配置。"""
    login_response = client.post(
        "/ama/api/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert login_response.status_code == 200

    captured: dict[str, object] = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            model="openai_compatible/draft-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content="hello from draft"))],
        )

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_completion)

    response = client.post(
        "/ama/api/config/llm/health",
        json={
            "provider": "openai_compatible",
            "base_url": "https://draft.example.com/v1",
            "api_key": "draft-key",
            "model_name": "draft-model",
            "timeout": 17,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["reply"] == "hello from draft"
    assert captured["model"] == "openai/draft-model"
    assert captured["base_url"] == "https://draft.example.com/v1"
    assert captured["api_key"] == "draft-key"
    assert captured["timeout"] == 17


class _DummyWebSocket:
    def __init__(self, *, should_fail: bool = False):
        self.accepted = False
        self.sent: list[dict] = []
        self.should_fail = should_fail

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        if self.should_fail:
            raise RuntimeError("socket closed")
        self.sent.append(message)


def test_chat_connection_manager_keeps_other_websockets_for_same_session():
    """同一业务 session 下移除一个连接时，不应影响其他连接。"""
    manager = chat_route.ConnectionManager()
    ws1 = _DummyWebSocket()
    ws2 = _DummyWebSocket()

    async def scenario():
        await manager.connect("session-1", ws1)
        await manager.connect("session-1", ws2)
        manager.disconnect("session-1", ws1)
        await manager.send_message("session-1", {"type": "status", "content": "ok"})

    asyncio.run(scenario())

    assert ws1.accepted is True
    assert ws2.accepted is True
    assert ws1.sent == []
    assert ws2.sent == [{"type": "status", "content": "ok"}]


def test_batch_connection_manager_broadcasts_to_all_websockets_for_same_session():
    """批量任务广播应覆盖同一 session 下的多个连接。"""
    manager = batch_route.BatchConnectionManager()
    ws1 = _DummyWebSocket()
    ws2 = _DummyWebSocket()

    async def scenario():
        await manager.connect("batch-1", ws1)
        await manager.connect("batch-1", ws2)
        await manager.broadcast({"type": "status", "content": "running"})
        manager.disconnect("batch-1", ws1)
        await manager.broadcast({"type": "status", "content": "done"})

    asyncio.run(scenario())

    assert ws1.accepted is True
    assert ws2.accepted is True
    assert ws1.sent == [{"type": "status", "content": "running"}]
    assert ws2.sent == [
        {"type": "status", "content": "running"},
        {"type": "status", "content": "done"},
    ]


def test_websocket_requires_login(client: TestClient):
    """未登录时 WebSocket 握手应被拒绝。"""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ama/api/chat/test-session"):
            pass

    assert exc_info.value.code == 1008
