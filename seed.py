"""
seed.py — Complete seed for AIET AI Department, Fall 2010 session.

Usage:
    python cleanup.py
    python seed.py

Creates:
- 9 subject areas
- 59 courses (lecture + lab for EVERY course)
- 20 professors → teach lectures (max 3 courses each → 60 slots ≥ 59 needed ✓)
    · 4 original staff
    · 16 new staff (Arabic names, starting K or later alphabetically)
- 12 instructors → teach labs (max 6 each → 72 slots ≥ 59 needed ✓)
    · 9 original staff
    · 3 new staff (Arabic names, starting K or later alphabetically)
- 24 students (3 per major per year, balanced DS/IN across years 1-4)
  Each student is enrolled in ONE semester only (odd or even for their year)
- Semester-package enrollments
- Time preferences for solver
- Room-department links
- Building: 03, Rooms: 03-222 etc.
- Ossama Badawy teaches: Mobile Computing, Data Mining, Big Data Analytics
"""

import asyncio
import random
from datetime import datetime
import aiomysql
from app.config import settings

# ══════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════
SESSION_ID    = 231379
DEPARTMENT_ID = 231383
DATE_PATTERN_ID = 853
BUILDING_ABBR = "03"

ITYPE_LECTURE = 10
ITYPE_LAB     = 30

LECTURE_CAP  = 50
LAB_CAP      = 25
CONFIG_LIMIT = 50

NUM_STUDENTS        = 24   # 3 DS + 3 IN per year × 4 years
STUDENTS_PER_GROUP  = 3    # per (year, major) pair
MAX_LECTURES_PER_PROF  = 3
MAX_LABS_PER_INSTRUCTOR = 6

# ══════════════════════════════════════════════════════
# ROOMS
# ══════════════════════════════════════════════════════
ROOMS = [
    ("03-010", 25, 20, True,  "Robotics Lab"),
    ("03-222", 30, 25, True,  "Lab"),
    ("03-307", 25, 20, True,  "Digital Logic Lab"),
    ("03-322", 30, 25, True,  "Lab"),
    ("03-323", 60, 50, False, "Lecture Room"),
    ("03-324", 60, 50, False, "Lecture Room"),
    ("03-325", 60, 50, False, "Lecture Room"),
    ("03-327", 60, 50, False, "Lecture Room"),
    ("03-425", 30, 25, True,  "Lab"),
    ("03-427", 25, 20, True,  "Special Lab"),
]

# ══════════════════════════════════════════════════════
# SUBJECT AREAS
# ══════════════════════════════════════════════════════
SUBJECT_AREAS = [
    ("AGN", "General Computing & Engineering"),
    ("AIN", "Artificial Intelligence"),
    ("ADS", "Data Science"),
    ("AIS", "Intelligent Systems"),
    ("ACY", "Cybersecurity"),
    ("EBA", "Basic Sciences & Mathematics"),
    ("UNR", "University Requirements"),
    ("APT", "Professional Training"),
    ("ARB", "Major Electives"),
]

# ══════════════════════════════════════════════════════
# STAFF
# 20 professors total:  4 original + 16 new (K+ Arabic names)
# 12 instructors total: 9 original + 3  new (K+ Arabic names)
# ══════════════════════════════════════════════════════
PROFESSORS = [
    # ── Original 4 ──────────────────────────────────────── index 0-3
    ("Ahmed",   "Abo El-Farag", "ahmed.aboelfarag@aiet.edu"),
    ("Ahmed",   "Elshaer",      "ahmed.elshaer@aiet.edu"),
    ("Ossama",  "Badawy",       "ossama.badawy@aiet.edu"),
    ("Hany",    "Hanafy",       "hany.hanafy@aiet.edu"),
    # ── New 16 — Arabic names, both names start with K or later ── index 4-19
    ("Karim",   "Khalil",       "karim.khalil@aiet.edu"),
    ("Khalid",  "Mansour",      "khalid.mansour@aiet.edu"),
    ("Khaled",  "Nasser",       "khaled.nasser@aiet.edu"),
    ("Kareem",  "Lotfi",        "kareem.lotfi@aiet.edu"),
    ("Layla",   "Mohamed",      "layla.mohamed@aiet.edu"),
    ("Mohamed", "Ragab",        "mohamed.ragab@aiet.edu"),
    ("Mahmoud", "Saleh",        "mahmoud.saleh@aiet.edu"),
    ("Mona",    "Tawfik",       "mona.tawfik@aiet.edu"),
    ("Nour",    "Youssef",      "nour.youssef@aiet.edu"),
    ("Nadia",   "Mostafa",      "nadia.mostafa@aiet.edu"),
    ("Omar",    "Zaki",         "omar.zaki@aiet.edu"),
    ("Rania",   "Wahba",        "rania.wahba@aiet.edu"),
    ("Samir",   "Sharaf",       "samir.sharaf@aiet.edu"),
    ("Tarek",   "Zahran",       "tarek.zahran@aiet.edu"),
    ("Wael",    "Zidan",        "wael.zidan@aiet.edu"),
    ("Youssef", "Kamal",        "youssef.kamal@aiet.edu"),
]
# Capacity check: 20 × 3 = 60 ≥ 59 courses  ✓

INSTRUCTORS_LIST = [
    # ── Original 9 ──────────────────────────────────────── index 0-8
    ("Aya",     "Abdelhamid", "aya.abdelhamid@aiet.edu"),
    ("Mazen",   "Aziz",       "mazen.aziz@aiet.edu"),
    ("Mohamed", "Elsayed",    "mohamed.elsayed@aiet.edu"),
    ("Nagy",    "Khairat",    "nagy.khairat@aiet.edu"),
    ("Haya",    "Medhat",     "haya.medhat@aiet.edu"),
    ("Salma",   "Mohamed",    "salma.mohamed@aiet.edu"),
    ("Ahmed",   "Metwalli",   "ahmed.metwalli@aiet.edu"),
    ("Belal",   "Sameh",      "belal.sameh@aiet.edu"),
    ("Mahmoud", "Khaled",     "mahmoud.khaled@aiet.edu"),
    # ── New 3 — Arabic names, both names start with K or later ── index 9-11
    ("Kareem",  "Magdy",      "kareem.magdy@aiet.edu"),
    ("Nadia",   "Shahin",     "nadia.shahin@aiet.edu"),
    ("Ramy",    "Lotfy",      "ramy.lotfy@aiet.edu"),
]
# Capacity check: 12 × 6 = 72 ≥ 59 labs  ✓

