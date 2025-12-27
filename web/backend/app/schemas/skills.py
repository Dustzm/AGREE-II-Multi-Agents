from typing import List, Optional
from pydantic import BaseModel

class SkillBase(BaseModel):
    item_id: str
    enabled: bool
    domain: int
    domain_name: str
    title: str
    description: str
    locating_info: str
    criteria: List[str]
    scoring_principle: List[dict]
    
    # Editing fields
    title_zh: Optional[str] = None
    description_zh: Optional[str] = None
    locating_info_zh: Optional[str] = None
    title_en: Optional[str] = None
    description_en: Optional[str] = None
    locating_info_en: Optional[str] = None

class SkillUpdate(BaseModel):
    enabled: bool

class SkillUpdateDetails(BaseModel):
    title_zh: str
    description_zh: str
    locating_info_zh: str
    # English fields
    title: str
    description: str
    locating_info: str
    criteria: List[str]
    scoring_principle: List[dict]

