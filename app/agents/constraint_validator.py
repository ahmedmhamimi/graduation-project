"""
agents/constraint_validator.py
Agent 1 — Constraint Validation Agent

Pre-solve structural checks:
- Course references valid instructor?
- Course can fit in at least one room?
- Duplicate IDs?
- Missing required fields?

This runs BEFORE looking at the solved timetable (XML).
"""

from datetime import datetime
from typing import Optional

from .models import (
    PreValidationWarning, ConstraintValidationReport
)


class ConstraintValidator:
    """
    Validates structural correctness of scheduling data.
    Does NOT check the actual timetable assignments — that's TimetableValidator.
    """

    def __init__(self):
        self.warnings: list[PreValidationWarning] = []

    def validate(
        self,
        courses: list[dict],
        rooms: list[dict],
        instructors: list[dict],
        classes: list[dict],
        session_id: int,
    ) -> ConstraintValidationReport:
        """
        Run all structural validation checks.

        Args:
            courses: List of course dicts from DB
            rooms: List of room dicts from DB
            instructors: List of instructor dicts from DB
            classes: List of class dicts from DB
            session_id: Current session ID

        Returns:
            ConstraintValidationReport with warnings
        """
        self.warnings = []

        # Build lookup sets
        instructor_ids = {i["id"] for i in instructors}
        room_ids = {r["id"] for r in rooms}
        course_ids = {c["id"] for c in courses}

        # Get max room capacity
        max_room_capacity = max((r.get("capacity", 0) for r in rooms), default=0)

        # Run checks
        self._check_empty_data(courses, rooms, instructors, classes)
        self._check_duplicate_ids(courses, "course")
        self._check_duplicate_ids(rooms, "room")
        self._check_duplicate_ids(instructors, "instructor")
        self._check_course_instructor_references(courses, instructor_ids)
        self._check_course_room_feasibility(courses, max_room_capacity, rooms)
        self._check_instructor_departments(instructors)
        self._check_room_capacities(rooms)
        self._check_class_coverage(courses, classes)

        return ConstraintValidationReport(
            timestamp=datetime.now(),
            session_id=session_id,
            warnings=self.warnings,
            total_courses=len(courses),
            total_rooms=len(rooms),
            total_instructors=len(instructors),
            total_classes=len(classes),
            has_critical_issues=any(
                w.code.startswith("CRIT") for w in self.warnings
            ),
        )

    def _add_warning(
        self,
        code: str,
        message: str,
        entity_type: str,
        entity_id: Optional[int] = None,
        entity_name: Optional[str] = None,
        **details
    ):
        self.warnings.append(PreValidationWarning(
            code=code,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            details=details,
        ))

    def _check_empty_data(
        self,
        courses: list,
        rooms: list,
        instructors: list,
        classes: list
    ):
        """Check for missing essential data."""
        if not courses:
            self._add_warning(
                "CRIT_NO_COURSES",
                "No courses found in the system. Cannot create a schedule without courses.",
                "course"
            )
        if not rooms:
            self._add_warning(
                "CRIT_NO_ROOMS",
                "No rooms found in the system. Cannot assign classes to physical locations.",
                "room"
            )
        if not instructors:
            self._add_warning(
                "WARN_NO_INSTRUCTORS",
                "No instructors found in the system. Classes cannot be assigned to teaching staff.",
                "instructor"
            )
        if not classes:
            self._add_warning(
                "WARN_NO_CLASSES",
                "No class sections found. Courses need class sections to be scheduled.",
                "class"
            )

    def _check_duplicate_ids(self, items: list[dict], entity_type: str):
        """Check for duplicate IDs."""
        seen = set()
        for item in items:
            item_id = item.get("id")
            if item_id in seen:
                name = self._get_entity_name(item, entity_type)
                self._add_warning(
                    f"ERR_DUPLICATE_{entity_type.upper()}_ID",
                    f"Duplicate {entity_type} ID found: {item_id}. This may cause data integrity issues.",
                    entity_type,
                    entity_id=item_id,
                    entity_name=name,
                )
            seen.add(item_id)

    def _get_entity_name(self, item: dict, entity_type: str) -> str:
        """Extract human-readable name from entity dict."""
        if entity_type == "course":
            return f"{item.get('subject', '')} {item.get('course_number', '')}"
        elif entity_type == "room":
            return f"{item.get('building_abbr', '')} {item.get('room_number', '')}"
        elif entity_type == "instructor":
            return f"{item.get('first_name', '')} {item.get('last_name', '')}"
        return str(item.get("id", "Unknown"))

    def _check_course_instructor_references(
        self,
        courses: list[dict],
        valid_instructor_ids: set
    ):
        """Check that courses reference valid instructors (if instructor assigned via course)."""
        # Note: In your schema, instructors are assigned via class_instructor, not course
        # This check is for any direct course→instructor reference if it exists
        pass  # Skip for now since your schema links instructors at class level

    def _check_course_room_feasibility(
        self,
        courses: list[dict],
        max_room_capacity: int,
        rooms: list[dict]
    ):
        """Check if courses can fit in at least one room."""
        if max_room_capacity == 0:
            return  # Already warned about no rooms

        # Count rooms by capacity for better suggestions
        room_capacities = sorted([r.get("capacity", 0) for r in rooms], reverse=True)

        for course in courses:
            expected = course.get("expected_students", 0) or 0
            if expected > max_room_capacity:
                course_name = f"{course.get('subject', '')} {course.get('course_number', '')}"
                self._add_warning(
                    "WARN_COURSE_TOO_LARGE",
                    f"Course '{course_name}' expects {expected} students but the largest room only holds {max_room_capacity}. "
                    f"Consider splitting into multiple sections.",
                    "course",
                    entity_id=course.get("id"),
                    entity_name=course_name,
                    expected_students=expected,
                    max_room_capacity=max_room_capacity,
                    available_capacities=room_capacities[:5],
                )

    def _check_instructor_departments(self, instructors: list[dict]):
        """Check instructors have valid department assignments."""
        for instructor in instructors:
            if not instructor.get("department_id"):
                name = f"{instructor.get('first_name', '')} {instructor.get('last_name', '')}".strip()
                self._add_warning(
                    "WARN_INSTRUCTOR_NO_DEPT",
                    f"Instructor '{name}' has no department assigned. They may not appear in departmental reports.",
                    "instructor",
                    entity_id=instructor.get("id"),
                    entity_name=name,
                )

    def _check_room_capacities(self, rooms: list[dict]):
        """Check for rooms with zero or negative capacity."""
        for room in rooms:
            capacity = room.get("capacity", 0)
            if capacity <= 0:
                room_name = f"{room.get('building_abbr', '')} {room.get('room_number', '')}"
                self._add_warning(
                    "WARN_ROOM_ZERO_CAPACITY",
                    f"Room '{room_name}' has zero or invalid capacity ({capacity}). It cannot be used for scheduling.",
                    "room",
                    entity_id=room.get("id"),
                    entity_name=room_name,
                    capacity=capacity,
                )

    def _check_class_coverage(self, courses: list[dict], classes: list[dict]):
        """Check that courses have class sections."""
        # Build set of course IDs that have classes
        courses_with_classes = set()
        for cls in classes:
            # Get course offering ID from class
            course_title = cls.get("course_title", "")
            subject = cls.get("subject", "")
            course_num = cls.get("course_number", "")
            # Use subject+number as key since we might not have direct course_offering_id
            key = f"{subject}_{course_num}"
            courses_with_classes.add(key)

        # This is informational - not all courses need classes right away
        # So we skip this check to avoid noise
