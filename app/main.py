from fastapi import UploadFile, File
from app.services.bulk_upload import BulkUploader

from app.services.chatbot import ScheduleChatbot
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse

from app.viewmodels.auth_vm import AuthViewModel
from app.services.unitime_client import UniTimeClient
from app.services.unitime_db import UniTimeDB
from app.services.xml_schedule import XMLSchedule, build_room_map
from app.services.auth_service import AuthService
from app.services.session import create_session, get_session, clear_session
from app.models.user import (
    session_to_user_context, ROLE_TITLES, ROLE_PERMISSIONS, can_read, can_write
)
from app.config import settings

from app.agents import (
    ConstraintValidator,
    TimetableValidator,
    ViolationClassifier,
    CorrectionSuggester,
    FullAnalysisReport,
)

VALID_ROLES = {"student", "scheduler", "ta", "lecturer", "vicedean", "dean"}


# ══════════════════════════════════════════════════════════════════════════════
# TIMETABLE GRID HELPERS
# ══════════════════════════════════════════════════════════════════════════════

DAY_BITS = {
    64: "Monday",
    32: "Tuesday",
    16: "Wednesday",
    8:  "Thursday",
    2:  "Saturday",
    1:  "Sunday",
}

DAY_ORDER = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]

SLOT_TO_TIME = {
    108: {"start": "9:00",  "end": "10:40"},
    132: {"start": "11:00", "end": "12:40"},
    156: {"start": "13:00", "end": "14:40"},
}

TIME_SLOTS = [
    {"start": "9:00",  "end": "10:40", "slot_id": 108},
    {"start": "11:00", "end": "12:40", "slot_id": 132},
    {"start": "13:00", "end": "14:40", "slot_id": 156},
]


# ══════════════════════════════════════════════════════════════════════════════
# COURSE COLOR MAP — module-level constant
# Fixed unique color index per course key (subject_coursenumber).
# Indices 0–19 map to .course-color-N CSS classes in style.css.
# ══════════════════════════════════════════════════════════════════════════════

COURSE_COLOR_MAP: Dict[str, int] = {
    # Add fixed color assignments here as needed: "SUBJ_COURSE": index (0-19)
}

# Set of all indices already claimed by the fixed map
_FIXED_COLOR_INDICES: set = set(COURSE_COLOR_MAP.values())


def parse_days_from_bits(day_bits) -> List[str]:
    if day_bits is None:
        return []
    try:
        day_bits = int(day_bits)
    except (ValueError, TypeError):
        return []
    return [name for bit, name in DAY_BITS.items() if day_bits & bit]


def get_slot_start(slot) -> Optional[str]:
    if slot is None:
        return None
    try:
        slot = int(slot)
    except (ValueError, TypeError):
        return None
    if slot in SLOT_TO_TIME:
        return SLOT_TO_TIME[slot]["start"]
    minutes = slot * 5
    return f"{minutes // 60}:{minutes % 60:02d}"


def slot_to_time_str(slot) -> str:
    if slot is None:
        return "TBA"
    try:
        slot = int(slot)
    except (ValueError, TypeError):
        return "TBA"
    if slot in SLOT_TO_TIME:
        t = SLOT_TO_TIME[slot]
        return f"{t['start']} - {t['end']}"
    minutes = slot * 5
    end = minutes + 80
    return f"{minutes // 60}:{minutes % 60:02d} - {end // 60}:{end % 60:02d}"


def prepare_timetable_data(schedule: List[Dict[str, Any]]) -> Dict:
    timetable_grid: Dict[str, list] = {}
    unique_courses: Dict[str, dict] = {}
    course_colors:  Dict[str, int]  = {}

    # Auto-increment starts after the highest fixed index
    # and never reuses a fixed index
    _next_auto = [max(_FIXED_COLOR_INDICES) + 1 if _FIXED_COLOR_INDICES else 0]

    def _get_auto_color() -> int:
        """Return next color index not already used by the fixed map."""
        idx = _next_auto[0]
        while idx in _FIXED_COLOR_INDICES:
            idx += 1
        _next_auto[0] = idx + 1
        return idx % 20   # CSS defines slots 0-19

    for item in schedule:
        subject       = item.get("subject", "")
        course_number = item.get("course_number", "")
        course_key    = f"{subject}_{course_number}"

        # ── Assign color once per course key ──────────────────────
        if course_key not in course_colors:
            if course_key in COURSE_COLOR_MAP:
                course_colors[course_key] = COURSE_COLOR_MAP[course_key]
            else:
                course_colors[course_key] = _get_auto_color()

        color_idx = course_colors[course_key]

        # ── Track unique courses (first occurrence wins) ───────────
        if course_key not in unique_courses:
            unique_courses[course_key] = {**item, "color_index": color_idx}

        # ── Build timetable grid ───────────────────────────────────
        assigned_days = item.get("assigned_days")
        assigned_slot = item.get("assigned_slot")

        if assigned_days is not None and assigned_slot is not None:
            days       = parse_days_from_bits(assigned_days)
            start_time = get_slot_start(assigned_slot)
            if start_time and days:
                enriched = {
                    **item,
                    "color_index":  color_idx,
                    "time_display": slot_to_time_str(assigned_slot),
                }
                for day in days:
                    grid_key = f"{day}_{start_time}"
                    timetable_grid.setdefault(grid_key, []).append(enriched)

    return {
        "time_slots":     TIME_SLOTS,
        "timetable_grid": timetable_grid,
        "unique_courses": sorted(
            unique_courses.values(),
            key=lambda c: (c.get("subject", ""), c.get("course_number", ""))
        ),
        "days": DAY_ORDER,
    }


# ══════════════════════════════════════════════════════════════════════════════
# XML ENRICHMENT HELPER
# ══════════════════════════════════════════════════════════════════════════════

