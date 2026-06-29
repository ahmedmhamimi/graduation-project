"""
fix.py — Complete fix for AIET UniTime configuration.

Fixes applied:
  1. Creates custom time patterns (AIET 1x80, 2x80, 3x80)
  2. Sets time slots: 9:00-10:20, 11:00-12:20, 1:00-2:20
  3. Configures 6 working days (Sat-Thu, Friday OFF)
  4. Fixes break_time to show full 80-minute periods
  5. Rebuilds course structure:
       Lecture : 50 cap, 1×80 min/week (professor, max 3 per prof)
       Section : 25 cap, 1×80 min/week (instructor, round-robin)
       ALL 59 courses get both a lecture AND a section
  6. 20 professors (max 3 lectures each → 60 slots ≥ 59 needed ✓)
       · 4 original staff
       · 16 new staff (Arabic names, starting K or later)
  7. 12 instructors (max 6 sections each → 72 slots ≥ 59 needed ✓)
       · 9 original staff
       · 3 new staff (Arabic names, starting K or later)
  8. FIX: Each student enrolled in ONE semester only (not both semesters
     of their year) — student profile ordering matches seed.py exactly
  9. Ossama Badawy: Mobile Computing, Data Mining, Big Data Analytics

Usage:
    python cleanup.py
    python seed.py
    python fix.py
"""

import asyncio
import random
import aiomysql
from app.config import settings

# ══════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════
SESSION_ID      = 231379
DEPARTMENT_ID   = 231383
DATE_PATTERN_ID = 853

LECTURE_CAPACITY = 50
SECTION_CAPACITY = 25

ITYPE_LECTURE = 10
ITYPE_SECTION = 30

MAX_LECTURES_PER_PROF    = 3
MAX_SECTIONS_PER_INSTRUCTOR = 6

# Must match seed.py
NUM_STUDENTS       = 24
STUDENTS_PER_GROUP = 3   # per (year, major) pair

# ══════════════════════════════════════════════════════
# DAY BITMASKS
# ══════════════════════════════════════════════════════
DAY_BITS = {"Mon": 64, "Tue": 32, "Wed": 16, "Thu": 8, "Fri": 4, "Sat": 2, "Sun": 1}
WORKING_DAYS = ["Sat", "Sun", "Mon", "Tue", "Wed", "Thu"]
SINGLE_DAYS  = [2, 1, 64, 32, 16, 8]   # Sat Sun Mon Tue Wed Thu

DAY_PAIRS = [
    DAY_BITS["Sat"] + DAY_BITS["Mon"],  DAY_BITS["Sat"] + DAY_BITS["Tue"],
    DAY_BITS["Sat"] + DAY_BITS["Wed"],  DAY_BITS["Sat"] + DAY_BITS["Thu"],
    DAY_BITS["Sun"] + DAY_BITS["Tue"],  DAY_BITS["Sun"] + DAY_BITS["Wed"],
    DAY_BITS["Sun"] + DAY_BITS["Thu"],  DAY_BITS["Mon"] + DAY_BITS["Wed"],
    DAY_BITS["Mon"] + DAY_BITS["Thu"],  DAY_BITS["Tue"] + DAY_BITS["Thu"],
    DAY_BITS["Sun"] + DAY_BITS["Mon"],  DAY_BITS["Wed"] + DAY_BITS["Thu"],
]

DAY_TRIPLES = [
    DAY_BITS["Sat"] + DAY_BITS["Mon"] + DAY_BITS["Wed"],
    DAY_BITS["Sat"] + DAY_BITS["Tue"] + DAY_BITS["Thu"],
    DAY_BITS["Sun"] + DAY_BITS["Mon"] + DAY_BITS["Wed"],
    DAY_BITS["Sun"] + DAY_BITS["Tue"] + DAY_BITS["Thu"],
    DAY_BITS["Sun"] + DAY_BITS["Mon"] + DAY_BITS["Thu"],
    DAY_BITS["Sat"] + DAY_BITS["Mon"] + DAY_BITS["Thu"],
]

# Time slots: 9:00=108, 11:00=132, 13:00=156
VALID_SLOTS      = [108, 132, 156]
MINUTES_PER_PERIOD = 80
SLOTS_PER_MEETING  = 16   # 80 min / 5 min per slot

TIME_PATTERNS = [
    {"name": "AIET 1x80", "nr_mtgs": 1, "mins_pmt": 80, "slots_pmt": 16,
     "break_time": 0, "type": 0, "visible": 1,
     "day_combos": SINGLE_DAYS, "start_slots": VALID_SLOTS},
    {"name": "AIET 2x80", "nr_mtgs": 2, "mins_pmt": 80, "slots_pmt": 16,
     "break_time": 0, "type": 0, "visible": 1,
     "day_combos": DAY_PAIRS, "start_slots": VALID_SLOTS},
    {"name": "AIET 3x80", "nr_mtgs": 3, "mins_pmt": 80, "slots_pmt": 16,
     "break_time": 0, "type": 0, "visible": 1,
     "day_combos": DAY_TRIPLES, "start_slots": VALID_SLOTS},
]

# ══════════════════════════════════════════════════════
# STAFF  — must match seed.py exactly
# 20 professors: 4 original + 16 new (Arabic, K+ names)
# 12 instructors: 9 original + 3 new (Arabic, K+ names)
# ══════════════════════════════════════════════════════
PROFESSORS_ORDER = [
    # ── Original 4 ──────────────────────────────────── index 0-3
    ("Ahmed",   "Abo El-Farag"),
    ("Ahmed",   "Elshaer"),
    ("Ossama",  "Badawy"),
    ("Hany",    "Hanafy"),
    # ── New 16 — Arabic, both names start with K or later ── index 4-19
    ("Karim",   "Khalil"),
    ("Khalid",  "Mansour"),
    ("Khaled",  "Nasser"),
    ("Kareem",  "Lotfi"),
    ("Layla",   "Mohamed"),
    ("Mohamed", "Ragab"),
    ("Mahmoud", "Saleh"),
    ("Mona",    "Tawfik"),
    ("Nour",    "Youssef"),
    ("Nadia",   "Mostafa"),
    ("Omar",    "Zaki"),
    ("Rania",   "Wahba"),
    ("Samir",   "Sharaf"),
    ("Tarek",   "Zahran"),
    ("Wael",    "Zidan"),
    ("Youssef", "Kamal"),
]
# Capacity: 20 × 3 = 60 ≥ 59 lectures ✓

