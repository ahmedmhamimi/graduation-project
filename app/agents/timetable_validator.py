"""
agents/timetable_validator.py
Agent 2 — Timetable Validation Agent

Post-solve checks against the XML solution:
- H1: All classes assigned?
- H2: Room conflicts (double-booking)?
- H3: Room capacity violations?
- H4: Instructor conflicts?
- H5: Student group conflicts? (if data available)
- H6: Workload limits? (if data available)
"""

from datetime import datetime
from collections import defaultdict
from typing import Optional

from .models import (
    Violation, ViolationType, Severity,
    TimetableValidationReport
)


# Day bit values (UniTime standard)
DAY_NAMES = {
    64: "Mon", 32: "Tue", 16: "Wed",
    8: "Thu", 4: "Fri", 2: "Sat", 1: "Sun"
}

# Reverse mapping
DAY_BITS = {v: k for k, v in DAY_NAMES.items()}

# Slots per 80-min class (80 / 5 = 16 slots)
SLOTS_PER_CLASS = 16


def slot_to_time(slot: int) -> str:
    """Convert slot number to human-readable time."""
    if slot is None or slot == 0:
        return "Unknown"
    minutes = slot * 5
    hours = minutes // 60
    mins = minutes % 60
    period = "AM" if hours < 12 else "PM"
    display_hour = hours % 12
    if display_hour == 0:
        display_hour = 12
    return f"{display_hour}:{mins:02d} {period}"


def days_to_string(days_bits: int) -> str:
    """Convert day bitmask to string like 'Mon, Wed'."""
    if days_bits is None or days_bits == 0:
        return "Unknown"
    result = []
    for bit, name in sorted(DAY_NAMES.items(), reverse=True):
        if days_bits & bit:
            result.append(name)
    return ", ".join(result) if result else "Unknown"


def slots_overlap(slot1: int, slot2: int, duration: int = SLOTS_PER_CLASS) -> bool:
    """Check if two time slots overlap given a duration."""
    end1 = slot1 + duration
    end2 = slot2 + duration
    return slot1 < end2 and slot2 < end1


