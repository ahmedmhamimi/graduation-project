"""
UniTime API Client — Final Version

COMPLETE verified endpoint map:

READ (GET):
    /api/roles                    → academic sessions
    /api/rooms?term=              → rooms
    /api/instructors?term=        → instructors by department
    /api/curricula?term=          → curricula list (summary)
    /api/curricula?term=&id=      → single curriculum with COURSES
    /api/enrollments?term=        → enrollments (empty)

WRITE (POST):
    /api/rooms?term=              → update room (CONFIRMED WORKING)

READ-ONLY (501 on POST):
    /api/instructors              → no write
    /api/curricula                → no write
    /api/json?type=               → no read or write

BROKEN:
    /api/events                   → JDBC timezone bug

NOT AVAILABLE (400):
    /api/classes, /api/courses, /api/sessions, /api/departments,
    /api/offerings, /api/subject-areas, /api/course-offerings,
    /api/curriculum, /api/info, /api/solver, /api/student,
    /api/exam, /api/reservation, /api/distribution, /api/data-exchange,
    /api/class, /api/section, /api/subject, /api/timetable,
    /api/schedule, /api/offering, /api/course-offering
"""

import httpx
from typing import Optional


class UniTimeClient:

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        timeout: float = 30.0
    ) -> list | dict:
        url = f"{self.base_url}{path}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                auth=httpx.BasicAuth(self.username, self.password),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()

            text = response.text.strip()
            if not text:
                return []

            return response.json()

    async def _post_raw(
        self,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict | list] = None,
        timeout: float = 30.0
    ) -> dict:
        url = f"{self.base_url}{path}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                params=params,
                json=json_body,
                auth=httpx.BasicAuth(self.username, self.password),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )

            text = response.text.strip()
            json_response = None
            if text:
                try:
                    json_response = response.json()
                except Exception:
                    json_response = None

            return {
                "status_code": response.status_code,
                "ok": 200 <= response.status_code < 300,
                "body": json_response if json_response is not None else text[:500]
            }

    # ══════════════════════════════════════════
    # READ ENDPOINTS
    # ══════════════════════════════════════════

    async def get_sessions(self) -> list:
        """GET /api/roles → list of academic sessions."""
        return await self._request("GET", "/roles")

    async def get_rooms(self, term: str) -> list:
        """GET /api/rooms?term= → rooms for a session."""
        return await self._request("GET", "/rooms", params={"term": term})

    async def get_instructors(self, term: str) -> list:
        """GET /api/instructors?term= → instructors by department."""
        return await self._request("GET", "/instructors", params={"term": term})

    async def get_enrollments(self, term: str) -> list:
        """GET /api/enrollments?term= → enrollment data."""
        return await self._request("GET", "/enrollments", params={"term": term})

    async def get_curricula(self, term: str) -> list:
        """
        GET /api/curricula?term= → list of curricula (summary).

        Returns list of dicts with:
            id, abbv, name, academicArea, majors, dept
        Does NOT include courses — use get_curriculum_detail for that.
        """
        return await self._request("GET", "/curricula", params={"term": term})

    async def get_curriculum_detail(self, term: str, curriculum_id: int) -> dict:
        """
        GET /api/curricula?term=&id= → single curriculum with full course list.

        Returns dict with:
            id, abbv, name, academicArea, majors, dept,
            clasf (classifications like Junior Year, Senior Year),
            courses (list of courses with enrollments per classification)

        The courses list contains entries like:
            courseId: 135755
            courseName: "BIOL 101"
            curriculumCourses: [{share, enrollment, ...}, ...]
        """
        return await self._request(
            "GET", "/curricula",
            params={"term": term, "id": str(curriculum_id)}
        )

    async def get_all_courses(self, term: str) -> list:
        """
        Get ALL courses across all curricula.

        Since there is no direct /api/courses endpoint, we extract
        courses from every curriculum's detail view.

        Returns deduplicated list of course dicts:
            courseId (int)
            courseName (str) — e.g. "BIOL 101"
            subject (str) — extracted, e.g. "BIOL"
            number (str) — extracted, e.g. "101"
            total_enrollment (int) — sum across all classifications
            curricula (list) — which curricula include this course
        """
        curricula = await self.get_curricula(term=term)
        all_courses = {}

        for curr_summary in curricula:
            curr_id = curr_summary.get("id")
            curr_name = curr_summary.get("name", "")

            try:
                detail = await self.get_curriculum_detail(term=term, curriculum_id=curr_id)
            except Exception:
                continue

            for course in detail.get("courses", []):
                course_id = course.get("courseId")
                course_name = course.get("courseName", "")

                if course_id not in all_courses:
                    # Split "BIOL 101" into subject and number
                    parts = course_name.split(" ", 1)
                    subject = parts[0] if parts else ""
                    number = parts[1] if len(parts) > 1 else ""

                    all_courses[course_id] = {
                        "courseId": course_id,
                        "courseName": course_name,
                        "subject": subject,
                        "number": number,
                        "total_enrollment": 0,
                        "curricula": []
                    }

                # Sum enrollment across classifications
                for cc in course.get("curriculumCourses", []):
                    if cc is not None:
                        all_courses[course_id]["total_enrollment"] += cc.get("enrollment", 0)

                if curr_name not in all_courses[course_id]["curricula"]:
                    all_courses[course_id]["curricula"].append(curr_name)

        # Sort by course name
        return sorted(all_courses.values(), key=lambda c: c["courseName"])

    # ══════════════════════════════════════════
    # WRITE ENDPOINTS
    # ══════════════════════════════════════════

    async def update_room(self, term: str, room_data: dict) -> dict:
        """
        POST /api/rooms?term= → update room in UniTime.
        CONFIRMED WORKING.

        Required fields: uniqueId, name, building{id, abbreviation},
        roomType{id, reference}, capacity, examCapacity,
        ignoreTooFar, ignoreRoomCheck
        """
        return await self._post_raw(
            "/rooms",
            params={"term": term},
            json_body=room_data
        )

    # ══════════════════════════════════════════
    # HEALTH CHECK
    # ══════════════════════════════════════════

    async def health_check(self) -> dict:
        try:
            sessions = await self.get_sessions()
            return {
                "connected": True,
                "session_count": len(sessions),
                "sessions": [
                    {
                        "reference": s.get("reference"),
                        "label": f"{s.get('term')} {s.get('year')} ({s.get('campus')})",
                        "has_classes": s.get("status", {}).get("classes", False)
                    }
                    for s in sessions
                ],
                "error": None
            }
        except httpx.ConnectError:
            return {
                "connected": False, "session_count": 0,
                "sessions": [], "error": f"Cannot connect to {self.base_url}"
            }
        except httpx.HTTPStatusError as e:
            return {
                "connected": False, "session_count": 0,
                "sessions": [], "error": f"HTTP {e.response.status_code}"
            }
        except Exception as e:
            return {
                "connected": False, "session_count": 0,
                "sessions": [], "error": str(e)
            }