def enrich_classes_from_xml(
    records: List[Dict],
    xml_sched: "XMLSchedule",
    room_map: Dict,
    id_field: str = "id",
) -> List[Dict]:
    if not xml_sched.is_loaded():
        return records

    enriched = []
    for rec in records:
        class_id = rec.get(id_field) or rec.get("class_id") or rec.get("id")
        if not class_id:
            enriched.append(rec)
            continue

        sol = xml_sched.get_class(int(class_id))
        r   = dict(rec)

        if sol is None:
            if r.get("assigned_days") is None:
                r["assigned_days"] = None
            if r.get("assigned_slot") is None:
                r["assigned_slot"] = None
            if r.get("assigned_building") is None:
                r["assigned_building"] = None
            if r.get("assigned_room_number") is None:
                r["assigned_room_number"] = None
            enriched.append(r)
            continue

        # DB assignment is authoritative; XML is fallback
        db_has_days = rec.get("assigned_days") is not None
        db_has_slot = rec.get("assigned_slot") is not None

        if db_has_days and db_has_slot:
            r["assigned_days"] = rec["assigned_days"]
            r["assigned_slot"] = rec["assigned_slot"]
        else:
            r["assigned_days"] = sol["days"]
            r["assigned_slot"] = sol["slot"]

        db_has_room = (
            rec.get("assigned_building") is not None
            or rec.get("assigned_room_number") is not None
        )

        if db_has_room:
            r["assigned_building"]    = rec.get("assigned_building")
            r["assigned_room_number"] = rec.get("assigned_room_number")
        else:
            room_info = room_map.get(sol["room_id"])
            if room_info:
                r["assigned_building"]    = room_info.get("building_abbr")
                r["assigned_room_number"] = room_info.get("room_number")
            else:
                r["assigned_building"]    = None
                r["assigned_room_number"] = f"Room {sol['room_id']}"

        enriched.append(r)
    return enriched


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS PIPELINE HELPER
# ══════════════════════════════════════════════════════════════════════════════

async def _run_analysis_pipeline(
    db: UniTimeDB,
    xml_sched: XMLSchedule,
    session_id: int,
) -> tuple:
    try:
        courses     = await db.get_courses(session_id=session_id)
        rooms       = await db.get_rooms(session_id=session_id)
        instructors = await db.get_instructors(session_id=session_id)
        classes     = await db.get_classes(session_id=session_id)

        constraint_report = ConstraintValidator().validate(
            courses=courses,
            rooms=rooms,
            instructors=instructors,
            classes=classes,
            session_id=session_id,
        )

        validation_report = TimetableValidator().validate(
            classes=classes,
            rooms=rooms,
            instructors=instructors,
            xml_schedule=xml_sched,
            session_id=session_id,
        )

        classification_report = ViolationClassifier().classify(
            violations=validation_report.violations,
            session_id=session_id,
        )

        suggestion_report = CorrectionSuggester().suggest(
            classified_violations=classification_report.classified,
            classes=classes,
            rooms=rooms,
            instructors=instructors,
            xml_schedule=xml_sched,
            session_id=session_id,
        )

        report = FullAnalysisReport(
            timestamp=datetime.now(),
            session_id=session_id,
            constraint_report=constraint_report,
            validation_report=validation_report,
            classification_report=classification_report,
            suggestion_report=suggestion_report,
        )
        return report, None

    except Exception as exc:
        return None, str(exc)


# ══════════════════════════════════════════════════════════════════════════════
# APP LIFESPAN
# ══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 55)
    print("Starting application...")

    client = UniTimeClient(
        base_url=settings.UNITIME_BASE_URL,
        username=settings.UNITIME_USERNAME,
        password=settings.UNITIME_PASSWORD,
    )
    health = await client.health_check()
    print(f"UniTime API: {'CONNECTED' if health['connected'] else 'NOT REACHABLE'}")
    app.state.unitime = client

    db = UniTimeDB()
    try:
        await db.connect()
        db_health = await db.health_check()
        print(f"UniTime DB:  {'CONNECTED' if db_health['connected'] else 'ERROR'}")
    except Exception as e:
        print(f"UniTime DB:  FAILED — {e}")
    app.state.db = db

    xml_sched = XMLSchedule("course-solution BEST.xml")
    xml_sched.load()
    app.state.xml_schedule = xml_sched

    try:
        db_rooms = await db.get_rooms(session_id=db.session_id)
        app.state.room_map = build_room_map(db_rooms)
        print(f"Room map:    {len(app.state.room_map)} rooms indexed")
    except Exception as e:
        print(f"Room map:    FAILED — {e}")
        app.state.room_map = {}

    try:
        with open("app/aast website/aast_homepage.html", "r", encoding="utf-8") as f:
            app.state.aast_homepage_html = f.read()
        print("AAST Homepage: LOADED")
    except Exception as e:
        print(f"AAST Homepage: FAILED — {e}")
        app.state.aast_homepage_html = None

    app.state.analysis_report = None
    app.state.analysis_error  = None

    print("=" * 55)
    yield

    await db.disconnect()


# ══════════════════════════════════════════════════════════════════════════════
# APP INIT
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/aast-assets", StaticFiles(directory="app/aast website"), name="aast-assets")
templates    = Jinja2Templates(directory="app/views")
auth_service = AuthService()


def require_login(request: Request):
    return get_session(request)


def require_role(request: Request, allowed_roles: set):
    session = get_session(request)
    if session and session.get("role") in allowed_roles:
        return session
    return None


async def get_active_session_id(db: UniTimeDB) -> int:
    return db.session_id


# ══════════════════════════════════════════════════════════════════════════════
# OTHER COLLEGES — static assets + page routes
# ══════════════════════════════════════════════════════════════════════════════

app.mount(
    "/college-assets",
    StaticFiles(directory="app/other_colleges_frontend/frontend/assets"),
    name="college-assets",
)


@app.get("/colleges/{college}")
async def college_page(request: Request, college: str):
    import os
    from fastapi.responses import FileResponse

    ALLOWED = {
        "engineering", "management", "pharmacy",
        "logistics", "dentistry", "medicine", "health",
    }
    if college not in ALLOWED:
        return RedirectResponse(url="/", status_code=303)

    file_path = os.path.join(
        "app", "other_colleges_frontend", "frontend", "pages", f"{college}.html"
    )
    if not os.path.isfile(file_path):
        return RedirectResponse(url="/", status_code=303)

    return FileResponse(file_path, media_type="text/html")


