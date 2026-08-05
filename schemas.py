from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum

class MaterialType(str, Enum):
    CONCRETE = "Concrete"
    STEEL = "Steel"
    REBAR = "Rebar"
    FORMWORK = "Formwork"

class MaterialRequirement(BaseModel):
    item_id: str
    material_type: MaterialType
    specification: str
    quantity: float
    unit: str

class DesignUpdatePayload(BaseModel):
    revision_id: str
    affected_element: str
    requirements: List[MaterialRequirement]