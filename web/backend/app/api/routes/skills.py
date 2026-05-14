from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Header

from web.backend.app.schemas.skills import SkillBase, SkillUpdate, SkillUpdateDetails
from web.backend.app.services.skills_service import get_all_skills, update_skill_status, update_skill_details
from web.backend.app.i18n import get_language
from web.backend.app.permissions import require_admin

router = APIRouter()

@router.get("/skills", response_model=List[SkillBase])
async def list_skills(
    accept_language: Optional[str] = Header(None, alias="Accept-Language")
):
    """
    List all available skills (items)
    
    根据 Accept-Language 请求头返回对应语言的内容
    """
    lang = get_language(accept_language)
    return get_all_skills(lang)

@router.put("/skills/{skill_id}", response_model=dict)
async def update_skill(
    skill_id: str = PathParam(..., description="The ID of the skill to update"),
    data: SkillUpdate = ...,
    _user=Depends(require_admin),
):
    """
    Update a skill's configuration (enable/disable)
    
    仅管理员可操作
    """
    try:
        updated_config = update_skill_status(skill_id, data.enabled)
        return updated_config
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/skills/{skill_id}/details")
async def update_item_details(
    skill_id: str,
    details: SkillUpdateDetails,
    _user=Depends(require_admin),
):
    """
    更新条目详细信息
    
    仅管理员可操作
    """
    try:
        return update_skill_details(skill_id, details)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Skill not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/skills/batch/status", response_model=List[dict])
async def batch_update_skills(
    data: SkillUpdate,
    _user=Depends(require_admin),
):
    """
    批量更新所有 skills 的启用状态
    
    - enabled: true 全部启用，false 全部禁用
    - 仅管理员可操作
    """
    from web.backend.app.services.skills_service import update_all_skills_status
    try:
        return update_all_skills_status(data.enabled)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
