from typing import List

from fastapi import APIRouter, HTTPException, Path as PathParam

from web.backend.app.schemas.skills import SkillBase, SkillUpdate, SkillUpdateDetails
from web.backend.app.services.skills_service import get_all_skills, update_skill_status, update_skill_details

router = APIRouter()

@router.get("/skills", response_model=List[SkillBase])
async def list_skills():
    """
    List all available skills (items)
    """
    return get_all_skills()

@router.put("/skills/{skill_id}", response_model=dict)
async def update_skill(
    skill_id: str = PathParam(..., description="The ID of the skill to update"),
    data: SkillUpdate = ...
):
    """
    Update a skill's configuration (enable/disable)
    """
    try:
        updated_config = update_skill_status(skill_id, data.enabled)
        return updated_config
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/skills/{skill_id}/details")
async def update_item_details(skill_id: str, details: SkillUpdateDetails):
    try:
        return update_skill_details(skill_id, details)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Skill not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
