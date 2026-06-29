"""
Direct database access to UniTime's MySQL database.
All operations target the AIET Fal 2010 session.

Mirrors seed.py + fix.py exactly:
  - Same ID generation (hibernate_unique_key hi-lo)
  - Same schema discovery (column names vary across UniTime versions)
  - Same class structure: class_ with ALL required fields
  - Same time_pref creation with correct grid strings
  - Same assignment + assigned_rooms creation
  - Same room_dept linking
  - Lecture (itype=10) + Lab (itype=30) support
  - AIET time patterns (1x80, 2x80, 3x80)
"""

import math
import decimal
import datetime
import asyncio
import aiomysql
from typing import Optional
from app.config import settings


# ══════════════════════════════════════════════════════
# CONSTANTS — matches fix.py exactly
# ══════════════════════════════════════════════════════

SESSION_ID = settings.UNITIME_SESSION_ID            # 231379
DEPARTMENT_ID = settings.UNITIME_DEPARTMENT_ID      # 231383
DATE_PATTERN_ID = settings.UNITIME_DATE_PATTERN_ID  # 853

ITYPE_LECTURE = 10
ITYPE_LAB = 30

LECTURE_CAPACITY = 50
LAB_CAPACITY = 25
MINUTES_PER_PERIOD = 80
SLOTS_PER_MEETING = 16  # 80 min / 5 min per slot

# Day bit values (UniTime standard)
DAY_BITS = {
    "Mon": 64, "Tue": 32, "Wed": 16,
    "Thu": 8, "Fri": 4, "Sat": 2, "Sun": 1,
}

# Working days (6 days, no Friday) — matches fix.py
SINGLE_DAYS = [2, 1, 64, 32, 16, 8]  # Sat, Sun, Mon, Tue, Wed, Thu

# Valid time slots — matches fix.py
VALID_SLOTS = [108, 132, 156]  # 9:00, 11:00, 13:00

# Courses with NO lab component — matches seed.py and fix.py
NO_LAB_COURSES = {
    "UNR 122Z", "UNR 222Z",
    "UNR 1403", "UNR 1407", "UNR 2101",
    "APT 2101", "APT 2102", "APT 3103", "APT 3201", "APT 4202",
    "ADS 4501", "ADS 4502",
    "AIS 4501", "AIS 4502",
    "ARB 322Z", "ARB 4221", "ARB 4222",
    "EBA 1271", "EBA 1272", "EBA 2204",
    "AGN 1301",
}


