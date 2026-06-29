"""
agents/correction_suggester.py
Agent 4 — Correction Suggester

Generates actionable suggestions for each violation.
DOES NOT apply any changes — just suggests.
"""

from datetime import datetime
from collections import defaultdict
from typing import Optional

from .models import (
    Violation, ViolationType, ClassifiedViolation,
    Suggestion, SuggestionType, SuggestionReport
)
from .timetable_validator import DAY_NAMES, slot_to_time, days_to_string


# Valid time slots (from your config)
VALID_SLOTS = [108, 132, 156]  # 9:00, 11:00, 13:00
SINGLE_DAYS = [64, 32, 16, 8, 2, 1]  # Mon, Tue, Wed, Thu, Sat, Sun


class CorrectionSuggester:
    """
    Generates correction suggestions for violations.
    Never applies changes — just creates actionable recommendations.
    """

    def __init__(self):
        self.suggestions: list[Suggestion] = []
        self._suggestion_counter = 0

    def _next_suggestion_id(self) -> str:
        self._suggestion_counter += 1
        return f"S{self._suggestion_counter:04d}"

    def suggest(
        self,
        classified_violations: list[ClassifiedViolation],
        classes: list[dict],
        rooms: list[dict],
        instructors: list[dict],
        xml_schedule,  # XMLSchedule instance
        session_id: int,
    ) -> SuggestionReport:
        """
        Generate suggestions for all classified violations.

        Args:
            classified_violations: List from ViolationClassifier
            classes: Class list from DB
            rooms: Room list from DB
            instructors: Instructor list from DB
            xml_schedule: XMLSchedule instance with loaded solution
            session_id: Current session ID

        Returns:
            SuggestionReport with all suggestions
        """
        self.suggestions = []
        self._suggestion_counter = 0

        # Build lookups
        room_by_id = {r["id"]: r for r in rooms}
        class_by_id = {c["id"]: c for c in classes}

        # Get assignments from XML
        xml_assignments = xml_schedule.class_solutions if xml_schedule and xml_schedule.is_loaded() else {}

        # Build occupancy map: {(room_id, day_bit, slot): class_id}
        occupancy = self._build_occupancy_map(xml_assignments)

        # Generate suggestions for each violation
        for cv in classified_violations:
            violation = cv.violation

            if violation.type == ViolationType.ROOM_CONFLICT:
                self._suggest_for_room_conflict(
                    violation, rooms, class_by_id, room_by_id, occupancy
                )
            elif violation.type == ViolationType.CAPACITY_OVERFLOW:
                self._suggest_for_capacity_overflow(
                    violation, rooms, class_by_id, room_by_id
                )
            elif violation.type == ViolationType.INSTRUCTOR_CONFLICT:
                self._suggest_for_instructor_conflict(
                    violation, class_by_id, room_by_id, occupancy, instructors
                )
            elif violation.type == ViolationType.UNASSIGNED_CLASS:
                self._suggest_for_unassigned(
                    violation, rooms, class_by_id, room_by_id, occupancy
                )

        # Group by type
        by_type = defaultdict(list)
        for s in self.suggestions:
            by_type[s.type.value].append(s)

        return SuggestionReport(
            timestamp=datetime.now(),
            session_id=session_id,
            suggestions=self.suggestions,
            by_type=dict(by_type),
        )

    def _build_occupancy_map(
        self,
        xml_assignments: dict
    ) -> dict[tuple, int]:
        """Build map of occupied (room, day, slot) combinations."""
        occupancy = {}
        for cls_id, assignment in xml_assignments.items():
            room_id = assignment.get("room_id")
            days = assignment.get("days", 0)
            slot = assignment.get("slot", 0)

            if room_id and days and slot:
                for day_bit in DAY_NAMES.keys():
                    if days & day_bit:
                        occupancy[(room_id, day_bit, slot)] = cls_id
        return occupancy

    def _find_available_slots(
        self,
        room_id: int,
        occupancy: dict,
        exclude_slots: list[tuple] = None
    ) -> list[tuple]:
        """Find available (day, slot) combinations for a room."""
        exclude = set(exclude_slots or [])
        available = []

        for day_bit in SINGLE_DAYS:
            for slot in VALID_SLOTS:
                if (room_id, day_bit, slot) not in occupancy:
                    if (day_bit, slot) not in exclude:
                        available.append((day_bit, slot))

        return available

    def _find_available_rooms(
        self,
        day_bit: int,
        slot: int,
        min_capacity: int,
        rooms: list[dict],
        occupancy: dict
    ) -> list[dict]:
        """Find rooms available at a specific day/slot with sufficient capacity."""
        available = []
        for room in rooms:
            room_id = room["id"]
            capacity = room.get("capacity", 0)

            if capacity >= min_capacity:
                if (room_id, day_bit, slot) not in occupancy:
                    available.append(room)

        # Sort by capacity (smallest sufficient room first)
        available.sort(key=lambda r: r.get("capacity", 0))
        return available

    def _get_class_name(self, cls: dict) -> str:
        """Get human-readable class name."""
        subject = cls.get("subject", "")
        number = cls.get("course_number", "")
        suffix = cls.get("class_suffix", "")
        return f"{subject} {number}-{suffix}".strip()

    def _add_suggestion(
        self,
        stype: SuggestionType,
        description: str,
        rationale: str,
        violation_id: str,
        target_class_id: int = None,
        target_class_name: str = None,
        proposed_room_id: int = None,
        proposed_room_name: str = None,
        proposed_days: int = None,
        proposed_slot: int = None,
        proposed_instructor_id: int = None,
        proposed_instructor_name: str = None,
        edit_link: str = None,
        confidence: float = 1.0,
    ):
        time_desc = None
        if proposed_days is not None and proposed_slot is not None:
            time_desc = f"{days_to_string(proposed_days)} {slot_to_time(proposed_slot)}"

        self.suggestions.append(Suggestion(
            id=self._next_suggestion_id(),
            type=stype,
            description=description,
            rationale=rationale,
            target_class_id=target_class_id,
            target_class_name=target_class_name,
            proposed_room_id=proposed_room_id,
            proposed_room_name=proposed_room_name,
            proposed_days=proposed_days,
            proposed_slot=proposed_slot,
            proposed_time_description=time_desc,
            proposed_instructor_id=proposed_instructor_id,
            proposed_instructor_name=proposed_instructor_name,
            edit_link=edit_link,
            addresses_violation_id=violation_id,
            confidence=confidence,
        ))

    def _suggest_for_room_conflict(
        self,
        violation: Violation,
        rooms: list[dict],
        class_by_id: dict,
        room_by_id: dict,
        occupancy: dict
    ):
        """Generate suggestions for room double-booking."""
        if len(violation.affected_class_ids) < 2:
            return

        # Get the conflicting time slot
        if not violation.affected_time_slots:
            return
        conflict_day, conflict_slot = violation.affected_time_slots[0]
        conflict_room_id = violation.affected_room_ids[0] if violation.affected_room_ids else None

        # For each class except the first (keep one in place), suggest alternatives
        for cls_id in violation.affected_class_ids[1:]:
            cls = class_by_id.get(cls_id, {})
            cls_name = self._get_class_name(cls)
            cls_capacity = cls.get("expected_capacity", 30) or 30

            # Option 1: Find different room at same time
            alt_rooms = self._find_available_rooms(
                conflict_day, conflict_slot, cls_capacity, rooms, occupancy
            )

            for alt_room in alt_rooms[:2]:  # Limit to 2 suggestions per type
                room_name = f"{alt_room.get('building_abbr', '')} {alt_room.get('room_number', '')}"
                self._add_suggestion(
                    SuggestionType.REASSIGN_ROOM,
                    f"Move '{cls_name}' to room {room_name}",
                    f"Room {room_name} (capacity {alt_room.get('capacity')}) is available at the same time slot",
                    violation.id,
                    target_class_id=cls_id,
                    target_class_name=cls_name,
                    proposed_room_id=alt_room["id"],
                    proposed_room_name=room_name,
                    edit_link=f"/manage/class/edit/{cls_id}",
                    confidence=0.9,
                )

            # Option 2: Find different time in same room
            if conflict_room_id:
                alt_slots = self._find_available_slots(
                    conflict_room_id, occupancy,
                    exclude_slots=[(conflict_day, conflict_slot)]
                )

                for day_bit, slot in alt_slots[:2]:
                    room = room_by_id.get(conflict_room_id, {})
                    room_name = f"{room.get('building_abbr', '')} {room.get('room_number', '')}"
                    time_desc = f"{days_to_string(day_bit)} {slot_to_time(slot)}"

                    self._add_suggestion(
                        SuggestionType.CHANGE_TIME,
                        f"Move '{cls_name}' to {time_desc}",
                        f"Keep in {room_name} but change time to avoid conflict",
                        violation.id,
                        target_class_id=cls_id,
                        target_class_name=cls_name,
                        proposed_room_id=conflict_room_id,
                        proposed_room_name=room_name,
                        proposed_days=day_bit,
                        proposed_slot=slot,
                        edit_link=f"/manage/class/edit/{cls_id}",
                        confidence=0.8,
                    )

    def _suggest_for_capacity_overflow(
        self,
        violation: Violation,
        rooms: list[dict],
        class_by_id: dict,
        room_by_id: dict
    ):
        """Generate suggestions for capacity violations."""
        if not violation.affected_class_ids:
            return

        cls_id = violation.affected_class_ids[0]
        cls = class_by_id.get(cls_id, {})
        cls_name = self._get_class_name(cls)
        cls_capacity = cls.get("expected_capacity", 30) or 30

        # Find larger rooms
        larger_rooms = [r for r in rooms if r.get("capacity", 0) >= cls_capacity]
        larger_rooms.sort(key=lambda r: r.get("capacity", 0))

        for room in larger_rooms[:3]:
            room_name = f"{room.get('building_abbr', '')} {room.get('room_number', '')}"
            self._add_suggestion(
                SuggestionType.REASSIGN_ROOM,
                f"Move '{cls_name}' to room {room_name}",
                f"Room has capacity of {room.get('capacity')}, sufficient for {cls_capacity} students",
                violation.id,
                target_class_id=cls_id,
                target_class_name=cls_name,
                proposed_room_id=room["id"],
                proposed_room_name=room_name,
                edit_link=f"/manage/class/edit/{cls_id}",
                confidence=0.95,
            )

        # Suggest splitting if class is large
        if cls_capacity > 40:
            self._add_suggestion(
                SuggestionType.SPLIT_SECTION,
                f"Split '{cls_name}' into multiple sections",
                f"Current enrollment ({cls_capacity}) could be split into 2 sections of ~{cls_capacity // 2} each",
                violation.id,
                target_class_id=cls_id,
                target_class_name=cls_name,
                edit_link=f"/manage/course/edit/{cls.get('course_offering_id', '')}",
                confidence=0.7,
            )

    def _suggest_for_instructor_conflict(
        self,
        violation: Violation,
        class_by_id: dict,
        room_by_id: dict,
        occupancy: dict,
        instructors: list[dict]
    ):
        """Generate suggestions for instructor double-booking."""
        if len(violation.affected_class_ids) < 2:
            return

        conflict_day, conflict_slot = violation.affected_time_slots[0] if violation.affected_time_slots else (0, 0)

        # For each class except first, suggest alternatives
        for cls_id in violation.affected_class_ids[1:]:
            cls = class_by_id.get(cls_id, {})
            cls_name = self._get_class_name(cls)

            # Option 1: Change time
            self._add_suggestion(
                SuggestionType.CHANGE_TIME,
                f"Reschedule '{cls_name}' to a different time",
                "Move to a time slot when the instructor is available",
                violation.id,
                target_class_id=cls_id,
                target_class_name=cls_name,
                edit_link=f"/manage/class/edit/{cls_id}",
                confidence=0.85,
            )

            # Option 2: Assign different instructor (suggest first 2 alternatives)
            for inst in instructors[:2]:
                inst_name = f"{inst.get('first_name', '')} {inst.get('last_name', '')}".strip()
                if inst_name and inst_name not in (violation.affected_instructor_names or []):
                    self._add_suggestion(
                        SuggestionType.REASSIGN_INSTRUCTOR,
                        f"Assign '{inst_name}' to '{cls_name}'",
                        "Alternative instructor who may be available at this time",
                        violation.id,
                        target_class_id=cls_id,
                        target_class_name=cls_name,
                        proposed_instructor_id=inst["id"],
                        proposed_instructor_name=inst_name,
                        edit_link=f"/manage/class/edit/{cls_id}",
                        confidence=0.6,
                    )

    def _suggest_for_unassigned(
        self,
        violation: Violation,
        rooms: list[dict],
        class_by_id: dict,
        room_by_id: dict,
        occupancy: dict
    ):
        """Generate suggestions for unassigned classes."""
        if not violation.affected_class_ids:
            return

        cls_id = violation.affected_class_ids[0]
        cls = class_by_id.get(cls_id, {})
        cls_name = self._get_class_name(cls)
        cls_capacity = cls.get("expected_capacity", 30) or 30

        suggestions_added = 0

        # Find any available room/time combination
        for room in rooms:
            if room.get("capacity", 0) >= cls_capacity:
                room_id = room["id"]
                room_name = f"{room.get('building_abbr', '')} {room.get('room_number', '')}"

                available = self._find_available_slots(room_id, occupancy)

                for day_bit, slot in available[:1]:  # Just one suggestion per room
                    time_desc = f"{days_to_string(day_bit)} {slot_to_time(slot)}"
                    self._add_suggestion(
                        SuggestionType.ADD_CLASS,
                        f"Assign '{cls_name}' to {room_name} at {time_desc}",
                        f"Room capacity ({room.get('capacity')}) is sufficient for class ({cls_capacity})",
                        violation.id,
                        target_class_id=cls_id,
                        target_class_name=cls_name,
                        proposed_room_id=room_id,
                        proposed_room_name=room_name,
                        proposed_days=day_bit,
                        proposed_slot=slot,
                        edit_link=f"/manage/class/edit/{cls_id}",
                        confidence=0.9,
                    )
                    suggestions_added += 1

                if suggestions_added >= 3:  # Limit total suggestions
                    break