# ══════════════════════════════════════════════════════════════════════════════
# AAST HOMEPAGE
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def aast_homepage(request: Request):
    html = request.app.state.aast_homepage_html
    if html:
        return HTMLResponse(content=html)
    return RedirectResponse(url="/portal", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# ROLE SELECTION
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/portal")
async def role_selection(request: Request):
    session = get_session(request)
    if session:
        return RedirectResponse(url=f"/dashboard/{session['role']}", status_code=303)
    vm = AuthViewModel()
    return templates.TemplateResponse("role_select.html", {
        "request": request,
        "roles":   vm.get_roles(),
        "user":    None,
        "title":   "Select Your Role",
    })


# ══════════════════════════════════════════════════════════════════════════════
# AUTH — LOGIN
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/login/{role_id}")
async def login_page(request: Request, role_id: str,
                     error: str = None, success: str = None):
    if role_id not in VALID_ROLES:
        return RedirectResponse(url="/portal", status_code=303)
    session = get_session(request)
    if session:
        return RedirectResponse(url=f"/dashboard/{session['role']}", status_code=303)
    return templates.TemplateResponse("login.html", {
        "request":    request,
        "role_title": ROLE_TITLES.get(role_id, "User"),
        "role_id":    role_id,
        "error":      error,
        "success":    success,
        "user":       None,
        "title":      f"Login — {ROLE_TITLES.get(role_id)}",
    })


@app.post("/login/{role_id}")
async def login_submit(request: Request, role_id: str,
                       email: str = Form(...), password: str = Form(...)):
    if role_id not in VALID_ROLES:
        return RedirectResponse(url="/portal", status_code=303)

    result = auth_service.sign_in(email, password)
    if not result["success"]:
        return templates.TemplateResponse("login.html", {
            "request":    request,
            "role_title": ROLE_TITLES.get(role_id),
            "role_id":    role_id,
            "error":      result["error"],
            "success":    None,
            "user":       None,
            "title":      f"Login — {ROLE_TITLES.get(role_id)}",
        })

    user_data   = result["user"]
    stored_role = user_data.get("role", "student")
    if stored_role != role_id:
        return templates.TemplateResponse("login.html", {
            "request":    request,
            "role_title": ROLE_TITLES.get(role_id),
            "role_id":    role_id,
            "error":      (
                f"This account is registered as "
                f"'{ROLE_TITLES.get(stored_role, stored_role)}'. "
                f"Please select the correct role."
            ),
            "success":    None,
            "user":       None,
            "title":      f"Login — {ROLE_TITLES.get(role_id)}",
        })

    session_data = {
        "id":           user_data["id"],
        "email":        user_data["email"],
        "full_name":    user_data["full_name"],
        "role":         stored_role,
        "access_token": result.get("access_token", ""),
    }
    response = RedirectResponse(url=f"/dashboard/{stored_role}", status_code=303)
    create_session(response, session_data)
    return response


# ══════════════════════════════════════════════════════════════════════════════
# AUTH — SIGNUP
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/signup/{role_id}")
async def signup_page(request: Request, role_id: str, error: str = None):
    if role_id not in VALID_ROLES:
        return RedirectResponse(url="/portal", status_code=303)
    session = get_session(request)
    if session:
        return RedirectResponse(url=f"/dashboard/{session['role']}", status_code=303)
    return templates.TemplateResponse("signup.html", {
        "request":    request,
        "role_title": ROLE_TITLES.get(role_id),
        "role_id":    role_id,
        "error":      error,
        "user":       None,
        "title":      f"Sign Up — {ROLE_TITLES.get(role_id)}",
    })


@app.post("/signup/{role_id}")
async def signup_submit(
    request: Request, role_id: str,
    email: str = Form(...), password: str = Form(...),
    confirm_password: str = Form(...), full_name: str = Form(""),
    first_name: str = Form(""), last_name: str = Form(""),
    student_id: str = Form(""), department: str = Form(""),
    academic_year: str = Form(""), employee_id: str = Form(""),
    position: str = Form(""), academic_position: str = Form(""),
):
    if role_id not in VALID_ROLES:
        return RedirectResponse(url="/portal", status_code=303)
    role_title = ROLE_TITLES.get(role_id)

    if not full_name.strip() and first_name.strip():
        full_name = f"{first_name.strip()} {last_name.strip()}".strip()
    if not full_name.strip():
        full_name = "User"

    if password != confirm_password:
        return templates.TemplateResponse("signup.html", {
            "request": request, "role_title": role_title, "role_id": role_id,
            "error": "Passwords do not match.", "user": None,
            "title": f"Sign Up — {role_title}",
        })
    if len(password) < 6:
        return templates.TemplateResponse("signup.html", {
            "request": request, "role_title": role_title, "role_id": role_id,
            "error": "Password must be at least 6 characters.", "user": None,
            "title": f"Sign Up — {role_title}",
        })

    profile_data = {
        "full_name":         full_name.strip(),
        "first_name":        first_name.strip(),
        "last_name":         last_name.strip(),
        "student_id":        student_id.strip(),
        "department":        department.strip(),
        "academic_year":     int(academic_year) if academic_year else None,
        "employee_id":       employee_id.strip(),
        "position":          position.strip(),
        "academic_position": academic_position.strip(),
    }
    result = auth_service.sign_up(email, password, role_id, profile_data)
    if not result["success"]:
        return templates.TemplateResponse("signup.html", {
            "request": request, "role_title": role_title, "role_id": role_id,
            "error": result["error"], "user": None,
            "title": f"Sign Up — {role_title}",
        })
    return RedirectResponse(
        url=f"/login/{role_id}?success=Account+created+successfully.+Please+sign+in.",
        status_code=303,
    )


# ══════════════════════════════════════════════════════════════════════════════
# AUTH — LOGOUT
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/logout")
async def logout(request: Request):
    session = get_session(request)
    if session:
        auth_service.sign_out(session.get("access_token", ""))
    response = RedirectResponse(url="/", status_code=303)
    clear_session(response)
    return response


# ══════════════════════════════════════════════════════════════════════════════
# STUDENT DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/dashboard/student")
async def student_dashboard(request: Request):
    session = require_role(request, {"student"})
    if not session:
        return RedirectResponse(url="/login/student", status_code=303)

    user       = session_to_user_context(session)
    db         = request.app.state.db
    xml_sched  = request.app.state.xml_schedule
    room_map   = request.app.state.room_map
    session_id = await get_active_session_id(db)
    profile    = auth_service.get_profile(session["id"])

    schedule    = []
    db_students = await db.get_students(session_id)
    student_record = next(
        (s for s in db_students
         if s.get("email", "").lower() == session["email"].lower()),
        None,
    )

    if student_record:
        student_db_id = int(student_record["id"])

        if xml_sched.is_loaded():
            xml_class_ids = xml_sched.get_student_class_ids(student_db_id)
            raw_schedule  = await db.get_student_schedule(student_db_id)

            if xml_class_ids:
                filtered = [
                    r for r in raw_schedule
                    if int(r.get("class_id", 0)) in xml_class_ids
                ]
                schedule = enrich_classes_from_xml(
                    filtered, xml_sched, room_map, id_field="class_id"
                )
            else:
                schedule = enrich_classes_from_xml(
                    raw_schedule, xml_sched, room_map, id_field="class_id"
                )
        else:
            schedule = await db.get_student_schedule(student_db_id)

    timetable_data = prepare_timetable_data(schedule)

    return templates.TemplateResponse("dashboard_student.html", {
        "request":        request,
        "user":           user,
        "profile":        profile or {},
        "schedule":       schedule,
        "time_slots":     timetable_data["time_slots"],
        "timetable_grid": timetable_data["timetable_grid"],
        "unique_courses": timetable_data["unique_courses"],
        "days":           timetable_data["days"],
        "title":          "My Schedule",
        "role_title":     "Student",
        "flash_success":  request.query_params.get("success"),
        "flash_error":    request.query_params.get("error"),
    })


# ══════════════════════════════════════════════════════════════════════════════
# STAFF DASHBOARDS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/dashboard/ta")
async def ta_dashboard(request: Request):
    return await _staff_dashboard(request, "ta")


@app.get("/dashboard/lecturer")
async def lecturer_dashboard(request: Request):
    return await _staff_dashboard(request, "lecturer")


async def _staff_dashboard(request: Request, role: str):
    session = require_role(request, {role})
    if not session:
        return RedirectResponse(url=f"/login/{role}", status_code=303)

    user       = session_to_user_context(session)
    db         = request.app.state.db
    xml_sched  = request.app.state.xml_schedule
    room_map   = request.app.state.room_map
    session_id = await get_active_session_id(db)
    profile    = auth_service.get_profile(session["id"])

    classes  = []
    students = []


    instructor = await _find_instructor_by_email(db, session_id, session["email"])

    if instructor:
        instructor_db_id = int(instructor["id"])

        if xml_sched.is_loaded():
            xml_class_ids = xml_sched.get_instructor_class_ids(instructor_db_id)
            raw_classes   = await db.get_classes(
                session_id=session_id, instructor_id=instructor_db_id
            )

            if xml_class_ids:
                filtered = [
                    c for c in raw_classes
                    if int(c.get("id", 0)) in xml_class_ids
                ]
                classes = enrich_classes_from_xml(
                    filtered, xml_sched, room_map, id_field="id"
                )
            else:
                classes = enrich_classes_from_xml(
                    raw_classes, xml_sched, room_map, id_field="id"
                )
        else:
            classes = await db.get_classes(
                session_id=session_id, instructor_id=instructor_db_id
            )

        for c in classes:
            try:
                course = await db._fetch_one(
                    "SELECT co.uniqueid "
                    "FROM   course_offering co "
                    "JOIN   instructional_offering io  ON co.instr_offr_id  = io.uniqueid "
                    "JOIN   instr_offering_config  ioc ON ioc.instr_offr_id = io.uniqueid "
                    "JOIN   scheduling_subpart     sp  ON sp.config_id      = ioc.uniqueid "
                    "JOIN   class_                 cl  ON cl.subpart_id     = sp.uniqueid "
                    "WHERE  cl.uniqueid = %s AND co.is_control = 1",
                    (c["id"],),
                )
                if course:
                    enrolled = await db.get_enrollments_by_course(course["uniqueid"])
                    students.extend(enrolled)
            except Exception:
                pass

    timetable_data = prepare_timetable_data(classes)

    return templates.TemplateResponse("dashboard_staff.html", {
        "request":        request,
        "user":           user,
        "profile":        profile or {},
        "classes":        classes,
        "students":       students,
        "time_slots":     timetable_data["time_slots"],
        "timetable_grid": timetable_data["timetable_grid"],
        "unique_courses": timetable_data["unique_courses"],
        "days":           timetable_data["days"],
        "title":          f"{ROLE_TITLES.get(role)} Dashboard",
        "role_title":     ROLE_TITLES.get(role),
        "flash_success":  request.query_params.get("success"),
        "flash_error":    request.query_params.get("error"),
    })


async def _find_instructor_by_email(
    db: UniTimeDB, session_id: int, email: str
) -> Optional[dict]:
    instructors = await db.get_instructors(session_id=session_id)
    for i in instructors:
        if i.get("email") and i["email"].lower() == email.lower():
            return i
    return None


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN DASHBOARDS
# ══════════════════════════════════════════════════════════════════════════════

ADMIN_ROLES = {"scheduler", "dean", "vicedean"}


@app.get("/dashboard/vicedean")
async def vicedean_dashboard(request: Request):
    return await _admin_dashboard(request, "vicedean")


@app.get("/dashboard/dean")
async def dean_dashboard(request: Request):
    return await _admin_dashboard(request, "dean")


@app.get("/dashboard/scheduler")
async def scheduler_dashboard(request: Request):
    return await _admin_dashboard(request, "scheduler")


async def _admin_dashboard(request: Request, role: str):
    session = require_role(request, {role})
    if not session:
        return RedirectResponse(url=f"/login/{role}", status_code=303)

    user       = session_to_user_context(session)
    db         = request.app.state.db
    session_id = await get_active_session_id(db)
    profile    = auth_service.get_profile(session["id"])
    user_stats = auth_service.get_user_stats()

    stats          = await db.get_stats(session_id)
    instructors    = await db.get_instructors(session_id=session_id)
    courses        = await db.get_courses(session_id=session_id)
    rooms          = await db.get_rooms(session_id=session_id)
    classes        = await db.get_classes(session_id=session_id)
    departments    = await db.get_departments(session_id=session_id)
    subject_areas  = await db.get_subject_areas(session_id=session_id)
    position_types = await db.get_position_types()
    buildings      = await db.get_buildings(session_id=session_id)
    room_types     = await db.get_room_types()
    time_patterns  = await db.get_time_patterns(session_id=session_id)

    students = []
    if can_read(role, "students"):
        students = await db.get_students(session_id=session_id)

    permissions = ROLE_PERMISSIONS.get(role, {})

    xml_sched = request.app.state.xml_schedule
    room_map  = request.app.state.room_map
    if xml_sched.is_loaded():
        classes = enrich_classes_from_xml(classes, xml_sched, room_map, id_field="id")

    ctx = {
        "request":               request,
        "user":                  user,
        "profile":               profile or {},
        "user_stats":            user_stats,
        "title":                 ROLE_TITLES.get(role),
        "role_title":            ROLE_TITLES.get(role),
        "session_id":            session_id,
        "stats":                 stats,
        "instructors":           instructors,
        "courses":               courses,
        "rooms":                 rooms,
        "classes":               classes,
        "students":              students,
        "departments":           departments,
        "subject_areas":         subject_areas,
        "position_types":        position_types,
        "buildings":             buildings,
        "room_types":            room_types,
        "time_patterns":         time_patterns,
        "permissions":           permissions,
        "can_write_instructors": can_write(role, "instructors"),
        "can_write_courses":     can_write(role, "courses"),
        "can_write_rooms":       can_write(role, "rooms"),
        "can_write_classes":     can_write(role, "classes"),
        "can_view_students":     can_read(role,  "students"),
        "can_write_students":    can_write(role, "students"),
        "show_analysis":         False,
        "analysis_report":       None,
        "analysis_error":        None,
        "flash_success":         request.query_params.get("success"),
        "flash_error":           request.query_params.get("error"),
    }

    if role == "scheduler":
        return templates.TemplateResponse("dashboard_scheduler.html", ctx)
    return templates.TemplateResponse("dashboard_admin.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS — MULTI-AGENT SCHEDULE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

async def _analysis_base_context(request: Request, role: str) -> dict:
    session    = get_session(request)
    user       = session_to_user_context(session)
    db         = request.app.state.db
    session_id = await get_active_session_id(db)
    profile    = auth_service.get_profile(session["id"])
    user_stats = auth_service.get_user_stats()

    stats          = await db.get_stats(session_id)
    instructors    = await db.get_instructors(session_id=session_id)
    courses        = await db.get_courses(session_id=session_id)
    rooms          = await db.get_rooms(session_id=session_id)
    classes        = await db.get_classes(session_id=session_id)
    departments    = await db.get_departments(session_id=session_id)
    subject_areas  = await db.get_subject_areas(session_id=session_id)
    position_types = await db.get_position_types()
    buildings      = await db.get_buildings(session_id=session_id)
    room_types     = await db.get_room_types()
    time_patterns  = await db.get_time_patterns(session_id=session_id)

    xml_sched = request.app.state.xml_schedule
    room_map  = request.app.state.room_map
    if xml_sched.is_loaded():
        classes = enrich_classes_from_xml(classes, xml_sched, room_map, id_field="id")

    students = []
    if can_read(role, "students"):
        students = await db.get_students(session_id=session_id)

    permissions = ROLE_PERMISSIONS.get(role, {})

    return {
        "request":               request,
        "user":                  user,
        "profile":               profile or {},
        "user_stats":            user_stats,
        "title":                 f"Schedule Analysis — {ROLE_TITLES.get(role)}",
        "role_title":            ROLE_TITLES.get(role),
        "session_id":            session_id,
        "stats":                 stats,
        "instructors":           instructors,
        "courses":               courses,
        "rooms":                 rooms,
        "classes":               classes,
        "students":              students,
        "departments":           departments,
        "subject_areas":         subject_areas,
        "position_types":        position_types,
        "buildings":             buildings,
        "room_types":            room_types,
        "time_patterns":         time_patterns,
        "permissions":           permissions,
        "can_write_instructors": can_write(role, "instructors"),
        "can_write_courses":     can_write(role, "courses"),
        "can_write_rooms":       can_write(role, "rooms"),
        "can_write_classes":     can_write(role, "classes"),
        "can_view_students":     can_read(role,  "students"),
        "can_write_students":    can_write(role, "students"),
        "show_analysis":         True,
    }


@app.get("/admin/analysis")
async def analysis_page(request: Request):
    session = require_role(request, ADMIN_ROLES)
    if not session:
        return RedirectResponse(url="/portal", status_code=303)

    role      = session["role"]
    db        = request.app.state.db
    xml_sched = request.app.state.xml_schedule

    if request.app.state.analysis_report is None:
        session_id    = await get_active_session_id(db)
        report, error = await _run_analysis_pipeline(db, xml_sched, session_id)
        request.app.state.analysis_report = report
        request.app.state.analysis_error  = error

    ctx = await _analysis_base_context(request, role)
    ctx["analysis_report"] = request.app.state.analysis_report
    ctx["analysis_error"]  = request.app.state.analysis_error

    template_name = (
        "dashboard_scheduler.html" if role == "scheduler" else "dashboard_admin.html"
    )
    return templates.TemplateResponse(template_name, ctx)


@app.post("/admin/analysis/refresh")
async def analysis_refresh(request: Request):
    session = require_role(request, ADMIN_ROLES)
    if not session:
        return RedirectResponse(url="/portal", status_code=303)

    db         = request.app.state.db
    xml_sched  = request.app.state.xml_schedule
    session_id = await get_active_session_id(db)

    report, error = await _run_analysis_pipeline(db, xml_sched, session_id)
    request.app.state.analysis_report = report
    request.app.state.analysis_error  = error

    return RedirectResponse(url="/admin/analysis", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# MANAGE — INSTRUCTORS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/manage/instructor/add")
async def add_instructor(
    request: Request, session_id: int = Form(...),
    first_name: str = Form(...), last_name: str = Form(...),
    middle_name: str = Form(""), email: str = Form(""),
    external_id: str = Form(""), department_id: int = Form(...),
    position_type_id: str = Form(""),
):
    session = require_role(request, {"scheduler", "dean", "vicedean"})
    if not session or not can_write(session["role"], "instructors"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    await db.add_instructor({
        "first_name":       first_name.strip(),
        "last_name":        last_name.strip(),
        "middle_name":      middle_name.strip(),
        "email":            email.strip(),
        "external_id":      external_id.strip(),
        "department_id":    department_id,
        "position_type_id": int(position_type_id) if position_type_id else None,
    })
    return RedirectResponse(
        url=f"/dashboard/{session['role']}?success=Instructor+added", status_code=303
    )


@app.get("/manage/instructor/edit/{instructor_id}")
async def edit_instructor_page(request: Request, instructor_id: int):
    session = require_role(request, {"scheduler", "dean", "vicedean"})
    if not session or not can_write(session["role"], "instructors"):
        return RedirectResponse(url="/portal", status_code=303)
    user          = session_to_user_context(session)
    db: UniTimeDB = request.app.state.db
    instructor    = await db.get_instructor_by_id(instructor_id)
    if not instructor:
        return RedirectResponse(url=f"/dashboard/{session['role']}", status_code=303)
    session_id     = await get_active_session_id(db)
    departments    = await db.get_departments(session_id=session_id)
    position_types = await db.get_position_types()
    return templates.TemplateResponse("manage_edit_instructor.html", {
        "request": request, "user": user, "instructor": instructor,
        "departments": departments, "position_types": position_types,
        "session_id": session_id, "title": "Edit Instructor",
        "role_title": ROLE_TITLES.get(session["role"]),
    })


@app.post("/manage/instructor/edit/{instructor_id}")
async def edit_instructor_submit(
    request: Request, instructor_id: int,
    session_id: int = Form(...),
    first_name: str = Form(...), last_name: str = Form(...),
    middle_name: str = Form(""), email: str = Form(""),
    external_id: str = Form(""), department_id: int = Form(...),
    position_type_id: str = Form(""),
):
    session = require_role(request, {"scheduler", "dean", "vicedean"})
    if not session or not can_write(session["role"], "instructors"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    await db.update_instructor(instructor_id, {
        "first_name":       first_name.strip(),
        "last_name":        last_name.strip(),
        "middle_name":      middle_name.strip(),
        "email":            email.strip(),
        "external_id":      external_id.strip(),
        "department_id":    department_id,
        "position_type_id": int(position_type_id) if position_type_id else None,
    })
    return RedirectResponse(
        url=f"/dashboard/{session['role']}?success=Instructor+updated", status_code=303
    )


@app.post("/manage/instructor/delete/{instructor_id}")
async def delete_instructor(
    request: Request, instructor_id: int, session_id: int = Form(...)
):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "instructors"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    await db.delete_instructor(instructor_id)
    return RedirectResponse(
        url=f"/dashboard/{session['role']}?success=Instructor+deleted", status_code=303
    )


# ══════════════════════════════════════════════════════════════════════════════
# MANAGE — COURSES
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/manage/course/add")
async def add_course(
    request: Request, session_id: int = Form(...),
    subject_area_id: str = Form(""), course_number: str = Form(...),
    title: str = Form(...), expected_students: int = Form(30),
):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "courses"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    sa_id = int(subject_area_id) if subject_area_id else None
    if not sa_id:
        sas   = await db.get_subject_areas(session_id=session_id)
        sa_id = sas[0]["id"] if sas else None
        if not sa_id:
            return RedirectResponse(
                url=f"/dashboard/{session['role']}?error=No+subject+areas",
                status_code=303,
            )
    await db.add_course({
        "session_id":        session_id,
        "subject_area_id":   sa_id,
        "course_number":     course_number.strip(),
        "title":             title.strip(),
        "expected_students": expected_students,
        "projected_demand":  expected_students,
    })
    return RedirectResponse(
        url=f"/dashboard/{session['role']}?success=Course+added", status_code=303
    )


@app.get("/manage/course/edit/{course_id}")
async def edit_course_page(request: Request, course_id: int):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "courses"):
        return RedirectResponse(url="/portal", status_code=303)
    user          = session_to_user_context(session)
    db: UniTimeDB = request.app.state.db
    course        = await db.get_course_by_id(course_id)
    if not course:
        return RedirectResponse(url=f"/dashboard/{session['role']}", status_code=303)
    session_id = await get_active_session_id(db)
    return templates.TemplateResponse("manage_edit_course.html", {
        "request": request, "user": user, "course": course,
        "session_id": session_id, "title": "Edit Course",
        "role_title": ROLE_TITLES.get(session["role"]),
    })


@app.post("/manage/course/edit/{course_id}")
async def edit_course_submit(
    request: Request, course_id: int,
    session_id: int = Form(...),
    course_number: str = Form(...),
    title: str = Form(...),
    expected_students: int = Form(0),
):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "courses"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    await db.update_course(course_id, {
        "course_number":     course_number.strip(),
        "title":             title.strip(),
        "expected_students": expected_students,
    })
    return RedirectResponse(
        url=f"/dashboard/{session['role']}?success=Course+updated", status_code=303
    )


@app.post("/manage/course/delete/{course_id}")
async def delete_course(
    request: Request, course_id: int, session_id: int = Form(...)
):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "courses"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    await db.delete_course(course_id)
    return RedirectResponse(
        url=f"/dashboard/{session['role']}?success=Course+deleted", status_code=303
    )


# ══════════════════════════════════════════════════════════════════════════════
# MANAGE — ROOMS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/manage/room/add")
async def add_room(
    request: Request, session_id: int = Form(...),
    building_id: int = Form(...), room_number: str = Form(...),
    capacity: int = Form(30), exam_capacity: int = Form(20),
    room_type_id: int = Form(...),
):
    session = require_role(request, {"scheduler", "dean", "vicedean"})
    if not session or not can_write(session["role"], "rooms"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    try:
        await db.add_room({
            "building_id":   building_id,
            "room_number":   room_number.strip(),
            "capacity":      capacity,
            "exam_capacity": exam_capacity,
            "room_type_id":  room_type_id,
        })
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?success=Room+added", status_code=303
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?error={str(e)}", status_code=303
        )


@app.get("/manage/room/edit/{room_id}")
async def edit_room_page(request: Request, room_id: int):
    session = require_role(request, {"scheduler", "dean", "vicedean"})
    if not session or not can_write(session["role"], "rooms"):
        return RedirectResponse(url="/portal", status_code=303)
    user          = session_to_user_context(session)
    db: UniTimeDB = request.app.state.db
    room          = await db.get_room_by_id(room_id)
    if not room:
        return RedirectResponse(url=f"/dashboard/{session['role']}", status_code=303)
    session_id = await get_active_session_id(db)
    return templates.TemplateResponse("manage_edit_room.html", {
        "request": request, "user": user, "room": room,
        "session_id": session_id, "title": "Edit Room",
        "role_title": ROLE_TITLES.get(session["role"]),
    })


@app.post("/manage/room/edit/{room_id}")
async def edit_room_submit(
    request: Request, room_id: int,
    session_id: int = Form(...),
    capacity: int = Form(...),
    exam_capacity: int = Form(...),
):
    session = require_role(request, {"scheduler", "dean", "vicedean"})
    if not session or not can_write(session["role"], "rooms"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    await db.update_room(room_id, capacity=capacity, exam_capacity=exam_capacity)
    return RedirectResponse(
        url=f"/dashboard/{session['role']}?success=Room+updated", status_code=303
    )


@app.post("/manage/room/delete/{room_id}")
async def delete_room(
    request: Request, room_id: int, session_id: int = Form(...)
):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "rooms"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    try:
        await db.delete_room(room_id)
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?success=Room+deleted", status_code=303
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?error={str(e)}", status_code=303
        )


# ══════════════════════════════════════════════════════════════════════════════
# MANAGE — CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/manage/class/add")
async def add_class(
    request: Request, session_id: int = Form(...),
    course_id: int = Form(...), expected_capacity: int = Form(30),
    instructor_id: str = Form(""), room_id: str = Form(""),
    time_pattern_id: str = Form(""),
):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "classes"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    try:
        course = await db.get_course_by_id(course_id)
        if not course:
            return RedirectResponse(
                url=f"/dashboard/{session['role']}?error=Course+not+found",
                status_code=303,
            )
        configs = await db.get_configs_for_offering(course["offering_id"])
        if not configs:
            return RedirectResponse(
                url=f"/dashboard/{session['role']}?error=No+config+found",
                status_code=303,
            )
        subparts = await db.get_subparts_for_config(configs[0]["id"])
        if not subparts:
            return RedirectResponse(
                url=f"/dashboard/{session['role']}?error=No+subpart+found",
                status_code=303,
            )
        subpart_id  = subparts[0]["id"]
        existing    = await db._fetch_all(
            "SELECT COUNT(*) as cnt FROM class_ WHERE subpart_id = %s",
            (subpart_id,),
        )
        section_num = (existing[0]["cnt"] if existing else 0) + 1
        cls_id      = db._next_id()

        async def _do(cur):
            await cur.execute(
                "INSERT INTO class_ "
                "(uniqueid, subpart_id, expected_capacity, nbr_rooms, "
                " date_pattern_id, class_suffix, section_number, "
                " display_instructor, display_in_sched_book, cancelled, managing_dept) "
                "VALUES (%s,%s,%s,1,%s,%s,%s,1,1,0,%s)",
                (
                    cls_id, subpart_id, expected_capacity, db.date_pattern_id,
                    str(section_num), section_num, db.department_id,
                ),
            )
            if instructor_id:
                ci_id = db._next_id()
                await cur.execute(
                    "INSERT INTO class_instructor "
                    "(uniqueid, class_id, instructor_id, percent_share, is_lead) "
                    "VALUES (%s,%s,%s,100,1)",
                    (ci_id, cls_id, int(instructor_id)),
                )
            if room_id and time_pattern_id:
                a_id = db._next_id()
                await cur.execute(
                    "INSERT INTO assignment "
                    "(uniqueid, class_id, days, slot, date_pattern_id, time_pattern_id) "
                    "VALUES (%s,%s,0,0,%s,%s)",
                    (a_id, cls_id, db.date_pattern_id, int(time_pattern_id)),
                )
                ar_id = db._next_id()
                await cur.execute(
                    "INSERT INTO assigned_rooms (uniqueid, assignment_id, room_id) "
                    "VALUES (%s,%s,%s)",
                    (ar_id, a_id, int(room_id)),
                )
            return cls_id

        result = await db._write_transaction(_do)
        if not result["success"]:
            raise Exception(result.get("error"))
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?success=Class+added", status_code=303
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?error={str(e)}", status_code=303
        )


@app.post("/manage/class/delete/{class_id}")
async def delete_class(
    request: Request, class_id: int, session_id: int = Form(...)
):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "classes"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    await db.delete_class(class_id)
    return RedirectResponse(
        url=f"/dashboard/{session['role']}?success=Class+deleted", status_code=303
    )


# ══════════════════════════════════════════════════════════════════════════════
# MANAGE — STUDENTS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/manage/student/add")
async def add_student(
    request: Request, session_id: int = Form(...),
    first_name: str = Form(...), last_name: str = Form(...),
    middle_name: str = Form(""), email: str = Form(""),
    external_id: str = Form(""),
):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "students"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    try:
        await db.add_student({
            "first_name":  first_name.strip(),
            "last_name":   last_name.strip(),
            "middle_name": middle_name.strip(),
            "email":       email.strip(),
            "external_id": external_id.strip(),
        })
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?success=Student+added", status_code=303
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?error={str(e)}", status_code=303
        )


@app.get("/manage/student/edit/{student_id}")
async def edit_student_page(request: Request, student_id: int):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "students"):
        return RedirectResponse(url="/portal", status_code=303)
    user          = session_to_user_context(session)
    db: UniTimeDB = request.app.state.db
    student       = await db.get_student_by_id(student_id)
    if not student:
        return RedirectResponse(url=f"/dashboard/{session['role']}", status_code=303)
    session_id = await get_active_session_id(db)
    return templates.TemplateResponse("manage_edit_student.html", {
        "request": request, "user": user, "student": student,
        "session_id": session_id, "title": "Edit Student",
        "role_title": ROLE_TITLES.get(session["role"]),
    })


@app.post("/manage/student/edit/{student_id}")
async def edit_student_submit(
    request: Request, student_id: int,
    session_id: int = Form(...),
    first_name: str = Form(...), last_name: str = Form(...),
    middle_name: str = Form(""), email: str = Form(""),
    external_id: str = Form(""),
):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "students"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    await db.update_student(student_id, {
        "first_name":  first_name.strip(),
        "last_name":   last_name.strip(),
        "middle_name": middle_name.strip(),
        "email":       email.strip(),
        "external_id": external_id.strip(),
    })
    return RedirectResponse(
        url=f"/dashboard/{session['role']}?success=Student+updated", status_code=303
    )


@app.post("/manage/student/delete/{student_id}")
async def delete_student(
    request: Request, student_id: int, session_id: int = Form(...)
):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "students"):
        return RedirectResponse(url="/portal", status_code=303)
    db: UniTimeDB = request.app.state.db
    await db.delete_student(student_id)
    return RedirectResponse(
        url=f"/dashboard/{session['role']}?success=Student+deleted", status_code=303
    )