# ══════════════════════════════════════════════════════
# PROFESSOR → COURSE (fixed, max 3 each)
# ══════════════════════════════════════════════════════
PROFESSOR_COURSE_MAP = {
    # Ahmed Abo El-Farag (0): maths / sciences
    "EBA 1110": 0, "EBA 1271": 0, "EBA 1272": 0,
    # Ahmed Elshaer (1): AI / ML fundamentals
    "AIN 2101": 1, "AIN 2102": 1, "AIN 3103": 1,
    # Ossama Badawy (2): Mobile Computing, Data Mining, Big Data Analytics
    "AGN 4305": 2, "ADS 3105": 2, "ADS 4106": 2,
    # Hany Hanafy (3): Systems
    "AGN 2106": 3, "AGN 2302": 3, "AGN 2203": 3,
}
# Remaining 47 courses are assigned round-robin to professors 4-19

# ══════════════════════════════════════════════════════
# COURSES (subject, number, title, credits, semester, dept_tag)
# dept_tag: CENTRAL=all, DS=data-science only, IN=intelligent-systems only
# 59 courses total
# ══════════════════════════════════════════════════════
COURSES = [
    # ── Semester 1 (Year 1) ──
    ("AGN", "1101", "Introduction to Computing",           2, 1, "CENTRAL"),
    ("AGN", "1102", "Problem Solving & Programming",       3, 1, "CENTRAL"),
    ("AGN", "1201", "Fundamentals of Electronics",         3, 1, "CENTRAL"),
    ("EBA", "1110", "Physics",                             3, 1, "CENTRAL"),
    ("EBA", "1271", "Math Essentials",                     3, 1, "CENTRAL"),
    ("UNR", "1403", "Academic English",                    2, 1, "CENTRAL"),
    ("UNR", "122Z", "UNR Elective I",                      2, 1, "CENTRAL"),
    # ── Semester 2 (Year 1) ──
    ("AGN", "1103", "Data Structures & Algorithms",        3, 2, "CENTRAL"),
    ("AGN", "1202", "Digital Logic Design",                3, 2, "CENTRAL"),
    ("EBA", "1272", "Calculus",                            3, 2, "CENTRAL"),
    ("AGN", "1301", "Discrete Mathematics",                3, 2, "CENTRAL"),
    ("UNR", "1407", "Academic Writing",                    2, 2, "CENTRAL"),
    ("UNR", "2101", "Communication and Presentation Skills", 2, 2, "CENTRAL"),
    ("UNR", "222Z", "UNR Elective II",                     2, 2, "CENTRAL"),
    # ── Semester 3 (Year 2) ──
    ("AIN", "2101", "Fundamentals of Artificial Intelligence", 3, 3, "CENTRAL"),
    ("AGN", "2104", "Database Systems Design",             3, 3, "CENTRAL"),
    ("AGN", "2105", "Object-Oriented Programming",         3, 3, "CENTRAL"),
    ("AGN", "2203", "Computer Organization",               3, 3, "CENTRAL"),
    ("EBA", "2203", "Probability and Statistics",          3, 3, "CENTRAL"),
    ("EBA", "2204", "Linear Algebra",                      3, 3, "CENTRAL"),
    ("APT", "2101", "Professional Training I",             3, 3, "CENTRAL"),
    # ── Semester 4 (Year 2) ──
    ("AIN", "2102", "Machine Learning",                    3, 4, "CENTRAL"),
    ("AGN", "2106", "Computer Networks",                   3, 4, "CENTRAL"),
    ("AGN", "2302", "Operating Systems",                   3, 4, "CENTRAL"),
    ("AGN", "2303", "Programming for AI",                  3, 4, "CENTRAL"),
    ("AGN", "2204", "Embedded Systems & IoT",              3, 4, "CENTRAL"),
    ("ADS", "2101", "Fundamentals of Data Science",        3, 4, "CENTRAL"),
    ("APT", "2102", "Professional Training II",            3, 4, "CENTRAL"),
    # ── Semester 5 — Shared ──
    ("AIN", "3103", "Deep Learning",                       3, 5, "CENTRAL"),
    ("AIN", "3104", "Image Processing & Pattern Recognition", 3, 5, "CENTRAL"),
    ("APT", "3103", "Professional Training III",           3, 5, "CENTRAL"),
    # ── Semester 5 — DS ──
    ("ACY", "3102", "Data Security",                       3, 5, "DS"),
    ("ADS", "3102", "Advanced Database",                   3, 5, "DS"),
    ("ADS", "3103", "Statistics for Data Science",         3, 5, "DS"),
    # ── Semester 5 — IN ──
    ("AIS", "3101", "Intelligent Robotics",                3, 5, "IN"),
    ("AIS", "3102", "Intelligent Systems Security",        3, 5, "IN"),
    ("AIS", "3103", "Artificial Intelligence of Things",   3, 5, "IN"),
    # ── Semester 6 — Shared ──
    ("AIN", "3105", "Computer Vision",                     3, 6, "CENTRAL"),
    ("AIN", "3107", "Software Engineering",                3, 6, "CENTRAL"),
    ("ARB", "322Z", "Major Elective I",                    3, 6, "CENTRAL"),
    ("APT", "3201", "Practical Training I",                3, 6, "CENTRAL"),
    # ── Semester 6 — DS ──
    ("ADS", "3104", "Time Series Data Analysis",           3, 6, "DS"),
    ("ADS", "3105", "Data Mining",                         3, 6, "DS"),
    # ── Semester 6 — IN ──
    ("AIS", "3104", "Reinforcement Learning",              3, 6, "IN"),
    ("AIS", "3105", "Localization & Path Planning",        3, 6, "IN"),
    # ── Semester 7 — Shared ──
    ("AIN", "4106", "Natural Language Processing",         3, 7, "CENTRAL"),
    ("ARB", "4221", "Major Elective II",                   3, 7, "CENTRAL"),
    ("APT", "4202", "Practical Training II",               3, 7, "CENTRAL"),
    ("AGN", "4305", "Mobile Computing",                    3, 7, "CENTRAL"),
    # ── Semester 7 — DS ──
    ("ADS", "4501", "Project I",                           3, 7, "DS"),
    ("ADS", "4106", "Big Data Analytics",                  3, 7, "DS"),
    # ── Semester 7 — IN ──
    ("AIS", "4501", "Project I",                           3, 7, "IN"),
    ("AIS", "4101", "Swarm Intelligence",                  3, 7, "IN"),
    # ── Semester 8 — Shared ──
    ("AIN", "4107", "Deep Generative Models",              3, 8, "CENTRAL"),
    ("ARB", "4222", "High-Performance Computing",          3, 8, "CENTRAL"),
    # ── Semester 8 — DS ──
    ("ADS", "4502", "Project II",                          3, 8, "DS"),
    ("ADS", "4107", "Information Retrieval & Search Engine", 3, 8, "DS"),
    # ── Semester 8 — IN ──
    ("AIS", "4502", "Project II",                          3, 8, "IN"),
    ("AIS", "4102", "Autonomous Robots",                   3, 8, "IN"),
]
# Total: 59 courses