class TimetableValidator:
    """
    Validates a solved timetable (from XML) against hard constraints.
    """

    def __init__(self):
        self.violations: list[Violation] = []
        self._violation_counter = 0

    def _next_violation_id(self) -> str:
        self._violation_counter += 1
        return f"V{self._violation_counter:04d}"

    def validate(
        self,
        classes: list[dict],
        rooms: list[dict],
        instructors: list[dict],
        xml_schedule,  # XMLSchedule instance
        session_id: int,
    ) -> TimetableValidationReport:
        """
        Validate timetable against all hard constraints.

        Args:
            classes: List of class dicts from DB (includes instructor_names, expected_capacity)
            rooms: List of room dicts from DB
            instructors: List of instructor dicts from DB
            xml_schedule: XMLSchedule instance with loaded solution
            session_id: Current session ID

        Returns:
            TimetableValidationReport with violations
        """
        self.violations = []
        self._violation_counter = 0

        # Build lookups
        room_by_id = {r["id"]: r for r in rooms}
        class_by_id = {c["id"]: c for c in classes}

        # Get assignments from XML
        xml_assignments = xml_schedule.class_solutions if xml_schedule.is_loaded() else {}

        # Run checks
        self._check_unassigned_classes(classes, xml_assignments, class_by_id)
        self._check_room_conflicts(xml_assignments, class_by_id, room_by_id)
        self._check_capacity_violations(xml_assignments, class_by_id, room_by_id)
        self._check_instructor_conflicts(xml_assignments, class_by_id, xml_schedule)

        return TimetableValidationReport(
            timestamp=datetime.now(),
            session_id=session_id,
            violations=self.violations,
            xml_file=xml_schedule.path if xml_schedule else None,
            xml_loaded=xml_schedule.is_loaded() if xml_schedule else False,
            total_assignments=len(xml_assignments),
        )

    def _add_violation(
        self,
        vtype: ViolationType,
        severity: Severity,
        description: str,
        hint: str = "",
        class_ids: list[int] = None,
        class_names: list[str] = None,
        room_ids: list[int] = None,
        room_names: list[str] = None,
        instructor_ids: list[int] = None,
        instructor_names: list[str] = None,
        time_slots: list[tuple] = None,
        time_descriptions: list[str] = None,
    ):
        self.violations.append(Violation(
            id=self._next_violation_id(),
            type=vtype,
            severity=severity,
            description=description,
            hint=hint,
            affected_class_ids=class_ids or [],
            affected_class_names=class_names or [],
            affected_room_ids=room_ids or [],
            affected_room_names=room_names or [],
            affected_instructor_ids=instructor_ids or [],
            affected_instructor_names=instructor_names or [],
            affected_time_slots=time_slots or [],
            affected_time_descriptions=time_descriptions or [],
        ))

    def _get_class_name(self, cls: dict) -> str:
        """Get human-readable class name."""
        subject = cls.get("subject", "")
        number = cls.get("course_number", "")
        suffix = cls.get("class_suffix", "")
        itype = cls.get("instruction_type", 10)
        type_label = "Lab" if itype == 30 else "Lec"
        return f"{subject} {number} {type_label}-{suffix}".strip()

    def _check_unassigned_classes(
        self,
        classes: list[dict],
        xml_assignments: dict,
        class_by_id: dict
    ):
        """H1: Check for classes that exist in DB but not in XML solution."""
        assigned_ids = set(xml_assignments.keys())

        for cls in classes:
            cls_id = cls["id"]
            if cls_id not in assigned_ids:
                cls_name = self._get_class_name(cls)
                self._add_violation(
                    ViolationType.UNASSIGNED_CLASS,
                    Severity.HARD,
                    f"Class '{cls_name}' has no assignment in the solver solution",
                    hint="Run the solver or manually assign a time and room to this class",
                    class_ids=[cls_id],
                    class_names=[cls_name],
                )

    def _check_room_conflicts(
        self,
        xml_assignments: dict,
        class_by_id: dict,
        room_by_id: dict
    ):
        """H2: Check for room double-booking (same room, same day, overlapping time)."""
        # Group assignments by room
        room_assignments: dict[int, list] = defaultdict(list)

        for cls_id, assignment in xml_assignments.items():
            room_id = assignment.get("room_id")
            days = assignment.get("days", 0)
            slot = assignment.get("slot", 0)

            if room_id and days and slot:
                room_assignments[room_id].append({
                    "class_id": cls_id,
                    "days": days,
                    "slot": slot,
                })

        # Check each room for conflicts
        for room_id, assignments in room_assignments.items():
            # Check each pair of assignments
            for i, a1 in enumerate(assignments):
                for a2 in assignments[i+1:]:
                    # Check if days overlap
                    common_days = a1["days"] & a2["days"]
                    if common_days == 0:
                        continue

                    # Check if time slots overlap
                    if not slots_overlap(a1["slot"], a2["slot"]):
                        continue

                    # We have a conflict!
                    room = room_by_id.get(room_id, {})
                    room_name = f"{room.get('building_abbr', '')} {room.get('room_number', 'Unknown')}"

                    cls1 = class_by_id.get(a1["class_id"], {})
                    cls2 = class_by_id.get(a2["class_id"], {})
                    cls1_name = self._get_class_name(cls1)
                    cls2_name = self._get_class_name(cls2)

                    day_str = days_to_string(common_days)
                    time_str = slot_to_time(a1["slot"])  # Use first slot for display

                    self._add_violation(
                        ViolationType.ROOM_CONFLICT,
                        Severity.HARD,
                        f"Room '{room_name}' is double-booked on {day_str} at {time_str}: '{cls1_name}' and '{cls2_name}'",
                        hint="Move one of these classes to a different room or time slot",
                        class_ids=[a1["class_id"], a2["class_id"]],
                        class_names=[cls1_name, cls2_name],
                        room_ids=[room_id],
                        room_names=[room_name],
                        time_slots=[(common_days, a1["slot"])],
                        time_descriptions=[f"{day_str} {time_str}"],
                    )

    def _check_capacity_violations(
        self,
        xml_assignments: dict,
        class_by_id: dict,
        room_by_id: dict
    ):
        """H3: Check if class enrollment exceeds room capacity."""
        for cls_id, assignment in xml_assignments.items():
            room_id = assignment.get("room_id")
            if not room_id:
                continue

            cls = class_by_id.get(cls_id, {})
            room = room_by_id.get(room_id, {})

            class_capacity = cls.get("expected_capacity", 0) or 0
            room_capacity = room.get("capacity", 0) or 0

            if class_capacity > room_capacity and room_capacity > 0:
                cls_name = self._get_class_name(cls)
                room_name = f"{room.get('building_abbr', '')} {room.get('room_number', '')}"
                overflow = class_capacity - room_capacity

                self._add_violation(
                    ViolationType.CAPACITY_OVERFLOW,
                    Severity.HARD,
                    f"Class '{cls_name}' ({class_capacity} students) exceeds room '{room_name}' capacity ({room_capacity}) by {overflow} seats",
                    hint="Move to a larger room or split into multiple sections",
                    class_ids=[cls_id],
                    class_names=[cls_name],
                    room_ids=[room_id],
                    room_names=[room_name],
                )

    def _check_instructor_conflicts(
        self,
        xml_assignments: dict,
        class_by_id: dict,
        xml_schedule
    ):
        """H4: Check for instructor double-booking."""
        # Build instructor → assignments map from XML
        instructor_assignments: dict[int, list] = defaultdict(list)

        for cls_id, assignment in xml_assignments.items():
            instructor_id = assignment.get("instructor_id")
            days = assignment.get("days", 0)
            slot = assignment.get("slot", 0)

            if instructor_id and days and slot:
                instructor_assignments[instructor_id].append({
                    "class_id": cls_id,
                    "days": days,
                    "slot": slot,
                })

        # Also check using xml_schedule.instructor_classes
        if xml_schedule and xml_schedule.is_loaded():
            for inst_id, class_ids in xml_schedule.instructor_classes.items():
                for cls_id in class_ids:
                    if cls_id in xml_assignments:
                        assignment = xml_assignments[cls_id]
                        if inst_id not in instructor_assignments or not any(
                            a["class_id"] == cls_id for a in instructor_assignments[inst_id]
                        ):
                            instructor_assignments[inst_id].append({
                                "class_id": cls_id,
                                "days": assignment.get("days", 0),
                                "slot": assignment.get("slot", 0),
                            })

        # Check each instructor for conflicts
        for inst_id, assignments in instructor_assignments.items():
            for i, a1 in enumerate(assignments):
                for a2 in assignments[i+1:]:
                    # Check if days overlap
                    common_days = a1["days"] & a2["days"]
                    if common_days == 0:
                        continue

                    # Check if time slots overlap
                    if not slots_overlap(a1["slot"], a2["slot"]):
                        continue

                    # We have a conflict!
                    cls1 = class_by_id.get(a1["class_id"], {})
                    cls2 = class_by_id.get(a2["class_id"], {})
                    cls1_name = self._get_class_name(cls1)
                    cls2_name = self._get_class_name(cls2)

                    # Get instructor name from class data
                    inst_name = cls1.get("instructor_names") or cls2.get("instructor_names") or f"Instructor {inst_id}"

                    day_str = days_to_string(common_days)
                    time_str = slot_to_time(a1["slot"])

                    self._add_violation(
                        ViolationType.INSTRUCTOR_CONFLICT,
                        Severity.HARD,
                        f"Instructor '{inst_name}' is double-booked on {day_str} at {time_str}: '{cls1_name}' and '{cls2_name}'",
                        hint="Move one class to a different time or assign a different instructor",
                        class_ids=[a1["class_id"], a2["class_id"]],
                        class_names=[cls1_name, cls2_name],
                        instructor_ids=[inst_id],
                        instructor_names=[inst_name],
                        time_slots=[(common_days, a1["slot"])],
                        time_descriptions=[f"{day_str} {time_str}"],
                    )