# ══════════════════════════════════════════════════════════════════════════════
# BULK UPLOAD
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/manage/bulk-upload")
async def bulk_upload(
    request: Request,
    upload_type: str = Form(...),
    csv_file: UploadFile = File(...),
):
    session = require_role(request, {"scheduler", "dean"})
    if not session or not can_write(session["role"], "courses"):
        return RedirectResponse(url="/portal", status_code=303)

    db: UniTimeDB = request.app.state.db
    uploader      = BulkUploader(db)

    try:
        raw     = await csv_file.read()
        content = raw.decode("utf-8-sig")
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?error=Could+not+read+file:+{str(e)}",
            status_code=303,
        )

    handlers = {
        "courses":     uploader.upload_courses,
        "instructors": uploader.upload_instructors,
        "students":    uploader.upload_students,
        "rooms":       uploader.upload_rooms,
    }

    handler = handlers.get(upload_type)
    if not handler:
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?error=Invalid+upload+type:+{upload_type}",
            status_code=303,
        )

    result = await handler(content)

    if result.success:
        msg = f"Bulk+upload+complete:+{result.summary}"
        if result.warnings:
            msg += f"+({len(result.warnings)}+warnings)"
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?success={msg}", status_code=303
        )
    else:
        err = result.errors[0] if result.errors else "Unknown+error"
        return RedirectResponse(
            url=f"/dashboard/{session['role']}?error=Upload+failed:+{err}",
            status_code=303,
        )


