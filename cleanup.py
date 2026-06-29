"""
cleanup.py — Thorough cleanup of ALL seeded data from Fal 2010 session.

Removes:
  - All students added by seed.py (identified by @student.aiet.edu email)
  - All new instructors/professors (anyone not in ORIGINAL_INSTRUCTORS)
  - All new courses, classes, subparts, configs, offerings
  - All assignments and room assignments for new courses
  - All time preferences for new subparts
  - All rooms added in building "03" (or "AIET")
  - The building "03" itself (if we created it)
  - All subject areas added by seed.py
  - All AIET time patterns (AIET 1x80, 2x80, 3x80)
  - Orphaned time_pref records
  - Orphaned assignments

Usage:
    python cleanup.py

Safe: never touches data with IDs in ORIGINAL_* lists.
"""

import asyncio
import aiomysql
from app.config import settings

# ══════════════════════════════════════════════════════
# ORIGINAL DATA — NEVER DELETE THESE
# ══════════════════════════════════════════════════════

# Original Fal 2010 instructor IDs (leave untouched)
ORIGINAL_INSTRUCTORS = [
    231385, 231386, 231387, 231388, 231389,
    4751360, 4882432, 8912896,
]

# Original course offering IDs (leave untouched)
ORIGINAL_COURSE_IDS = [
    135753, 135754, 135755, 135756, 135757, 135758,
    135759, 135760, 135761, 135762, 135763, 135764,
    135765, 135766, 135767, 135768, 135769, 135770,
    135771, 135772,
]

SESSION_ID = 231379

# Building abbreviations we created — will be deleted
OUR_BUILDINGS = ["03", "AIET"]

# Subject area abbreviations we created
OUR_SUBJECT_AREAS = [
    "AGN", "AIN", "ADS", "AIS", "ACY",
    "EBA", "UNR", "APT", "ARB",
]

# Time pattern name prefixes we created
OUR_TIME_PATTERN_PREFIX = "AIET"