class UniTimeDB:

    def __init__(self):
        self.pool: Optional[aiomysql.Pool] = None
        self.session_id = SESSION_ID
        self.department_id = DEPARTMENT_ID
        self.date_pattern_id = DATE_PATTERN_ID
        self._id_counter = None
        self._id_lock = None

        # Schema discovery results — populated on connect()
        self._all_tables = set()
        self._class_columns = []
        self._time_pref_columns = []
        self._tp_days_has_uniqueid = False
        self._tp_days_fk_col = None
        self._tp_days_day_col = None
        self._tp_time_has_uniqueid = False
        self._tp_time_fk_col = None
        self._tp_time_slot_col = None
        self._tpd_tp_col = None
        self._tpd_dept_col = None
        self._required_pref_id = None
        self._can_add_time_prefs = False

        # Cached lookups — populated on connect()
        self._aiet_1x80_id = None
        self._lecture_room_ids = []
        self._lab_room_ids = []

    # ══════════════════════════════════════════════
    # CONNECTION + INITIALIZATION
    # ══════════════════════════════════════════════

    async def connect(self):
        self.pool = await aiomysql.create_pool(
            host=settings.UNITIME_DB_HOST,
            port=settings.UNITIME_DB_PORT,
            user=settings.UNITIME_DB_USER,
            password=settings.UNITIME_DB_PASSWORD,
            db=settings.UNITIME_DB_NAME,
            autocommit=False,
            minsize=2,
            maxsize=10,
            charset="utf8mb4",
        )
        self._id_lock = asyncio.Lock()
        await self._init_id_counter()
        await self._discover_schema()
        await self._cache_lookups()
        print(f"UniTime DB: Connected to "
              f"{settings.UNITIME_DB_HOST}:{settings.UNITIME_DB_PORT}"
              f"/{settings.UNITIME_DB_NAME}")
        print(f"UniTime DB: Using session {self.session_id} (Fal 2010)")
        print(f"UniTime DB: ID counter starts at {self._id_counter}")
        print(f"UniTime DB: AIET 1x80 pattern id={self._aiet_1x80_id}")
        print(f"UniTime DB: {len(self._lecture_room_ids)} lecture rooms, "
              f"{len(self._lab_room_ids)} lab rooms cached")

    async def disconnect(self):
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
            print("UniTime DB: Disconnected")

    # ══════════════════════════════════════════════
    # ID GENERATION — exact copy from seed.py
    # ══════════════════════════════════════════════

    async def _init_id_counter(self):
        async with self.pool.acquire() as conn:
            cur = await conn.cursor(aiomysql.DictCursor)
            try:
                await cur.execute(
                    "SELECT next_hi FROM hibernate_unique_key FOR UPDATE"
                )
                row = await cur.fetchone()
                if row:
                    current_hi = int(row["next_hi"])
                    new_hi = current_hi + 500
                    await cur.execute(
                        "UPDATE hibernate_unique_key SET next_hi = %s",
                        (new_hi,)
                    )
                    await conn.commit()
                    self._id_counter = current_hi * 32
                    print(f"  ID range starts at {self._id_counter} "
                          f"(reserved hi {current_hi}–{new_hi})")
                    await cur.close()
                    return
            except Exception as e:
                await conn.rollback()
                print(f"  hibernate_unique_key read failed: {e}")

            max_id = 0
            tables = [
                "departmental_instructor", "instructional_offering",
                "course_offering", "class_", "scheduling_subpart",
                "instr_offering_config", "assignment", "student",
                "student_class_enrl", "class_instructor", "room",
            ]
            for table in tables:
                try:
                    await cur.execute(
                        f"SELECT COALESCE(MAX(uniqueid), 0) AS m "
                        f"FROM {table}"
                    )
                    row = await cur.fetchone()
                    if row and row["m"]:
                        max_id = max(max_id, int(row["m"]))
                except Exception:
                    pass
            await conn.commit()
            await cur.close()
            self._id_counter = max_id + 1000
            print(f"  ID range starts at {self._id_counter} (fallback)")

    def _next_id(self) -> int:
        uid = self._id_counter
        self._id_counter += 1
        return uid

    # ══════════════════════════════════════════════
    # SCHEMA DISCOVERY — mirrors seed.py + fix.py
    # ══════════════════════════════════════════════

    async def _discover_schema(self):
        async with self.pool.acquire() as conn:
            cur = await conn.cursor(aiomysql.DictCursor)

            # All tables
            await cur.execute("SHOW TABLES")
            for row in await cur.fetchall():
                for v in row.values():
                    self._all_tables.add(str(v).lower())

            # class_ columns
            await cur.execute("DESCRIBE class_")
            self._class_columns = [
                c["Field"] for c in await cur.fetchall()
            ]

            # time_pref columns
            if "time_pref" in self._all_tables:
                await cur.execute("DESCRIBE time_pref")
                self._time_pref_columns = [
                    c["Field"] for c in await cur.fetchall()
                ]

            # time_pattern_days schema
            if "time_pattern_days" in self._all_tables:
                await cur.execute("DESCRIBE time_pattern_days")
                for c in await cur.fetchall():
                    col = c["Field"]
                    col_l = col.lower()
                    if col_l == "uniqueid":
                        self._tp_days_has_uniqueid = True
                    elif "pattern" in col_l:
                        self._tp_days_fk_col = col
                    elif col_l in ("day_code", "day", "days"):
                        self._tp_days_day_col = col

            # time_pattern_time schema
            if "time_pattern_time" in self._all_tables:
                await cur.execute("DESCRIBE time_pattern_time")
                for c in await cur.fetchall():
                    col = c["Field"]
                    col_l = col.lower()
                    if col_l == "uniqueid":
                        self._tp_time_has_uniqueid = True
                    elif "pattern" in col_l:
                        self._tp_time_fk_col = col
                    elif col_l in ("start_slot", "slot", "time_slot"):
                        self._tp_time_slot_col = col

            # time_pattern_dept schema
            if "time_pattern_dept" in self._all_tables:
                await cur.execute("DESCRIBE time_pattern_dept")
                for c in await cur.fetchall():
                    col = c["Field"]
                    if "pattern" in col.lower():
                        self._tpd_tp_col = col
                    if "dept" in col.lower():
                        self._tpd_dept_col = col

            # preference levels — matches seed.py logic
            pref_table = (
                "preference_level"
                if "preference_level" in self._all_tables
                else "pref_level"
                if "pref_level" in self._all_tables
                else None
            )
            if pref_table:
                await cur.execute(
                    f"SELECT * FROM {pref_table} ORDER BY uniqueid"
                )
                all_levels = await cur.fetchall()
                for pl in all_levels:
                    if str(pl.get("pref_prolog", "")).upper() == "R":
                        self._required_pref_id = int(pl["uniqueid"])
                        self._can_add_time_prefs = True
                        break
                if not self._required_pref_id:
                    for pl in all_levels:
                        if str(pl.get("pref_prolog", "")) == "0":
                            self._required_pref_id = int(pl["uniqueid"])
                            self._can_add_time_prefs = True
                            break

            await conn.commit()
            await cur.close()

        print(f"  Schema: class_ has {len(self._class_columns)} cols, "
              f"time_pref={'yes' if self._can_add_time_prefs else 'no'}, "
              f"pref_level={self._required_pref_id}")

    # ══════════════════════════════════════════════
    # CACHE LOOKUPS (time patterns, rooms)
    # ══════════════════════════════════════════════

    async def _cache_lookups(self):
        """Cache the AIET 1x80 pattern ID and room classifications."""
        # Find AIET 1x80 time pattern
        row = await self._fetch_one(
            "SELECT uniqueid FROM time_pattern "
            "WHERE session_id = %s AND name = %s",
            (self.session_id, "AIET 1x80")
        )
        if row:
            self._aiet_1x80_id = int(row["uniqueid"])
        else:
            # Fallback: find any 1x80 pattern
            row = await self._fetch_one(
                "SELECT uniqueid FROM time_pattern "
                "WHERE session_id = %s AND nr_mtgs = 1 AND mins_pmt = 80 "
                "LIMIT 1",
                (self.session_id,)
            )
            if row:
                self._aiet_1x80_id = int(row["uniqueid"])

        # Cache room classifications — matches fix.py logic
        rooms = await self._fetch_all("""
            SELECT r.uniqueid, r.capacity
            FROM room r
            JOIN building b ON r.building_id = b.uniqueid
            WHERE r.session_id = %s AND b.abbreviation = 'AIET'
            ORDER BY r.capacity DESC
        """, (self.session_id,))
        for r in rooms:
            rid = int(r["uniqueid"])
            cap = int(r["capacity"])
            if cap >= LECTURE_CAPACITY:
                self._lecture_room_ids.append(rid)
            else:
                self._lab_room_ids.append(rid)

    async def refresh_cache(self):
        """Re-cache lookups after external changes (e.g. adding rooms)."""
        self._lecture_room_ids = []
        self._lab_room_ids = []
        self._aiet_1x80_id = None
        await self._cache_lookups()

    # ══════════════════════════════════════════════
    # INTERNAL HELPERS
    # ══════════════════════════════════════════════

    def _clean_row(self, row: dict) -> dict:
        cleaned = {}
        for k, v in row.items():
            if isinstance(v, decimal.Decimal):
                cleaned[k] = int(v) if v == int(v) else float(v)
            elif isinstance(v, datetime.datetime):
                cleaned[k] = v.strftime("%Y-%m-%d %H:%M")
            elif isinstance(v, datetime.date):
                cleaned[k] = v.strftime("%Y-%m-%d")
            elif isinstance(v, bytes):
                cleaned[k] = v.decode("utf-8", errors="replace")
            else:
                cleaned[k] = v
        return cleaned

    async def _fetch_all(self, query: str, args=None) -> list[dict]:
        async with self.pool.acquire() as conn:
            cur = await conn.cursor(aiomysql.DictCursor)
            await cur.execute(query, args)
            rows = await cur.fetchall()
            await conn.commit()
            await cur.close()
            return [self._clean_row(r) for r in rows]

    async def _fetch_one(self, query: str, args=None) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            cur = await conn.cursor(aiomysql.DictCursor)
            await cur.execute(query, args)
            row = await cur.fetchone()
            await conn.commit()
            await cur.close()
            return self._clean_row(row) if row else None

    async def _write_transaction(self, callback) -> dict:
        async with self.pool.acquire() as conn:
            cur = await conn.cursor(aiomysql.DictCursor)
            try:
                result = await callback(cur)
                await conn.commit()
                return {"success": True, "result": result}
            except Exception as e:
                await conn.rollback()
                print(f"DB Transaction Error: {e}")
                return {"success": False, "error": str(e)}
            finally:
                await cur.close()

    # ══════════════════════════════════════════════
    # TIME PREF HELPER — mirrors seed.py + fix.py
    # ══════════════════════════════════════════════

    async def _insert_time_pref(self, cur, owner_id: int, tp_id: int):
        """
        Insert a time_pref record for a scheduling_subpart.
        Grid = n_days × n_times filled with '2' (neutral preference).
        Mirrors seed.py _insert_time_prefs() and fix.py _add_time_pref().
        """
        if not self._can_add_time_prefs:
            return

        # Get grid size from time_pattern_days/time_pattern_time
        await cur.execute(
            f"SELECT COUNT(*) AS n FROM time_pattern_days "
            f"WHERE {self._tp_days_fk_col} = %s",
            (tp_id,)
        )
        n_days = int((await cur.fetchone())["n"])

        await cur.execute(
            f"SELECT COUNT(*) AS n FROM time_pattern_time "
            f"WHERE {self._tp_time_fk_col} = %s",
            (tp_id,)
        )
        n_times = int((await cur.fetchone())["n"])

        grid_size = n_days * n_times
        pref_str = "2" * grid_size if grid_size > 0 else None

        pref_id = self._next_id()

        if ("pref_level_id" in self._time_pref_columns
                and self._required_pref_id):
            await cur.execute(
                "INSERT INTO time_pref "
                "(uniqueid, owner_id, pref_level_id, "
                "time_pattern_id, preference) "
                "VALUES (%s, %s, %s, %s, %s)",
                (pref_id, owner_id, self._required_pref_id,
                 tp_id, pref_str),
            )
        else:
            await cur.execute(
                "INSERT INTO time_pref "
                "(uniqueid, owner_id, "
                "time_pattern_id, preference) "
                "VALUES (%s, %s, %s, %s)",
                (pref_id, owner_id, tp_id, pref_str),
            )

    # ══════════════════════════════════════════════
    # CLASS INSERT HELPER — mirrors fix.py _create_class()
    # ══════════════════════════════════════════════

    async def _insert_class(self, cur, sp_id: int, capacity: int,
                            suffix: str, section_num: int) -> int:
        """
        Insert a class with ALL required fields.
        Matches fix.py _create_class() exactly.
        """
        cls_id = self._next_id()
        cols, vals = ["uniqueid"], [cls_id]

        def add(c, v):
            if c in self._class_columns:
                cols.append(c)
                vals.append(v)

        add("subpart_id", sp_id)
        add("date_pattern_id", self.date_pattern_id)
        add("managing_dept", self.department_id)
        add("class_suffix", suffix)
        add("section_number", section_num)
        add("expected_capacity", capacity)
        add("max_expected_capacity", capacity)
        add("room_capacity", capacity)
        add("room_ratio", 1.0)
        add("nbr_rooms", 1)
        add("display_instructor", 1)
        add("display_in_sched_book", 1)
        add("cancelled", 0)
        add("enrollment", 0)
        add("rooms_split_att", 0)

        ph = ",".join(["%s"] * len(vals))
        await cur.execute(
            f"INSERT INTO class_ ({','.join(cols)}) VALUES ({ph})",
            vals,
        )
        return cls_id

    # ══════════════════════════════════════════════
    # ASSIGNMENT HELPER — mirrors fix.py _create_assignment()
    # ══════════════════════════════════════════════

    async def _insert_assignment(self, cur, class_id: int,
                                 days: int, slot: int,
                                 tp_id: int,
                                 room_id: int = None) -> int:
        """
        Create assignment + assigned_rooms.
        Matches fix.py _create_assignment() exactly.
        """
        aid = self._next_id()
        await cur.execute("""
            INSERT INTO assignment
                (uniqueid, days, slot, time_pattern_id,
                 class_id, date_pattern_id)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (aid, days, slot, tp_id, class_id, self.date_pattern_id))

        if room_id:
            await cur.execute(
                "INSERT INTO assigned_rooms "
                "(assignment_id, room_id) VALUES (%s, %s)",
                (aid, room_id)
            )
        return aid

    # ══════════════════════════════════════════════
    # ROOM-DEPT LINK HELPER — mirrors seed.py
    # ══════════════════════════════════════════════

    async def _ensure_room_dept(self, cur, room_id: int):
        """
        Ensure room_dept link exists for this room + department.
        Matches seed.py seed_rooms() room_dept logic.
        """
        if "room_dept" not in self._all_tables:
            return
        await cur.execute(
            "SELECT uniqueid FROM room_dept "
            "WHERE room_id = %s AND department_id = %s",
            (room_id, self.department_id)
        )
        if not await cur.fetchone():
            rdid = self._next_id()
            await cur.execute(
                "INSERT INTO room_dept "
                "(uniqueid, room_id, department_id, is_control) "
                "VALUES (%s, %s, %s, 1)",
                (rdid, room_id, self.department_id)
            )

    # ══════════════════════════════════════════════
    # SESSIONS
    # ══════════════════════════════════════════════

    async def get_sessions(self) -> list[dict]:
        return await self._fetch_all("""
            SELECT
                s.uniqueid as id,
                s.academic_initiative as campus,
                s.academic_term as term,
                s.academic_year as year,
                s.session_begin_date_time as begin_date,
                s.session_end_date_time as end_date,
                s.classes_end_date_time as classes_end,
                dst.reference as status_ref,
                dst.label as status_label
            FROM sessions s
            LEFT JOIN dept_status_type dst
                ON s.status_type = dst.uniqueid
            ORDER BY s.academic_year DESC, s.academic_term
        """)

    async def get_active_session_id(self) -> int:
        return self.session_id

    # ══════════════════════════════════════════════
    # DEPARTMENTS (READ)
    # ══════════════════════════════════════════════

    async def get_departments(self, session_id: int = None) -> list[dict]:
        sid = session_id or self.session_id
        return await self._fetch_all("""
            SELECT d.uniqueid as id, d.dept_code, d.abbreviation,
                   d.name, d.external_uid as external_id
            FROM department d WHERE d.session_id = %s ORDER BY d.name
        """, (sid,))

    async def get_department_by_id(self, dept_id: int) -> Optional[dict]:
        return await self._fetch_one("""
            SELECT uniqueid as id, dept_code, abbreviation,
                   name, session_id
            FROM department WHERE uniqueid = %s
        """, (dept_id,))

    # ══════════════════════════════════════════════
    # ROOMS
    # ══════════════════════════════════════════════

    async def get_buildings(self, session_id: int = None) -> list[dict]:
        sid = session_id or self.session_id
        return await self._fetch_all("""
            SELECT uniqueid as id, abbreviation, name,
                   coordinate_x, coordinate_y
            FROM building WHERE session_id = %s ORDER BY name
        """, (sid,))

    async def get_rooms(self, session_id: int = None) -> list[dict]:
        sid = session_id or self.session_id
        return await self._fetch_all("""
            SELECT r.uniqueid as id,
                   r.room_number,
                   r.capacity,
                   r.exam_capacity,
                   r.room_type as room_type_id,
                   r.building_id,
                   b.abbreviation as building_abbr,
                   b.name as building_name,
                   rt.label as room_type_label
            FROM room r
            JOIN building b ON r.building_id = b.uniqueid
            LEFT JOIN room_type rt ON r.room_type = rt.uniqueid
            WHERE r.session_id = %s
            ORDER BY b.abbreviation, r.room_number
        """, (sid,))

    async def get_room_by_id(self, room_id: int) -> Optional[dict]:
        return await self._fetch_one("""
            SELECT r.uniqueid as id,
                   r.room_number,
                   r.capacity,
                   r.exam_capacity,
                   r.room_type as room_type_id,
                   r.building_id,
                   b.abbreviation as building_abbr,
                   b.name as building_name
            FROM room r
            JOIN building b ON r.building_id = b.uniqueid
            WHERE r.uniqueid = %s
        """, (room_id,))

    async def get_room_types(self) -> list[dict]:
        return await self._fetch_all(
            "SELECT uniqueid as id, reference, label, is_room "
            "FROM room_type ORDER BY ord"
        )

    async def get_room_features(self, room_id: int) -> list[dict]:
        return await self._fetch_all("""
            SELECT rf.uniqueid as id, rf.label, rf.abbv
            FROM room_feature rf
            JOIN room_join_room_feature rjf
                ON rf.uniqueid = rjf.feature_id
            WHERE rjf.room_id = %s
        """, (room_id,))

    async def add_room(self, data: dict) -> int:
        """
        Insert a new room AND create room_dept link.
        Matches seed.py seed_rooms() exactly.

        Required: building_id, room_number, capacity,
                  exam_capacity, room_type_id
        Optional: ignore_too_far, ignore_room_check
        """
        required = ["building_id", "room_number", "capacity",
                     "exam_capacity", "room_type_id"]
        for field in required:
            if not data.get(field):
                raise ValueError(
                    f"add_room: missing required field '{field}'"
                )

        new_id = self._next_id()
        perm_id = new_id

        async def _do(cur):
            # Insert room — matches seed.py
            await cur.execute("""
                INSERT INTO room (
                    uniqueid, session_id, building_id, room_number,
                    capacity, exam_capacity, room_type, permanent_id,
                    ignore_too_far, ignore_room_check
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                new_id, self.session_id,
                int(data["building_id"]),
                str(data["room_number"]),
                int(data["capacity"]),
                int(data["exam_capacity"]),
                int(data["room_type_id"]),
                perm_id,
                int(data.get("ignore_too_far", 0)),
                int(data.get("ignore_room_check", 0)),
            ))

            # Create room_dept link — matches seed.py
            await self._ensure_room_dept(cur, new_id)

            return new_id

        result = await self._write_transaction(_do)
        if not result["success"]:
            raise Exception(
                f"Failed to add room: {result.get('error')}"
            )

        # Refresh room cache
        await self.refresh_cache()
        return new_id

    async def update_room(self, room_id: int,
                          capacity: int = None,
                          exam_capacity: int = None) -> bool:
        updates, args = [], []
        if capacity is not None:
            updates.append("capacity = %s")
            args.append(int(capacity))
        if exam_capacity is not None:
            updates.append("exam_capacity = %s")
            args.append(int(exam_capacity))
        if not updates:
            return False
        args.append(room_id)

        async def _do(cur):
            await cur.execute(
                f"UPDATE room SET {', '.join(updates)} "
                f"WHERE uniqueid = %s",
                args
            )

        result = await self._write_transaction(_do)
        if result.get("success"):
            await self.refresh_cache()
        return result.get("success", False)

    async def delete_room(self, room_id: int) -> bool:
        """
        Delete room with all FK references.
        Removes assigned_rooms and room_dept first.
        """
        async def _do(cur):
            await cur.execute(
                "DELETE FROM assigned_rooms WHERE room_id = %s",
                (room_id,)
            )
            if "room_dept" in self._all_tables:
                await cur.execute(
                    "DELETE FROM room_dept WHERE room_id = %s",
                    (room_id,)
                )
            await cur.execute(
                "DELETE FROM room WHERE uniqueid = %s", (room_id,)
            )

        result = await self._write_transaction(_do)
        if result.get("success"):
            await self.refresh_cache()
        return result.get("success", False)

    # ══════════════════════════════════════════════
    # INSTRUCTORS
    # ══════════════════════════════════════════════

    async def get_instructors(self, department_id: int = None,
                              session_id: int = None) -> list[dict]:
        sid = session_id or self.session_id
        query = """
            SELECT di.uniqueid as id,
                   di.external_uid as external_id,
                   di.fname as first_name,
                   di.mname as middle_name,
                   di.lname as last_name,
                   di.email,
                   d.uniqueid as department_id,
                   d.name as department_name,
                   d.abbreviation as dept_abbr,
                   pt.label as position_label,
                   di.pos_code_type as position_type_id
            FROM departmental_instructor di
            JOIN department d
                ON di.department_uniqueid = d.uniqueid
            LEFT JOIN position_type pt
                ON di.pos_code_type = pt.uniqueid
            WHERE d.session_id = %s
        """
        args = [sid]
        if department_id:
            query += " AND di.department_uniqueid = %s"
            args.append(department_id)
        query += " ORDER BY di.lname, di.fname"
        return await self._fetch_all(query, args)

    async def get_instructor_by_id(self,
                                   instructor_id: int) -> Optional[dict]:
        return await self._fetch_one("""
            SELECT di.uniqueid as id,
                   di.external_uid as external_id,
                   di.fname as first_name,
                   di.mname as middle_name,
                   di.lname as last_name,
                   di.email,
                   di.department_uniqueid as department_id,
                   d.name as department_name,
                   d.abbreviation as dept_abbr,
                   pt.label as position_label,
                   di.pos_code_type as position_type_id
            FROM departmental_instructor di
            JOIN department d
                ON di.department_uniqueid = d.uniqueid
            LEFT JOIN position_type pt
                ON di.pos_code_type = pt.uniqueid
            WHERE di.uniqueid = %s
        """, (instructor_id,))

    async def get_position_types(self) -> list[dict]:
        return await self._fetch_all(
            "SELECT uniqueid as id, reference, label "
            "FROM position_type ORDER BY sort_order"
        )

    async def add_instructor(self, data: dict) -> int:
        """
        Insert instructor. Matches seed.py seed_staff() exactly.

        Required: first_name, last_name
        Optional: middle_name, email, external_id,
                  department_id, position_type_id
        """
        if not data.get("first_name") or not data.get("last_name"):
            raise ValueError(
                "add_instructor: first_name and last_name required"
            )

        new_id = self._next_id()

        dept_id = data.get("department_id")
        if dept_id:
            dept_id = int(dept_id)
        else:
            dept_id = self.department_id

        # Verify department session
        dept = await self._fetch_one(
            "SELECT uniqueid, session_id FROM department "
            "WHERE uniqueid = %s",
            (dept_id,)
        )
        if not dept:
            raise ValueError(
                f"add_instructor: department_id {dept_id} not found"
            )
        if int(dept["session_id"]) != self.session_id:
            raise ValueError(
                f"add_instructor: department {dept_id} belongs to "
                f"session {dept['session_id']}, not {self.session_id}"
            )

        # External UID — matches seed.py
        ext_id = data.get("external_id")
        if not ext_id:
            email = data.get("email", "")
            if email and "@" in email:
                ext_id = email.split("@")[0]
            else:
                ext_id = str(new_id)

        # Position type — matches seed.py
        pos_type = data.get("position_type_id")
        if pos_type:
            pos_type = int(pos_type)
        else:
            row = await self._fetch_one(
                "SELECT uniqueid FROM position_type "
                "ORDER BY sort_order LIMIT 1"
            )
            pos_type = int(row["uniqueid"]) if row else None

        async def _do(cur):
            await cur.execute("""
                INSERT INTO departmental_instructor
                    (uniqueid, external_uid, fname, mname, lname,
                     email, department_uniqueid, pos_code_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                new_id, ext_id,
                data["first_name"].strip(),
                data.get("middle_name", "").strip(),
                data["last_name"].strip(),
                data.get("email", "").strip(),
                dept_id, pos_type,
            ))
            return new_id

        result = await self._write_transaction(_do)
        if not result["success"]:
            raise Exception(
                f"Failed to add instructor: {result.get('error')}"
            )
        return new_id

    async def update_instructor(self, instructor_id: int,
                                data: dict) -> bool:
        updates, args = [], []
        field_map = {
            "first_name":       "fname",
            "middle_name":      "mname",
            "last_name":        "lname",
            "email":            "email",
            "external_id":      "external_uid",
            "department_id":    "department_uniqueid",
            "position_type_id": "pos_code_type",
        }
        for key, col in field_map.items():
            if key in data and data[key] is not None:
                updates.append(f"{col} = %s")
                args.append(data[key])
        if not updates:
            return False
        args.append(instructor_id)

        async def _do(cur):
            await cur.execute(
                f"UPDATE departmental_instructor "
                f"SET {', '.join(updates)} WHERE uniqueid = %s",
                args
            )

        result = await self._write_transaction(_do)
        return result.get("success", False)

    async def delete_instructor(self, instructor_id: int) -> bool:
        async def _do(cur):
            await cur.execute(
                "DELETE FROM class_instructor "
                "WHERE instructor_id = %s",
                (instructor_id,)
            )
            await cur.execute(
                "DELETE FROM departmental_instructor "
                "WHERE uniqueid = %s",
                (instructor_id,)
            )

        result = await self._write_transaction(_do)
        return result.get("success", False)

    # ══════════════════════════════════════════════
    # SUBJECT AREAS (READ)
    # ══════════════════════════════════════════════

    async def get_subject_areas(self, session_id: int = None,
                                department_id: int = None) -> list[dict]:
        sid = session_id or self.session_id
        query = """
            SELECT sa.uniqueid as id,
                   sa.subject_area_abbreviation as abbreviation,
                   sa.long_title as title,
                   sa.department_uniqueid as department_id
            FROM subject_area sa WHERE sa.session_id = %s
        """
        args = [sid]
        if department_id:
            query += " AND sa.department_uniqueid = %s"
            args.append(department_id)
        query += " ORDER BY sa.subject_area_abbreviation"
        return await self._fetch_all(query, args)

    # ══════════════════════════════════════════════
    # COURSES (READ + WRITE)
    # ══════════════════════════════════════════════

    async def get_courses(self, session_id: int = None,
                          subject_area_id: int = None) -> list[dict]:
        sid = session_id or self.session_id
        query = """
            SELECT co.uniqueid as id,
                   co.course_nbr as course_number,
                   co.title,
                   co.perm_id,
                   co.proj_demand as projected_demand,
                   co.nbr_expected_stdents as expected_students,
                   co.enrollment as actual_demand,
                   sa.subject_area_abbreviation as subject,
                   sa.uniqueid as subject_area_id,
                   io.uniqueid as offering_id,
                   d.name as department_name,
                   d.uniqueid as department_id
            FROM course_offering co
            JOIN instructional_offering io
                ON co.instr_offr_id = io.uniqueid
            JOIN subject_area sa
                ON co.subject_area_id = sa.uniqueid
            LEFT JOIN department d
                ON sa.department_uniqueid = d.uniqueid
            WHERE io.session_id = %s
        """
        args = [sid]
        if subject_area_id:
            query += " AND co.subject_area_id = %s"
            args.append(subject_area_id)
        query += (" ORDER BY sa.subject_area_abbreviation, "
                   "co.course_nbr")
        return await self._fetch_all(query, args)

    async def get_course_by_id(self,
                               course_id: int) -> Optional[dict]:
        return await self._fetch_one("""
            SELECT co.uniqueid as id,
                   co.course_nbr as course_number,
                   co.title,
                   co.perm_id,
                   co.proj_demand as projected_demand,
                   co.nbr_expected_stdents as expected_students,
                   sa.subject_area_abbreviation as subject,
                   sa.uniqueid as subject_area_id,
                   io.uniqueid as offering_id,
                   io.session_id
            FROM course_offering co
            JOIN instructional_offering io
                ON co.instr_offr_id = io.uniqueid
            JOIN subject_area sa
                ON co.subject_area_id = sa.uniqueid
            WHERE co.uniqueid = %s
        """, (course_id,))

    async def add_course(self, data: dict) -> dict:
        """
        Create a complete course with solver-compatible structure.
        Mirrors seed.py seed_courses() + fix.py rebuild_classes():

        instructional_offering
          → course_offering
          → instr_offering_config
          → scheduling_subpart (lecture, itype=10)
            → class_ (with ALL fields)
            → time_pref
            → assignment + assigned_rooms
            → class_instructor (if provided)
          → scheduling_subpart (lab, itype=30) [if has_lab]
            → class_ sections (ceil(expected/25))
            → time_pref per subpart
            → assignment + assigned_rooms per section
            → class_instructor per section (if provided)

        Required: subject_area_id, course_number, title
        Optional: expected_students (default 50),
                  has_lab (auto-detected from NO_LAB_COURSES),
                  lecture_instructor_id, lab_instructor_ids (list),
                  session_id
        """
        if not data.get("subject_area_id"):
            raise ValueError("add_course: subject_area_id is required")
        if not data.get("course_number"):
            raise ValueError("add_course: course_number is required")
        if not data.get("title"):
            raise ValueError("add_course: title is required")

        sa_id = int(data["subject_area_id"])
        number = str(data["course_number"]).strip()
        title = data["title"].strip()
        sid = data.get("session_id", self.session_id)
        expected = int(data.get("expected_students", LECTURE_CAPACITY))

        # Determine subject abbreviation for NO_LAB_COURSES check
        sa_row = await self._fetch_one(
            "SELECT subject_area_abbreviation FROM subject_area "
            "WHERE uniqueid = %s",
            (sa_id,)
        )
        subject_abbr = sa_row["subject_area_abbreviation"] if sa_row else ""
        course_key = f"{subject_abbr} {number}"

        # Determine if this course has a lab
        if "has_lab" in data:
            has_lab = bool(data["has_lab"])
        else:
            has_lab = course_key not in NO_LAB_COURSES

        # Ensure we have a time pattern
        tp_id = self._aiet_1x80_id
        if not tp_id:
            raise Exception(
                "AIET 1x80 time pattern not found. "
                "Run fix.py first to create time patterns."
            )

        # Pre-generate all IDs
        io_id = self._next_id()
        co_id = self._next_id()
        config_id = self._next_id()
        lec_sp_id = self._next_id()

        # Calculate sections — matches fix.py
        num_lectures = math.ceil(expected / LECTURE_CAPACITY)
        num_labs = (math.ceil(expected / LAB_CAPACITY)
                    if has_lab else 0)

        # Get scheduling indices for day/slot/room round-robin
        # Count existing assignments to continue the rotation
        existing_lec = await self._fetch_one("""
            SELECT COUNT(*) AS cnt FROM class_ c
            JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            WHERE io.session_id = %s AND sp.itype = %s
        """, (self.session_id, ITYPE_LECTURE))
        lec_offset = int(existing_lec["cnt"]) if existing_lec else 0

        existing_lab = await self._fetch_one("""
            SELECT COUNT(*) AS cnt FROM class_ c
            JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            WHERE io.session_id = %s AND sp.itype = %s
        """, (self.session_id, ITYPE_LAB))
        lab_offset = int(existing_lab["cnt"]) if existing_lab else 0

        # Instructor IDs
        lec_instructor_id = data.get("lecture_instructor_id")
        lab_instructor_ids = data.get("lab_instructor_ids", [])

        async def _do(cur):
            # 1. Instructional offering — matches seed.py
            await cur.execute("""
                INSERT INTO instructional_offering
                    (uniqueid, session_id, instr_offering_perm_id,
                     not_offered)
                VALUES (%s, %s, %s, 0)
            """, (io_id, sid, io_id))

            # 2. Course offering — matches seed.py
            await cur.execute("""
                INSERT INTO course_offering
                    (uniqueid, course_nbr, title, perm_id,
                     subject_area_id, instr_offr_id, is_control,
                     nbr_expected_stdents, proj_demand)
                VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s)
            """, (co_id, number, title, str(co_id),
                  sa_id, io_id, expected, expected))

            # 3. Config — matches seed.py / fix.py
            await cur.execute("""
                INSERT INTO instr_offering_config
                    (uniqueid, config_limit, instr_offr_id,
                     unlimited_enrollment, name)
                VALUES (%s, %s, %s, 0, %s)
            """, (config_id, expected, io_id, "1"))

            # 4. Lecture subpart — matches fix.py
            await cur.execute("""
                INSERT INTO scheduling_subpart
                    (uniqueid, min_per_wk, config_id, itype,
                     auto_time_spread, student_allow_overlap)
                VALUES (%s, %s, %s, %s, 1, 0)
            """, (lec_sp_id, MINUTES_PER_PERIOD, config_id,
                  ITYPE_LECTURE))

            # 5. Time pref for lecture subpart
            await self._insert_time_pref(cur, lec_sp_id, tp_id)

            # 6. Lecture class sections
            result_classes = {"lectures": [], "labs": []}

            for sec in range(num_lectures):
                cap = min(LECTURE_CAPACITY,
                          expected - (sec * LECTURE_CAPACITY))
                if cap <= 0:
                    cap = LECTURE_CAPACITY

                cls_id = await self._insert_class(
                    cur, lec_sp_id, cap,
                    str(sec + 1), sec + 1
                )
                result_classes["lectures"].append(cls_id)

                # Assign instructor if provided
                if lec_instructor_id:
                    ci_id = self._next_id()
                    await cur.execute("""
                        INSERT INTO class_instructor
                            (uniqueid, class_id, instructor_id,
                             percent_share, is_lead)
                        VALUES (%s, %s, %s, 100, 1)
                    """, (ci_id, cls_id, int(lec_instructor_id)))

                # Assignment — round-robin day/slot/room
                idx = lec_offset + sec
                days = SINGLE_DAYS[idx % len(SINGLE_DAYS)]
                slot = VALID_SLOTS[idx % len(VALID_SLOTS)]

                room_id = None
                if self._lecture_room_ids:
                    room_id = self._lecture_room_ids[
                        idx % len(self._lecture_room_ids)
                    ]
                elif self._lab_room_ids:
                    room_id = self._lab_room_ids[0]

                await self._insert_assignment(
                    cur, cls_id, days, slot, tp_id, room_id
                )

            # 7. Lab subpart + sections (if applicable)
            if has_lab and num_labs > 0:
                lab_sp_id = self._next_id()
                await cur.execute("""
                    INSERT INTO scheduling_subpart
                        (uniqueid, min_per_wk, config_id, itype,
                         auto_time_spread, student_allow_overlap)
                    VALUES (%s, %s, %s, %s, 1, 0)
                """, (lab_sp_id, MINUTES_PER_PERIOD, config_id,
                      ITYPE_LAB))

                # Time pref for lab subpart
                await self._insert_time_pref(cur, lab_sp_id, tp_id)

                for sec in range(num_labs):
                    cap = min(LAB_CAPACITY,
                              expected - (sec * LAB_CAPACITY))
                    if cap <= 0:
                        cap = LAB_CAPACITY

                    cls_id = await self._insert_class(
                        cur, lab_sp_id, cap,
                        str(sec + 1), sec + 1
                    )
                    result_classes["labs"].append(cls_id)

                    # Assign lab instructor if provided
                    if lab_instructor_ids and sec < len(lab_instructor_ids):
                        ci_id = self._next_id()
                        await cur.execute("""
                            INSERT INTO class_instructor
                                (uniqueid, class_id, instructor_id,
                                 percent_share, is_lead)
                            VALUES (%s, %s, %s, 100, 1)
                        """, (ci_id, cls_id,
                              int(lab_instructor_ids[sec])))

                    # Assignment — round-robin
                    idx = lab_offset + sec
                    days = SINGLE_DAYS[idx % len(SINGLE_DAYS)]
                    slot = VALID_SLOTS[idx % len(VALID_SLOTS)]

                    room_id = None
                    if self._lab_room_ids:
                        room_id = self._lab_room_ids[
                            idx % len(self._lab_room_ids)
                        ]

                    await self._insert_assignment(
                        cur, cls_id, days, slot, tp_id, room_id
                    )

            return {
                "offering_id": io_id,
                "course_id": co_id,
                "config_id": config_id,
                "lecture_subpart_id": lec_sp_id,
                "classes": result_classes,
                "has_lab": has_lab,
                "num_lectures": num_lectures,
                "num_labs": num_labs,
            }

        result = await self._write_transaction(_do)
        if not result["success"]:
            raise Exception(
                f"Failed to add course: {result.get('error')}"
            )
        return result["result"]

    async def update_course(self, course_id: int,
                            data: dict) -> bool:
        updates, args = [], []
        field_map = {
            "course_number":     "course_nbr",
            "title":             "title",
            "expected_students": "nbr_expected_stdents",
            "projected_demand":  "proj_demand",
        }
        for key, col in field_map.items():
            if key in data and data[key] is not None:
                updates.append(f"{col} = %s")
                args.append(data[key])
        if not updates:
            return False
        args.append(course_id)

        async def _do(cur):
            await cur.execute(
                f"UPDATE course_offering "
                f"SET {', '.join(updates)} WHERE uniqueid = %s",
                args
            )

            # If expected_students changed, update config_limit
            # and class capacities too — matches fix.py
            if "expected_students" in data:
                new_expected = int(data["expected_students"])
                course = await self._fetch_one(
                    "SELECT instr_offr_id FROM course_offering "
                    "WHERE uniqueid = %s",
                    (course_id,)
                )
                if course:
                    await cur.execute(
                        "UPDATE instr_offering_config "
                        "SET config_limit = %s "
                        "WHERE instr_offr_id = %s",
                        (new_expected, course["instr_offr_id"])
                    )

        result = await self._write_transaction(_do)
        return result.get("success", False)

    async def delete_course(self, course_id: int) -> bool:
        """
        Delete course with full cascade.
        Matches the reverse of seed.py creation order.
        """
        course = await self.get_course_by_id(course_id)
        if not course:
            return False
        offering_id = course["offering_id"]

        classes = await self._fetch_all("""
            SELECT c.uniqueid FROM class_ c
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            WHERE ioc.instr_offr_id = %s
        """, (offering_id,))

        subparts = await self._fetch_all("""
            SELECT sp.uniqueid FROM scheduling_subpart sp
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            WHERE ioc.instr_offr_id = %s
        """, (offering_id,))

        async def _do(cur):
            # Delete classes and their dependencies
            for c in classes:
                cid = c["uniqueid"]
                await cur.execute(
                    "DELETE FROM student_class_enrl "
                    "WHERE class_id = %s", (cid,)
                )
                await cur.execute("""
                    DELETE FROM assigned_rooms
                    WHERE assignment_id IN (
                        SELECT uniqueid FROM assignment
                        WHERE class_id = %s
                    )
                """, (cid,))
                await cur.execute(
                    "DELETE FROM assignment "
                    "WHERE class_id = %s", (cid,)
                )
                await cur.execute(
                    "DELETE FROM class_instructor "
                    "WHERE class_id = %s", (cid,)
                )
                await cur.execute(
                    "DELETE FROM class_ WHERE uniqueid = %s", (cid,)
                )

            # Delete subparts and their time_pref
            for sp in subparts:
                sp_id = sp["uniqueid"]
                await cur.execute(
                    "DELETE FROM time_pref WHERE owner_id = %s",
                    (sp_id,)
                )
                await cur.execute(
                    "DELETE FROM scheduling_subpart "
                    "WHERE uniqueid = %s", (sp_id,)
                )

            # Delete config
            await cur.execute(
                "DELETE FROM instr_offering_config "
                "WHERE instr_offr_id = %s",
                (offering_id,)
            )
            # Delete course offering
            await cur.execute(
                "DELETE FROM course_offering "
                "WHERE uniqueid = %s", (course_id,)
            )
            # Delete instructional offering
            await cur.execute(
                "DELETE FROM instructional_offering "
                "WHERE uniqueid = %s", (offering_id,)
            )

        result = await self._write_transaction(_do)
        return result.get("success", False)

    # ══════════════════════════════════════════════
    # CLASSES (READ + WRITE)
    # ══════════════════════════════════════════════

    async def get_classes(self, session_id: int = None,
                          instructor_id: int = None) -> list[dict]:
        sid = session_id or self.session_id
        query = """
            SELECT
                c.uniqueid as id,
                ANY_VALUE(c.expected_capacity) as expected_capacity,
                ANY_VALUE(c.nbr_rooms) as nbr_rooms,
                ANY_VALUE(c.class_suffix) as class_suffix,
                ANY_VALUE(c.section_number) as section_number,
                ANY_VALUE(c.enrollment) as enrollment,
                ANY_VALUE(co.title) as course_title,
                ANY_VALUE(co.course_nbr) as course_number,
                ANY_VALUE(sa.subject_area_abbreviation) as subject,
                ANY_VALUE(d.name) as department_name,
                ANY_VALUE(dp.name) as date_pattern_name,
                ANY_VALUE(tp.name) as time_pattern_name,
                ANY_VALUE(tp.mins_pmt) as minutes_per_meeting,
                ANY_VALUE(tp.nr_mtgs) as meetings_per_week,
                ANY_VALUE(a.days) as assigned_days,
                ANY_VALUE(a.slot) as assigned_slot,
                ANY_VALUE(ar.room_id) as assigned_room_id,
                ANY_VALUE(r.room_number) as assigned_room_number,
                ANY_VALUE(b.abbreviation) as assigned_building,
                ANY_VALUE(sp.itype) as instruction_type,
                GROUP_CONCAT(
                    DISTINCT CONCAT(di.fname, ' ', di.lname)
                    SEPARATOR ', '
                ) as instructor_names
            FROM class_ c
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            JOIN course_offering co
                ON co.instr_offr_id = io.uniqueid
                AND co.is_control = 1
            JOIN subject_area sa
                ON co.subject_area_id = sa.uniqueid
            LEFT JOIN department d
                ON sa.department_uniqueid = d.uniqueid
            LEFT JOIN date_pattern dp
                ON c.date_pattern_id = dp.uniqueid
            LEFT JOIN assignment a
                ON a.class_id = c.uniqueid
            LEFT JOIN time_pattern tp
                ON a.time_pattern_id = tp.uniqueid
            LEFT JOIN assigned_rooms ar
                ON ar.assignment_id = a.uniqueid
            LEFT JOIN room r ON ar.room_id = r.uniqueid
            LEFT JOIN building b ON r.building_id = b.uniqueid
            LEFT JOIN class_instructor ci
                ON ci.class_id = c.uniqueid
            LEFT JOIN departmental_instructor di
                ON ci.instructor_id = di.uniqueid
            WHERE io.session_id = %s
        """
        args = [sid]
        if instructor_id:
            query += " AND ci.instructor_id = %s"
            args.append(instructor_id)
        query += (
            " GROUP BY c.uniqueid"
            " ORDER BY ANY_VALUE(sa.subject_area_abbreviation),"
            "          ANY_VALUE(co.course_nbr),"
            "          ANY_VALUE(sp.itype),"
            "          ANY_VALUE(c.section_number)"
        )
        return await self._fetch_all(query, args)

    async def get_class_by_id(self,
                              class_id: int) -> Optional[dict]:
        return await self._fetch_one("""
            SELECT c.uniqueid as id,
                   c.expected_capacity,
                   c.class_suffix,
                   c.date_pattern_id,
                   c.section_number,
                   c.subpart_id,
                   sp.itype as instruction_type,
                   co.title as course_title,
                   co.course_nbr as course_number,
                   co.uniqueid as course_offering_id,
                   sa.subject_area_abbreviation as subject,
                   io.session_id
            FROM class_ c
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            JOIN course_offering co
                ON co.instr_offr_id = io.uniqueid
                AND co.is_control = 1
            JOIN subject_area sa
                ON co.subject_area_id = sa.uniqueid
            WHERE c.uniqueid = %s
        """, (class_id,))

    async def add_class(self, data: dict) -> int:
        """
        Add a class section to an existing course offering.
        Creates class_ with ALL required fields, plus assignment,
        assigned_rooms, time_pref, and class_instructor.

        Matches fix.py rebuild_classes() exactly.

        Required: course_id (course_offering.uniqueid)
        Optional: expected_capacity (default matches itype),
                  instruction_type (10=lecture, 30=lab, default 10),
                  instructor_id, room_id,
                  days (day bitmask), slot (start slot),
                  class_suffix
        """
        course = await self.get_course_by_id(int(data["course_id"]))
        if not course:
            raise ValueError(
                f"add_class: course_id {data['course_id']} not found"
            )

        configs = await self.get_configs_for_offering(
            course["offering_id"]
        )
        if not configs:
            raise ValueError(
                "add_class: no config found for this course"
            )
        config_id = configs[0]["id"]

        itype = int(data.get("instruction_type", ITYPE_LECTURE))

        # Find or create matching subpart
        subparts = await self.get_subparts_for_config(config_id)
        sp_id = None
        for sp in subparts:
            if int(sp["itype"]) == itype:
                sp_id = sp["id"]
                break

        tp_id = self._aiet_1x80_id
        if not tp_id:
            raise Exception(
                "AIET 1x80 time pattern not found. Run fix.py first."
            )

        # If no matching subpart exists, create one
        create_subpart = sp_id is None

        # Determine next section number
        if sp_id:
            row = await self._fetch_one(
                "SELECT COALESCE(MAX(section_number), 0) AS max_sec "
                "FROM class_ WHERE subpart_id = %s",
                (sp_id,)
            )
            next_section = int(row["max_sec"]) + 1 if row else 1
        else:
            next_section = 1

        # Default capacity based on itype — matches fix.py
        if itype == ITYPE_LAB:
            default_cap = LAB_CAPACITY
        else:
            default_cap = LECTURE_CAPACITY
        capacity = int(data.get("expected_capacity", default_cap))

        suffix = str(data.get("class_suffix", str(next_section)))

        # Day/slot — use provided or round-robin
        days = data.get("days")
        slot = data.get("slot")
        room_id = data.get("room_id")

        if days is None or slot is None:
            # Count existing classes of this itype for offset
            existing = await self._fetch_one("""
                SELECT COUNT(*) AS cnt FROM class_ c
                JOIN scheduling_subpart sp
                    ON c.subpart_id = sp.uniqueid
                JOIN instr_offering_config ioc
                    ON sp.config_id = ioc.uniqueid
                JOIN instructional_offering io
                    ON ioc.instr_offr_id = io.uniqueid
                WHERE io.session_id = %s AND sp.itype = %s
            """, (self.session_id, itype))
            idx = int(existing["cnt"]) if existing else 0

            if days is None:
                days = SINGLE_DAYS[idx % len(SINGLE_DAYS)]
            if slot is None:
                slot = VALID_SLOTS[idx % len(VALID_SLOTS)]

        days = int(days)
        slot = int(slot)

        if room_id is None:
            # Auto-assign room based on itype
            existing = await self._fetch_one("""
                SELECT COUNT(*) AS cnt FROM class_ c
                JOIN scheduling_subpart sp
                    ON c.subpart_id = sp.uniqueid
                JOIN instr_offering_config ioc
                    ON sp.config_id = ioc.uniqueid
                JOIN instructional_offering io
                    ON ioc.instr_offr_id = io.uniqueid
                WHERE io.session_id = %s AND sp.itype = %s
            """, (self.session_id, itype))
            idx = int(existing["cnt"]) if existing else 0

            if itype == ITYPE_LAB and self._lab_room_ids:
                room_id = self._lab_room_ids[
                    idx % len(self._lab_room_ids)
                ]
            elif self._lecture_room_ids:
                room_id = self._lecture_room_ids[
                    idx % len(self._lecture_room_ids)
                ]
        else:
            room_id = int(room_id)

        instructor_id = data.get("instructor_id")

        async def _do(cur):
            nonlocal sp_id

            # Create subpart if needed
            if create_subpart:
                sp_id = self._next_id()
                await cur.execute("""
                    INSERT INTO scheduling_subpart
                        (uniqueid, min_per_wk, config_id, itype,
                         auto_time_spread, student_allow_overlap)
                    VALUES (%s, %s, %s, %s, 1, 0)
                """, (sp_id, MINUTES_PER_PERIOD, config_id, itype))

                # Time pref for new subpart
                await self._insert_time_pref(cur, sp_id, tp_id)

            # Create class with ALL fields — matches fix.py
            cls_id = await self._insert_class(
                cur, sp_id, capacity, suffix, next_section
            )

            # Assign instructor
            if instructor_id:
                ci_id = self._next_id()
                await cur.execute("""
                    INSERT INTO class_instructor
                        (uniqueid, class_id, instructor_id,
                         percent_share, is_lead)
                    VALUES (%s, %s, %s, 100, 1)
                """, (ci_id, cls_id, int(instructor_id)))

            # Create assignment + room
            await self._insert_assignment(
                cur, cls_id, days, slot, tp_id, room_id
            )

            return cls_id

        result = await self._write_transaction(_do)
        if not result["success"]:
            raise Exception(
                f"Failed to add class: {result.get('error')}"
            )
        return result["result"]

    async def delete_class(self, class_id: int) -> bool:
        """Delete class with full cascade — matches fix.py order."""
        async def _do(cur):
            await cur.execute(
                "DELETE FROM student_class_enrl "
                "WHERE class_id = %s", (class_id,)
            )
            await cur.execute("""
                DELETE FROM assigned_rooms
                WHERE assignment_id IN (
                    SELECT uniqueid FROM assignment
                    WHERE class_id = %s
                )
            """, (class_id,))
            await cur.execute(
                "DELETE FROM assignment WHERE class_id = %s",
                (class_id,)
            )
            await cur.execute(
                "DELETE FROM class_instructor "
                "WHERE class_id = %s", (class_id,)
            )
            await cur.execute(
                "DELETE FROM class_ WHERE uniqueid = %s",
                (class_id,)
            )

            # If this was the last class in its subpart,
            # clean up the subpart and time_pref too
            # (Check done after delete)

        result = await self._write_transaction(_do)
        return result.get("success", False)

    async def assign_instructor_to_class(
        self, class_id: int, instructor_id: int,
        percent_share: int = 100, is_lead: int = 1
    ) -> int:
        """Assign instructor — matches seed.py pattern."""
        # Remove existing first to avoid duplicates
        await self.remove_instructor_from_class(
            class_id, instructor_id
        )

        new_id = self._next_id()

        async def _do(cur):
            await cur.execute("""
                INSERT INTO class_instructor
                    (uniqueid, class_id, instructor_id,
                     percent_share, is_lead)
                VALUES (%s, %s, %s, %s, %s)
            """, (new_id, class_id, instructor_id,
                  percent_share, is_lead))
            return new_id

        result = await self._write_transaction(_do)
        if not result["success"]:
            raise Exception(
                f"Failed to assign instructor: {result.get('error')}"
            )
        return new_id

    async def remove_instructor_from_class(
        self, class_id: int, instructor_id: int
    ) -> bool:
        async def _do(cur):
            await cur.execute(
                "DELETE FROM class_instructor "
                "WHERE class_id = %s AND instructor_id = %s",
                (class_id, instructor_id)
            )

        result = await self._write_transaction(_do)
        return result.get("success", False)

    async def get_time_patterns(self,
                                session_id: int = None) -> list[dict]:
        sid = session_id or self.session_id
        return await self._fetch_all("""
            SELECT uniqueid as id, name,
                   nr_mtgs as meetings_per_week,
                   mins_pmt as minutes_per_meeting,
                   slots_pmt, type
            FROM time_pattern WHERE session_id = %s
        """, (sid,))

    async def get_date_patterns(self,
                                session_id: int = None) -> list[dict]:
        sid = session_id or self.session_id
        return await self._fetch_all(
            "SELECT uniqueid as id, name, pattern, type "
            "FROM date_pattern WHERE session_id = %s",
            (sid,)
        )

    async def get_configs_for_offering(self,
                                       offering_id: int) -> list[dict]:
        return await self._fetch_all(
            "SELECT uniqueid as id, name, config_limit "
            "FROM instr_offering_config WHERE instr_offr_id = %s",
            (offering_id,)
        )

    async def get_subparts_for_config(self,
                                      config_id: int) -> list[dict]:
        return await self._fetch_all(
            "SELECT uniqueid as id, min_per_wk, itype, "
            "subpart_suffix "
            "FROM scheduling_subpart WHERE config_id = %s",
            (config_id,)
        )

    # ══════════════════════════════════════════════
    # STUDENTS & ENROLLMENTS
    # ══════════════════════════════════════════════

    async def get_students(self,
                           session_id: int = None) -> list[dict]:
        sid = session_id or self.session_id
        return await self._fetch_all("""
            SELECT s.uniqueid as id,
                   s.external_uid as external_id,
                   s.first_name, s.middle_name, s.last_name,
                   s.email, s.session_id
            FROM student s WHERE s.session_id = %s
            ORDER BY s.last_name, s.first_name
        """, (sid,))

    async def get_student_by_id(self,
                                student_id: int) -> Optional[dict]:
        return await self._fetch_one("""
            SELECT uniqueid as id,
                   external_uid as external_id,
                   first_name, middle_name, last_name,
                   email, session_id
            FROM student WHERE uniqueid = %s
        """, (student_id,))

    async def get_student_schedule(self,
                                   student_id: int) -> list[dict]:
        return await self._fetch_all("""
            SELECT
                sce.uniqueid as enrollment_id,
                ANY_VALUE(c.uniqueid) as class_id,
                ANY_VALUE(co.title) as course_title,
                ANY_VALUE(co.course_nbr) as course_number,
                ANY_VALUE(sa.subject_area_abbreviation) as subject,
                ANY_VALUE(sp.itype) as instruction_type,
                ANY_VALUE(tp.name) as time_pattern,
                ANY_VALUE(tp.mins_pmt) as minutes_per_meeting,
                ANY_VALUE(tp.nr_mtgs) as meetings_per_week,
                ANY_VALUE(dp.name) as date_pattern,
                ANY_VALUE(r.room_number) as room_number,
                ANY_VALUE(b.abbreviation) as building,
                ANY_VALUE(a.days) as assigned_days,
                ANY_VALUE(a.slot) as assigned_slot,
                ANY_VALUE(c.section_number) as section_number,
                ANY_VALUE(c.class_suffix) as class_suffix,
                GROUP_CONCAT(
                    DISTINCT CONCAT(di.fname, ' ', di.lname)
                    SEPARATOR ', '
                ) as instructors
            FROM student_class_enrl sce
            JOIN class_ c ON sce.class_id = c.uniqueid
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            JOIN course_offering co
                ON co.instr_offr_id = io.uniqueid
                AND co.is_control = 1
            JOIN subject_area sa
                ON co.subject_area_id = sa.uniqueid
            LEFT JOIN assignment a ON a.class_id = c.uniqueid
            LEFT JOIN time_pattern tp
                ON a.time_pattern_id = tp.uniqueid
            LEFT JOIN date_pattern dp
                ON a.date_pattern_id = dp.uniqueid
            LEFT JOIN assigned_rooms ar
                ON ar.assignment_id = a.uniqueid
            LEFT JOIN room r ON ar.room_id = r.uniqueid
            LEFT JOIN building b ON r.building_id = b.uniqueid
            LEFT JOIN class_instructor ci
                ON ci.class_id = c.uniqueid
            LEFT JOIN departmental_instructor di
                ON ci.instructor_id = di.uniqueid
            WHERE sce.student_id = %s
            GROUP BY sce.uniqueid
            ORDER BY ANY_VALUE(sa.subject_area_abbreviation),
                     ANY_VALUE(co.course_nbr),
                     ANY_VALUE(sp.itype)
        """, (student_id,))

    async def get_enrollments_by_course(self,
                                        course_id: int) -> list[dict]:
        return await self._fetch_all("""
            SELECT sce.uniqueid as enrollment_id,
                   ANY_VALUE(s.uniqueid) as student_id,
                   ANY_VALUE(s.first_name) as first_name,
                   ANY_VALUE(s.last_name) as last_name,
                   ANY_VALUE(s.email) as email,
                   ANY_VALUE(c.class_suffix) as class_suffix,
                   ANY_VALUE(co.course_nbr) as course_number,
                   ANY_VALUE(sa.subject_area_abbreviation) as subject
            FROM student_class_enrl sce
            JOIN student s ON sce.student_id = s.uniqueid
            JOIN class_ c ON sce.class_id = c.uniqueid
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            JOIN course_offering co
                ON co.instr_offr_id = io.uniqueid
                AND co.is_control = 1
            JOIN subject_area sa
                ON co.subject_area_id = sa.uniqueid
            WHERE co.uniqueid = %s
            GROUP BY sce.uniqueid
            ORDER BY ANY_VALUE(s.last_name),
                     ANY_VALUE(s.first_name)
        """, (course_id,))

    async def add_student(self, data: dict) -> int:
        """
        Add student. Matches seed.py seed_students() exactly.

        Required: first_name, last_name
        Optional: middle_name, email, external_id
        """
        if not data.get("first_name") or not data.get("last_name"):
            raise ValueError(
                "add_student: first_name and last_name required"
            )

        new_id = self._next_id()
        ext_id = data.get("external_id")
        if not ext_id:
            email = data.get("email", "")
            ext_id = (email.split("@")[0]
                      if "@" in email else f"STU{new_id}")

        async def _do(cur):
            await cur.execute("""
                INSERT INTO student
                    (uniqueid, external_uid, first_name,
                     middle_name, last_name, email, session_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                new_id, ext_id,
                data["first_name"].strip(),
                data.get("middle_name", "").strip(),
                data["last_name"].strip(),
                data.get("email", "").strip(),
                self.session_id,
            ))
            return new_id

        result = await self._write_transaction(_do)
        if not result["success"]:
            raise Exception(
                f"Failed to add student: {result.get('error')}"
            )
        return new_id

    async def update_student(self, student_id: int,
                             data: dict) -> bool:
        updates, args = [], []
        field_map = {
            "first_name":  "first_name",
            "middle_name": "middle_name",
            "last_name":   "last_name",
            "email":       "email",
            "external_id": "external_uid",
        }
        for key, col in field_map.items():
            if key in data and data[key] is not None:
                updates.append(f"{col} = %s")
                args.append(data[key])
        if not updates:
            return False
        args.append(student_id)

        async def _do(cur):
            await cur.execute(
                f"UPDATE student SET {', '.join(updates)} "
                f"WHERE uniqueid = %s",
                args
            )

        result = await self._write_transaction(_do)
        return result.get("success", False)

    async def delete_student(self, student_id: int) -> bool:
        async def _do(cur):
            await cur.execute(
                "DELETE FROM student_class_enrl "
                "WHERE student_id = %s",
                (student_id,)
            )
            await cur.execute(
                "DELETE FROM student WHERE uniqueid = %s",
                (student_id,)
            )

        result = await self._write_transaction(_do)
        return result.get("success", False)

    async def enroll_student(self, student_id: int,
                             class_id: int) -> int:
        """
        Enroll student in a class with capacity check.
        Matches seed.py seed_enrollments() + fix.py
        rebuild_enrollments().
        """
        # Prevent duplicate enrollment
        existing = await self._fetch_one(
            "SELECT uniqueid FROM student_class_enrl "
            "WHERE student_id = %s AND class_id = %s",
            (student_id, class_id)
        )
        if existing:
            return int(existing["uniqueid"])

        # Capacity check — matches fix.py
        cap_row = await self._fetch_one("""
            SELECT c.expected_capacity,
                   COUNT(sce.uniqueid) AS enrolled
            FROM class_ c
            LEFT JOIN student_class_enrl sce
                ON sce.class_id = c.uniqueid
            WHERE c.uniqueid = %s
            GROUP BY c.uniqueid
        """, (class_id,))
        if cap_row:
            if int(cap_row["enrolled"]) >= int(cap_row["expected_capacity"]):
                raise ValueError(
                    f"Class {class_id} is full "
                    f"({cap_row['enrolled']}/{cap_row['expected_capacity']})"
                )

        # Get course_offering_id
        row = await self._fetch_one("""
            SELECT co.uniqueid as course_offering_id
            FROM class_ c
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            JOIN course_offering co
                ON co.instr_offr_id = io.uniqueid
                AND co.is_control = 1
            WHERE c.uniqueid = %s
        """, (class_id,))
        if not row:
            raise ValueError(
                f"enroll_student: class_id {class_id} not found"
            )

        new_id = self._next_id()

        async def _do(cur):
            await cur.execute("""
                INSERT INTO student_class_enrl
                    (uniqueid, student_id, class_id,
                     course_offering_id, timestamp)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                new_id, student_id, class_id,
                row["course_offering_id"],
                datetime.datetime.now()
            ))
            return new_id

        result = await self._write_transaction(_do)
        if not result["success"]:
            raise Exception(
                f"Failed to enroll student: {result.get('error')}"
            )
        return new_id

    async def unenroll_student(self, student_id: int,
                               class_id: int) -> bool:
        async def _do(cur):
            await cur.execute(
                "DELETE FROM student_class_enrl "
                "WHERE student_id = %s AND class_id = %s",
                (student_id, class_id)
            )

        result = await self._write_transaction(_do)
        return result.get("success", False)

    # ══════════════════════════════════════════════
    # ANALYTICS / STATS
    # ══════════════════════════════════════════════

    async def get_stats(self, session_id: int = None) -> dict:
        sid = session_id or self.session_id
        stats = {}

        row = await self._fetch_one(
            "SELECT COUNT(*) as cnt "
            "FROM departmental_instructor di "
            "JOIN department d "
            "ON di.department_uniqueid = d.uniqueid "
            "WHERE d.session_id = %s", (sid,)
        )
        stats["total_instructors"] = row["cnt"] if row else 0

        row = await self._fetch_one(
            "SELECT COUNT(*) as cnt FROM room "
            "WHERE session_id = %s", (sid,)
        )
        stats["total_rooms"] = row["cnt"] if row else 0

        row = await self._fetch_one("""
            SELECT COUNT(*) as cnt FROM course_offering co
            JOIN instructional_offering io
                ON co.instr_offr_id = io.uniqueid
            WHERE io.session_id = %s
        """, (sid,))
        stats["total_courses"] = row["cnt"] if row else 0

        row = await self._fetch_one("""
            SELECT COUNT(*) as cnt FROM class_ c
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            WHERE io.session_id = %s
        """, (sid,))
        stats["total_classes"] = row["cnt"] if row else 0

        row = await self._fetch_one(
            "SELECT COUNT(*) as cnt FROM student "
            "WHERE session_id = %s", (sid,)
        )
        stats["total_students"] = row["cnt"] if row else 0

        row = await self._fetch_one(
            "SELECT COALESCE(SUM(capacity), 0) as total "
            "FROM room WHERE session_id = %s", (sid,)
        )
        stats["total_room_capacity"] = (
            int(row["total"]) if row else 0
        )

        row = await self._fetch_one("""
            SELECT COUNT(DISTINCT ar.room_id) as cnt
            FROM assigned_rooms ar
            JOIN assignment a
                ON ar.assignment_id = a.uniqueid
            JOIN class_ c ON a.class_id = c.uniqueid
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            WHERE io.session_id = %s
        """, (sid,))
        stats["rooms_in_use"] = row["cnt"] if row else 0

        row = await self._fetch_one("""
            SELECT COUNT(DISTINCT ci.instructor_id) as cnt
            FROM class_instructor ci
            JOIN class_ c ON ci.class_id = c.uniqueid
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            WHERE io.session_id = %s
        """, (sid,))
        stats["instructors_assigned"] = row["cnt"] if row else 0

        row = await self._fetch_one("""
            SELECT COUNT(*) as cnt FROM student_class_enrl sce
            JOIN class_ c ON sce.class_id = c.uniqueid
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            WHERE io.session_id = %s
        """, (sid,))
        stats["total_enrollments"] = row["cnt"] if row else 0

        stats["departments"] = await self._fetch_all("""
            SELECT d.uniqueid as dept_id,
                   d.name as department,
                   d.abbreviation as abbr,
                   COUNT(DISTINCT di.uniqueid) as instructor_count,
                   COUNT(DISTINCT co.uniqueid) as course_count
            FROM department d
            LEFT JOIN departmental_instructor di
                ON di.department_uniqueid = d.uniqueid
            LEFT JOIN subject_area sa
                ON sa.department_uniqueid = d.uniqueid
            LEFT JOIN course_offering co
                ON co.subject_area_id = sa.uniqueid
            WHERE d.session_id = %s
            GROUP BY d.uniqueid, d.name, d.abbreviation
            ORDER BY d.name
        """, (sid,))

        stats["room_utilization"] = await self._fetch_all("""
            SELECT r.uniqueid as room_id,
                   CONCAT(b.abbreviation, ' ', r.room_number)
                       as room_name,
                   r.capacity,
                   COUNT(DISTINCT ar.assignment_id) as class_count
            FROM room r
            JOIN building b ON r.building_id = b.uniqueid
            LEFT JOIN assigned_rooms ar
                ON ar.room_id = r.uniqueid
            WHERE r.session_id = %s
            GROUP BY r.uniqueid, b.abbreviation,
                     r.room_number, r.capacity
            ORDER BY class_count DESC
            LIMIT 20
        """, (sid,))

        stats["schedule_by_day"] = await self._fetch_all("""
            SELECT a.days as day_bits,
                   COUNT(*) as class_count
            FROM assignment a
            JOIN class_ c ON a.class_id = c.uniqueid
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            WHERE io.session_id = %s AND a.days IS NOT NULL
            GROUP BY a.days ORDER BY a.days
        """, (sid,))

        stats["schedule_by_slot"] = await self._fetch_all("""
            SELECT a.slot as time_slot,
                   COUNT(*) as class_count
            FROM assignment a
            JOIN class_ c ON a.class_id = c.uniqueid
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            WHERE io.session_id = %s AND a.slot IS NOT NULL
            GROUP BY a.slot ORDER BY a.slot
        """, (sid,))

        return stats

    # ══════════════════════════════════════════════
    # HEALTH CHECK
    # ══════════════════════════════════════════════

    async def health_check(self) -> dict:
        try:
            row = await self._fetch_one(
                "SELECT COUNT(*) as cnt FROM sessions"
            )
            session = await self._fetch_one(
                "SELECT uniqueid, academic_term, academic_year "
                "FROM sessions WHERE uniqueid = %s",
                (self.session_id,)
            )
            return {
                "connected": True,
                "session_count": row["cnt"] if row else 0,
                "active_session": session,
                "aiet_1x80_pattern": self._aiet_1x80_id,
                "lecture_rooms": len(self._lecture_room_ids),
                "lab_rooms": len(self._lab_room_ids),
                "schema_ok": self._can_add_time_prefs,
                "error": None,
            }
        except Exception as e:
            return {
                "connected": False,
                "session_count": 0,
                "active_session": None,
                "error": str(e),
            }

    # ══════════════════════════════════════════════
    # TIMETABLE HELPERS
    # ══════════════════════════════════════════════

    async def get_timetable_by_room(self, room_id: int,
                                    session_id: int = None
                                    ) -> list[dict]:
        sid = session_id or self.session_id
        return await self._fetch_all("""
            SELECT
                c.uniqueid as class_id,
                co.title as course_title,
                co.course_nbr as course_number,
                sa.subject_area_abbreviation as subject,
                a.days as assigned_days,
                a.slot as assigned_slot,
                tp.name as time_pattern,
                tp.mins_pmt as minutes_per_meeting,
                GROUP_CONCAT(
                    DISTINCT CONCAT(di.fname, ' ', di.lname)
                    SEPARATOR ', '
                ) as instructors
            FROM assigned_rooms ar
            JOIN assignment a
                ON ar.assignment_id = a.uniqueid
            JOIN class_ c ON a.class_id = c.uniqueid
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            JOIN course_offering co
                ON co.instr_offr_id = io.uniqueid
                AND co.is_control = 1
            JOIN subject_area sa
                ON co.subject_area_id = sa.uniqueid
            LEFT JOIN time_pattern tp
                ON a.time_pattern_id = tp.uniqueid
            LEFT JOIN class_instructor ci
                ON ci.class_id = c.uniqueid
            LEFT JOIN departmental_instructor di
                ON ci.instructor_id = di.uniqueid
            WHERE ar.room_id = %s AND io.session_id = %s
            GROUP BY c.uniqueid, co.title, co.course_nbr,
                     sa.subject_area_abbreviation,
                     a.days, a.slot, tp.name, tp.mins_pmt
            ORDER BY a.days, a.slot
        """, (room_id, sid))

    async def get_timetable_by_instructor(
        self, instructor_id: int,
        session_id: int = None
    ) -> list[dict]:
        sid = session_id or self.session_id
        return await self._fetch_all("""
            SELECT
                c.uniqueid as class_id,
                co.title as course_title,
                co.course_nbr as course_number,
                sa.subject_area_abbreviation as subject,
                a.days as assigned_days,
                a.slot as assigned_slot,
                tp.name as time_pattern,
                tp.mins_pmt as minutes_per_meeting,
                r.room_number,
                b.abbreviation as building
            FROM class_instructor ci
            JOIN class_ c ON ci.class_id = c.uniqueid
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            JOIN course_offering co
                ON co.instr_offr_id = io.uniqueid
                AND co.is_control = 1
            JOIN subject_area sa
                ON co.subject_area_id = sa.uniqueid
            LEFT JOIN assignment a ON a.class_id = c.uniqueid
            LEFT JOIN time_pattern tp
                ON a.time_pattern_id = tp.uniqueid
            LEFT JOIN assigned_rooms ar
                ON ar.assignment_id = a.uniqueid
            LEFT JOIN room r ON ar.room_id = r.uniqueid
            LEFT JOIN building b ON r.building_id = b.uniqueid
            WHERE ci.instructor_id = %s
                AND io.session_id = %s
            ORDER BY a.days, a.slot
        """, (instructor_id, sid))

    async def check_room_conflicts(
        self, room_id: int, days: int, slot: int,
        duration_slots: int = SLOTS_PER_MEETING,
        exclude_class_id: int = None,
        session_id: int = None
    ) -> list[dict]:
        sid = session_id or self.session_id
        end_slot = slot + duration_slots

        query = """
            SELECT
                c.uniqueid as class_id,
                co.title as course_title,
                co.course_nbr as course_number,
                sa.subject_area_abbreviation as subject,
                a.days as assigned_days,
                a.slot as assigned_slot,
                tp.mins_pmt as minutes_per_meeting
            FROM assigned_rooms ar
            JOIN assignment a
                ON ar.assignment_id = a.uniqueid
            JOIN class_ c ON a.class_id = c.uniqueid
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            JOIN course_offering co
                ON co.instr_offr_id = io.uniqueid
                AND co.is_control = 1
            JOIN subject_area sa
                ON co.subject_area_id = sa.uniqueid
            LEFT JOIN time_pattern tp
                ON a.time_pattern_id = tp.uniqueid
            WHERE ar.room_id = %s
              AND io.session_id = %s
              AND (a.days & %s) > 0
              AND a.slot < %s
              AND (a.slot + COALESCE(tp.slots_pmt, %s)) > %s
        """
        args = [room_id, sid, days, end_slot,
                SLOTS_PER_MEETING, slot]

        if exclude_class_id:
            query += " AND c.uniqueid != %s"
            args.append(exclude_class_id)

        return await self._fetch_all(query, args)

    async def check_instructor_conflicts(
        self, instructor_id: int, days: int, slot: int,
        duration_slots: int = SLOTS_PER_MEETING,
        exclude_class_id: int = None,
        session_id: int = None
    ) -> list[dict]:
        sid = session_id or self.session_id
        end_slot = slot + duration_slots

        query = """
            SELECT
                c.uniqueid as class_id,
                co.title as course_title,
                co.course_nbr as course_number,
                sa.subject_area_abbreviation as subject,
                a.days as assigned_days,
                a.slot as assigned_slot,
                r.room_number,
                b.abbreviation as building
            FROM class_instructor ci
            JOIN class_ c ON ci.class_id = c.uniqueid
            JOIN scheduling_subpart sp
                ON c.subpart_id = sp.uniqueid
            JOIN instr_offering_config ioc
                ON sp.config_id = ioc.uniqueid
            JOIN instructional_offering io
                ON ioc.instr_offr_id = io.uniqueid
            JOIN course_offering co
                ON co.instr_offr_id = io.uniqueid
                AND co.is_control = 1
            JOIN subject_area sa
                ON co.subject_area_id = sa.uniqueid
            LEFT JOIN assignment a ON a.class_id = c.uniqueid
            LEFT JOIN time_pattern tp
                ON a.time_pattern_id = tp.uniqueid
            LEFT JOIN assigned_rooms ar
                ON ar.assignment_id = a.uniqueid
            LEFT JOIN room r ON ar.room_id = r.uniqueid
            LEFT JOIN building b ON r.building_id = b.uniqueid
            WHERE ci.instructor_id = %s
              AND io.session_id = %s
              AND (a.days & %s) > 0
              AND a.slot < %s
              AND (a.slot + COALESCE(tp.slots_pmt, %s)) > %s
        """
        args = [instructor_id, sid, days, end_slot,
                SLOTS_PER_MEETING, slot]

        if exclude_class_id:
            query += " AND c.uniqueid != %s"
            args.append(exclude_class_id)

        return await self._fetch_all(query, args)