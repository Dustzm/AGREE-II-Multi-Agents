"""
SQLite 数据库模块。

提供用户表的建表、种子数据初始化、CRUD 操作，
以及从账号种子文件导入用户的能力。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import bcrypt

logger = logging.getLogger(__name__)

# 数据库文件路径：项目根目录/data/ama.db
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_DIR = PROJECT_ROOT / "data"
DB_PATH = DB_DIR / "ama.db"

# 线程本地存储，确保每个线程使用独立连接
_local = threading.local()


# ---------- 连接管理 ----------

def get_db() -> sqlite3.Connection:
    """获取当前线程的数据库连接（懒初始化）。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def close_db() -> None:
    """关闭当前线程的数据库连接。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


# ---------- 初始化 ----------

_CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT    NOT NULL UNIQUE,
    password     TEXT    NOT NULL,
    display_name TEXT    DEFAULT '',
    role         TEXT    NOT NULL DEFAULT 'user' CHECK(role IN ('admin', 'user')),
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_AUTH_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS auth_sessions (
    session_id              TEXT PRIMARY KEY,
    username                TEXT NOT NULL,
    issued_at               INTEGER NOT NULL,
    expires_at              INTEGER NOT NULL,
    revoked_at              INTEGER,
    replaced_by_session_id  TEXT,
    last_seen_at            INTEGER NOT NULL,
    created_ip              TEXT DEFAULT '',
    user_agent              TEXT DEFAULT '',
    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_username ON auth_sessions(username);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at ON auth_sessions(expires_at);
"""


def init_db() -> None:
    """
    初始化数据库：建表 → 导入种子账号 → 确保 admin 存在。

    该函数可重复调用，具有幂等性。
    """
    conn = get_db()
    conn.executescript(_CREATE_USERS_TABLE)
    conn.executescript(_CREATE_AUTH_SESSIONS_TABLE)
    from .services.batch_jobs import init_batch_job_tables

    init_batch_job_tables(conn)
    conn.commit()

    # 优先从账号种子文件导入
    _seed_users_from_file(conn)

    # 确保至少有一个 admin 用户
    row = conn.execute(
        "SELECT id FROM users WHERE role = 'admin' LIMIT 1"
    ).fetchone()
    if row is None:
        hashed = UserDB.hash_password("Ontoweb2026")
        conn.execute(
            "INSERT INTO users (username, password, display_name, role, enabled) "
            "VALUES (?, ?, ?, ?, ?)",
            ("admin", hashed, "管理员", "admin", 1),
        )
        conn.commit()
        logger.warning("未找到账号种子文件，已创建默认管理员账户: admin / 123456")


def _seed_users_from_file(conn: sqlite3.Connection) -> None:
    """在空库初始化时，从账号种子文件导入用户。"""
    seed_file = _find_seed_users_file()
    if seed_file is None:
        return

    # 如果数据库中已有用户，说明已初始化过，跳过
    row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    if row[0] > 0:
        return

    try:
        raw = json.loads(seed_file.read_text(encoding="utf-8"))
        users = raw.get("users", [])
        if not isinstance(users, list):
            logger.warning("账号种子文件格式无效: %s", seed_file)
            return

        imported = 0
        for entry in users:
            if not isinstance(entry, dict):
                continue
            username = str(entry.get("username", "")).strip()
            password = str(entry.get("password", ""))
            if not username or not password:
                continue

            display_name = str(entry.get("display_name", "")).strip()
            enabled = 1 if entry.get("enabled", True) else 0
            role = str(entry.get("role", "admin")).strip() or "admin"

            hashed = UserDB.hash_password(password)
            conn.execute(
                "INSERT OR IGNORE INTO users "
                "(username, password, display_name, role, enabled) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, hashed, display_name, role, enabled),
            )
            imported += 1

        conn.commit()
        logger.info("已从账号种子文件导入 %d 个用户: %s", imported, seed_file.name)

    except Exception:
        logger.exception("导入账号种子文件时出错: %s", seed_file)


def _find_seed_users_file() -> Path | None:
    """查找配置指定的账号种子文件。"""
    configured_file = Path(os.environ.get("AMA_AUTH_USERS_FILE", "users.json"))
    if not configured_file.is_absolute():
        configured_file = PROJECT_ROOT / configured_file

    if configured_file.exists():
        return configured_file
    return None


