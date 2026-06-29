"""
AI Chatbot Service — Groq-powered schedule assistant.

Flow:
1. Fetch the data relevant to this user + question from XML + DB.
2. Send focused context to Groq and let the LLM answer.
"""

import os
from typing import Optional, List, Dict, Set, Tuple, Any

from groq import Groq

from app.services.xml_schedule import XMLSchedule
from app.services.unitime_db import UniTimeDB


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

DAY_BITS_INT = {
    64: "Saturday", 32: "Sunday", 16: "Monday",
    8: "Tuesday", 4: "Wednesday", 2: "Thursday", 1: "Friday",
}

SLOT_TO_TIME = {
    108: {"start": "9:00", "end": "10:30", "label": "9:00–10:30"},
    132: {"start": "11:00", "end": "12:30", "label": "11:00–12:30"},
    156: {"start": "13:00", "end": "14:30", "label": "13:00–14:30"},
}

DAY_ORDER = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]

DAY_ALIASES = {
    "saturday": "Saturday", "sat": "Saturday",
    "sunday": "Sunday", "sun": "Sunday",
    "monday": "Monday", "mon": "Monday",
    "tuesday": "Tuesday", "tue": "Tuesday", "tues": "Tuesday",
    "wednesday": "Wednesday", "wed": "Wednesday",
    "thursday": "Thursday", "thu": "Thursday", "thurs": "Thursday",
    "friday": "Friday", "fri": "Friday",
    "tomorrow": None,
    "today": None,
}


def _days_from_bits(bits) -> List[str]:
    if bits is None:
        return []
    try:
        bits = int(bits)
    except (ValueError, TypeError):
        return []
    return [name for bit, name in DAY_BITS_INT.items() if bits & bit]


def _slot_label(slot) -> str:
    if slot is None:
        return "TBA"
    try:
        slot = int(slot)
    except (ValueError, TypeError):
        return "TBA"
    info = SLOT_TO_TIME.get(slot)
    if info:
        return info["label"]
    minutes = slot * 5
    end = minutes + 80
    return f"{minutes // 60}:{minutes % 60:02d}–{end // 60}:{end % 60:02d}"


def _room_label(room_id, room_map) -> str:
    ri = room_map.get(room_id)
    if ri:
        return f"{ri.get('building_abbr', '')} {ri.get('room_number', '')}".strip()
    return f"Room {room_id}" if room_id else "TBA"


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULE RECORD
# ══════════════════════════════════════════════════════════════════════════════

class ScheduleEntry:
    """One resolved class with day/time/room/instructor info."""

    __slots__ = (
        "class_id", "subject", "course_number", "course_title",
        "section", "instructor", "capacity",
        "days", "time_label", "room_label",
    )

    def __init__(
        self, class_id: int, subject: str, course_number: str,
        course_title: str, section: str, instructor: str, capacity: Any,
        days: List[str], time_label: str, room_label: str,
    ):
        self.class_id = class_id
        self.subject = subject
        self.course_number = course_number
        self.course_title = course_title
        self.section = section
        self.instructor = instructor
        self.capacity = capacity
        self.days = days
        self.time_label = time_label
        self.room_label = room_label

    @property
    def code(self) -> str:
        return f"{self.subject} {self.course_number}".strip()

    def short(self) -> str:
        s = self.code
        if self.course_title:
            s += f" ({self.course_title[:40]})"
        if self.section:
            s += f" Sec {self.section}"
        return s

    def full_line(self) -> str:
        return (
            f"{self.short()} | {','.join(self.days) if self.days else 'TBA'} "
            f"{self.time_label} | Room: {self.room_label} | "
            f"Instructor: {self.instructor} | Cap: {self.capacity}"
        )


