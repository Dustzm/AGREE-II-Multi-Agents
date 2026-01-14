"""
健康检查路由
"""
from fastapi import APIRouter

router = APIRouter()


import time

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
    # TODO: 添加依赖服务的健康检查 (LLM, MinerU)
    return {"status": "ready"}
