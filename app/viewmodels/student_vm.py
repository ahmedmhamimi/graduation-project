from app.models.user import UserContext
from app.models.schedule import CourseBlock
from typing import List, Dict, Any


# Day bit values (from fix.py)
DAY_BITS = {
    64: "Monday",
    32: "Tuesday",
    16: "Wednesday",
    8:  "Thursday",
    2:  "Saturday",
    1:  "Sunday",
}

# Display order
DAY_ORDER = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]

# Slot to time mapping
SLOT_TO_TIME = {
    108: {"start": "9:00",  "end": "10:40"},
    132: {"start": "11:00", "end": "12:40"},
    156: {"start": "13:00", "end": "14:40"},
}

# Default time slots for the grid
TIME_SLOTS = [
    {"start": "9:00",  "end": "10:40",  "slot_id": 108},
    {"start": "11:00", "end": "12:40",  "slot_id": 132},
    {"start": "13:00", "end": "14:40",  "slot_id": 156},
]


def parse_day_from_bits(day_bits: int) -> str:
    """Convert day bitmask to day name. Returns first matching day."""
    if day_bits is None:
        return None
    day_bits = int(day_bits)
    for bit, day_name in DAY_BITS.items():
        if day_bits & bit:
            return day_name
    return None


def parse_days_from_bits(day_bits: int) -> List[str]:
    """Convert day bitmask to list of day names."""
    if day_bits is None:
        return []
    day_bits = int(day_bits)
    days = []
    for bit, day_name in DAY_BITS.items():
        if day_bits & bit:
            days.append(day_name)
    return days


def slot_to_time_str(slot: int) -> str:
    """Convert slot number to time string."""
    if slot is None:
        return "TBA"
    slot = int(slot)
    if slot in SLOT_TO_TIME:
        t = SLOT_TO_TIME[slot]
        return f"{t['start']} - {t['end']}"
    minutes = slot * 5
    h = minutes // 60
    m = minutes % 60
    end_minutes = minutes + 80
    end_h = end_minutes // 60
    end_m = end_minutes % 60
    return f"{h}:{m:02d} - {end_h}:{end_m:02d}"


def get_slot_start(slot: int) -> str:
    """Get just the start time for a slot."""
    if slot is None:
        return None
    slot = int(slot)
    if slot in SLOT_TO_TIME:
        return SLOT_TO_TIME[slot]["start"]
    minutes = slot * 5
    h = minutes // 60
    m = minutes % 60
    return f"{h}:{m:02d}"


def prepare_timetable_data(schedule: List[Dict[str, Any]]) -> Dict:
    """
    Transform flat schedule list into a grid structure for the timetable template.

    Returns:
        dict with:
            - time_slots: list of {start, end, slot_id}
            - timetable_grid: dict with keys like "Monday_9:00" -> LIST of course dicts
            - unique_courses: deduplicated list of courses with color index
            - days: ordered list of day names
    """
    timetable_grid = {}
    unique_courses = {}
    course_colors = {}
    color_index = 0

    for item in schedule:
        assigned_days = item.get("assigned_days")
        assigned_slot = item.get("assigned_slot")

        subject = item.get("subject", "")
        course_number = item.get("course_number", "")
        course_key = f"{subject}_{course_number}"

        # Assign consistent color per course
        if course_key not in course_colors:
            course_colors[course_key] = color_index % 6
            color_index += 1

        # Add to unique courses (deduplicated)
        if course_key not in unique_courses:
            unique_courses[course_key] = {
                **item,
                "color_index": course_colors[course_key],
            }

        # Parse day and time for grid placement
        if assigned_days is not None and assigned_slot is not None:
            days = parse_days_from_bits(int(assigned_days))
            start_time = get_slot_start(int(assigned_slot))

            if start_time:
                course_entry = {
                    **item,
                    "color_index": course_colors[course_key],
                    "time_display": slot_to_time_str(assigned_slot),
                    "day": None,
                }
                # Add course to each day it meets
                for day in days:
                    grid_key = f"{day}_{start_time}"
                    # ═══ FIX: store as LIST, not single dict ═══
                    if grid_key not in timetable_grid:
                        timetable_grid[grid_key] = []
                    timetable_grid[grid_key].append({
                        **course_entry,
                        "day": day,
                    })

    # Sort unique courses by subject and number
    sorted_courses = sorted(
        unique_courses.values(),
        key=lambda c: (c.get("subject", ""), c.get("course_number", ""))
    )

    return {
        "time_slots": TIME_SLOTS,
        "timetable_grid": timetable_grid,
        "unique_courses": sorted_courses,
        "days": DAY_ORDER,
    }


class StudentViewModel:
    def get_context(self) -> UserContext:
        return UserContext(
            id="st123",
            name="Student User",
            role_id="student",
            email="student@uni.edu"
        )

    def get_schedule(self):
        slots = ["9:00 - 10:40", "11:00 - 12:40", "13:00 - 14:40"]
        days = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]
        courses = []
        return {"slots": slots, "days": days, "courses": courses}