MAJOR_ELECTIVES = [
    "VR", "Blockchain", "Mobile Computing", "Optimization",
    "AI in Web", "Data Compression", "Selected Topics in AI",
]

# ══════════════════════════════════════════════════════
# STUDENT NAMES
# ══════════════════════════════════════════════════════
STUDENT_FIRST = [
    "Ahmed", "Mohamed", "Mazen", "Omar", "Khaled", "Youssef",
    "Fatma", "Nour", "Sara", "Aya", "Hana", "Mariam",
    "Ziad", "Samer", "Fares", "Ramy", "Wael", "Adel",
    "Dina", "Rania", "Heba", "Salma", "Yasmine", "Laila",
]

STUDENT_LAST = [
    "Hassan", "Ibrahim", "Ali", "Mohamed", "Ahmed", "Khalil",
    "Nasser", "Mansour", "Saleh", "Abdallah", "Mahmoud", "Omar",
    "Farouk", "Aziz", "Hamdy", "Ragab", "Saad", "Zaki",
    "Ismail", "Badawi", "Mostafa", "Sharaf", "Fathy", "Galal",
]

# ══════════════════════════════════════════════════════
# CLASS DEFAULTS
# ══════════════════════════════════════════════════════
CLASS_DEFAULTS = {
    "expected_capacity":     30,
    "max_expected_capacity": 30,
    "room_ratio":            1.0,
    "nbr_rooms":             1,
    "room_capacity":         30,
    "display_instructor":    1,
    "display_in_sched_book": 1,
    "cancelled":             0,
    "enrollment":            0,
    "rooms_split_att":       0,
}