# ---------- 用户 CRUD ----------

@dataclass(frozen=True)
class UserRow:
    """从数据库行映射的用户记录。"""

    id: int
    username: str
    password: str
    display_name: str
    role: str
    enabled: bool
    created_at: str
    updated_at: str


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """将 sqlite3.Row 转为字典，布尔化 enabled。"""
    if row is None:
        return None
    d = dict(row)
    d["enabled"] = bool(d.get("enabled", 0))
    # 不向外暴露密码哈希
    d.pop("password", None)
    return d


class UserDB:
    """用户表 CRUD 操作封装（类方法，无需实例化）。"""

    @staticmethod
    def hash_password(plain: str) -> str:
        """对明文密码进行 bcrypt 哈希。"""
        return bcrypt.hashpw(
            plain.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        """校验明文密码与 bcrypt 哈希是否匹配。"""
        try:
            return bcrypt.checkpw(
                plain.encode("utf-8"), hashed.encode("utf-8")
            )
        except Exception:
            return False

    @staticmethod
    def get_by_username(username: str) -> dict[str, Any] | None:
        """按用户名查询（返回不含密码的字典）。"""
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return _row_to_dict(row)

    @staticmethod
    def get_by_username_with_password(username: str) -> dict[str, Any] | None:
        """按用户名查询（保留密码哈希，仅供认证使用）。"""
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["enabled"] = bool(d.get("enabled", 0))
        return d

    @staticmethod
    def get_all() -> list[dict[str, Any]]:
        """获取所有用户（不含密码）。"""
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM users ORDER BY id ASC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]  # type: ignore[misc]

    @staticmethod
    def create(
        username: str,
        password: str,
        display_name: str = "",
        role: str = "user",
    ) -> dict[str, Any]:
        """创建用户，返回不含密码的字典。"""
        conn = get_db()
        hashed = UserDB.hash_password(password)
        try:
            conn.execute(
                "INSERT INTO users (username, password, display_name, role, enabled) "
                "VALUES (?, ?, ?, ?, 1)",
                (username, hashed, display_name, role),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"用户名已存在: {username}") from exc
        return UserDB.get_by_username(username)  # type: ignore[return-value]

    @staticmethod
    def update(username: str, **fields: Any) -> dict[str, Any] | None:
        """
        更新用户字段。

        可更新字段: display_name, role, enabled, password（会自动哈希）。
        """
        allowed = {"display_name", "role", "enabled", "password"}
        to_set: dict[str, Any] = {}
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "password":
                to_set["password"] = UserDB.hash_password(v)
            elif k == "enabled":
                to_set["enabled"] = 1 if v else 0
            else:
                to_set[k] = v

        if not to_set:
            return UserDB.get_by_username(username)

        to_set["updated_at"] = "datetime('now')"
        set_parts = []
        values: list[Any] = []
        for k, v in to_set.items():
            if k == "updated_at":
                set_parts.append(f"{k} = datetime('now')")
            else:
                set_parts.append(f"{k} = ?")
                values.append(v)

        values.append(username)
        sql = f"UPDATE users SET {', '.join(set_parts)} WHERE username = ?"

        conn = get_db()
        cursor = conn.execute(sql, values)
        conn.commit()

        if cursor.rowcount == 0:
            return None
        return UserDB.get_by_username(username)

    @staticmethod
    def delete(username: str) -> bool:
        """删除用户，返回是否成功。"""
        conn = get_db()
        cursor = conn.execute(
            "DELETE FROM users WHERE username = ?", (username,)
        )
        conn.commit()
        return cursor.rowcount > 0