async def cleanup():
    conn = await aiomysql.connect(
        host=settings.UNITIME_DB_HOST,
        port=settings.UNITIME_DB_PORT,
        user=settings.UNITIME_DB_USER,
        password=settings.UNITIME_DB_PASSWORD,
        db=settings.UNITIME_DB_NAME,
        autocommit=False,
        charset="utf8mb4",
    )
    cur = await conn.cursor(aiomysql.DictCursor)

    print("=" * 60)
    print("🧹 THOROUGH CLEANUP — Fal 2010 seeded data")
    print("=" * 60)

    try:
        # ──────────────────────────────────────────────
        # STEP 1: STUDENTS
        # ──────────────────────────────────────────────
        print("\n── Step 1: Students ──")

        # Match both email domains used across seed versions
        await cur.execute("""
            SELECT uniqueid FROM student
            WHERE session_id = %s
              AND (
                email LIKE '%%@student.aiet.edu'
                OR email LIKE '%%@student.woebegon.edu'
              )
        """, (SESSION_ID,))
        new_students = [int(r["uniqueid"]) for r in await cur.fetchall()]

        if new_students:
            ph = ",".join(["%s"] * len(new_students))
            await cur.execute(
                f"DELETE FROM student_class_enrl WHERE student_id IN ({ph})",
                new_students)
            enrl_del = cur.rowcount
            await cur.execute(
                f"DELETE FROM student WHERE uniqueid IN ({ph})",
                new_students)
            print(f"  ✓ Deleted {len(new_students)} students, {enrl_del} enrollments")
        else:
            print("  ✓ No seeded students found")

        # ──────────────────────────────────────────────
        # STEP 2: COURSES (classes → subparts → configs → offerings)
        # ──────────────────────────────────────────────
        print("\n── Step 2: Courses & Classes ──")

        orig_str = ",".join(str(x) for x in ORIGINAL_COURSE_IDS)
        await cur.execute(f"""
            SELECT co.uniqueid AS course_id, io.uniqueid AS offering_id
            FROM course_offering co
            JOIN instructional_offering io ON co.instr_offr_id = io.uniqueid
            WHERE io.session_id = {SESSION_ID}
              AND co.uniqueid NOT IN ({orig_str})
        """)
        new_courses = await cur.fetchall()

        course_del = 0
        for course in new_courses:
            oid = int(course["offering_id"])
            cid = int(course["course_id"])

            # student enrollments
            await cur.execute(f"""
                DELETE sce FROM student_class_enrl sce
                JOIN class_ c ON sce.class_id = c.uniqueid
                JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid
                JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid
                WHERE ioc.instr_offr_id = {oid}
            """)
            # assigned rooms
            await cur.execute(f"""
                DELETE ar FROM assigned_rooms ar
                JOIN assignment a ON ar.assignment_id = a.uniqueid
                JOIN class_ c ON a.class_id = c.uniqueid
                JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid
                JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid
                WHERE ioc.instr_offr_id = {oid}
            """)
            # assignments
            await cur.execute(f"""
                DELETE a FROM assignment a
                JOIN class_ c ON a.class_id = c.uniqueid
                JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid
                JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid
                WHERE ioc.instr_offr_id = {oid}
            """)
            # class instructors
            await cur.execute(f"""
                DELETE ci FROM class_instructor ci
                JOIN class_ c ON ci.class_id = c.uniqueid
                JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid
                JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid
                WHERE ioc.instr_offr_id = {oid}
            """)
            # time prefs on subparts
            await cur.execute(f"""
                DELETE tp FROM time_pref tp
                JOIN scheduling_subpart sp ON tp.owner_id = sp.uniqueid
                JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid
                WHERE ioc.instr_offr_id = {oid}
            """)
            # classes
            await cur.execute(f"""
                DELETE c FROM class_ c
                JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid
                JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid
                WHERE ioc.instr_offr_id = {oid}
            """)
            # subparts
            await cur.execute(f"""
                DELETE FROM scheduling_subpart
                WHERE config_id IN (
                    SELECT uniqueid FROM instr_offering_config
                    WHERE instr_offr_id = {oid}
                )
            """)
            # config
            await cur.execute(
                "DELETE FROM instr_offering_config WHERE instr_offr_id = %s", (oid,))
            # course offering
            await cur.execute(
                "DELETE FROM course_offering WHERE uniqueid = %s", (cid,))
            # instructional offering
            await cur.execute(
                "DELETE FROM instructional_offering WHERE uniqueid = %s", (oid,))
            course_del += 1

        print(f"  ✓ Deleted {course_del} courses and all their classes/subparts")

        # ──────────────────────────────────────────────
        # STEP 3: ORPHANED ASSIGNMENTS & TIME PREFS
        # ──────────────────────────────────────────────
        print("\n── Step 3: Orphaned records ──")

        # Assignments with no matching class
        await cur.execute("""
            DELETE a FROM assignment a
            LEFT JOIN class_ c ON a.class_id = c.uniqueid
            WHERE c.uniqueid IS NULL
        """)
        print(f"  ✓ Orphaned assignments deleted: {cur.rowcount}")

        # Assigned_rooms with no matching assignment
        await cur.execute("""
            DELETE ar FROM assigned_rooms ar
            LEFT JOIN assignment a ON ar.assignment_id = a.uniqueid
            WHERE a.uniqueid IS NULL
        """)
        print(f"  ✓ Orphaned assigned_rooms deleted: {cur.rowcount}")

        # Time prefs with no matching subpart
        await cur.execute("""
            DELETE FROM time_pref
            WHERE owner_id NOT IN (SELECT uniqueid FROM scheduling_subpart)
        """)
        print(f"  ✓ Orphaned time_pref deleted: {cur.rowcount}")

        # ──────────────────────────────────────────────
        # STEP 4: INSTRUCTORS / PROFESSORS
        # ──────────────────────────────────────────────
        print("\n── Step 4: Instructors & Professors ──")

        orig_inst_str = ",".join(str(x) for x in ORIGINAL_INSTRUCTORS)
        await cur.execute(f"""
            SELECT di.uniqueid FROM departmental_instructor di
            JOIN department d ON di.department_uniqueid = d.uniqueid
            WHERE d.session_id = {SESSION_ID}
              AND di.uniqueid NOT IN ({orig_inst_str})
        """)
        new_instructors = [int(r["uniqueid"]) for r in await cur.fetchall()]

        if new_instructors:
            ph = ",".join(["%s"] * len(new_instructors))
            # Clean up any leftover class_instructor links
            await cur.execute(
                f"DELETE FROM class_instructor WHERE instructor_id IN ({ph})",
                new_instructors)
            await cur.execute(
                f"DELETE FROM departmental_instructor WHERE uniqueid IN ({ph})",
                new_instructors)
            print(f"  ✓ Deleted {len(new_instructors)} instructors/professors")
        else:
            print("  ✓ No seeded instructors found")

        # ──────────────────────────────────────────────
        # STEP 5: TIME PATTERNS (AIET 1x80, 2x80, 3x80)
        # ──────────────────────────────────────────────
        print("\n── Step 5: Time Patterns ──")

        await cur.execute("""
            SELECT uniqueid, name FROM time_pattern
            WHERE session_id = %s AND name LIKE %s
        """, (SESSION_ID, f"{OUR_TIME_PATTERN_PREFIX}%"))
        our_patterns = await cur.fetchall()

        # Also find column names for time_pattern_days and time_pattern_time
        await cur.execute("SHOW TABLES")
        all_tables = {list(r.values())[0].lower() for r in await cur.fetchall()}

        tp_days_fk = None
        tp_time_fk = None
        tpd_tp_col = None
        tpd_dept_col = None

        if "time_pattern_days" in all_tables:
            await cur.execute("DESCRIBE time_pattern_days")
            for c in await cur.fetchall():
                if "pattern" in c["Field"].lower() and c["Field"].lower() != "uniqueid":
                    tp_days_fk = c["Field"]

        if "time_pattern_time" in all_tables:
            await cur.execute("DESCRIBE time_pattern_time")
            for c in await cur.fetchall():
                if "pattern" in c["Field"].lower() and c["Field"].lower() != "uniqueid":
                    tp_time_fk = c["Field"]

        if "time_pattern_dept" in all_tables:
            await cur.execute("DESCRIBE time_pattern_dept")
            for c in await cur.fetchall():
                col = c["Field"]
                if "pattern" in col.lower():
                    tpd_tp_col = col
                if "dept" in col.lower():
                    tpd_dept_col = col

        for p in our_patterns:
            tp_id = int(p["uniqueid"])
            name = p["name"]

            if tp_days_fk:
                await cur.execute(
                    f"DELETE FROM time_pattern_days WHERE {tp_days_fk} = %s", (tp_id,))
            if tp_time_fk:
                await cur.execute(
                    f"DELETE FROM time_pattern_time WHERE {tp_time_fk} = %s", (tp_id,))
            if tpd_tp_col:
                await cur.execute(
                    f"DELETE FROM time_pattern_dept WHERE {tpd_tp_col} = %s", (tp_id,))
            await cur.execute(
                "DELETE FROM time_pref WHERE time_pattern_id = %s", (tp_id,))
            await cur.execute(
                "DELETE FROM time_pattern WHERE uniqueid = %s", (tp_id,))
            print(f"  ✓ Deleted time pattern '{name}'")

        if not our_patterns:
            print("  ✓ No AIET time patterns found")

        # ──────────────────────────────────────────────
        # STEP 6: ROOMS
        # ──────────────────────────────────────────────
        print("\n── Step 6: Rooms ──")

        room_del = 0
        for bldg_abbr in OUR_BUILDINGS:
            await cur.execute("""
                SELECT r.uniqueid AS room_id
                FROM room r
                JOIN building b ON r.building_id = b.uniqueid
                WHERE r.session_id = %s AND b.abbreviation = %s
            """, (SESSION_ID, bldg_abbr))
            rooms = [int(r["room_id"]) for r in await cur.fetchall()]

            if not rooms:
                continue

            ph = ",".join(["%s"] * len(rooms))

            # Remove room from assigned_rooms
            await cur.execute(
                f"DELETE ar FROM assigned_rooms ar "
                f"JOIN assignment a ON ar.assignment_id = a.uniqueid "
                f"JOIN class_ c ON a.class_id = c.uniqueid "
                f"WHERE ar.room_id IN ({ph})",
                rooms)

            # Remove room_dept links
            if "room_dept" in all_tables:
                await cur.execute(
                    f"DELETE FROM room_dept WHERE room_id IN ({ph})", rooms)

            # Remove room_feature_assignment links
            if "room_feature_assignment" in all_tables:
                await cur.execute(
                    f"DELETE FROM room_feature_assignment WHERE room_id IN ({ph})", rooms)

            # Remove room_group_room links
            if "room_group_room" in all_tables:
                await cur.execute(
                    f"DELETE FROM room_group_room WHERE room_id IN ({ph})", rooms)

            # Delete the rooms
            await cur.execute(
                f"DELETE FROM room WHERE uniqueid IN ({ph})", rooms)
            room_del += len(rooms)

        print(f"  ✓ Deleted {room_del} rooms")

        # ──────────────────────────────────────────────
        # STEP 7: BUILDINGS
        # ──────────────────────────────────────────────
        print("\n── Step 7: Buildings ──")

        bldg_del = 0
        for bldg_abbr in OUR_BUILDINGS:
            # Only delete if it has no rooms left
            await cur.execute("""
                SELECT b.uniqueid
                FROM building b
                LEFT JOIN room r ON r.building_id = b.uniqueid AND r.session_id = %s
                WHERE b.session_id = %s AND b.abbreviation = %s
                  AND r.uniqueid IS NULL
            """, (SESSION_ID, SESSION_ID, bldg_abbr))
            row = await cur.fetchone()
            if row:
                await cur.execute(
                    "DELETE FROM building WHERE uniqueid = %s", (int(row["uniqueid"]),))
                print(f"  ✓ Deleted building '{bldg_abbr}'")
                bldg_del += 1

        if bldg_del == 0:
            print("  ✓ No empty buildings to delete (or not found)")

        # ──────────────────────────────────────────────
        # STEP 8: SUBJECT AREAS
        # ──────────────────────────────────────────────
        print("\n── Step 8: Subject Areas ──")

        sa_abbr_ph = ",".join(["%s"] * len(OUR_SUBJECT_AREAS))
        await cur.execute(f"""
            SELECT uniqueid, subject_area_abbreviation
            FROM subject_area
            WHERE session_id = %s
              AND subject_area_abbreviation IN ({sa_abbr_ph})
        """, [SESSION_ID] + list(OUR_SUBJECT_AREAS))
        our_sas = await cur.fetchall()

        sa_del = 0
        for sa in our_sas:
            sa_id = int(sa["uniqueid"])
            abbr = sa["subject_area_abbreviation"]

            # Check if any course offerings still reference this SA
            await cur.execute(
                "SELECT COUNT(*) AS cnt FROM course_offering WHERE subject_area_id = %s",
                (sa_id,))
            cnt = int((await cur.fetchone())["cnt"])
            if cnt == 0:
                await cur.execute(
                    "DELETE FROM subject_area WHERE uniqueid = %s", (sa_id,))
                print(f"  ✓ Deleted subject area '{abbr}'")
                sa_del += 1
            else:
                print(f"  ⚠ Skipped '{abbr}' — still has {cnt} course offerings")

        if sa_del == 0 and not our_sas:
            print("  ✓ No seeded subject areas found")

        # ──────────────────────────────────────────────
        # FINAL COMMIT
        # ──────────────────────────────────────────────
        await conn.commit()

        print("\n" + "=" * 60)
        print("✅ CLEANUP COMPLETE")
        print("=" * 60)
        print(f"  Students deleted:       {len(new_students)}")
        print(f"  Courses deleted:        {course_del}")
        print(f"  Instructors deleted:    {len(new_instructors)}")
        print(f"  Time patterns deleted:  {len(our_patterns)}")
        print(f"  Rooms deleted:          {room_del}")
        print(f"  Buildings deleted:      {bldg_del}")
        print(f"  Subject areas deleted:  {sa_del}")
        print()
        print("  ✔ Original Fal 2010 data untouched")
        print()

    except Exception as e:
        await conn.rollback()
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        await cur.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(cleanup())