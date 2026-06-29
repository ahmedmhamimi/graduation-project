from pydantic import BaseModel
from typing import List, Optional


class CourseBlock(BaseModel):
    id: str
    name: str
    code: str
    room: str
    instructor: str
    time_slot: str
    day: str


class StatMetric(BaseModel):
    id: str
    label: str
    value: str
    total: Optional[str] = None