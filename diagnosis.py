"""
diagnose_student_schedule.py — Check why students see empty timetables
"""

import asyncio
import aiomysql
from app.config import settings

SESSION_ID = 231379

async def diagnose():
    conn = await aiomysql.connect(
        host=settings.UNITIME_DB_HOST,
        port=settings.UNITIME_DB_PORT,
        user=settings.UNITIME_DB_USER,
        password=settings.UNITIME_DB_PASSWORD,
        db=settings.UNITIME_DB_NAME,
        charset="utf8mb4",
    )
    cur = await conn.cursor(aiomysql.DictCursor)
    
    print("=" * 80)
    print("STUDENT SCHEDULE DIAGNOSIS")
    print("=" * 80)
    
    # 1. Check students
    print("\n1. STUDENTS IN DATABASE:")
    await cur.execute("""
        SELECT uniqueid, external_uid, first_name, last_name, email
        FROM student WHERE session_id = %s
        ORDER BY last_name, first_name
        LIMIT 10
    """, (SESSION_ID,))
    students = await cur.fetchall()
    
    if not students:
        print("   ❌ NO STUDENTS FOUND!")
        return
    
    for s in students:
        print(f"   ID: {s['uniqueid']}, Name: {s['first_name']} {s['last_name']}, Email: {s['email']}")
    
    # 2. Check enrollments
    print("\n2. STUDENT ENROLLMENTS:")
    await cur.execute("""
        SELECT 
            s.uniqueid as student_id,
            CONCAT(s.first_name, ' ', s.last_name) as student_name,
            COUNT(sce.uniqueid) as enrollment_count
        FROM student s
        LEFT JOIN student_class_enrl sce ON s.uniqueid = sce.student_id
        WHERE s.session_id = %s
        GROUP BY s.uniqueid, s.first_name, s.last_name
        ORDER BY enrollment_count DESC
        LIMIT 10
    """, (SESSION_ID,))
    enrollments = await cur.fetchall()
    
    for e in enrollments:
        status = "✅" if e['enrollment_count'] > 0 else "❌"
        print(f"   {status} {e['student_name']}: {e['enrollment_count']} enrollments")
    
    # 3. Check assignments (the key issue!)
    print("\n3. CLASS ASSIGNMENTS (from solver):")
    await cur.execute("""
        SELECT COUNT(*) as total FROM assignment a
        JOIN class_ c ON a.class_id = c.uniqueid
        JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid
        JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid
        JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid
        WHERE io.session_id = %s
    """, (SESSION_ID,))
    result = await cur.fetchone()
    print(f"   Total assignments in database: {result['total']}")
    
    # 4. Check assignments with actual time slots
    await cur.execute("""
        SELECT COUNT(*) as with_time FROM assignment a
        JOIN class_ c ON a.class_id = c.uniqueid
        JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid
        JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid
        JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid
        WHERE io.session_id = %s
          AND a.days IS NOT NULL 
          AND a.days > 0
          AND a.slot IS NOT NULL
    """, (SESSION_ID,))
    result = await cur.fetchone()
    print(f"   Assignments with valid day/slot: {result['with_time']}")
    
    # 5. Check a specific student's full path
    print("\n4. SAMPLE STUDENT FULL TRACE:")
    
    # Pick a student with enrollments
    await cur.execute("""
        SELECT s.uniqueid, s.first_name, s.last_name, s.email,
               COUNT(sce.uniqueid) as enrl_count
        FROM student s
        JOIN student_class_enrl sce ON s.uniqueid = sce.student_id
        WHERE s.session_id = %s
        GROUP BY s.uniqueid
        HAVING enrl_count > 0
        ORDER BY enrl_count DESC
        LIMIT 1
    """, (SESSION_ID,))
    sample = await cur.fetchone()
    
    if not sample:
        print("   ❌ No students with enrollments found!")
        await cur.close()
        conn.close()
        return
    
    print(f"\n   Selected Student: {sample['first_name']} {sample['last_name']}")
    print(f"   Email: {sample['email']}")
    print(f"   Student ID: {sample['uniqueid']}")
    print(f"   Enrollments: {sample['enrl_count']}")
    
    # Get this student's classes with assignment details
    await cur.execute("""
        SELECT 
            sce.uniqueid as enrollment_id,
            c.uniqueid as class_id,
            co.title as course_title,
            co.course_nbr as course_number,
            sa.subject_area_abbreviation as subject,
            a.uniqueid as assignment_id,
            a.days as assigned_days,
            a.slot as assigned_slot,
            r.room_number,
            b.abbreviation as building
        FROM student_class_enrl sce
        JOIN class_ c ON sce.class_id = c.uniqueid
        JOIN scheduling_subpart sp ON c.subpart_id = sp.uniqueid
        JOIN instr_offering_config ioc ON sp.config_id = ioc.uniqueid
        JOIN instructional_offering io ON ioc.instr_offr_id = io.uniqueid
        JOIN course_offering co ON co.instr_offr_id = io.uniqueid AND co.is_control = 1
        JOIN subject_area sa ON co.subject_area_id = sa.uniqueid
        LEFT JOIN assignment a ON a.class_id = c.uniqueid
        LEFT JOIN assigned_rooms ar ON ar.assignment_id = a.uniqueid
        LEFT JOIN room r ON ar.room_id = r.uniqueid
        LEFT JOIN building b ON r.building_id = b.uniqueid
        WHERE sce.student_id = %s
        ORDER BY sa.subject_area_abbreviation, co.course_nbr
    """, (sample['uniqueid'],))
    
    classes = await cur.fetchall()
    
    print(f"\n   Classes enrolled ({len(classes)}):")
    print(f"   {'Course':<15} {'Class ID':<10} {'Assignment':<12} {'Days':<8} {'Slot':<8} {'Room'}")
    print("   " + "-" * 70)
    
    has_schedule = False
    for c in classes:
        course = f"{c['subject']} {c['course_number']}"
        assign_id = c['assignment_id'] or "NONE"
        days = c['assigned_days'] if c['assigned_days'] else "-"
        slot = c['assigned_slot'] if c['assigned_slot'] else "-"
        room = f"{c['building']} {c['room_number']}" if c['building'] else "TBA"
        
        if c['assigned_days'] and c['assigned_slot']:
            has_schedule = True
            status = "✅"
        else:
            status = "❌"
        
        print(f"   {status} {course:<13} {str(c['class_id']):<10} {str(assign_id):<12} {str(days):<8} {str(slot):<8} {room}")
    
    if has_schedule:
        print(f"\n   ✅ This student HAS scheduled classes!")
        print(f"\n   👤 LOGIN WITH: {sample['email']}")
    else:
        print(f"\n   ❌ This student has NO scheduled classes (days/slot are NULL)")
        print(f"   → The solver solution needs to be imported into the database!")
    
    await cur.close()
    conn.close()

if __name__ == "__main__":
    asyncio.run(diagnose())