"""
viewmodels/admin_vm.py
Add these imports and routes to your existing admin_vm.py file.

NOTE: Keep all your existing code. Just add/merge the following.
"""

# ═══════════════════════════════════════════════════════════════════
# ADD THESE IMPORTS at the top of your existing admin_vm.py
# ═══════════════════════════════════════════════════════════════════

from app.services.agent_service import AgentService
from app.services.xml_schedule import XMLSchedule

# ═══════════════════════════════════════════════════════════════════
# ADD THIS ROUTE to your existing router
# ═══════════════════════════════════════════════════════════════════

@router.get("/admin/analysis")
async def admin_analysis(request: Request):
    """
    Run full agent analysis and display results in 4 agent tabs.
    """
    # Auth check (copy your existing auth pattern)
    user = request.state.user if hasattr(request.state, 'user') else None
    session = request.session
    
    if not user and session.get("user_id"):
        # Fetch user from your auth service
        from app.services.auth_service import get_user_by_id
        user = await get_user_by_id(session["user_id"])
    
    if not user:
        return RedirectResponse("/login", status_code=303)
    
    # Permission check
    role = user.get("role", "")
    allowed_roles = ["admin", "dean", "vicedean", "scheduler"]
    if role not in allowed_roles:
        return RedirectResponse("/dashboard", status_code=303)
    
    # Get DB and XML schedule from app state
    db: UniTimeDB = request.app.state.unitime_db
    xml_schedule: XMLSchedule = getattr(request.app.state, 'xml_schedule', None)
    
    session_id = db.session_id
    
    # Run analysis
    agent_service = AgentService(db, xml_schedule)
    
    try:
        report = await agent_service.run_full_analysis(session_id=session_id)
        analysis_error = None
    except Exception as e:
        import traceback
        traceback.print_exc()
        report = None
        analysis_error = str(e)
    
    # Get stats for header
    stats = await db.get_stats(session_id)
    
    # Build permissions dict (adapt to your existing pattern)
    permissions = {
        "can_write_instructors": role in ["admin", "dean", "scheduler"],
        "can_write_courses": role in ["admin", "dean", "scheduler"],
        "can_write_rooms": role in ["admin", "dean", "scheduler"],
        "can_write_classes": role in ["admin", "dean", "scheduler"],
        "can_write_students": role in ["admin", "dean"],
        "can_view_students": role in ["admin", "dean", "vicedean"],
        "description": "Schedule Analysis Dashboard",
    }
    
    return templates.TemplateResponse(
        "dashboard_admin.html",
        {
            "request": request,
            "user": user,
            "title": "Schedule Analysis",
            "permissions": permissions,
            "stats": stats,
            "session_id": session_id,
            
            # Analysis data (new!)
            "show_analysis": True,
            "analysis_report": report,
            "analysis_error": analysis_error,
            
            # Empty lists for non-analysis tabs (they're hidden in analysis mode)
            "instructors": [],
            "courses": [],
            "rooms": [],
            "classes": [],
            "students": [],
            "departments": [],
            "buildings": [],
            "subject_areas": [],
            "position_types": [],
            "room_types": [],
            "time_patterns": [],
            "user_stats": None,
            
            # Permission flags
            "can_write_instructors": permissions["can_write_instructors"],
            "can_write_courses": permissions["can_write_courses"],
            "can_write_rooms": permissions["can_write_rooms"],
            "can_write_classes": permissions["can_write_classes"],
            "can_write_students": permissions["can_write_students"],
            "can_view_students": permissions["can_view_students"],
        }
    )


@router.post("/admin/analysis/refresh")
async def refresh_analysis(request: Request):
    """
    Re-run analysis and reload XML.
    """
    # Reload XML schedule
    xml_schedule: XMLSchedule = getattr(request.app.state, 'xml_schedule', None)
    if xml_schedule:
        xml_schedule.loaded = False
        xml_schedule.class_solutions = {}
        xml_schedule.student_classes = {}
        xml_schedule.instructor_classes = {}
        xml_schedule.load()
    
    return RedirectResponse("/admin/analysis", status_code=303)