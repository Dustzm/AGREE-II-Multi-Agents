"""
提示词管理 API 路由
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional

from ...permissions import require_admin
from ...services import prompts_service

from ...i18n.messages import LocalizedHTTPException

router = APIRouter(prefix="/prompts", tags=["prompts"])


class PromptResponse(BaseModel):
    """提示词响应模型"""
    agent_name: str
    lang: str
    content: str
    variables: List[str]


class PromptUpdateRequest(BaseModel):
    """提示词更新请求"""
    content: str


@router.get("", response_model=List[str])
async def list_agents():
    """获取所有可用的智能体列表"""
    return prompts_service.get_agent_list()


@router.get("/{agent_name}", response_model=PromptResponse)
async def get_prompt(
    agent_name: str,
    lang: str = Query(default="en", regex="^(en|zh)$")
):
    """获取指定智能体的提示词"""
    result = prompts_service.get_prompt(agent_name, lang)
    
    if result is None:
        raise LocalizedHTTPException(404, "error.prompt_not_found", agent=agent_name, lang=lang)
    
    return result


@router.put("/{agent_name}", response_model=PromptResponse)
async def update_prompt(
    agent_name: str,
    request: PromptUpdateRequest,
    lang: str = Query(default="en", regex="^(en|zh)$"),
    _user=Depends(require_admin),
):
    """更新指定智能体的提示词（仅管理员）"""
    try:
        result = prompts_service.update_prompt(agent_name, lang, request.content)
        return result
    except Exception as e:
        raise LocalizedHTTPException(500, "error.save_failed", diagnostic=e)
