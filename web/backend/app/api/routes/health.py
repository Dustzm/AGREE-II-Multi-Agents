"""
健康检查路由
"""
from fastapi import APIRouter

router = APIRouter()


import time

from ...auth import is_auth_enabled
from ...database import get_db

from ...i18n.messages import LocalizedHTTPException

STARTUP_TIME = int(time.time() * 1000)

@router.get("/health")
async def health_check():
    """健康检查接口"""
    return {
        "status": "ok", 
        "service": "kg-weaver",
        "startup_time": STARTUP_TIME
    }


@router.get("/ready")
async def readiness_check():
    """就绪检查接口"""
    if not is_auth_enabled():
        return {"status": "ready"}

    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        raise LocalizedHTTPException(503, "error.database_unavailable") from exc

    return {"status": "ready"}
