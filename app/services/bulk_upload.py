"""
Bulk Upload Service — Parse CSV files and insert data into UniTime DB.

Replicates the logic from seed.py + fix.py but driven by uploaded CSV data.
After inserting raw records, applies fix.py-style restructuring
(correct capacities, sections, time patterns, enrollments).

Supported CSV types:
  - courses: subject,course_number,title,credits,semester,dept_tag,has_lab
  - instructors: first_name,last_name,email,role (professor|instructor)
  - students: first_name,last_name,email,major,year
  - rooms: room_number,capacity,exam_capacity,is_lab
"""

import csv
import io
import math
import random
from datetime import datetime
from typing import List, Dict, Tuple, Optional

import aiomysql

from app.services.unitime_db import UniTimeDB

# ══════════════════════════════════════════════════════
# CONSTANTS (must match seed.py / fix.py)
# ══════════════════════════════════════════════════════

SESSION_ID = 231379
DEPARTMENT_ID = 231383
DATE_PATTERN_ID = 853

ITYPE_LECTURE = 10
ITYPE_LAB = 30

LECTURE_CAPACITY = 50
LAB_CAPACITY = 25
MINUTES_PER_PERIOD = 80

SINGLE_DAYS = [2, 1, 64, 32, 16, 8]  # Sat, Sun, Mon, Tue, Wed, Thu
VALID_SLOTS = [108, 132, 156]  # 9:00, 11:00, 13:00

NO_LAB_KEYWORDS = {
    "project", "training", "elective", "seminar", "thesis",
    "internship", "practicum",
}


class BulkUploadResult:
    """Holds the result of a bulk upload operation."""

    def __init__(self):
        self.success = False
        self.total_rows = 0
        self.inserted = 0
        self.skipped = 0
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.details: List[str] = []

    @property
    def summary(self) -> str:
        parts = [f"{self.inserted} inserted, {self.skipped} skipped"]
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return ". ".join(parts)


