"""
app/services/xml_schedule.py
────────────────────────────
Parses the UniTime solver output XML ("course-solution BEST.xml") once at
startup and exposes fast in-memory lookups for the SOLVED schedule.

All IDs in the XML are UniTime uniqueids that map directly to DB primary keys:
    XML class id      == class_.uniqueid
    XML instructor id == departmental_instructor.uniqueid
    XML room id       == location.uniqueid
    XML student id    == student.uniqueid

Day encoding (7-bit binary string, MSB = bit 64):
    "1000000" = 64 = Monday
    "0100000" = 32 = Tuesday
    "0010000" = 16 = Wednesday
    "0001000" =  8 = Thursday
    "0000010" =  2 = Saturday
    "0000001" =  1 = Sunday

Slot encoding: same integer as the DB assignment table.
    108 → 9:00–10:20   132 → 11:00–12:20   156 → 13:00–14:20
"""

import os
import xml.etree.ElementTree as ET
from typing import Dict, Optional, Set


class XMLSchedule:
    """
    Parsed, in-memory representation of the solver solution XML.

    After load(), three dicts are available:

    class_solutions : dict[int, dict]
        class_id → {
            "days":          int   (bitmask, e.g. 32 = Tuesday),
            "slot":          int   (e.g. 156),
            "room_id":       int   (location.uniqueid),
            "instructor_id": int | None,
        }
        Only classes with solution="true" on both <time> and <room> appear.

    student_classes : dict[int, set[int]]
        student_id → frozenset of class_ids assigned by solver.
        Populated from <students><student><class .../> children.

    instructor_classes : dict[int, set[int]]
        instructor_id → set of class_ids where that instructor is marked
        solution="true".  Used by the staff dashboard.
    """

    def __init__(self, path: str = "course-solution BEST.xml"):
        self.path = path
        self.loaded = False
        self.class_solutions:    Dict[int, dict]     = {}
        self.student_classes:    Dict[int, Set[int]] = {}
        self.instructor_classes: Dict[int, Set[int]] = {}

    # ── public ────────────────────────────────────────────────────

    def load(self) -> bool:
        """Parse the XML file.  Safe to call multiple times (idempotent)."""
        if not os.path.isfile(self.path):
            print(f"[XMLSchedule] '{self.path}' not found — "
                  "timetables will fall back to DB assignment data.")
            return False
        try:
            tree = ET.parse(self.path)
            root = tree.getroot()
            self._parse_classes(root)
            self._parse_students(root)
            self.loaded = True
            print(f"[XMLSchedule] Loaded '{self.path}': "
                  f"{len(self.class_solutions)} assigned classes, "
                  f"{len(self.student_classes)} student mappings, "
                  f"{len(self.instructor_classes)} instructors with assignments.")
            return True
        except ET.ParseError as exc:
            print(f"[XMLSchedule] Parse error: {exc}")
            return False
        except Exception as exc:
            print(f"[XMLSchedule] Unexpected error: {exc}")
            return False

    def is_loaded(self) -> bool:
        return self.loaded

    def get_class(self, class_id: int) -> Optional[dict]:
        """Return solved assignment for class_id, or None if unassigned."""
        return self.class_solutions.get(int(class_id))

    def get_student_class_ids(self, student_id: int) -> Set[int]:
        """Return the set of class_ids the solver assigned to this student."""
        return self.student_classes.get(int(student_id), set())

    def get_instructor_class_ids(self, instructor_id: int) -> Set[int]:
        """Return the set of class_ids where this instructor is solution=true."""
        return self.instructor_classes.get(int(instructor_id), set())

    # ── internal parsers ──────────────────────────────────────────

    def _parse_classes(self, root: ET.Element):
        classes_el = root.find("classes")
        if classes_el is None:
            return

        for cls in classes_el.findall("class"):
            class_id = int(cls.get("id", 0))
            if not class_id:
                continue

            # solution instructor (may be absent)
            sol_instructor_id = None
            for inst in cls.findall("instructor"):
                if inst.get("solution") == "true":
                    sol_instructor_id = int(inst.get("id", 0)) or None
                    break

            # solution time — must have solution="true"
            sol_days = None
            sol_slot = None
            for t in cls.findall("time"):
                if t.get("solution") == "true":
                    try:
                        sol_days = int(t.get("days", "0000000"), 2)
                    except ValueError:
                        sol_days = 0
                    sol_slot = int(t.get("start", 0))
                    break

            # solution room — must have solution="true"
            sol_room_id = None
            for r in cls.findall("room"):
                if r.get("solution") == "true":
                    sol_room_id = int(r.get("id", 0)) or None
                    break

            # Only store classes that are fully assigned (time AND room)
            if sol_days is not None and sol_room_id is not None:
                self.class_solutions[class_id] = {
                    "days":          sol_days,
                    "slot":          sol_slot,
                    "room_id":       sol_room_id,
                    "instructor_id": sol_instructor_id,
                }
                # Index by instructor for staff dashboard O(1) lookup
                if sol_instructor_id:
                    self.instructor_classes.setdefault(sol_instructor_id, set()).add(class_id)

    def _parse_students(self, root: ET.Element):
        students_el = root.find("students")
        if students_el is None:
            return

        for student in students_el.findall("student"):
            student_id = int(student.get("id", 0))
            if not student_id:
                continue
            class_ids = {
                int(c.get("id"))
                for c in student.findall("class")
                if c.get("id")
            }
            if class_ids:
                self.student_classes[student_id] = class_ids


# ── helper used in main.py lifespan ──────────────────────────────

def build_room_map(db_rooms: list) -> Dict[int, dict]:
    """
    Index the list returned by unitime_db.get_rooms() by room uniqueid.

    Each entry in db_rooms looks like:
        {"id": 712032, "room_number": "425", "building_abbr": "AIET", ...}

    Returns:
        {712032: {"room_number": "425", "building_abbr": "AIET", ...}, ...}
    """
    return {int(r["id"]): r for r in db_rooms if r.get("id")}