# ══════════════════════════════════════════════════════
# PROFESSOR → COURSE (fixed, max 3 each)
# Must match seed.py exactly
# ══════════════════════════════════════════════════════
PROFESSOR_COURSE_MAP = {
    "EBA 1110": 0, "EBA 1271": 0, "EBA 1272": 0,   # Ahmed Abo El-Farag
    "AIN 2101": 1, "AIN 2102": 1, "AIN 3103": 1,   # Ahmed Elshaer
    "AGN 4305": 2, "ADS 3105": 2, "ADS 4106": 2,   # Ossama Badawy
    "AGN 2106": 3, "AGN 2302": 3, "AGN 2203": 3,   # Hany Hanafy
}

# ══════════════════════════════════════════════════════
# COURSE → (SEMESTER, DEPT_TAG)
# Must match seed.py's COURSES list exactly (59 courses total)
# ══════════════════════════════════════════════════════
COURSE_SEMESTER_MAP = {
    # Semester 1
    "AGN 1101": (1, "CENTRAL"), "AGN 1102": (1, "CENTRAL"),
    "AGN 1201": (1, "CENTRAL"), "EBA 1110": (1, "CENTRAL"),
    "EBA 1271": (1, "CENTRAL"), "UNR 1403": (1, "CENTRAL"),
    "UNR 122Z": (1, "CENTRAL"),
    # Semester 2
    "AGN 1103": (2, "CENTRAL"), "AGN 1202": (2, "CENTRAL"),
    "EBA 1272": (2, "CENTRAL"), "AGN 1301": (2, "CENTRAL"),
    "UNR 1407": (2, "CENTRAL"), "UNR 2101": (2, "CENTRAL"),
    "UNR 222Z": (2, "CENTRAL"),
    # Semester 3
    "AIN 2101": (3, "CENTRAL"), "AGN 2104": (3, "CENTRAL"),
    "AGN 2105": (3, "CENTRAL"), "AGN 2203": (3, "CENTRAL"),
    "EBA 2203": (3, "CENTRAL"), "EBA 2204": (3, "CENTRAL"),
    "APT 2101": (3, "CENTRAL"),
    # Semester 4
    "AIN 2102": (4, "CENTRAL"), "AGN 2106": (4, "CENTRAL"),
    "AGN 2302": (4, "CENTRAL"), "AGN 2303": (4, "CENTRAL"),
    "AGN 2204": (4, "CENTRAL"), "ADS 2101": (4, "CENTRAL"),
    "APT 2102": (4, "CENTRAL"),
    # Semester 5 shared
    "AIN 3103": (5, "CENTRAL"), "AIN 3104": (5, "CENTRAL"),
    "APT 3103": (5, "CENTRAL"),
    # Semester 5 DS
    "ACY 3102": (5, "DS"), "ADS 3102": (5, "DS"), "ADS 3103": (5, "DS"),
    # Semester 5 IN
    "AIS 3101": (5, "IN"), "AIS 3102": (5, "IN"), "AIS 3103": (5, "IN"),
    # Semester 6 shared
    "AIN 3105": (6, "CENTRAL"), "AIN 3107": (6, "CENTRAL"),
    "ARB 322Z": (6, "CENTRAL"), "APT 3201": (6, "CENTRAL"),
    # Semester 6 DS
    "ADS 3104": (6, "DS"), "ADS 3105": (6, "DS"),
    # Semester 6 IN
    "AIS 3104": (6, "IN"), "AIS 3105": (6, "IN"),
    # Semester 7 shared
    "AIN 4106": (7, "CENTRAL"), "ARB 4221": (7, "CENTRAL"),
    "APT 4202": (7, "CENTRAL"), "AGN 4305": (7, "CENTRAL"),
    # Semester 7 DS
    "ADS 4501": (7, "DS"), "ADS 4106": (7, "DS"),
    # Semester 7 IN
    "AIS 4501": (7, "IN"), "AIS 4101": (7, "IN"),
    # Semester 8 shared
    "AIN 4107": (8, "CENTRAL"), "ARB 4222": (8, "CENTRAL"),
    # Semester 8 DS
    "ADS 4502": (8, "DS"), "ADS 4107": (8, "DS"),
    # Semester 8 IN
    "AIS 4502": (8, "IN"), "AIS 4102": (8, "IN"),
}
# Total: 59 entries


