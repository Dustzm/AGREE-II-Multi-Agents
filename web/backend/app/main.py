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

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from .api.routes import health, upload, chat, files, reports, skills, prompts

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
    docs_url="/ama/docs",
    redoc_url="/ama/redoc",
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

# 注册路由 - 统一使用 /ama 前缀
app.include_router(health.router, prefix="/ama", tags=["健康检查"])
app.include_router(upload.router, prefix="/ama/api", tags=["文件上传"])
app.include_router(chat.router, prefix="/ama/api", tags=["对话"])
app.include_router(files.router, prefix="/ama/api", tags=["文件管理"])
app.include_router(reports.router, prefix="/ama/api", tags=["报告"])
app.include_router(skills.router, prefix="/ama/api", tags=["条目配置"])
app.include_router(prompts.router, prefix="/ama/api", tags=["提示词管理"])


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": "AMA API",
        "version": "0.1.0",
        "description": "AGREE-Multi-Agents 评价系统",
        "docs": "/ama/docs"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8012)
