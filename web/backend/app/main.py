"""
AMA Web API 主入口

AGREE-Multi-Agents 评价系统 Web 服务
"""
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import time

# 记录服务启动时间
STARTUP_TIME = int(time.time() * 1000)

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from .api.routes import auth, health, upload, chat, files, reports, skills, prompts, batch, benchmark, config, manual, users
from .auth import validate_auth_configuration
from .auth_middleware import AuthMiddleware

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("🚀 AMA Web API 正在启动...")

    validate_auth_configuration()
    
    # 检查 LLM 服务可达性
    try:
        from core.config.settings import get_settings
        import httpx
        settings = get_settings()
        if settings.llm.base_url:
            llm_url = f"{settings.llm.base_url.rstrip('/')}/models"
            logger.info(f"🔍 检查 LLM 服务可达性: {llm_url}")
            headers = {}
            if settings.llm.api_key:
                headers["Authorization"] = f"Bearer {settings.llm.api_key}"
            
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(llm_url, headers=headers)
                if response.status_code == 200:
                    logger.info("✅ LLM 服务检查通过，服务可达")
                elif response.status_code == 401:
                    logger.warning(f"⚠️ LLM 服务未授权(401)，请检查 API_KEY。响应: {response.text[:100]}")
                elif response.status_code == 403:
                    logger.warning(f"⚠️ LLM 服务拒绝访问(403)。响应: {response.text[:100]}")
                elif response.status_code == 404:
                    logger.warning(f"⚠️ LLM 服务端点未找到(404)，URL 可能有误。响应: {response.text[:100]}")
                else:
                    logger.warning(f"⚠️ LLM 服务状态异常。状态码: {response.status_code}, 响应: {response.text[:100]}")
        else:
            logger.warning("⚠️ 未配置 LLM 基础 URL")
    except Exception as e:
        logger.warning(f"❌ LLM 服务不可达或请求超时: {str(e)}")
    
    # 确保数据目录存在
    data_root = Path(os.environ.get("DATA_ROOT", "./output"))
    data_root.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"📂 数据目录: {data_root.absolute()}")
    
    yield
    
    logger.info("👋 AMA Web API 已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="AMA API",
    description="AGREE-Multi-Agents 评价系统 API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/ama/api-docs",
    redoc_url="/ama/api-redoc",
    openapi_url="/ama/openapi.json",
)

# CORS 中间件配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)

# 注册路由 - 统一使用 /ama 前缀
app.include_router(health.router, prefix="/ama", tags=["健康检查"])
app.include_router(auth.router, prefix="/ama/api", tags=["认证"])
app.include_router(upload.router, prefix="/ama/api", tags=["文件上传"])
app.include_router(chat.router, prefix="/ama/api", tags=["对话"])
app.include_router(files.router, prefix="/ama/api", tags=["文件管理"])
app.include_router(reports.router, prefix="/ama/api", tags=["报告"])
app.include_router(skills.router, prefix="/ama/api", tags=["条目配置"])
app.include_router(prompts.router, prefix="/ama/api", tags=["提示词管理"])
app.include_router(batch.router, prefix="/ama/api", tags=["批量任务"])
app.include_router(benchmark.router, prefix="/ama/api", tags=["Benchmark"])
app.include_router(config.router, prefix="/ama/api", tags=["配置管理"])
app.include_router(manual.router, prefix="/ama/api", tags=["使用手册"])
app.include_router(users.router, prefix="/ama/api", tags=["用户管理"])


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": "AMA API",
        "version": "0.1.0",
        "description": "AGREE-Multi-Agents 评价系统",
        "docs": "/ama/api-docs",
        "startup_time": STARTUP_TIME
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8012)