class AuthSessionDB:
    """认证会话表操作封装。"""

    @staticmethod
    def _now_ts() -> int:
        return int(time.time())

    @staticmethod
    def create(
        username: str,
        *,
        ttl_seconds: int,
        created_ip: str = "",
        user_agent: str = "",
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """创建新会话。"""
        use_conn = conn or get_db()
        now = AuthSessionDB._now_ts()
        session_id = uuid4().hex
        use_conn.execute(
            """
            INSERT INTO auth_sessions (
                session_id, username, issued_at, expires_at, revoked_at,
                replaced_by_session_id, last_seen_at, created_ip, user_agent
            )
            VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (
                session_id,
                username,
                now,
                now + ttl_seconds,
                now,
                created_ip,
                user_agent,
            ),
        )
        if conn is None:
            use_conn.commit()
        return AuthSessionDB.get(session_id, conn=use_conn)

    @staticmethod
    def get(session_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        """按 session_id 查询会话。"""
        use_conn = conn or get_db()
        row = use_conn.execute(
            "SELECT * FROM auth_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def get_active(
        session_id: str,
        *,
        username: str | None = None,
        now: int | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """查询仍然有效的会话。"""
        use_conn = conn or get_db()
        current = now or AuthSessionDB._now_ts()
        sql = """
            SELECT * FROM auth_sessions
            WHERE session_id = ?
              AND revoked_at IS NULL
              AND expires_at > ?
        """
        params: list[Any] = [session_id, current]
        if username:
            sql += " AND username = ?"
            params.append(username)
        row = use_conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def touch(session_id: str, *, conn: sqlite3.Connection | None = None) -> None:
        """刷新会话最近访问时间。"""
        use_conn = conn or get_db()
        use_conn.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE session_id = ?",
            (AuthSessionDB._now_ts(), session_id),
        )
        if conn is None:
            use_conn.commit()

    @staticmethod
    def revoke(
        session_id: str,
        *,
        replaced_by_session_id: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        """撤销指定会话。"""
        use_conn = conn or get_db()
        cursor = use_conn.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = ?, replaced_by_session_id = COALESCE(?, replaced_by_session_id)
            WHERE session_id = ? AND revoked_at IS NULL
            """,
            (AuthSessionDB._now_ts(), replaced_by_session_id, session_id),
        )
        if conn is None:
            use_conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def revoke_all_for_user(
        username: str,
        *,
        replaced_by_session_id: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> list[str]:
        """撤销某个用户的所有活跃会话，并返回被撤销的 session_id 列表。"""
        use_conn = conn or get_db()
        current = AuthSessionDB._now_ts()
        rows = use_conn.execute(
            """
            SELECT session_id
            FROM auth_sessions
            WHERE username = ?
              AND revoked_at IS NULL
              AND expires_at > ?
            """,
            (username, current),
        ).fetchall()
        session_ids = [str(row["session_id"]) for row in rows]
        if not session_ids:
            return []

        use_conn.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = ?, replaced_by_session_id = COALESCE(?, replaced_by_session_id)
            WHERE username = ?
              AND revoked_at IS NULL
              AND expires_at > ?
            """,
            (current, replaced_by_session_id, username, current),
        )
        if conn is None:
            use_conn.commit()
        return session_ids

    @staticmethod
    def replace_for_user(
        username: str,
        *,
        ttl_seconds: int,
        created_ip: str = "",
        user_agent: str = "",
    ) -> tuple[dict[str, Any], list[str]]:
        """为用户创建新会话并撤销旧会话。"""
        conn = get_db()
        now = AuthSessionDB._now_ts()
        session_id = uuid4().hex
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT session_id
                FROM auth_sessions
                WHERE username = ?
                  AND revoked_at IS NULL
                  AND expires_at > ?
                """,
                (username, now),
            ).fetchall()
            revoked_session_ids = [str(row["session_id"]) for row in rows]
            if revoked_session_ids:
                conn.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = ?, replaced_by_session_id = ?
                    WHERE username = ?
                      AND revoked_at IS NULL
                      AND expires_at > ?
                    """,
                    (now, session_id, username, now),
                )
            conn.execute(
                """
                INSERT INTO auth_sessions (
                    session_id, username, issued_at, expires_at, revoked_at,
                    replaced_by_session_id, last_seen_at, created_ip, user_agent
                )
                VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    session_id,
                    username,
                    now,
                    now + ttl_seconds,
                    now,
                    created_ip,
                    user_agent,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        session = AuthSessionDB.get(session_id, conn=conn)
        if session is None:
            raise RuntimeError("新会话创建失败")
        return session, revoked_session_ids

    @staticmethod
    def delete_expired(*, conn: sqlite3.Connection | None = None) -> int:
        """清理已过期的会话记录。"""
        use_conn = conn or get_db()
        cursor = use_conn.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ?",
            (AuthSessionDB._now_ts(),),
        )
        if conn is None:
            use_conn.commit()
        return cursor.rowcount