# ══════════════════════════════════════════════════════════════════════════════
# JSON API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/stats")
async def api_stats(request: Request):
    session = require_role(request, {"scheduler", "dean", "vicedean"})
    if not session:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    db: UniTimeDB = request.app.state.db
    session_id    = await get_active_session_id(db)
    stats         = await db.get_stats(session_id)
    return JSONResponse({"success": True, "data": stats})


@app.get("/api/user-stats")
async def api_user_stats(request: Request):
    session = require_role(request, {"scheduler", "dean", "vicedean"})
    if not session:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    stats = auth_service.get_user_stats()
    return JSONResponse({"success": True, "data": stats})


@app.get("/api/xml-status")
async def api_xml_status(request: Request):
    xml_sched: XMLSchedule = request.app.state.xml_schedule
    return JSONResponse({
        "loaded":             xml_sched.is_loaded(),
        "assigned_classes":   len(xml_sched.class_solutions),
        "students_mapped":    len(xml_sched.student_classes),
        "instructors_mapped": len(xml_sched.instructor_classes),
        "path":               xml_sched.path,
    })


# ══════════════════════════════════════════════════════════════════════════════
# CHAT API — Groq-powered schedule assistant
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/chat")
async def api_chat(request: Request):
    session = get_session(request)
    if not session:
        return JSONResponse(
            {"error": "Please log in to use the chat assistant."},
            status_code=401,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid request body."}, status_code=400)

    user_message = (body.get("message") or "").strip()
    if not user_message:
        return JSONResponse({"error": "Empty message."}, status_code=400)

    history    = body.get("history") or []
    role       = session.get("role", "student")
    db         = request.app.state.db
    xml_sched  = request.app.state.xml_schedule
    room_map   = request.app.state.room_map
    session_id = await get_active_session_id(db)

    try:
        chatbot = ScheduleChatbot()
        reply   = await chatbot.chat(
            user_message=user_message,
            role=role,
            session_data=session,
            db=db,
            xml_sched=xml_sched,
            room_map=room_map,
            session_id=session_id,
            history=history,
        )
        return JSONResponse({"reply": reply})
    except ValueError as ve:
        return JSONResponse(
            {"error": f"Chat service configuration error: {str(ve)}"},
            status_code=500,
        )
    except Exception as e:
        return JSONResponse({"error": f"Chat error: {str(e)}"}, status_code=500)


# ══════════════════════════════════════════════════════════════════════════════
# UNITIME REST API PROXY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/unitime/health")
async def unitime_health(request: Request):
    client: UniTimeClient = request.app.state.unitime
    return JSONResponse(await client.health_check())


@app.get("/api/unitime/sessions")
async def unitime_sessions(request: Request):
    client: UniTimeClient = request.app.state.unitime
    try:
        data = await client.get_sessions()
        return JSONResponse({"success": True, "count": len(data), "data": data})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/unitime/rooms")
async def unitime_rooms(request: Request, term: str = None):
    client: UniTimeClient = request.app.state.unitime
    api_term = term or settings.UNITIME_API_TERM
    try:
        raw        = await client.get_rooms(term=api_term)
        simplified = [{
            "id":            r.get("uniqueId"),
            "name":          r.get("name"),
            "full_name":     f"{r.get('building',{}).get('abbreviation','')} {r.get('name','')}".strip(),
            "capacity":      r.get("capacity"),
            "exam_capacity": r.get("examCapacity"),
            "type":          r.get("roomType", {}).get("label", "Unknown"),
            "features":      [f.get("label") for f in r.get("features", [])],
        } for r in raw]
        return JSONResponse({
            "success": True, "term": api_term,
            "count": len(raw), "rooms": simplified,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/unitime/instructors")
async def unitime_instructors(request: Request, term: str = None):
    client: UniTimeClient = request.app.state.unitime
    api_term  = term or settings.UNITIME_API_TERM
    try:
        raw       = await client.get_instructors(term=api_term)
        all_instr = []
        for dept in raw:
            for i in dept.get("instructors", []):
                parts = [
                    i.get("firstName", ""),
                    i.get("middleName", ""),
                    i.get("lastName", ""),
                ]
                all_instr.append({
                    "id":         i.get("instructorId"),
                    "name":       " ".join(p for p in parts if p),
                    "position":   i.get("position", {}).get("label"),
                    "department": dept.get("name", "Unknown"),
                })
        return JSONResponse({
            "success": True, "term": api_term,
            "count": len(all_instr), "instructors": all_instr,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/unitime/courses")
async def unitime_courses(request: Request, term: str = None):
    client: UniTimeClient = request.app.state.unitime
    api_term = term or settings.UNITIME_API_TERM
    try:
        courses = await client.get_all_courses(term=api_term)
        return JSONResponse({
            "success": True, "term": api_term,
            "count": len(courses), "courses": courses,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/unitime/curricula")
async def unitime_curricula(
    request: Request, term: str = None, id: Optional[int] = None
):
    client: UniTimeClient = request.app.state.unitime
    api_term = term or settings.UNITIME_API_TERM
    try:
        data = (
            await client.get_curriculum_detail(term=api_term, curriculum_id=id)
            if id
            else await client.get_curricula(term=api_term)
        )
        return JSONResponse({"success": True, "term": api_term, "data": data})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/unitime/enrollments")
async def unitime_enrollments(request: Request, term: str = None):
    client: UniTimeClient = request.app.state.unitime
    api_term = term or settings.UNITIME_API_TERM
    try:
        data = await client.get_enrollments(term=api_term)
        return JSONResponse({
            "success": True, "term": api_term,
            "count": len(data), "data": data,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/unitime/rooms/update")
async def update_room_api(request: Request):
    client: UniTimeClient = request.app.state.unitime
    try:
        body    = await request.json()
        term    = body.get("term", settings.UNITIME_API_TERM)
        room_id = body.get("id")
        if not room_id:
            return JSONResponse(
                {"success": False, "error": "Missing 'id'"}, status_code=400
            )

        rooms        = await client.get_rooms(term=term)
        current_room = next(
            (r for r in rooms if r.get("uniqueId") == room_id), None
        )
        if not current_room:
            return JSONResponse(
                {"success": False, "error": f"Room {room_id} not found"},
                status_code=404,
            )

        new_cap  = body.get("capacity",     current_room.get("capacity"))
        new_exam = body.get("examCapacity", current_room.get("examCapacity"))
        payload  = {
            "uniqueId":        room_id,
            "name":            current_room.get("name"),
            "capacity":        new_cap,
            "examCapacity":    new_exam,
            "building": {
                "id":           current_room.get("building", {}).get("id"),
                "abbreviation": current_room.get("building", {}).get("abbreviation"),
            },
            "roomType": {
                "id":        current_room.get("roomType", {}).get("id"),
                "reference": current_room.get("roomType", {}).get("reference"),
            },
            "ignoreTooFar":    current_room.get("ignoreTooFar", False),
            "ignoreRoomCheck": current_room.get("ignoreRoomCheck", False),
        }
        result = await client.update_room(term=term, room_data=payload)
        ok     = result.get("ok", False)
        return JSONResponse({
            "success": ok,
            "message": "Room updated" if ok else "UniTime rejected",
            "sent": {
                "room":              f"{current_room.get('building',{}).get('abbreviation','')} {current_room.get('name','')}",
                "old_capacity":      current_room.get("capacity"),
                "new_capacity":      new_cap,
                "old_exam_capacity": current_room.get("examCapacity"),
                "new_exam_capacity": new_exam,
            },
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ══════════════════════════════════════════════════════════════════════════════
# UNITIME TEST PAGE
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/test/unitime")
async def unitime_test_page(request: Request):
    return templates.TemplateResponse("unitime_test.html", {"request": request})