class AIETFixer:

    def __init__(self):
        self.conn = None
        self.cur  = None
        self._id_counter = None
        self._all_tables = set()

        # Schema columns
        self._tp_days_has_uniqueid = False
        self._tp_days_fk_col       = None
        self._tp_days_day_col      = None
        self._tp_time_has_uniqueid = False
        self._tp_time_fk_col       = None
        self._tp_time_slot_col     = None
        self._tpd_tp_col           = None
        self._tpd_dept_col         = None
        self._time_pref_columns    = []
        self._required_pref_id     = None
        self._class_columns        = []

        self.pattern_ids    = {}   # name → id
        self.professor_ids  = []   # ordered by PROFESSORS_ORDER
        self.instructor_ids = []
        self.lecture_room_ids = []
        self.section_room_ids = []
        self.prof_assignment  = {}  # course_key → professor_id

        self.stats = {
            "patterns_created":  0,
            "lectures_created":  0,
            "sections_created":  0,
            "enrollments_created": 0,
        }

    # ══════════════════════════════════════════
    # CONNECTION
    # ══════════════════════════════════════════

    async def connect(self):
        self.conn = await aiomysql.connect(
            host=settings.UNITIME_DB_HOST,
            port=settings.UNITIME_DB_PORT,
            user=settings.UNITIME_DB_USER,
            password=settings.UNITIME_DB_PASSWORD,
            db=settings.UNITIME_DB_NAME,
            autocommit=False,
            charset="utf8mb4",
        )
        self.cur = await self.conn.cursor(aiomysql.DictCursor)
        print("  Connected.")

    async def disconnect(self):
        if self.cur:  await self.cur.close()
        if self.conn: self.conn.close()
        print("  Disconnected.")

    # ══════════════════════════════════════════
    # ID GENERATION
    # ══════════════════════════════════════════

    async def _init_id_counter(self):
        try:
            await self.cur.execute(
                "SELECT next_hi FROM hibernate_unique_key FOR UPDATE"
            )
            row = await self.cur.fetchone()
            if row:
                hi = int(row["next_hi"])
                await self.cur.execute(
                    "UPDATE hibernate_unique_key SET next_hi = %s", (hi + 2000,)
                )
                self._id_counter = hi * 32
                print(f"  ID range starts at: {self._id_counter}")
                return
        except Exception as e:
            print(f"  ID init warning: {e}")
        self._id_counter = 9_000_000

    def next_id(self):
        uid = self._id_counter
        self._id_counter += 1
        return uid

    # ══════════════════════════════════════════
    # SCHEMA DISCOVERY
    # ══════════════════════════════════════════

    async def _discover_schema(self):
        print("\n── Schema Discovery ──")
        await self.cur.execute("SHOW TABLES")
        for row in await self.cur.fetchall():
            for v in row.values():
                self._all_tables.add(str(v).lower())

        await self.cur.execute("DESCRIBE class_")
        self._class_columns = [c["Field"] for c in await self.cur.fetchall()]

        if "time_pattern_days" in self._all_tables:
            await self.cur.execute("DESCRIBE time_pattern_days")
            for c in await self.cur.fetchall():
                col = c["Field"]
                if col.lower() == "uniqueid":
                    self._tp_days_has_uniqueid = True
                elif "pattern" in col.lower():
                    self._tp_days_fk_col = col
                elif col.lower() in ("day_code", "day", "days"):
                    self._tp_days_day_col = col

        if "time_pattern_time" in self._all_tables:
            await self.cur.execute("DESCRIBE time_pattern_time")
            for c in await self.cur.fetchall():
                col = c["Field"]
                if col.lower() == "uniqueid":
                    self._tp_time_has_uniqueid = True
                elif "pattern" in col.lower():
                    self._tp_time_fk_col = col
                elif col.lower() in ("start_slot", "slot", "time_slot"):
                    self._tp_time_slot_col = col

        if "time_pattern_dept" in self._all_tables:
            await self.cur.execute("DESCRIBE time_pattern_dept")
            for c in await self.cur.fetchall():
                col = c["Field"]
                if "pattern" in col.lower(): self._tpd_tp_col   = col
                if "dept"    in col.lower(): self._tpd_dept_col = col

        if "time_pref" in self._all_tables:
            await self.cur.execute("DESCRIBE time_pref")
            self._time_pref_columns = [c["Field"] for c in await self.cur.fetchall()]

        pref_table = (
            "preference_level" if "preference_level" in self._all_tables
            else "pref_level"   if "pref_level"       in self._all_tables
            else None
        )
        if pref_table:
            await self.cur.execute(f"SELECT * FROM {pref_table} ORDER BY uniqueid")
            for pl in await self.cur.fetchall():
                if str(pl.get("pref_prolog", "")).upper() == "R":
                    self._required_pref_id = int(pl["uniqueid"]); break

        print(f"  Required pref_level: {self._required_pref_id}")
        print(f"  time_pattern_days : fk={self._tp_days_fk_col}, day={self._tp_days_day_col}")
        print(f"  time_pattern_time : fk={self._tp_time_fk_col}, slot={self._tp_time_slot_col}")

    # ══════════════════════════════════════════
    # STEP 1: DELETE OLD AIET PATTERNS
    # ══════════════════════════════════════════

    async def delete_old_patterns(self):
        print("\n── Step 1: Delete old AIET patterns ──")
        await self.cur.execute(
            "SELECT uniqueid, name FROM time_pattern "
            "WHERE session_id = %s AND (name LIKE %s OR name LIKE %s)",
            (SESSION_ID, "AIET%", "%College%"),
        )
        patterns = await self.cur.fetchall()
        for p in patterns:
            tp_id = int(p["uniqueid"])
            if self._tp_days_fk_col:
                await self.cur.execute(
                    f"DELETE FROM time_pattern_days WHERE {self._tp_days_fk_col} = %s",
                    (tp_id,))
            if self._tp_time_fk_col:
                await self.cur.execute(
                    f"DELETE FROM time_pattern_time WHERE {self._tp_time_fk_col} = %s",
                    (tp_id,))
            if self._tpd_tp_col:
                await self.cur.execute(
                    f"DELETE FROM time_pattern_dept WHERE {self._tpd_tp_col} = %s",
                    (tp_id,))
            await self.cur.execute(
                "DELETE FROM time_pref WHERE time_pattern_id = %s", (tp_id,))
            await self.cur.execute(
                "DELETE FROM time_pattern WHERE uniqueid = %s", (tp_id,))
            print(f"  ✓ Deleted '{p['name']}'")
        if not patterns:
            print("  No old patterns found.")

    # ══════════════════════════════════════════
    # STEP 2: CREATE TIME PATTERNS
    # ══════════════════════════════════════════

    async def create_time_patterns(self):
        print("\n── Step 2: Create AIET time patterns ──")
        for pat in TIME_PATTERNS:
            name  = pat["name"]
            tp_id = self.next_id()
            await self.cur.execute(
                "INSERT INTO time_pattern "
                "(uniqueid,session_id,name,nr_mtgs,mins_pmt,slots_pmt,type,visible,break_time) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tp_id, SESSION_ID, name, pat["nr_mtgs"], pat["mins_pmt"],
                 pat["slots_pmt"], pat["type"], pat["visible"], pat["break_time"]),
            )
            self.pattern_ids[name] = tp_id

            for day_code in pat["day_combos"]:
                if self._tp_days_has_uniqueid:
                    await self.cur.execute(
                        f"INSERT INTO time_pattern_days "
                        f"(uniqueid,{self._tp_days_fk_col},{self._tp_days_day_col}) "
                        f"VALUES (%s,%s,%s)",
                        (self.next_id(), tp_id, day_code))
                else:
                    await self.cur.execute(
                        f"INSERT INTO time_pattern_days "
                        f"({self._tp_days_fk_col},{self._tp_days_day_col}) VALUES (%s,%s)",
                        (tp_id, day_code))

            for slot in pat["start_slots"]:
                if self._tp_time_has_uniqueid:
                    await self.cur.execute(
                        f"INSERT INTO time_pattern_time "
                        f"(uniqueid,{self._tp_time_fk_col},{self._tp_time_slot_col}) "
                        f"VALUES (%s,%s,%s)",
                        (self.next_id(), tp_id, slot))
                else:
                    await self.cur.execute(
                        f"INSERT INTO time_pattern_time "
                        f"({self._tp_time_fk_col},{self._tp_time_slot_col}) VALUES (%s,%s)",
                        (tp_id, slot))

            if self._tpd_tp_col and self._tpd_dept_col:
                await self.cur.execute(
                    f"INSERT INTO time_pattern_dept ({self._tpd_dept_col},{self._tpd_tp_col}) "
                    f"VALUES (%s,%s)",
                    (DEPARTMENT_ID, tp_id))

            self.stats["patterns_created"] += 1
            print(f"  ✓ '{name}': {pat['nr_mtgs']}×{pat['mins_pmt']}min, "
                  f"grid={len(pat['day_combos'])}×{len(pat['start_slots'])}")

    # ══════════════════════════════════════════
    # STEP 3: LOAD STAFF AND ROOMS
    # ══════════════════════════════════════════

    async def load_staff_and_rooms(self):
        print("\n── Step 3: Load staff and rooms ──")

        # Load professors in PROFESSORS_ORDER
        for fname, lname in PROFESSORS_ORDER:
            await self.cur.execute(
                "SELECT di.uniqueid FROM departmental_instructor di "
                "JOIN department d ON di.department_uniqueid = d.uniqueid "
                "WHERE d.session_id = %s AND di.fname = %s AND di.lname = %s",
                (SESSION_ID, fname, lname),
            )
            row = await self.cur.fetchone()
            if row:
                self.professor_ids.append(int(row["uniqueid"]))
            else:
                print(f"  ⚠ Professor not found: {fname} {lname}")
                self.professor_ids.append(None)

        # Load instructors (staff NOT in PROFESSORS_ORDER)
        prof_names = {(fn, ln) for fn, ln in PROFESSORS_ORDER}
        await self.cur.execute(
            "SELECT di.uniqueid, di.fname, di.lname "
            "FROM departmental_instructor di "
            "JOIN department d ON di.department_uniqueid = d.uniqueid "
            "WHERE d.session_id = %s ORDER BY di.lname",
            (SESSION_ID,),
        )
        for s in await self.cur.fetchall():
            if (s["fname"], s["lname"]) not in prof_names:
                self.instructor_ids.append(int(s["uniqueid"]))

        print(f"  Professors loaded  : {len([p for p in self.professor_ids if p])}")
        print(f"  Instructors loaded : {len(self.instructor_ids)}")

        self._build_prof_assignment()

        # Load rooms
        for abbr in ("03", "AIET"):
            await self.cur.execute(
                "SELECT r.uniqueid, r.capacity FROM room r "
                "JOIN building b ON r.building_id = b.uniqueid "
                "WHERE r.session_id = %s AND b.abbreviation = %s "
                "ORDER BY r.capacity DESC",
                (SESSION_ID, abbr),
            )
            room_rows = await self.cur.fetchall()
            if room_rows:
                break

        if not room_rows:
            await self.cur.execute(
                "SELECT uniqueid, capacity FROM room WHERE session_id = %s "
                "ORDER BY capacity DESC",
                (SESSION_ID,),
            )
            room_rows = await self.cur.fetchall()

        for r in room_rows:
            rid = int(r["uniqueid"]); cap = int(r["capacity"])
            if cap >= LECTURE_CAPACITY:
                self.lecture_room_ids.append(rid)
            elif cap >= SECTION_CAPACITY:
                self.section_room_ids.append(rid)
            else:
                await self.cur.execute(
                    "UPDATE room SET capacity = %s WHERE uniqueid = %s",
                    (SECTION_CAPACITY, rid))
                self.section_room_ids.append(rid)

        if not self.section_room_ids and self.lecture_room_ids:
            self.section_room_ids = self.lecture_room_ids[:]

        print(f"  Lecture rooms (≥{LECTURE_CAPACITY}): {len(self.lecture_room_ids)}")
        print(f"  Section rooms (≥{SECTION_CAPACITY}): {len(self.section_room_ids)}")

    def _build_prof_assignment(self):
        """Map course_key → professor_id, strictly max 3 lectures each."""
        n = len(PROFESSORS_ORDER)
        prof_count = [0] * n

        # Fixed assignments for original 4 professors
        for course_key, pidx in PROFESSOR_COURSE_MAP.items():
            if pidx < n and self.professor_ids[pidx] and prof_count[pidx] < MAX_LECTURES_PER_PROF:
                self.prof_assignment[course_key] = self.professor_ids[pidx]
                prof_count[pidx] += 1

        # Round-robin remaining among new professors (index 4+)
        new_indices = list(range(4, n))
        rr = 0
        for course_key in COURSE_SEMESTER_MAP:
            if course_key in self.prof_assignment:
                continue
            assigned = False
            for attempt in range(len(new_indices)):
                pidx = new_indices[(rr + attempt) % len(new_indices)]
                if prof_count[pidx] < MAX_LECTURES_PER_PROF:
                    if self.professor_ids[pidx]:
                        self.prof_assignment[course_key] = self.professor_ids[pidx]
                        prof_count[pidx] += 1
                        rr = (rr + attempt + 1) % len(new_indices)
                        assigned = True
                        break
            if not assigned:
                # Absolute fallback (should not trigger with 20 profs × 3 = 60 ≥ 59)
                for pidx in range(n):
                    if prof_count[pidx] < MAX_LECTURES_PER_PROF and self.professor_ids[pidx]:
                        self.prof_assignment[course_key] = self.professor_ids[pidx]
                        prof_count[pidx] += 1
                        break

        violations = [i for i, c in enumerate(prof_count) if c > MAX_LECTURES_PER_PROF]
        if violations:
            print(f"  ⚠ Professor cap violations at indices: {violations}")
        else:
            print(f"  ✓ All {n} professors within {MAX_LECTURES_PER_PROF}-lecture cap")
            print(f"    Counts (first 4): {prof_count[:4]}")

    # ══════════════════════════════════════════
    # STEP 4: DELETE EXISTING CLASSES
    # ══════════════════════════════════════════

    async def delete_existing_classes(self):
        print("\n── Step 4: Delete existing classes ──")
        await self.cur.execute(
            "SELECT c.uniqueid "
            "FROM class_ c "
            "JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid "
            "WHERE io.session_id = %s",
            (SESSION_ID,),
        )
        class_ids = [int(r["uniqueid"]) for r in await self.cur.fetchall()]

        for cid in class_ids:
            await self.cur.execute(
                "DELETE FROM student_class_enrl WHERE class_id = %s", (cid,))
            await self.cur.execute(
                "DELETE FROM assigned_rooms WHERE assignment_id IN "
                "(SELECT uniqueid FROM assignment WHERE class_id = %s)", (cid,))
            await self.cur.execute(
                "DELETE FROM assignment WHERE class_id = %s", (cid,))
            await self.cur.execute(
                "DELETE FROM class_instructor WHERE class_id = %s", (cid,))
            await self.cur.execute(
                "DELETE FROM class_ WHERE uniqueid = %s", (cid,))

        await self.cur.execute(
            "DELETE sp FROM scheduling_subpart sp "
            "JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid "
            "WHERE io.session_id = %s",
            (SESSION_ID,),
        )
        await self.cur.execute(
            "DELETE FROM time_pref WHERE owner_id NOT IN "
            "(SELECT uniqueid FROM scheduling_subpart)"
        )
        print(f"  Deleted {len(class_ids)} classes and all subparts")

    # ══════════════════════════════════════════
    # STEP 5: REBUILD CLASSES (lecture + section for ALL courses)
    # ══════════════════════════════════════════

    async def rebuild_classes(self):
        print("\n── Step 5: Rebuild classes ──")

        tp_1x80 = self.pattern_ids.get("AIET 1x80")
        if not tp_1x80:
            raise Exception("AIET 1x80 pattern not found!")

        # Build unique slot pools to avoid room+time conflicts
        random.seed(42)
        lec_pool = [
            (room_id, day, slot)
            for day in SINGLE_DAYS
            for slot in VALID_SLOTS
            for room_id in self.lecture_room_ids
        ]
        random.shuffle(lec_pool)

        random.seed(99)
        sec_pool = [
            (room_id, day, slot)
            for day in SINGLE_DAYS
            for slot in VALID_SLOTS
            for room_id in self.section_room_ids
        ]
        random.shuffle(sec_pool)

        lec_idx = 0
        sec_idx = 0

        # Load all courses
        await self.cur.execute(
            "SELECT co.uniqueid AS co_id, co.course_nbr, "
            "       sa.subject_area_abbreviation AS subject, "
            "       ioc.uniqueid AS config_id "
            "FROM course_offering co "
            "JOIN instructional_offering io ON co.instr_offr_id = io.uniqueid "
            "JOIN subject_area sa ON co.subject_area_id = sa.uniqueid "
            "JOIN instr_offering_config ioc ON ioc.instr_offr_id = io.uniqueid "
            "WHERE io.session_id = %s AND co.is_control = 1 "
            "ORDER BY sa.subject_area_abbreviation, co.course_nbr",
            (SESSION_ID,),
        )
        courses = await self.cur.fetchall()

        n_profs  = len(self.professor_ids)
        prof_rr  = 4   # start round-robin at first new professor
        inst_idx = 0

        for course in courses:
            course_key = f"{course['subject']} {course['course_nbr']}"
            config_id  = int(course["config_id"])

            await self.cur.execute(
                "UPDATE instr_offering_config SET config_limit = %s WHERE uniqueid = %s",
                (LECTURE_CAPACITY, config_id),
            )

            # ── Lecture subpart ──
            lec_sp_id = self.next_id()
            await self.cur.execute(
                "INSERT INTO scheduling_subpart "
                "(uniqueid,min_per_wk,config_id,itype,auto_time_spread,student_allow_overlap) "
                "VALUES (%s,%s,%s,%s,1,0)",
                (lec_sp_id, MINUTES_PER_PERIOD, config_id, ITYPE_LECTURE),
            )
            await self._add_time_pref(lec_sp_id, tp_1x80)

            lec_cls_id = await self._create_class(lec_sp_id, LECTURE_CAPACITY, "1", 1)
            self.stats["lectures_created"] += 1

            # Assign professor (fixed map first, then round-robin new professors)
            prof_id = self.prof_assignment.get(course_key)
            if not prof_id:
                for _ in range(n_profs):
                    candidate = self.professor_ids[prof_rr % n_profs]
                    prof_rr += 1
                    if candidate:
                        prof_id = candidate
                        break
            if prof_id:
                await self._assign_instructor(lec_cls_id, prof_id)

            # Unique lecture slot
            if lec_idx < len(lec_pool):
                lec_room_id, lec_days, lec_slot = lec_pool[lec_idx]; lec_idx += 1
            else:
                lec_room_id = self.lecture_room_ids[0] if self.lecture_room_ids else None
                lec_days, lec_slot = SINGLE_DAYS[0], VALID_SLOTS[0]
            await self._create_assignment(lec_cls_id, lec_days, lec_slot, tp_1x80, lec_room_id)

            # ── Section subpart ──
            sec_sp_id = self.next_id()
            await self.cur.execute(
                "INSERT INTO scheduling_subpart "
                "(uniqueid,min_per_wk,config_id,itype,auto_time_spread,student_allow_overlap) "
                "VALUES (%s,%s,%s,%s,1,0)",
                (sec_sp_id, MINUTES_PER_PERIOD, config_id, ITYPE_SECTION),
            )
            await self._add_time_pref(sec_sp_id, tp_1x80)

            sec_cls_id = await self._create_class(sec_sp_id, SECTION_CAPACITY, "1", 1)
            self.stats["sections_created"] += 1

            if self.instructor_ids:
                inst_id = self.instructor_ids[inst_idx % len(self.instructor_ids)]
                inst_idx += 1
                await self._assign_instructor(sec_cls_id, inst_id)

            # Unique section slot
            if sec_idx < len(sec_pool):
                sec_room_id, sec_days, sec_slot = sec_pool[sec_idx]; sec_idx += 1
            else:
                sec_room_id = self.section_room_ids[0] if self.section_room_ids else None
                sec_days, sec_slot = SINGLE_DAYS[1], VALID_SLOTS[1]
            await self._create_assignment(sec_cls_id, sec_days, sec_slot, tp_1x80, sec_room_id)

            print(f"  ✓ {course_key}")

        print(f"\n  Lectures: {self.stats['lectures_created']}, "
              f"Sections: {self.stats['sections_created']}")
        print(f"  Lec pool used: {lec_idx}/{len(lec_pool)}, "
              f"Sec pool used: {sec_idx}/{len(sec_pool)}")

    async def _create_class(self, sp_id, capacity, suffix, section_num):
        cls_id = self.next_id()
        wanted = {
            "subpart_id": sp_id, "expected_capacity": capacity,
            "max_expected_capacity": capacity, "room_capacity": capacity,
            "room_ratio": 1.0, "nbr_rooms": 1,
            "date_pattern_id": DATE_PATTERN_ID, "managing_dept": DEPARTMENT_ID,
            "display_instructor": 1, "display_in_sched_book": 1,
            "class_suffix": suffix, "section_number": section_num, "cancelled": 0,
        }
        cols = ["uniqueid"] + [c for c in wanted if c in self._class_columns]
        vals = [cls_id]    + [wanted[c] for c in cols[1:]]
        ph   = ",".join(["%s"] * len(vals))
        await self.cur.execute(
            f"INSERT INTO class_ ({','.join(cols)}) VALUES ({ph})", vals)
        return cls_id

    async def _assign_instructor(self, class_id, instructor_id):
        await self.cur.execute(
            "INSERT INTO class_instructor "
            "(uniqueid,class_id,instructor_id,percent_share,is_lead) "
            "VALUES (%s,%s,%s,100,1)",
            (self.next_id(), class_id, instructor_id),
        )

    async def _create_assignment(self, class_id, days, slot, tp_id, room_id):
        aid = self.next_id()
        await self.cur.execute(
            "INSERT INTO assignment "
            "(uniqueid,days,slot,time_pattern_id,class_id,date_pattern_id) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (aid, days, slot, tp_id, class_id, DATE_PATTERN_ID),
        )
        if room_id:
            await self.cur.execute(
                "INSERT INTO assigned_rooms (assignment_id,room_id) VALUES (%s,%s)",
                (aid, room_id),
            )

    async def _add_time_pref(self, subpart_id, tp_id):
        if not self._time_pref_columns:
            return
        await self.cur.execute(
            f"SELECT COUNT(*) AS n FROM time_pattern_days "
            f"WHERE {self._tp_days_fk_col} = %s", (tp_id,))
        n_days = int((await self.cur.fetchone())["n"])
        await self.cur.execute(
            f"SELECT COUNT(*) AS n FROM time_pattern_time "
            f"WHERE {self._tp_time_fk_col} = %s", (tp_id,))
        n_slots  = int((await self.cur.fetchone())["n"])
        grid     = n_days * n_slots
        pref_str = "2" * grid if grid > 0 else None
        pref_id  = self.next_id()
        if "pref_level_id" in self._time_pref_columns and self._required_pref_id:
            await self.cur.execute(
                "INSERT INTO time_pref "
                "(uniqueid,owner_id,pref_level_id,time_pattern_id,preference) "
                "VALUES (%s,%s,%s,%s,%s)",
                (pref_id, subpart_id, self._required_pref_id, tp_id, pref_str),
            )
        else:
            await self.cur.execute(
                "INSERT INTO time_pref "
                "(uniqueid,owner_id,time_pattern_id,preference) "
                "VALUES (%s,%s,%s,%s)",
                (pref_id, subpart_id, tp_id, pref_str),
            )

    # ══════════════════════════════════════════
    # STEP 6: REBUILD ENROLLMENTS
    # FIX: ONE semester per student, matching seed.py ordering exactly
    # ══════════════════════════════════════════

    async def rebuild_enrollments(self):
        print("\n── Step 6: Rebuild enrollments (ONE semester per student) ──")

        # Load student IDs in insertion order (matches seed.py creation order)
        await self.cur.execute(
            "SELECT uniqueid FROM student WHERE session_id = %s ORDER BY uniqueid",
            (SESSION_ID,),
        )
        student_ids = [int(r["uniqueid"]) for r in await self.cur.fetchall()]

        if not student_ids:
            print("  No students found.")
            return

        # ── Rebuild profiles in EXACTLY the same order as seed.py ──
        # seed.py iterates: for year in 1..4, for major in [DS,IN], for _ in range(3)
        # The i%2 parity determines odd/even semester for that student.
        majors_order = ["DS", "IN"]
        student_profiles = []   # (sid, major, year, active_sem)
        idx = 0
        for year in range(1, 5):
            for major in majors_order:
                for _ in range(STUDENTS_PER_GROUP):
                    if idx >= len(student_ids):
                        break
                    sid = student_ids[idx]
                    # Mirror seed.py: even index → odd semester, odd index → even semester
                    active_sem = (2 * year - 1) if (idx % 2 == 0) else (2 * year)
                    student_profiles.append((sid, major, year, active_sem))
                    idx += 1

        # ── Load class map: course_key → {co_id, lecture_cls_id, section_cls_id} ──
        await self.cur.execute(
            "SELECT co.uniqueid AS co_id, "
            "       sa.subject_area_abbreviation AS subject, "
            "       co.course_nbr, sp.itype, c.uniqueid AS class_id "
            "FROM class_ c "
            "JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid "
            "JOIN course_offering co ON co.instr_offr_id = io.uniqueid AND co.is_control = 1 "
            "JOIN subject_area sa ON co.subject_area_id = sa.uniqueid "
            "WHERE io.session_id = %s "
            "ORDER BY subject, co.course_nbr, sp.itype",
            (SESSION_ID,),
        )
        course_class_map = {}
        for row in await self.cur.fetchall():
            ck = f"{row['subject']} {row['course_nbr']}"
            if ck not in course_class_map:
                course_class_map[ck] = {"co_id": int(row["co_id"]),
                                         "lecture_cls_id": None,
                                         "section_cls_id": None}
            if int(row["itype"]) == ITYPE_LECTURE:
                course_class_map[ck]["lecture_cls_id"] = int(row["class_id"])
            else:
                course_class_map[ck]["section_cls_id"] = int(row["class_id"])

        # ── Enroll each student in ONE semester ──
        count = 0
        sem_student_count = {}

        for sid, major, year, active_sem in student_profiles:
            sem_student_count[active_sem] = sem_student_count.get(active_sem, 0) + 1
            courses_this_student = 0

            for course_key, cls_info in course_class_map.items():
                sem_info = COURSE_SEMESTER_MAP.get(course_key)
                if not sem_info:
                    continue
                sem, tag = sem_info

                # ── THE KEY FIX: check active_sem only (not both semesters) ──
                if sem != active_sem:
                    continue
                if tag != "CENTRAL" and tag != major:
                    continue

                co_id = cls_info["co_id"]
                courses_this_student += 1

                if cls_info["lecture_cls_id"]:
                    await self.cur.execute(
                        "INSERT INTO student_class_enrl "
                        "(uniqueid,student_id,class_id,course_offering_id,timestamp) "
                        "VALUES (%s,%s,%s,%s,NOW())",
                        (self.next_id(), sid, cls_info["lecture_cls_id"], co_id),
                    )
                    count += 1

                if cls_info["section_cls_id"]:
                    await self.cur.execute(
                        "INSERT INTO student_class_enrl "
                        "(uniqueid,student_id,class_id,course_offering_id,timestamp) "
                        "VALUES (%s,%s,%s,%s,NOW())",
                        (self.next_id(), sid, cls_info["section_cls_id"], co_id),
                    )
                    count += 1

        self.stats["enrollments_created"] = count
        print(f"  ✅ {count} enrollments created")
        print(f"  Students per active semester: {dict(sorted(sem_student_count.items()))}")

    # ══════════════════════════════════════════
    # VERIFICATION
    # ══════════════════════════════════════════

    async def verify(self):
        print("\n" + "=" * 70)
        print("VERIFICATION")
        print("=" * 70)

        # Time patterns
        print("\n  Time Patterns:")
        for name, tp_id in self.pattern_ids.items():
            await self.cur.execute(
                "SELECT nr_mtgs, mins_pmt, break_time FROM time_pattern WHERE uniqueid = %s",
                (tp_id,))
            tp = await self.cur.fetchone()
            print(f"    {name}: {tp['nr_mtgs']}×{tp['mins_pmt']}min, break={tp['break_time']}")

        # Class counts
        await self.cur.execute(
            "SELECT CASE sp.itype WHEN 10 THEN 'Lecture' WHEN 30 THEN 'Section' END AS type, "
            "COUNT(*) AS count "
            "FROM class_ c "
            "JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid "
            "WHERE io.session_id = %s GROUP BY sp.itype",
            (SESSION_ID,),
        )
        print("\n  Classes:")
        for r in await self.cur.fetchall():
            print(f"    {r['type']}: {r['count']}")

        # Enrollments
        await self.cur.execute(
            "SELECT COUNT(*) AS cnt FROM student_class_enrl sce "
            "JOIN class_ c ON sce.class_id = c.uniqueid "
            "JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid "
            "WHERE io.session_id = %s",
            (SESSION_ID,),
        )
        print(f"\n  Enrollments: {(await self.cur.fetchone())['cnt']}")

        # Max courses per student (should be ~7 for one semester, never 14+)
        await self.cur.execute(
            "SELECT sce.student_id, COUNT(DISTINCT co.uniqueid) AS n_courses "
            "FROM student_class_enrl sce "
            "JOIN class_ c ON sce.class_id = c.uniqueid "
            "JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid "
            "JOIN course_offering co ON co.instr_offr_id = io.uniqueid AND co.is_control = 1 "
            "WHERE io.session_id = %s "
            "GROUP BY sce.student_id "
            "ORDER BY n_courses DESC LIMIT 5",
            (SESSION_ID,),
        )
        rows = await self.cur.fetchall()
        if rows:
            print(f"\n  Top-5 student course loads: {[r['n_courses'] for r in rows]}")
            if rows[0]["n_courses"] > 9:
                print("  ⚠ Students have too many courses — check enrollment logic!")
            else:
                print("  ✓ Student course loads look correct")

        # Professor cap check
        await self.cur.execute(
            "SELECT CONCAT(di.fname,' ',di.lname) AS prof, COUNT(*) AS lectures "
            "FROM class_instructor ci "
            "JOIN departmental_instructor di ON ci.instructor_id = di.uniqueid "
            "JOIN class_ c ON ci.class_id = c.uniqueid "
            "JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid "
            "WHERE io.session_id = %s AND sp.itype = 10 "
            "GROUP BY di.uniqueid HAVING lectures > 3 ORDER BY lectures DESC",
            (SESSION_ID,),
        )
        over = await self.cur.fetchall()
        print(f"\n  Professors exceeding 3-lecture cap: "
              f"{'none ✓' if not over else [r['prof']+' ('+str(r['lectures'])+')' for r in over]}")

        # Ossama Badawy
        await self.cur.execute(
            "SELECT DISTINCT CONCAT(sa.subject_area_abbreviation,' ',co.course_nbr) AS course "
            "FROM class_instructor ci "
            "JOIN departmental_instructor di ON ci.instructor_id = di.uniqueid "
            "JOIN class_ c ON ci.class_id = c.uniqueid "
            "JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid "
            "JOIN course_offering co ON co.instr_offr_id = io.uniqueid AND co.is_control = 1 "
            "JOIN subject_area sa ON co.subject_area_id = sa.uniqueid "
            "WHERE di.fname='Ossama' AND di.lname='Badawy' "
            "  AND io.session_id = %s AND sp.itype = 10",
            (SESSION_ID,),
        )
        ossama = [r["course"] for r in await self.cur.fetchall()]
        print(f"\n  Ossama Badawy lectures: {ossama}")

        # Friday check
        await self.cur.execute(
            "SELECT COUNT(*) AS cnt FROM assignment a "
            "JOIN class_ c ON a.class_id = c.uniqueid "
            "JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid "
            "WHERE io.session_id = %s AND (a.days & 4) > 0",
            (SESSION_ID,),
        )
        fri = int((await self.cur.fetchone())["cnt"])
        print(f"\n  Friday classes: {fri} {'✅' if fri == 0 else '⚠'}")

        # Sample timetable
        await self.cur.execute(
            "SELECT sa.subject_area_abbreviation AS subject, co.course_nbr, "
            "  CASE sp.itype WHEN 10 THEN 'LEC' WHEN 30 THEN 'SEC' END AS type, "
            "  c.expected_capacity AS cap, "
            "  CONCAT(di.fname,' ',di.lname) AS staff, "
            "  a.days, a.slot, r.room_number AS room "
            "FROM class_ c "
            "JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid "
            "JOIN course_offering co ON co.instr_offr_id = io.uniqueid AND co.is_control = 1 "
            "JOIN subject_area sa ON co.subject_area_id = sa.uniqueid "
            "LEFT JOIN class_instructor ci ON ci.class_id = c.uniqueid "
            "LEFT JOIN departmental_instructor di ON ci.instructor_id = di.uniqueid "
            "LEFT JOIN assignment a ON a.class_id = c.uniqueid "
            "LEFT JOIN assigned_rooms ar ON ar.assignment_id = a.uniqueid "
            "LEFT JOIN room r ON ar.room_id = r.uniqueid "
            "WHERE io.session_id = %s "
            "ORDER BY subject, co.course_nbr, sp.itype LIMIT 20",
            (SESSION_ID,),
        )
        day_names = {64: "Mon", 32: "Tue", 16: "Wed", 8: "Thu", 2: "Sat", 1: "Sun"}
        print(f"\n  {'Course':<12} {'T':<4} {'Cap':<4} {'Day':<4} {'Time':<13} {'Room':<8} Staff")
        print(f"  {'─'*12} {'─'*4} {'─'*4} {'─'*4} {'─'*13} {'─'*8} {'─'*20}")
        for r in await self.cur.fetchall():
            days = int(r["days"]) if r["days"] else 0
            slot = int(r["slot"]) if r["slot"] else 0
            ds   = day_names.get(days, "?")
            h    = (slot * 5) // 60; m  = (slot * 5) % 60
            eh   = ((slot + SLOTS_PER_MEETING) * 5) // 60
            em   = ((slot + SLOTS_PER_MEETING) * 5) % 60
            ts   = f"{h}:{m:02d}-{eh}:{em:02d}"
            print(f"  {r['subject']+' '+r['course_nbr']:<12} "
                  f"{r['type']:<4} {r['cap']:<4} {ds:<4} {ts:<13} "
                  f"{(r['room'] or 'TBA'):<8} {r['staff'] or ''}")

    # ══════════════════════════════════════════
    # RUN
    # ══════════════════════════════════════════

    async def run(self):
        await self.connect()
        try:
            print("\n" + "=" * 70)
            print("AIET COMPLETE FIX")
            print("=" * 70)
            print("  Time  : 9:00-10:20, 11:00-12:20, 13:00-14:20 (80 min)")
            print("  Days  : Sat/Sun/Mon/Tue/Wed/Thu  (Friday OFF)")
            print("  Lec   : 50 cap · professor · max 3 per prof · 20 profs = 60 slots")
            print("  Sec   : 25 cap · instructor · round-robin  · 12 instr = 72 slots")
            print("  59 courses, each gets lecture + section")
            print("  Enrol : each student in ONE semester only (seed.py ordering)")
            print("  Ossama: Mobile Computing, Data Mining, Big Data Analytics")
            print("=" * 70)

            await self._init_id_counter()
            await self._discover_schema()
            await self.delete_old_patterns()
            await self.create_time_patterns()
            await self.load_staff_and_rooms()
            await self.delete_existing_classes()
            await self.rebuild_classes()
            await self.rebuild_enrollments()
            await self.verify()

            await self.conn.commit()

            print("\n" + "=" * 70)
            print("✅ ALL FIXES APPLIED")
            print("=" * 70)
            print(f"  Patterns  : {self.stats['patterns_created']}")
            print(f"  Lectures  : {self.stats['lectures_created']}")
            print(f"  Sections  : {self.stats['sections_created']}")
            print(f"  Enrolments: {self.stats['enrollments_created']}")
            print("\n  ⚠ Restart Tomcat to see changes in UniTime UI!")

        except Exception as e:
            await self.conn.rollback()
            print(f"\n❌ ERROR: {e}")
            import traceback; traceback.print_exc()
            raise
        finally:
            await self.disconnect()


if __name__ == "__main__":
    asyncio.run(AIETFixer().run())