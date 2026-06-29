from pydantic import BaseModel
from typing import Optional


class Role(BaseModel):
    id: str
    title: str
    icon_svg: str
    login_url: str
    description: str = ""


class UserContext(BaseModel):
    id: str
    name: str
    role_id: str
    email: str
    avatar_url: Optional[str] = None


ROLE_TITLES = {
    "student": "Student",
    "scheduler": "Academic Scheduler",
    "ta": "Teacher Assistant",
    "lecturer": "Lecturer",
    "vicedean": "Vice Dean",
    "dean": "Dean",
}

ROLE_PERMISSIONS = {
    "student": {
        "can_read": ["own_schedule"],
        "can_write": [],
        "description": "View personal schedule only",
    },
    "ta": {
        "can_read": ["own_schedule", "related_staff"],
        "can_write": ["own_availability"],
        "description": "View schedule + set availability",
    },
    "lecturer": {
        "can_read": ["own_schedule", "related_staff", "course_students"],
        "can_write": ["own_availability"],
        "description": "View schedule, students + set availability",
    },
    "scheduler": {
        "can_read": ["all_schedules", "rooms", "instructors", "courses", "classes", "students", "analytics"],
        "can_write": ["rooms", "instructors", "courses", "classes", "class_assignments", "students"],
        "description": "Full read/write access to scheduling data",
    },
    "vicedean": {
        "can_read": ["all_schedules", "rooms", "instructors", "courses", "classes", "analytics"],
        "can_write": ["rooms", "instructors"],
        "description": "Read all + manage rooms and instructors",
    },
    "dean": {
        "can_read": ["all_schedules", "rooms", "instructors", "courses", "classes", "students", "analytics", "departments"],
        "can_write": ["rooms", "instructors", "courses", "classes", "class_assignments", "students"],
        "description": "Full access to everything",
    },
}


def session_to_user_context(session: dict) -> UserContext:
    role = session.get("role", "student")
    return UserContext(
        id=session.get("id", ""),
        name=session.get("full_name", "User"),
        role_id=role,
        email=session.get("email", ""),
    )


def can_read(role: str, resource: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, {})
    return resource in perms.get("can_read", [])


def can_write(role: str, resource: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, {})
    return resource in perms.get("can_write", [])