class Seeder:

    def __init__(self):
        self.conn = None
        self.cur  = None
        self._id_counter       = None
        self._all_tables       = set()
        self._class_columns    = []
        self._time_pref_columns = []
        self._tpd_columns      = []
        self._tpd_tp_col       = None
        self._tpd_dept_col     = None
        self._required_pref_id = None
        self._can_add_time_prefs = False
        self._all_time_patterns  = []
        self._tp_grid_sizes      = {}

        self.building_id      = None
        self.room_map         = {}
        self.lecture_room_ids = []
        self.lab_room_ids     = []
        self.sa_map           = {}
        self.professor_ids    = []
        self.instructor_ids   = []
        self.pos_professor_id = None
        self.pos_instructor_id = None

        self.prof_assignment  = {}
        self.created_courses  = []
        self.lecture_class_ids = []
        self.lab_class_ids     = []
        self.student_ids      = []
        self.student_profiles = []   # (sid, major, year)
        self.course_registry  = {}   # course_key → info dict

    # ──────────────────────────────────────────
    # CONNECTION
    # ──────────────────────────────────────────

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
        print("Connected to database.")

    async def disconnect(self):
        if self.cur:
            await self.cur.close()
        if self.conn:
            self.conn.close()
        print("Disconnected.")

    # ──────────────────────────────────────────
    # ID GENERATION
    # ──────────────────────────────────────────

    async def _init_id_counter(self):
        try:
            await self.cur.execute(
                "SELECT next_hi FROM hibernate_unique_key FOR UPDATE"
            )
            row = await self.cur.fetchone()
            if row:
                hi = int(row["next_hi"])
                await self.cur.execute(
                    "UPDATE hibernate_unique_key SET next_hi = %s", (hi + 1000,)
                )
                self._id_counter = hi * 32
                print(f"  ID range starts at {self._id_counter}")
                return
        except Exception as e:
            print(f"  ID init warning: {e}")
        self._id_counter = 5_000_000

    def next_id(self):
        uid = self._id_counter
        self._id_counter += 1
        return uid

    # ──────────────────────────────────────────
    # SCHEMA DISCOVERY
    # ──────────────────────────────────────────

    async def _discover_schema(self):
        await self.cur.execute("SHOW TABLES")
        for row in await self.cur.fetchall():
            for v in row.values():
                self._all_tables.add(str(v).lower())

        await self.cur.execute("DESCRIBE class_")
        self._class_columns = [c["Field"] for c in await self.cur.fetchall()]

        if "time_pref" in self._all_tables:
            await self.cur.execute("DESCRIBE time_pref")
            self._time_pref_columns = [c["Field"] for c in await self.cur.fetchall()]

        if "time_pattern_dept" in self._all_tables:
            await self.cur.execute("DESCRIBE time_pattern_dept")
            self._tpd_columns = [c["Field"] for c in await self.cur.fetchall()]
            for col in self._tpd_columns:
                if "pattern" in col.lower():
                    self._tpd_tp_col = col
                if "dept" in col.lower():
                    self._tpd_dept_col = col

        pref_table = (
            "preference_level" if "preference_level" in self._all_tables
            else "pref_level"   if "pref_level"       in self._all_tables
            else None
        )
        if pref_table:
            await self.cur.execute(f"SELECT * FROM {pref_table} ORDER BY uniqueid")
            for pl in await self.cur.fetchall():
                code = str(pl.get("pref_prolog", "")).upper()
                if code == "R":
                    self._required_pref_id = int(pl["uniqueid"])
                    self._can_add_time_prefs = True
                    break
                if code == "0" and not self._required_pref_id:
                    self._required_pref_id = int(pl["uniqueid"])
                    self._can_add_time_prefs = True

        print(f"  Schema: {len(self._class_columns)} class_ cols, "
              f"time_pref={self._can_add_time_prefs}")

    # ──────────────────────────────────────────
    # TIME PATTERNS
    # ──────────────────────────────────────────

    async def _load_time_patterns(self):
        await self.cur.execute(
            "SELECT uniqueid AS tp_id, name, nr_mtgs, mins_pmt "
            "FROM time_pattern WHERE session_id = %s", (SESSION_ID,),
        )
        self._all_time_patterns = await self.cur.fetchall()

        for tp in self._all_time_patterns:
            tp_id = int(tp["tp_id"])
            await self.cur.execute(
                "SELECT COUNT(*) AS n FROM time_pattern_days "
                "WHERE time_pattern_id = %s", (tp_id,),
            )
            n_days = int((await self.cur.fetchone())["n"])
            await self.cur.execute(
                "SELECT COUNT(*) AS n FROM time_pattern_time "
                "WHERE time_pattern_id = %s", (tp_id,),
            )
            n_times = int((await self.cur.fetchone())["n"])
            self._tp_grid_sizes[tp_id] = n_days * n_times

        print(f"  Time patterns loaded: {len(self._all_time_patterns)}")

    def _compatible_patterns(self, mpw):
        return [
            tp for tp in self._all_time_patterns
            if int(tp["nr_mtgs"]) * int(tp["mins_pmt"]) == mpw
        ]

    async def _ensure_time_pattern_dept(self):
        if not self._tpd_tp_col or not self._tpd_dept_col:
            return
        await self.cur.execute(
            f"SELECT {self._tpd_tp_col} AS pid FROM time_pattern_dept "
            f"WHERE {self._tpd_dept_col} = %s", (DEPARTMENT_ID,),
        )
        linked = {int(r["pid"]) for r in await self.cur.fetchall()}
        for tp in self._all_time_patterns:
            tp_id = int(tp["tp_id"])
            if tp_id not in linked:
                await self.cur.execute(
                    f"INSERT INTO time_pattern_dept "
                    f"({self._tpd_dept_col}, {self._tpd_tp_col}) "
                    f"VALUES (%s, %s)", (DEPARTMENT_ID, tp_id),
                )

    async def _insert_time_prefs(self, owner_id, mpw):
        if not self._can_add_time_prefs:
            return 0
        compat = self._compatible_patterns(mpw)
        count = 0
        for tp in compat:
            tp_id   = int(tp["tp_id"])
            grid    = self._tp_grid_sizes.get(tp_id, 0)
            pref_str = "2" * grid if grid > 0 else None
            nid = self.next_id()
            if "pref_level_id" in self._time_pref_columns:
                await self.cur.execute(
                    "INSERT INTO time_pref "
                    "(uniqueid, owner_id, pref_level_id, time_pattern_id, preference) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (nid, owner_id, self._required_pref_id, tp_id, pref_str),
                )
            else:
                await self.cur.execute(
                    "INSERT INTO time_pref "
                    "(uniqueid, owner_id, time_pattern_id, preference) "
                    "VALUES (%s,%s,%s,%s)",
                    (nid, owner_id, tp_id, pref_str),
                )
            count += 1
        return count

    # ──────────────────────────────────────────
    # CLASS INSERT
    # ──────────────────────────────────────────

    async def _insert_class(self, sp_id, cap, suffix, sec_num):
        cls_id = self.next_id()
        cols, vals = ["uniqueid"], [cls_id]

        def add(c, v):
            if c in self._class_columns:
                cols.append(c); vals.append(v)

        add("subpart_id",            sp_id)
        add("date_pattern_id",       DATE_PATTERN_ID)
        add("managing_dept",         DEPARTMENT_ID)
        add("class_suffix",          suffix)
        add("section_number",        sec_num)
        add("expected_capacity",     cap)
        add("max_expected_capacity", cap)
        add("room_capacity",         cap)
        add("room_ratio",            1.0)
        add("nbr_rooms",             1)
        add("display_instructor",    1)
        add("display_in_sched_book", 1)
        add("cancelled",             0)
        add("enrollment",            0)
        add("rooms_split_att",       0)

        ph = ",".join(["%s"] * len(vals))
        await self.cur.execute(
            f"INSERT INTO class_ ({','.join(cols)}) VALUES ({ph})", vals,
        )
        return cls_id

    # ══════════════════════════════════════════
    # SEED: ROOMS
    # ══════════════════════════════════════════

    async def seed_rooms(self):
        print("\n  ROOMS:")

        await self.cur.execute(
            "SELECT uniqueid FROM building "
            "WHERE abbreviation = %s AND session_id = %s",
            (BUILDING_ABBR, SESSION_ID),
        )
        row = await self.cur.fetchone()
        if not row:
            bid = self.next_id()
            await self.cur.execute("DESCRIBE building")
            bld_cols = {c["Field"].lower(): c["Field"] for c in await self.cur.fetchall()}
            ins_cols = ["uniqueid", "session_id", "abbreviation", "name"]
            ins_vals = [bid, SESSION_ID, BUILDING_ABBR, "Building 03"]
            for cand in ("coordinate_x", "coordx", "x", "coord_x"):
                if cand in bld_cols:
                    ins_cols.append(bld_cols[cand]); ins_vals.append(0); break
            for cand in ("coordinate_y", "coordy", "y", "coord_y"):
                if cand in bld_cols:
                    ins_cols.append(bld_cols[cand]); ins_vals.append(0); break
            ph = ",".join(["%s"] * len(ins_vals))
            await self.cur.execute(
                f"INSERT INTO building ({','.join(ins_cols)}) VALUES ({ph})", ins_vals,
            )
            self.building_id = bid
        else:
            self.building_id = int(row["uniqueid"])

        await self.cur.execute(
            "SELECT uniqueid FROM room_type WHERE is_room = 1 ORDER BY ord LIMIT 1"
        )
        row  = await self.cur.fetchone()
        rt_id = int(row["uniqueid"]) if row else 1

        await self.cur.execute(
            "SELECT uniqueid, room_number FROM room "
            "WHERE building_id = %s AND session_id = %s",
            (self.building_id, SESSION_ID),
        )
        existing = {r["room_number"]: int(r["uniqueid"]) for r in await self.cur.fetchall()}

        for room_num, cap, exam_cap, is_lab, label in ROOMS:
            if room_num in existing:
                rid = existing[room_num]
                await self.cur.execute(
                    "UPDATE room SET capacity=%s, exam_capacity=%s WHERE uniqueid=%s",
                    (cap, exam_cap, rid),
                )
            else:
                rid = self.next_id()
                await self.cur.execute(
                    "INSERT INTO room "
                    "(uniqueid,session_id,building_id,room_number,"
                    "capacity,exam_capacity,room_type,permanent_id,"
                    "ignore_too_far,ignore_room_check) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,0)",
                    (rid, SESSION_ID, self.building_id, room_num,
                     cap, exam_cap, rt_id, rid),
                )

            self.room_map[room_num] = rid
            if is_lab:
                self.lab_room_ids.append(rid)
            else:
                self.lecture_room_ids.append(rid)

        if "room_dept" in self._all_tables:
            for rid in self.room_map.values():
                await self.cur.execute(
                    "SELECT uniqueid FROM room_dept "
                    "WHERE room_id=%s AND department_id=%s",
                    (rid, DEPARTMENT_ID),
                )
                if not await self.cur.fetchone():
                    await self.cur.execute(
                        "INSERT INTO room_dept "
                        "(uniqueid,room_id,department_id,is_control) "
                        "VALUES (%s,%s,%s,1)",
                        (self.next_id(), rid, DEPARTMENT_ID),
                    )

        print(f"    {len(self.room_map)} rooms "
              f"({len(self.lecture_room_ids)} lecture, {len(self.lab_room_ids)} lab)")

    # ══════════════════════════════════════════
    # SEED: SUBJECT AREAS
    # ══════════════════════════════════════════

    async def seed_subject_areas(self):
        print("\n  SUBJECT AREAS:")
        for abbr, title in SUBJECT_AREAS:
            await self.cur.execute(
                "SELECT uniqueid FROM subject_area "
                "WHERE subject_area_abbreviation=%s AND session_id=%s",
                (abbr, SESSION_ID),
            )
            row = await self.cur.fetchone()
            if row:
                self.sa_map[abbr] = int(row["uniqueid"])
            else:
                sa_id = self.next_id()
                await self.cur.execute(
                    "INSERT INTO subject_area "
                    "(uniqueid,session_id,subject_area_abbreviation,"
                    "long_title,department_uniqueid) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (sa_id, SESSION_ID, abbr, title, DEPARTMENT_ID),
                )
                self.sa_map[abbr] = sa_id
        print(f"    {len(self.sa_map)} subject areas")

    # ══════════════════════════════════════════
    # SEED: STAFF
    # ══════════════════════════════════════════

    async def seed_staff(self):
        print("\n  STAFF:")
        await self.cur.execute(
            "SELECT uniqueid, reference, label FROM position_type ORDER BY sort_order"
        )
        pos_types = await self.cur.fetchall()

        for pt in pos_types:
            ref = str(pt.get("reference", "")).lower()
            lab = str(pt.get("label", "")).lower()
            if ("professor" in ref or "professor" in lab) \
               and "assistant" not in ref and "associate" not in ref:
                if not self.pos_professor_id:
                    self.pos_professor_id = int(pt["uniqueid"])
            if "instruct" in ref or "instruct" in lab \
               or "teaching" in ref or "teaching" in lab:
                if not self.pos_instructor_id:
                    self.pos_instructor_id = int(pt["uniqueid"])

        if not self.pos_professor_id and pos_types:
            self.pos_professor_id = int(pos_types[0]["uniqueid"])
        if not self.pos_instructor_id and pos_types:
            self.pos_instructor_id = int(
                pos_types[1]["uniqueid"] if len(pos_types) > 1
                else pos_types[0]["uniqueid"]
            )

        for fname, lname, email in PROFESSORS:
            await self.cur.execute(
                "SELECT uniqueid FROM departmental_instructor "
                "WHERE fname=%s AND lname=%s AND department_uniqueid=%s",
                (fname, lname, DEPARTMENT_ID),
            )
            row = await self.cur.fetchone()
            if row:
                self.professor_ids.append(int(row["uniqueid"]))
            else:
                pid = self.next_id()
                await self.cur.execute(
                    "INSERT INTO departmental_instructor "
                    "(uniqueid,external_uid,fname,mname,lname,"
                    "email,department_uniqueid,pos_code_type) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (pid, email.split("@")[0], fname, "", lname,
                     email, DEPARTMENT_ID, self.pos_professor_id),
                )
                self.professor_ids.append(pid)

        for fname, lname, email in INSTRUCTORS_LIST:
            await self.cur.execute(
                "SELECT uniqueid FROM departmental_instructor "
                "WHERE fname=%s AND lname=%s AND department_uniqueid=%s",
                (fname, lname, DEPARTMENT_ID),
            )
            row = await self.cur.fetchone()
            if row:
                self.instructor_ids.append(int(row["uniqueid"]))
            else:
                iid = self.next_id()
                await self.cur.execute(
                    "INSERT INTO departmental_instructor "
                    "(uniqueid,external_uid,fname,mname,lname,"
                    "email,department_uniqueid,pos_code_type) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (iid, email.split("@")[0], fname, "", lname,
                     email, DEPARTMENT_ID, self.pos_instructor_id),
                )
                self.instructor_ids.append(iid)

        print(f"    {len(self.professor_ids)} professors, "
              f"{len(self.instructor_ids)} instructors")

    # ══════════════════════════════════════════
    # PROFESSOR ASSIGNMENTS  (strictly max 3 per professor)
    # ══════════════════════════════════════════

    def _build_professor_assignments(self):
        n = len(PROFESSORS)
        counts = [0] * n

        # ── Step 1: fixed assignments for original 4 professors ──
        for ck, pidx in PROFESSOR_COURSE_MAP.items():
            if pidx < n and counts[pidx] < MAX_LECTURES_PER_PROF:
                self.prof_assignment[ck] = self.professor_ids[pidx]
                counts[pidx] += 1

        # ── Step 2: round-robin remaining courses among new professors (idx 4+)
        #           47 courses ÷ 16 new professors → at most 3 each → fits in 48 ✓
        all_keys = [f"{s} {nbr}" for s, nbr, _, _, _, _ in COURSES]
        new_indices = list(range(4, n))   # professors 4..19
        rr = 0

        for ck in all_keys:
            if ck in self.prof_assignment:
                continue

            assigned = False
            for attempt in range(len(new_indices)):
                pidx = new_indices[(rr + attempt) % len(new_indices)]
                if counts[pidx] < MAX_LECTURES_PER_PROF:
                    self.prof_assignment[ck] = self.professor_ids[pidx]
                    counts[pidx] += 1
                    rr = (rr + attempt + 1) % len(new_indices)
                    assigned = True
                    break

            if not assigned:
                # Absolute safety fallback (should never trigger with 20 professors)
                mi = counts.index(min(counts))
                self.prof_assignment[ck] = self.professor_ids[mi]
                counts[mi] += 1
                print(f"    ⚠ Fallback assignment for {ck} (professor load: {counts})")

        # ── Summary ──
        for i, (fn, ln, _) in enumerate(PROFESSORS):
            assigned = [k for k, v in self.prof_assignment.items()
                        if v == self.professor_ids[i]]
            if assigned:
                print(f"    {fn} {ln}: {len(assigned)} lectures → {assigned}")

        # Verify cap
        violations = [i for i, c in enumerate(counts) if c > MAX_LECTURES_PER_PROF]
        if violations:
            print(f"    ⚠ Cap violations at professor indices: {violations}")
        else:
            print(f"    ✓ All {n} professors within {MAX_LECTURES_PER_PROF}-lecture cap")

    # ══════════════════════════════════════════
    # ENROLLMENT COUNT (for capacity sizing)
    # ══════════════════════════════════════════

    def _count_enrolled(self, semester, dept_tag):
        """How many students will be active in this semester for this tag."""
        count = 0
        for i, (_, major, year) in enumerate(self.student_profiles):
            active_sem = (2 * year - 1) if (i % 2 == 0) else (2 * year)
            if semester != active_sem:
                continue
            if dept_tag != "CENTRAL" and dept_tag != major:
                continue
            count += 1
        return count

    # ══════════════════════════════════════════
    # SEED: COURSES + CLASSES
    # ══════════════════════════════════════════

    async def seed_courses(self):
        print("\n  COURSES & CLASSES:")
        self._build_professor_assignments()
        self._precompute_student_profiles()

        tp_count = 0
        inst_idx = 0

        for subj, nbr, title, credits, semester, dept_tag in COURSES:
            sa_id = self.sa_map.get(subj)
            if not sa_id:
                continue

            course_key    = f"{subj} {nbr}"
            display_title = title if subj != "ARB" else random.choice(MAJOR_ELECTIVES)

            await self.cur.execute(
                "SELECT co.uniqueid FROM course_offering co "
                "WHERE co.subject_area_id=%s AND co.course_nbr=%s",
                (sa_id, nbr),
            )
            if await self.cur.fetchone():
                continue

            enrolled = self._count_enrolled(semester, dept_tag)
            lec_cap  = max(LECTURE_CAP, enrolled)
            lab_cap  = max(LAB_CAP,     enrolled)

            # Instructional offering
            io_id = self.next_id()
            await self.cur.execute(
                "INSERT INTO instructional_offering "
                "(uniqueid,session_id,instr_offering_perm_id,not_offered) "
                "VALUES (%s,%s,%s,0)",
                (io_id, SESSION_ID, io_id),
            )

            # Course offering
            co_id = self.next_id()
            await self.cur.execute(
                "INSERT INTO course_offering "
                "(uniqueid,course_nbr,title,perm_id,"
                "subject_area_id,instr_offr_id,is_control,"
                "nbr_expected_stdents,proj_demand) "
                "VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s)",
                (co_id, nbr, display_title, str(co_id),
                 sa_id, io_id, lec_cap, lec_cap),
            )

            self.created_courses.append({
                "co_id": co_id, "io_id": io_id,
                "subject": subj, "number": nbr,
                "title": display_title, "semester": semester,
                "dept_tag": dept_tag, "credits": credits,
            })
            self.course_registry[course_key] = {
                "co_id": co_id, "io_id": io_id,
                "semester": semester, "dept_tag": dept_tag,
                "lecture_class_ids": [], "lab_class_ids": [],
            }

            cfg_limit = max(CONFIG_LIMIT, enrolled)
            cfg_id = self.next_id()
            await self.cur.execute(
                "INSERT INTO instr_offering_config "
                "(uniqueid,config_limit,instr_offr_id,unlimited_enrollment,name) "
                "VALUES (%s,%s,%s,0,%s)",
                (cfg_id, cfg_limit, io_id, "1"),
            )

            # ── Lecture subpart ──
            lec_mpw   = max(credits * 50, 50)
            lec_sp_id = self.next_id()
            await self.cur.execute(
                "INSERT INTO scheduling_subpart "
                "(uniqueid,min_per_wk,config_id,itype,"
                "auto_time_spread,student_allow_overlap) "
                "VALUES (%s,%s,%s,%s,1,0)",
                (lec_sp_id, lec_mpw, cfg_id, ITYPE_LECTURE),
            )
            tp_count += await self._insert_time_prefs(lec_sp_id, lec_mpw)

            lec_cls_id = await self._insert_class(lec_sp_id, lec_cap, "Lec1", 1)
            self.lecture_class_ids.append(lec_cls_id)
            self.course_registry[course_key]["lecture_class_ids"].append(lec_cls_id)

            prof_id = self.prof_assignment.get(course_key)
            if prof_id:
                await self.cur.execute(
                    "INSERT INTO class_instructor "
                    "(uniqueid,class_id,instructor_id,percent_share,is_lead) "
                    "VALUES (%s,%s,%s,100,1)",
                    (self.next_id(), lec_cls_id, prof_id),
                )

            # ── Lab subpart ──
            lab_mpw   = 100
            lab_sp_id = self.next_id()
            await self.cur.execute(
                "INSERT INTO scheduling_subpart "
                "(uniqueid,min_per_wk,config_id,itype,"
                "auto_time_spread,student_allow_overlap) "
                "VALUES (%s,%s,%s,%s,1,0)",
                (lab_sp_id, lab_mpw, cfg_id, ITYPE_LAB),
            )
            tp_count += await self._insert_time_prefs(lab_sp_id, lab_mpw)

            lab_cls_id = await self._insert_class(lab_sp_id, lab_cap, "Lab1", 1)
            self.lab_class_ids.append(lab_cls_id)
            self.course_registry[course_key]["lab_class_ids"].append(lab_cls_id)

            # Round-robin instructor assignment
            inst_id = self.instructor_ids[inst_idx % len(self.instructor_ids)]
            inst_idx += 1
            await self.cur.execute(
                "INSERT INTO class_instructor "
                "(uniqueid,class_id,instructor_id,percent_share,is_lead) "
                "VALUES (%s,%s,%s,100,1)",
                (self.next_id(), lab_cls_id, inst_id),
            )

            print(f"    {course_key}: {display_title} "
                  f"[S{semester}/{dept_tag}] "
                  f"lec={lec_cap} lab={lab_cap} enrolled={enrolled}")

        print(f"\n    {len(self.created_courses)} courses, "
              f"{len(self.lecture_class_ids)} lectures, "
              f"{len(self.lab_class_ids)} labs, "
              f"{tp_count} time prefs")

    # ══════════════════════════════════════════
    # PRECOMPUTE STUDENT PROFILES
    # ══════════════════════════════════════════

    def _precompute_student_profiles(self):
        """Build ordered profiles matching enrollment logic exactly."""
        if self.student_profiles:
            return
        majors = ["DS", "IN"]
        idx = 0
        for year in range(1, 5):
            for major in majors:
                for _ in range(STUDENTS_PER_GROUP):
                    # placeholder sid=0; real IDs assigned in seed_students
                    self.student_profiles.append((0, major, year))
                    idx += 1
        print(f"    Pre-computed {len(self.student_profiles)} student profiles")

    # ══════════════════════════════════════════
    # SEED: STUDENTS
    # ══════════════════════════════════════════

    async def seed_students(self):
        print("\n  STUDENTS:")
        used          = set()
        real_profiles = []

        for i, (_, major, year) in enumerate(self.student_profiles):
            while True:
                fn = random.choice(STUDENT_FIRST)
                ln = random.choice(STUDENT_LAST)
                if (fn, ln) not in used:
                    used.add((fn, ln)); break

            sid = self.next_id()
            await self.cur.execute(
                "INSERT INTO student "
                "(uniqueid,external_uid,first_name,middle_name,"
                "last_name,email,session_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (sid, f"STU{sid}", fn, "", ln,
                 f"{fn.lower()}.{ln.lower()}@student.aiet.edu", SESSION_ID),
            )
            self.student_ids.append(sid)
            real_profiles.append((sid, major, year))

        self.student_profiles = real_profiles

        ds  = sum(1 for _, m, _ in self.student_profiles if m == "DS")
        ins = sum(1 for _, m, _ in self.student_profiles if m == "IN")
        print(f"    {len(self.student_ids)} students (DS={ds}, IN={ins})")
        for yr in range(1, 5):
            cnt = sum(1 for _, _, y in self.student_profiles if y == yr)
            print(f"      Year {yr}: {cnt}")

    # ══════════════════════════════════════════
    # SEED: ENROLLMENTS  (ONE semester per student)
    # ══════════════════════════════════════════

    async def seed_enrollments(self):
        print("\n  ENROLLMENTS:")
        count = 0

        for i, (sid, major, year) in enumerate(self.student_profiles):
            # ONE active semester per student (mirrors _precompute logic)
            active_sem = (2 * year - 1) if (i % 2 == 0) else (2 * year)

            for course_key, reg in self.course_registry.items():
                if reg["semester"] != active_sem:
                    continue
                tag = reg["dept_tag"]
                if tag != "CENTRAL" and tag != major:
                    continue

                co_id = reg["co_id"]
                for lec_cls in reg["lecture_class_ids"]:
                    await self.cur.execute(
                        "INSERT INTO student_class_enrl "
                        "(uniqueid,student_id,class_id,course_offering_id,timestamp) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (self.next_id(), sid, lec_cls, co_id, datetime.now()),
                    )
                    count += 1
                for lab_cls in reg["lab_class_ids"]:
                    await self.cur.execute(
                        "INSERT INTO student_class_enrl "
                        "(uniqueid,student_id,class_id,course_offering_id,timestamp) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (self.next_id(), sid, lab_cls, co_id, datetime.now()),
                    )
                    count += 1

        print(f"    {count} enrollments")

        # Enrollment-per-semester sanity check
        sem_counts = {}
        for i, (_, major, year) in enumerate(self.student_profiles):
            active_sem = (2 * year - 1) if (i % 2 == 0) else (2 * year)
            sem_counts[active_sem] = sem_counts.get(active_sem, 0) + 1
        print(f"    Students per active semester: {dict(sorted(sem_counts.items()))}")

    # ══════════════════════════════════════════
    # BACKFILL
    # ══════════════════════════════════════════

    async def backfill(self):
        print("\n  BACKFILL:")
        sets   = [f"{c}=COALESCE({c},{v})"
                  for c, v in CLASS_DEFAULTS.items() if c in self._class_columns]
        wheres = [f"{c} IS NULL"
                  for c in CLASS_DEFAULTS     if c in self._class_columns]
        if sets and wheres:
            await self.cur.execute(
                f"UPDATE class_ c "
                f"JOIN scheduling_subpart sp ON c.subpart_id=sp.uniqueid "
                f"JOIN instr_offering_config ioc ON sp.config_id=ioc.uniqueid "
                f"JOIN instructional_offering io ON ioc.instr_offr_id=io.uniqueid "
                f"SET {','.join(sets)} "
                f"WHERE io.session_id=%s AND ({' OR '.join(wheres)})",
                (SESSION_ID,),
            )

        await self.cur.execute(
            "SELECT sp.uniqueid AS id, sp.min_per_wk "
            "FROM scheduling_subpart sp "
            "JOIN instr_offering_config ioc ON sp.config_id=ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id=io.uniqueid "
            "WHERE io.session_id=%s "
            "  AND sp.uniqueid NOT IN (SELECT owner_id FROM time_pref)",
            (SESSION_ID,),
        )
        bf = 0
        for sp in await self.cur.fetchall():
            bf += await self._insert_time_prefs(int(sp["id"]), int(sp["min_per_wk"]))
        print(f"    {bf} time_pref backfilled")

    # ══════════════════════════════════════════
    # VERIFY
    # ══════════════════════════════════════════

    async def verify(self):
        print("\n  VERIFICATION:")

        await self.cur.execute(
            "SELECT COUNT(*) AS c FROM subject_area WHERE session_id=%s", (SESSION_ID,)
        )
        print(f"    Subject areas : {(await self.cur.fetchone())['c']}")

        await self.cur.execute(
            "SELECT COUNT(*) AS c FROM course_offering co "
            "JOIN instructional_offering io ON co.instr_offr_id=io.uniqueid "
            "WHERE io.session_id=%s", (SESSION_ID,),
        )
        print(f"    Courses       : {(await self.cur.fetchone())['c']}")

        await self.cur.execute(
            "SELECT "
            "  CASE sp.itype WHEN 10 THEN 'Lectures' "
            "  WHEN 30 THEN 'Labs' ELSE CONCAT('itype=',sp.itype) END AS type, "
            "  COUNT(*) AS cnt "
            "FROM class_ c "
            "JOIN scheduling_subpart sp ON c.subpart_id=sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id=ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id=io.uniqueid "
            "WHERE io.session_id=%s GROUP BY sp.itype ORDER BY sp.itype",
            (SESSION_ID,),
        )
        for r in await self.cur.fetchall():
            print(f"    {r['type']:10s}: {r['cnt']}")

        await self.cur.execute(
            "SELECT COUNT(*) AS cnt FROM student_class_enrl sce "
            "JOIN class_ c ON sce.class_id=c.uniqueid "
            "JOIN scheduling_subpart sp ON c.subpart_id=sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id=ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id=io.uniqueid "
            "WHERE io.session_id=%s", (SESSION_ID,),
        )
        print(f"    Enrollments   : {(await self.cur.fetchone())['cnt']}")

        # Over-capacity labs
        await self.cur.execute(
            "SELECT c.uniqueid, c.expected_capacity, COUNT(sce.uniqueid) AS enrolled "
            "FROM class_ c "
            "JOIN scheduling_subpart sp ON c.subpart_id=sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id=ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id=io.uniqueid "
            "LEFT JOIN student_class_enrl sce ON sce.class_id=c.uniqueid "
            "WHERE io.session_id=%s AND sp.itype=30 "
            "GROUP BY c.uniqueid, c.expected_capacity "
            "HAVING enrolled > expected_capacity",
            (SESSION_ID,),
        )
        overflows = await self.cur.fetchall()
        if overflows:
            print(f"    ⚠ {len(overflows)} labs over capacity!")
        else:
            print("    ✓ All labs within capacity")

        # Verify Ossama Badawy
        await self.cur.execute(
            "SELECT CONCAT(sa.subject_area_abbreviation,' ',co.course_nbr) AS course "
            "FROM class_instructor ci "
            "JOIN departmental_instructor di ON ci.instructor_id=di.uniqueid "
            "JOIN class_ c ON ci.class_id=c.uniqueid "
            "JOIN scheduling_subpart sp ON c.subpart_id=sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id=ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id=io.uniqueid "
            "JOIN course_offering co ON co.instr_offr_id=io.uniqueid AND co.is_control=1 "
            "JOIN subject_area sa ON co.subject_area_id=sa.uniqueid "
            "WHERE di.fname='Ossama' AND di.lname='Badawy' "
            "  AND io.session_id=%s AND sp.itype=10 "
            "GROUP BY course", (SESSION_ID,),
        )
        ossama = [r["course"] for r in await self.cur.fetchall()]
        print(f"    Ossama Badawy : {ossama}")

        # Professor load summary
        await self.cur.execute(
            "SELECT CONCAT(di.fname,' ',di.lname) AS prof, COUNT(*) AS lectures "
            "FROM class_instructor ci "
            "JOIN departmental_instructor di ON ci.instructor_id=di.uniqueid "
            "JOIN class_ c ON ci.class_id=c.uniqueid "
            "JOIN scheduling_subpart sp ON c.subpart_id=sp.uniqueid "
            "JOIN instr_offering_config ioc ON sp.config_id=ioc.uniqueid "
            "JOIN instructional_offering io ON ioc.instr_offr_id=io.uniqueid "
            "WHERE io.session_id=%s AND sp.itype=10 "
            "GROUP BY di.uniqueid HAVING lectures > 3 "
            "ORDER BY lectures DESC",
            (SESSION_ID,),
        )
        over = await self.cur.fetchall()
        if over:
            print("    ⚠ Professors exceeding 3-lecture cap:")
            for r in over:
                print(f"      {r['prof']}: {r['lectures']}")
        else:
            print("    ✓ All professors within 3-lecture cap")

        # Student distribution
        print(f"    Students      : {len(self.student_ids)}")
        for yr in range(1, 5):
            ds  = sum(1 for _, m, y in self.student_profiles if y == yr and m == "DS")
            ins = sum(1 for _, m, y in self.student_profiles if y == yr and m == "IN")
            print(f"      Year {yr}: DS={ds} IN={ins}")

    # ══════════════════════════════════════════
    # RUN
    # ══════════════════════════════════════════

    async def run(self):
        await self.connect()
        try:
            print("\n" + "=" * 60)
            print("  SEED: AIET AI Department — Fall 2010")
            print("=" * 60)

            await self._init_id_counter()
            await self._discover_schema()
            await self._load_time_patterns()
            await self._ensure_time_pattern_dept()

            await self.seed_rooms()
            await self.seed_subject_areas()
            await self.seed_staff()
            await self.seed_courses()
            await self.seed_students()
            await self.seed_enrollments()
            await self.backfill()
            await self.verify()

            await self.conn.commit()

            print("\n" + "=" * 60)
            print("  SEED COMPLETE")
            print("=" * 60)
            print(f"  Subject Areas : {len(self.sa_map)}")
            print(f"  Courses       : {len(self.created_courses)}")
            print(f"  Lectures      : {len(self.lecture_class_ids)}")
            print(f"  Labs          : {len(self.lab_class_ids)}")
            print(f"  Professors    : {len(self.professor_ids)}")
            print(f"  Instructors   : {len(self.instructor_ids)}")
            print(f"  Students      : {len(self.student_ids)}")
            print(f"\n  → Restart Tomcat → Solver → Load → Start")

        except Exception as e:
            await self.conn.rollback()
            print(f"\n  ERROR: {e}")
            import traceback; traceback.print_exc()
            raise
        finally:
            await self.disconnect()


if __name__ == "__main__":
    asyncio.run(Seeder().run())