def _resolve_entry(rec: dict, xml_sched: XMLSchedule, room_map: dict,
                    id_field: str = "id") -> ScheduleEntry:
    """Build a ScheduleEntry from a DB record + XML solution."""
    cid = int(rec.get(id_field) or rec.get("class_id") or rec.get("id") or 0)

    sol = xml_sched.get_class(cid) if xml_sched.is_loaded() else None
    if sol:
        days = _days_from_bits(sol["days"])
        time_l = _slot_label(sol["slot"])
        room_l = _room_label(sol["room_id"], room_map)
    else:
        days, time_l, room_l = [], "TBA", "TBA"

    return ScheduleEntry(
        class_id=cid,
        subject=rec.get("subject", ""),
        course_number=rec.get("course_number", ""),
        course_title=rec.get("course_title", "") or rec.get("title", ""),
        section=rec.get("class_suffix", ""),
        instructor=(
            rec.get("instructors", "") or
            rec.get("instructor_names", "") or "TBA"
        ),
        capacity=rec.get("expected_capacity", "?"),
        days=days,
        time_label=time_l,
        room_label=room_l,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULE FETCHERS
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_student_schedule(
    email: str, db: UniTimeDB, xml_sched: XMLSchedule,
    room_map: dict, session_id: int,
) -> Tuple[List[ScheduleEntry], Optional[str]]:
    students = await db.get_students(session_id)
    rec = next((s for s in students if (s.get("email") or "").lower() == email.lower()), None)
    if not rec:
        return [], f"No student found with email {email}."

    sid = int(rec["id"])
    xml_ids = xml_sched.get_student_class_ids(sid) if xml_sched.is_loaded() else set()
    raw = await db.get_student_schedule(sid)
    if xml_ids:
        raw = [r for r in raw if int(r.get("class_id", 0)) in xml_ids]

    entries = [_resolve_entry(r, xml_sched, room_map, id_field="class_id") for r in raw]
    return entries, None


async def _fetch_instructor_schedule(
    email: str, db: UniTimeDB, xml_sched: XMLSchedule,
    room_map: dict, session_id: int,
) -> Tuple[List[ScheduleEntry], List[str], Optional[str]]:
    instructors = await db.get_instructors(session_id=session_id)
    rec = next((i for i in instructors if (i.get("email") or "").lower() == email.lower()), None)
    if not rec:
        return [], [], f"No instructor found with email {email}."

    iid = int(rec["id"])
    xml_ids = xml_sched.get_instructor_class_ids(iid) if xml_sched.is_loaded() else set()
    raw = await db.get_classes(session_id=session_id, instructor_id=iid)
    if xml_ids:
        raw = [c for c in raw if int(c.get("id", 0)) in xml_ids]

    entries = [_resolve_entry(c, xml_sched, room_map) for c in raw]

    student_names = []
    for c in raw[:15]:
        try:
            cr = await db._fetch_one(
                "SELECT co.uniqueid FROM course_offering co "
                "JOIN instructional_offering io ON co.instr_offr_id=io.uniqueid "
                "JOIN instr_offering_config ioc ON ioc.instr_offr_id=io.uniqueid "
                "JOIN scheduling_subpart sp ON sp.config_id=ioc.uniqueid "
                "JOIN class_ cl ON cl.subpart_id=sp.uniqueid "
                "WHERE cl.uniqueid=%s AND co.is_control=1", (c["id"],)
            )
            if cr:
                enrolled = await db.get_enrollments_by_course(cr["uniqueid"])
                for s in enrolled:
                    nm = f"{s.get('first_name', '')} {s.get('last_name', '')}".strip()
                    course = f"{s.get('subject', '')}{s.get('course_number', '')}"
                    student_names.append(f"{nm} ({course})")
        except Exception:
            pass

    return entries, sorted(set(student_names)), None


async def _fetch_all_schedule(
    db: UniTimeDB, xml_sched: XMLSchedule,
    room_map: dict, session_id: int,
) -> List[ScheduleEntry]:
    raw = await db.get_classes(session_id=session_id)
    return [_resolve_entry(c, xml_sched, room_map) for c in raw]


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXT BUILDING — size management kept to avoid overflowing the LLM
# ══════════════════════════════════════════════════════════════════════════════

def _find_course_entries(entries: List[ScheduleEntry], query: str) -> List[ScheduleEntry]:
    q = query.lower().strip()
    results = []
    for e in entries:
        if (q in e.code.lower() or
            q in (e.course_title or "").lower() or
            q in e.subject.lower() or
            q in e.course_number.lower()):
            results.append(e)
    return results


def _entries_to_context(entries: List[ScheduleEntry], label: str = "Schedule") -> str:
    if not entries:
        return f"{label}: No classes found."

    grid: Dict[str, List[str]] = {d: [] for d in DAY_ORDER}
    for e in entries:
        line = f"{e.code}|{e.time_label}|{e.room_label}|{e.instructor}"
        if e.days:
            for d in e.days:
                if d in grid:
                    grid[d].append(line)
        else:
            grid.setdefault("Unscheduled", []).append(line)

    lines = [f"{label}:"]
    for day in DAY_ORDER:
        if grid[day]:
            lines.append(f"{day}: " + "; ".join(grid[day]))
        else:
            lines.append(f"{day}: FREE")

    if "Unscheduled" in grid:
        lines.append("Unscheduled: " + "; ".join(grid["Unscheduled"]))

    return "\n".join(lines)


def _filter_entries_by_query(
    entries: List[ScheduleEntry], query: str
) -> List[ScheduleEntry]:
    """If the query mentions a specific course or day, narrow down entries."""
    q = query.lower()

    mentioned_days = []
    for alias, canonical in DAY_ALIASES.items():
        if alias in q and canonical:
            mentioned_days.append(canonical)

    course_matches = _find_course_entries(entries, q)

    if mentioned_days and course_matches:
        return [e for e in course_matches if any(d in e.days for d in mentioned_days)]

    if mentioned_days:
        return [e for e in entries if any(d in e.days for d in mentioned_days)]

    if course_matches:
        return course_matches

    return entries


async def _build_admin_focused_context(
    entries: List[ScheduleEntry],
    query: str,
    db: UniTimeDB,
    xml_sched: XMLSchedule,
    room_map: dict,
    session_id: int,
) -> str:
    q = query.lower()

    focused = _filter_entries_by_query(entries, query)
    if len(focused) == len(entries) and len(focused) > 40:
        lines = [f"Total classes: {len(entries)}"]
        for day in DAY_ORDER:
            day_classes = [e for e in entries if day in e.days]
            if day_classes:
                lines.append(f"{day}({len(day_classes)}): " +
                             "; ".join(f"{e.code} {e.time_label} {e.room_label}" for e in day_classes[:8]))
                if len(day_classes) > 8:
                    lines.append(f"  ...+{len(day_classes)-8} more")
        context = "\n".join(lines)
    else:
        context = _entries_to_context(focused, "Classes")

    room_kw = {"room", "capacity", "building", "hall", "lab", "seat", "available room", "empty room"}
    if any(k in q for k in room_kw):
        rooms = await db.get_rooms(session_id=session_id)
        room_lines = [f"\nRooms({len(rooms)}):"]
        for r in rooms:
            room_lines.append(
                f"{r.get('building_abbr', '')} {r.get('room_number', '')} "
                f"cap:{r.get('capacity', '?')}"
            )
        context += "\n".join(room_lines)

    instr_kw = {"instructor", "professor", "prof", "teacher", "who teaches", "dr."}
    if any(k in q for k in instr_kw):
        instructors = await db.get_instructors(session_id=session_id)
        instr_lines = [f"\nInstructors({len(instructors)}):"]
        for i in instructors:
            nm = f"{i.get('first_name', '')} {i.get('last_name', '')}".strip()
            instr_lines.append(f"{nm}|{i.get('dept_abbr', '')}|{i.get('email', '')}")
        context += "\n".join(instr_lines)

    stu_kw = {"student", "enrolled", "enrollment"}
    if any(k in q for k in stu_kw):
        students = await db.get_students(session_id=session_id)
        stu_lines = [f"\nStudents({len(students)}):"]
        for s in students[:30]:
            nm = f"{s.get('first_name', '')} {s.get('last_name', '')}".strip()
            stu_lines.append(f"{nm}|{s.get('email', '')}")
        if len(students) > 30:
            stu_lines.append(f"...+{len(students)-30} more")
        context += "\n".join(stu_lines)

    if len(context) > 4000:
        context = context[:4000] + "\n...(truncated)"

    return context


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
You are AAST Schedule Assistant for the Arab Academy's Timetable System.
Be concise and friendly. Answer from the DATA below only. Never invent data.
For casual chat, respond naturally. Keep answers short.
ROLE: {role_instruction}

DATA:
{context}"""

ROLE_INSTRUCTIONS = {
    "student": "User is a STUDENT. Only discuss THEIR schedule.",
    "ta": "User is a TA. Discuss their teaching schedule and their students.",
    "lecturer": "User is a Lecturer. Discuss their teaching schedule and their students.",
    "scheduler": "User is a Scheduler (admin). Full access.",
    "vicedean": "User is a Vice Dean (admin). Full access.",
    "dean": "User is a Dean (admin). Full access.",
}


# ══════════════════════════════════════════════════════════════════════════════
# CHATBOT CLASS
# ══════════════════════════════════════════════════════════════════════════════

class ScheduleChatbot:

    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set in environment")
        self.client = Groq(api_key=api_key)
        self.model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

    async def chat(
        self,
        user_message: str,
        role: str,
        session_data: dict,
        db: UniTimeDB,
        xml_sched: XMLSchedule,
        room_map: Dict,
        session_id: int,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        msg = user_message.strip()
        if not msg:
            return "Please type a message!"

        # ── Fetch user's schedule entries ──
        entries, extra_context, error = await self._fetch_entries(
            role, session_data, db, xml_sched, room_map, session_id, msg
        )

        if error:
            return error

        # ── Build focused context and call LLM ──
        if role in ("scheduler", "vicedean", "dean"):
            context = await _build_admin_focused_context(
                entries, msg, db, xml_sched, room_map, session_id
            )
        else:
            focused = _filter_entries_by_query(entries, msg)
            context = _entries_to_context(focused, "Your schedule")

        if extra_context:
            context += "\n" + extra_context

        # Size cap to avoid overflowing the LLM context window
        if len(context) > 3500:
            context = context[:3500] + "\n...(truncated)"

        return await self._llm_call(msg, role, context, history)

    async def _fetch_entries(
        self, role, session_data, db, xml_sched, room_map, session_id, query
    ) -> Tuple[List[ScheduleEntry], str, Optional[str]]:
        email = (session_data.get("email") or "").lower()

        if role == "student":
            entries, err = await _fetch_student_schedule(
                email, db, xml_sched, room_map, session_id
            )
            return entries, "", err

        if role in ("ta", "lecturer"):
            entries, students, err = await _fetch_instructor_schedule(
                email, db, xml_sched, room_map, session_id
            )
            extra = ""
            if students:
                extra = f"Your students({len(students)}): " + "; ".join(students[:20])
                if len(students) > 20:
                    extra += f" ...+{len(students)-20} more"
            return entries, extra, err

        if role in ("scheduler", "vicedean", "dean"):
            entries = await _fetch_all_schedule(db, xml_sched, room_map, session_id)
            return entries, "", None

        return [], "", "Unknown role."

    async def _llm_call(
        self, msg: str, role: str, context: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        role_instr = ROLE_INSTRUCTIONS.get(role, ROLE_INSTRUCTIONS["student"])
        system = SYSTEM_PROMPT.format(role_instruction=role_instr, context=context)

        messages = [{"role": "system", "content": system}]

        if history:
            for h in history[-6:]:
                messages.append({
                    "role": h.get("role", "user"),
                    "content": (h.get("content") or "")[:200],
                })

        messages.append({"role": "user", "content": msg[:400]})

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                max_tokens=400,
                top_p=0.9,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            err = str(e)
            if "rate_limit" in err.lower() or "413" in err:
                return "I'm a bit busy right now. Please try a shorter question or wait a moment."
            return f"Sorry, something went wrong: {err[:150]}"