class BulkUploader:
    """
    Parses CSV content and inserts records into the UniTime database.
    Uses the same DB connection pool as UniTimeDB.
    """

    def __init__(self, db: UniTimeDB):
        self.db = db
        self._id_counter: Optional[int] = None

    # ──────────────────────────────────────────
    # ID generation (same approach as seed.py)
    # ──────────────────────────────────────────

    async def _init_ids(self, conn):
        """Reserve a block of IDs from hibernate_unique_key."""
        async with conn.cursor(aiomysql.DictCursor) as cur:
            try:
                await cur.execute(
                    "SELECT next_hi FROM hibernate_unique_key FOR UPDATE"
                )
                row = await cur.fetchone()
                if row:
                    hi = int(row["next_hi"])
                    await cur.execute(
                        "UPDATE hibernate_unique_key SET next_hi = %s",
                        (hi + 5000,)
                    )
                    self._id_counter = hi * 32
                    return
            except Exception:
                pass
        self._id_counter = 8000000 + random.randint(0, 999999)

    def _next_id(self) -> int:
        uid = self._id_counter
        self._id_counter += 1
        return uid

    # ──────────────────────────────────────────
    # CSV parsing
    # ──────────────────────────────────────────

    def _parse_csv(self, content: str) -> Tuple[List[Dict], List[str]]:
        """Parse CSV string into list of row dicts. Returns (rows, errors)."""
        errors = []
        rows = []
        try:
            # Handle BOM
            if content.startswith('\ufeff'):
                content = content[1:]

            reader = csv.DictReader(io.StringIO(content))
            if not reader.fieldnames:
                return [], ["CSV file has no headers."]

            for i, row in enumerate(reader, start=2):
                # Strip whitespace from keys and values
                cleaned = {
                    k.strip().lower().replace(' ', '_'): (v or '').strip()
                    for k, v in row.items() if k
                }
                if any(cleaned.values()):
                    rows.append(cleaned)
        except Exception as e:
            errors.append(f"CSV parse error: {str(e)}")

        return rows, errors

    # ══════════════════════════════════════════
    # UPLOAD: INSTRUCTORS
    # ══════════════════════════════════════════

    async def upload_instructors(self, csv_content: str) -> BulkUploadResult:
        """
        CSV columns: first_name, last_name, email, role
        role: professor | instructor (default: instructor)
        """
        result = BulkUploadResult()
        rows, parse_errors = self._parse_csv(csv_content)
        result.errors.extend(parse_errors)
        result.total_rows = len(rows)

        if not rows:
            result.errors.append("No data rows found in CSV.")
            return result

        # Validate headers
        required = {"first_name", "last_name"}
        headers = set(rows[0].keys())
        missing = required - headers
        if missing:
            result.errors.append(f"Missing required columns: {', '.join(missing)}")
            return result

        conn = await self.db.pool.acquire()
        try:
            await self._init_ids(conn)
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # Get position types
                await cur.execute(
                    "SELECT uniqueid, reference, label FROM position_type ORDER BY sort_order"
                )
                pos_types = await cur.fetchall()
                prof_pos_id = None
                inst_pos_id = None
                for pt in pos_types:
                    ref = str(pt.get("reference", "")).lower()
                    label = str(pt.get("label", "")).lower()
                    if "professor" in ref or "professor" in label:
                        if not prof_pos_id:
                            prof_pos_id = int(pt["uniqueid"])
                    if "instruct" in ref or "instruct" in label or "teaching" in ref:
                        if not inst_pos_id:
                            inst_pos_id = int(pt["uniqueid"])
                if not prof_pos_id and pos_types:
                    prof_pos_id = int(pos_types[0]["uniqueid"])
                if not inst_pos_id and pos_types:
                    inst_pos_id = int(pos_types[-1]["uniqueid"])

                for i, row in enumerate(rows, start=2):
                    fname = row.get("first_name", "").strip()
                    lname = row.get("last_name", "").strip()
                    email = row.get("email", "").strip()
                    role = row.get("role", "instructor").strip().lower()

                    if not fname or not lname:
                        result.errors.append(f"Row {i}: first_name and last_name required.")
                        result.skipped += 1
                        continue

                    # Check duplicate
                    await cur.execute(
                        "SELECT uniqueid FROM departmental_instructor "
                        "WHERE fname=%s AND lname=%s AND department_uniqueid=%s",
                        (fname, lname, DEPARTMENT_ID)
                    )
                    if await cur.fetchone():
                        result.skipped += 1
                        result.warnings.append(f"Row {i}: {fname} {lname} already exists.")
                        continue

                    pos_id = prof_pos_id if role == "professor" else inst_pos_id
                    ext_uid = email.split("@")[0] if email else f"{fname.lower()}.{lname.lower()}"

                    pid = self._next_id()
                    await cur.execute("""
                        INSERT INTO departmental_instructor
                            (uniqueid, external_uid, fname, mname, lname,
                             email, department_uniqueid, pos_code_type)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (pid, ext_uid, fname, "", lname, email, DEPARTMENT_ID, pos_id))

                    result.inserted += 1
                    result.details.append(f"{fname} {lname} ({role})")

            await conn.commit()
            result.success = True
        except Exception as e:
            await conn.rollback()
            result.errors.append(f"Database error: {str(e)}")
        finally:
            self.db.pool.release(conn)

        return result

    # ══════════════════════════════════════════
    # UPLOAD: STUDENTS
    # ══════════════════════════════════════════

    async def upload_students(self, csv_content: str) -> BulkUploadResult:
        """
        CSV columns: first_name, last_name, email, major, year
        major: DS | IN (optional, default: DS)
        year: 1-4 (optional, default: 1)
        """
        result = BulkUploadResult()
        rows, parse_errors = self._parse_csv(csv_content)
        result.errors.extend(parse_errors)
        result.total_rows = len(rows)

        if not rows:
            result.errors.append("No data rows found in CSV.")
            return result

        required = {"first_name", "last_name"}
        headers = set(rows[0].keys())
        missing = required - headers
        if missing:
            result.errors.append(f"Missing required columns: {', '.join(missing)}")
            return result

        conn = await self.db.pool.acquire()
        try:
            await self._init_ids(conn)
            async with conn.cursor(aiomysql.DictCursor) as cur:
                for i, row in enumerate(rows, start=2):
                    fname = row.get("first_name", "").strip()
                    lname = row.get("last_name", "").strip()
                    email = row.get("email", "").strip()

                    if not fname or not lname:
                        result.errors.append(f"Row {i}: first_name and last_name required.")
                        result.skipped += 1
                        continue

                    if not email:
                        email = f"{fname.lower()}.{lname.lower()}@student.aiet.edu"

                    # Check duplicate
                    await cur.execute(
                        "SELECT uniqueid FROM student "
                        "WHERE first_name=%s AND last_name=%s AND session_id=%s",
                        (fname, lname, SESSION_ID)
                    )
                    if await cur.fetchone():
                        result.skipped += 1
                        result.warnings.append(f"Row {i}: {fname} {lname} already exists.")
                        continue

                    sid = self._next_id()
                    await cur.execute("""
                        INSERT INTO student
                            (uniqueid, external_uid, first_name, middle_name,
                             last_name, email, session_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (sid, f"STU{sid}", fname, "", lname, email, SESSION_ID))

                    result.inserted += 1
                    major = row.get("major", "DS").strip().upper()
                    year = row.get("year", "1").strip()
                    result.details.append(f"{fname} {lname} ({major} Y{year})")

            await conn.commit()
            result.success = True
        except Exception as e:
            await conn.rollback()
            result.errors.append(f"Database error: {str(e)}")
        finally:
            self.db.pool.release(conn)

        return result

    # ══════════════════════════════════════════
    # UPLOAD: ROOMS
    # ══════════════════════════════════════════

    async def upload_rooms(self, csv_content: str) -> BulkUploadResult:
        """
        CSV columns: room_number, capacity, exam_capacity, is_lab
        is_lab: yes/no or true/false or 1/0 (default: no)
        """
        result = BulkUploadResult()
        rows, parse_errors = self._parse_csv(csv_content)
        result.errors.extend(parse_errors)
        result.total_rows = len(rows)

        if not rows:
            result.errors.append("No data rows found in CSV.")
            return result

        required = {"room_number", "capacity"}
        headers = set(rows[0].keys())
        missing = required - headers
        if missing:
            result.errors.append(f"Missing required columns: {', '.join(missing)}")
            return result

        conn = await self.db.pool.acquire()
        try:
            await self._init_ids(conn)
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # Find AIET building
                await cur.execute(
                    "SELECT uniqueid FROM building WHERE abbreviation='AIET' AND session_id=%s",
                    (SESSION_ID,)
                )
                bldg = await cur.fetchone()
                if not bldg:
                    result.errors.append("Building 'AIET' not found in database.")
                    return result
                building_id = int(bldg["uniqueid"])

                # Get room type
                await cur.execute(
                    "SELECT uniqueid FROM room_type WHERE is_room=1 ORDER BY ord LIMIT 1"
                )
                rt = await cur.fetchone()
                rt_id = int(rt["uniqueid"]) if rt else 1

                for i, row in enumerate(rows, start=2):
                    room_num = row.get("room_number", "").strip()
                    cap_str = row.get("capacity", "30").strip()
                    exam_str = row.get("exam_capacity", "").strip()
                    is_lab_str = row.get("is_lab", "no").strip().lower()

                    if not room_num:
                        result.errors.append(f"Row {i}: room_number is required.")
                        result.skipped += 1
                        continue

                    try:
                        cap = int(cap_str)
                    except ValueError:
                        result.errors.append(f"Row {i}: invalid capacity '{cap_str}'.")
                        result.skipped += 1
                        continue

                    exam_cap = int(exam_str) if exam_str.isdigit() else max(1, cap - 10)
                    is_lab = is_lab_str in ("yes", "true", "1", "y")

                    # Check duplicate
                    await cur.execute(
                        "SELECT uniqueid FROM room WHERE building_id=%s AND room_number=%s AND session_id=%s",
                        (building_id, room_num, SESSION_ID)
                    )
                    existing = await cur.fetchone()
                    if existing:
                        # Update capacity
                        await cur.execute(
                            "UPDATE room SET capacity=%s, exam_capacity=%s WHERE uniqueid=%s",
                            (cap, exam_cap, existing["uniqueid"])
                        )
                        result.skipped += 1
                        result.warnings.append(f"Row {i}: Room {room_num} exists — updated capacity.")
                        continue

                    rid = self._next_id()
                    await cur.execute("""
                        INSERT INTO room
                            (uniqueid, session_id, building_id, room_number,
                             capacity, exam_capacity, room_type, permanent_id,
                             ignore_too_far, ignore_room_check)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 0)
                    """, (rid, SESSION_ID, building_id, room_num, cap, exam_cap, rt_id, rid))

                    # Link to department
                    await cur.execute(
                        "SELECT uniqueid FROM room_dept WHERE room_id=%s AND department_id=%s",
                        (rid, DEPARTMENT_ID)
                    )
                    if not await cur.fetchone():
                        rdid = self._next_id()
                        await cur.execute(
                            "INSERT INTO room_dept (uniqueid, room_id, department_id, is_control) "
                            "VALUES (%s, %s, %s, 1)",
                            (rdid, rid, DEPARTMENT_ID)
                        )

                    lab_str = "Lab" if is_lab else "Lecture"
                    result.inserted += 1
                    result.details.append(f"Room {room_num} (cap:{cap}, {lab_str})")

            await conn.commit()
            result.success = True
        except Exception as e:
            await conn.rollback()
            result.errors.append(f"Database error: {str(e)}")
        finally:
            self.db.pool.release(conn)

        return result

    # ══════════════════════════════════════════
    # UPLOAD: COURSES (+ classes, like seed.py + fix.py)
    # ══════════════════════════════════════════

    async def upload_courses(self, csv_content: str) -> BulkUploadResult:
        """
        CSV columns: subject, course_number, title, credits, semester, has_lab, expected_enrollment
        has_lab: yes/no (default: yes)
        expected_enrollment: integer (default: 50)
        credits: integer (default: 3)
        semester: integer 1-8 (optional)
        """
        result = BulkUploadResult()
        rows, parse_errors = self._parse_csv(csv_content)
        result.errors.extend(parse_errors)
        result.total_rows = len(rows)

        if not rows:
            result.errors.append("No data rows found in CSV.")
            return result

        required = {"subject", "course_number", "title"}
        headers = set(rows[0].keys())
        missing = required - headers
        if missing:
            result.errors.append(f"Missing required columns: {', '.join(missing)}")
            return result

        conn = await self.db.pool.acquire()
        try:
            await self._init_ids(conn)
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # Load subject areas
                await cur.execute(
                    "SELECT uniqueid, subject_area_abbreviation FROM subject_area WHERE session_id=%s",
                    (SESSION_ID,)
                )
                sa_map = {r["subject_area_abbreviation"]: int(r["uniqueid"]) for r in await cur.fetchall()}

                # Load time pattern (AIET 1x80)
                await cur.execute(
                    "SELECT uniqueid FROM time_pattern WHERE session_id=%s AND name='AIET 1x80'",
                    (SESSION_ID,)
                )
                tp_row = await cur.fetchone()
                if not tp_row:
                    # Fallback to any 1x80 pattern
                    await cur.execute(
                        "SELECT uniqueid FROM time_pattern WHERE session_id=%s AND nr_mtgs=1 AND mins_pmt=80 LIMIT 1",
                        (SESSION_ID,)
                    )
                    tp_row = await cur.fetchone()
                if not tp_row:
                    await cur.execute(
                        "SELECT uniqueid FROM time_pattern WHERE session_id=%s LIMIT 1",
                        (SESSION_ID,)
                    )
                    tp_row = await cur.fetchone()

                tp_id = int(tp_row["uniqueid"]) if tp_row else None

                # Load preference level
                pref_id = None
                for tbl in ("preference_level", "pref_level"):
                    try:
                        await cur.execute(f"SELECT uniqueid, pref_prolog FROM {tbl}")
                        for pl in await cur.fetchall():
                            if str(pl.get("pref_prolog", "")).upper() == "R":
                                pref_id = int(pl["uniqueid"])
                                break
                        if pref_id:
                            break
                    except Exception:
                        pass

                # Load time_pref columns
                await cur.execute("DESCRIBE time_pref")
                tp_cols = [c["Field"] for c in await cur.fetchall()]
                has_pref_level = "pref_level_id" in tp_cols

                # Get grid size for time pref string
                grid_size = 0
                if tp_id:
                    await cur.execute(
                        "SELECT COUNT(*) AS n FROM time_pattern_days WHERE time_pattern_id=%s", (tp_id,))
                    nd = int((await cur.fetchone())["n"])
                    await cur.execute(
                        "SELECT COUNT(*) AS n FROM time_pattern_time WHERE time_pattern_id=%s", (tp_id,))
                    ns = int((await cur.fetchone())["n"])
                    grid_size = nd * ns

                # Load staff
                await cur.execute("""
                    SELECT di.uniqueid, pt.label FROM departmental_instructor di
                    LEFT JOIN position_type pt ON di.pos_code_type=pt.uniqueid
                    WHERE di.department_uniqueid=%s ORDER BY pt.sort_order, di.lname
                """, (DEPARTMENT_ID,))
                all_staff = await cur.fetchall()
                professors = []
                instructors = []
                for s in all_staff:
                    label = str(s.get("label", "")).lower()
                    sid = int(s["uniqueid"])
                    if "professor" in label and "assistant" not in label:
                        professors.append(sid)
                    else:
                        instructors.append(sid)
                if not professors and all_staff:
                    professors = [int(s["uniqueid"]) for s in all_staff[:4]]
                    instructors = [int(s["uniqueid"]) for s in all_staff[4:]]
                if not instructors:
                    instructors = professors.copy()

                # Load rooms
                await cur.execute("""
                    SELECT r.uniqueid, r.capacity FROM room r
                    JOIN building b ON r.building_id=b.uniqueid
                    WHERE r.session_id=%s AND b.abbreviation='AIET'
                    ORDER BY r.capacity DESC
                """, (SESSION_ID,))
                lec_rooms = []
                lab_rooms = []
                for r in await cur.fetchall():
                    rid = int(r["uniqueid"])
                    if int(r["capacity"]) >= LECTURE_CAPACITY:
                        lec_rooms.append(rid)
                    else:
                        lab_rooms.append(rid)

                # Scheduling counters
                prof_idx = 0
                inst_idx = 0
                day_idx = 0
                slot_idx = 0
                lec_room_idx = 0
                lab_room_idx = 0

                for i, row in enumerate(rows, start=2):
                    subj = row.get("subject", "").strip().upper()
                    nbr = row.get("course_number", "").strip()
                    title = row.get("title", "").strip()
                    credits = int(row.get("credits", "3") or "3")
                    has_lab_str = row.get("has_lab", "yes").strip().lower()
                    enrollment = int(row.get("expected_enrollment", "50") or "50")

                    if not subj or not nbr or not title:
                        result.errors.append(f"Row {i}: subject, course_number, and title are required.")
                        result.skipped += 1
                        continue

                    # Auto-detect no-lab from title
                    has_lab = has_lab_str in ("yes", "true", "1", "y")
                    title_lower = title.lower()
                    if any(kw in title_lower for kw in NO_LAB_KEYWORDS):
                        has_lab = False

                    # Find or create subject area
                    sa_id = sa_map.get(subj)
                    if not sa_id:
                        sa_id = self._next_id()
                        await cur.execute("""
                            INSERT INTO subject_area
                                (uniqueid, session_id, subject_area_abbreviation,
                                 long_title, department_uniqueid)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (sa_id, SESSION_ID, subj, subj, DEPARTMENT_ID))
                        sa_map[subj] = sa_id
                        result.warnings.append(f"Row {i}: Created new subject area '{subj}'.")

                    # Check duplicate course
                    await cur.execute(
                        "SELECT co.uniqueid FROM course_offering co "
                        "JOIN subject_area sa ON co.subject_area_id=sa.uniqueid "
                        "WHERE sa.uniqueid=%s AND co.course_nbr=%s",
                        (sa_id, nbr)
                    )
                    if await cur.fetchone():
                        result.skipped += 1
                        result.warnings.append(f"Row {i}: {subj} {nbr} already exists.")
                        continue

                    # ── Create offering chain ──
                    io_id = self._next_id()
                    await cur.execute("""
                        INSERT INTO instructional_offering
                            (uniqueid, session_id, instr_offering_perm_id, not_offered)
                        VALUES (%s, %s, %s, 0)
                    """, (io_id, SESSION_ID, io_id))

                    co_id = self._next_id()
                    await cur.execute("""
                        INSERT INTO course_offering
                            (uniqueid, course_nbr, title, perm_id,
                             subject_area_id, instr_offr_id, is_control,
                             nbr_expected_stdents, proj_demand)
                        VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s)
                    """, (co_id, nbr, title, str(co_id), sa_id, io_id, enrollment, enrollment))

                    cfg_id = self._next_id()
                    await cur.execute("""
                        INSERT INTO instr_offering_config
                            (uniqueid, config_limit, instr_offr_id, unlimited_enrollment, name)
                        VALUES (%s, %s, %s, 0, %s)
                    """, (cfg_id, enrollment, io_id, "1"))

                    # ── Lecture subpart + classes ──
                    num_lectures = math.ceil(enrollment / LECTURE_CAPACITY)

                    lec_sp_id = self._next_id()
                    await cur.execute("""
                        INSERT INTO scheduling_subpart
                            (uniqueid, min_per_wk, config_id, itype,
                             auto_time_spread, student_allow_overlap)
                        VALUES (%s, %s, %s, %s, 1, 0)
                    """, (lec_sp_id, MINUTES_PER_PERIOD, cfg_id, ITYPE_LECTURE))

                    # Time pref
                    if tp_id:
                        pref_str = "2" * grid_size if grid_size > 0 else None
                        tpref_id = self._next_id()
                        if has_pref_level and pref_id:
                            await cur.execute(
                                "INSERT INTO time_pref (uniqueid, owner_id, pref_level_id, time_pattern_id, preference) "
                                "VALUES (%s, %s, %s, %s, %s)",
                                (tpref_id, lec_sp_id, pref_id, tp_id, pref_str))
                        else:
                            await cur.execute(
                                "INSERT INTO time_pref (uniqueid, owner_id, time_pattern_id, preference) "
                                "VALUES (%s, %s, %s, %s)",
                                (tpref_id, lec_sp_id, tp_id, pref_str))

                    for sec in range(num_lectures):
                        cap = min(LECTURE_CAPACITY, enrollment - sec * LECTURE_CAPACITY)
                        if cap <= 0:
                            cap = LECTURE_CAPACITY

                        cls_id = self._next_id()
                        await cur.execute("""
                            INSERT INTO class_ (
                                uniqueid, subpart_id, expected_capacity,
                                max_expected_capacity, room_capacity, room_ratio,
                                nbr_rooms, date_pattern_id, managing_dept,
                                display_instructor, display_in_sched_book,
                                class_suffix, section_number, cancelled
                            ) VALUES (%s,%s,%s,%s,%s,1.0,1,%s,%s,1,1,%s,%s,0)
                        """, (cls_id, lec_sp_id, cap, cap, cap,
                              DATE_PATTERN_ID, DEPARTMENT_ID, str(sec+1), sec+1))

                        # Professor
                        if professors:
                            pid = professors[prof_idx % len(professors)]
                            prof_idx += 1
                            ci_id = self._next_id()
                            await cur.execute(
                                "INSERT INTO class_instructor (uniqueid, class_id, instructor_id, percent_share, is_lead) "
                                "VALUES (%s,%s,%s,100,1)", (ci_id, cls_id, pid))

                        # Assignment
                        if tp_id:
                            days = SINGLE_DAYS[day_idx % len(SINGLE_DAYS)]
                            slot = VALID_SLOTS[slot_idx % len(VALID_SLOTS)]
                            day_idx += 1
                            slot_idx += 1

                            aid = self._next_id()
                            await cur.execute(
                                "INSERT INTO assignment (uniqueid, days, slot, time_pattern_id, class_id, date_pattern_id) "
                                "VALUES (%s,%s,%s,%s,%s,%s)",
                                (aid, days, slot, tp_id, cls_id, DATE_PATTERN_ID))

                            room_id = None
                            if lec_rooms:
                                room_id = lec_rooms[lec_room_idx % len(lec_rooms)]
                                lec_room_idx += 1
                            elif lab_rooms:
                                room_id = lab_rooms[0]
                            if room_id:
                                await cur.execute(
                                    "INSERT INTO assigned_rooms (assignment_id, room_id) VALUES (%s,%s)",
                                    (aid, room_id))

                    # ── Lab subpart + classes ──
                    if has_lab:
                        num_labs = math.ceil(enrollment / LAB_CAPACITY)

                        lab_sp_id = self._next_id()
                        await cur.execute("""
                            INSERT INTO scheduling_subpart
                                (uniqueid, min_per_wk, config_id, itype,
                                 auto_time_spread, student_allow_overlap)
                            VALUES (%s, %s, %s, %s, 1, 0)
                        """, (lab_sp_id, MINUTES_PER_PERIOD, cfg_id, ITYPE_LAB))

                        if tp_id:
                            pref_str = "2" * grid_size if grid_size > 0 else None
                            tpref_id = self._next_id()
                            if has_pref_level and pref_id:
                                await cur.execute(
                                    "INSERT INTO time_pref (uniqueid, owner_id, pref_level_id, time_pattern_id, preference) "
                                    "VALUES (%s,%s,%s,%s,%s)",
                                    (tpref_id, lab_sp_id, pref_id, tp_id, pref_str))
                            else:
                                await cur.execute(
                                    "INSERT INTO time_pref (uniqueid, owner_id, time_pattern_id, preference) "
                                    "VALUES (%s,%s,%s,%s)",
                                    (tpref_id, lab_sp_id, tp_id, pref_str))

                        for sec in range(num_labs):
                            cap = min(LAB_CAPACITY, enrollment - sec * LAB_CAPACITY)
                            if cap <= 0:
                                cap = LAB_CAPACITY

                            cls_id = self._next_id()
                            await cur.execute("""
                                INSERT INTO class_ (
                                    uniqueid, subpart_id, expected_capacity,
                                    max_expected_capacity, room_capacity, room_ratio,
                                    nbr_rooms, date_pattern_id, managing_dept,
                                    display_instructor, display_in_sched_book,
                                    class_suffix, section_number, cancelled
                                ) VALUES (%s,%s,%s,%s,%s,1.0,1,%s,%s,1,1,%s,%s,0)
                            """, (cls_id, lab_sp_id, cap, cap, cap,
                                  DATE_PATTERN_ID, DEPARTMENT_ID, str(sec+1), sec+1))

                            if instructors:
                                iid = instructors[inst_idx % len(instructors)]
                                inst_idx += 1
                                ci_id = self._next_id()
                                await cur.execute(
                                    "INSERT INTO class_instructor (uniqueid, class_id, instructor_id, percent_share, is_lead) "
                                    "VALUES (%s,%s,%s,100,1)", (ci_id, cls_id, iid))

                            if tp_id:
                                days = SINGLE_DAYS[day_idx % len(SINGLE_DAYS)]
                                slot = VALID_SLOTS[slot_idx % len(VALID_SLOTS)]
                                day_idx += 1
                                slot_idx += 1

                                aid = self._next_id()
                                await cur.execute(
                                    "INSERT INTO assignment (uniqueid, days, slot, time_pattern_id, class_id, date_pattern_id) "
                                    "VALUES (%s,%s,%s,%s,%s,%s)",
                                    (aid, days, slot, tp_id, cls_id, DATE_PATTERN_ID))

                                room_id = None
                                if lab_rooms:
                                    room_id = lab_rooms[lab_room_idx % len(lab_rooms)]
                                    lab_room_idx += 1
                                if room_id:
                                    await cur.execute(
                                        "INSERT INTO assigned_rooms (assignment_id, room_id) VALUES (%s,%s)",
                                        (aid, room_id))

                    lab_str = f"+ {math.ceil(enrollment / LAB_CAPACITY)} labs" if has_lab else "(no lab)"
                    result.inserted += 1
                    result.details.append(f"{subj} {nbr}: {title} [{num_lectures} lec {lab_str}]")

            await conn.commit()
            result.success = True
        except Exception as e:
            await conn.rollback()
            result.errors.append(f"Database error: {str(e)}")
        finally:
            self.db.pool.release(conn)

        return result