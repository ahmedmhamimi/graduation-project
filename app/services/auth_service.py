from supabase import create_client, Client
from app.config import settings

_client: Client = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _client


class AuthService:
    """Supabase auth + user_profiles table."""

    def __init__(self):
        self.sb = get_supabase()

    # ═══════════════════════════════════════════
    # SIGN UP
    # ═══════════════════════════════════════════
    def sign_up(self, email: str, password: str, role: str, profile_data: dict) -> dict:
        full_name = profile_data.get("full_name", "User")
        try:
            res = self.sb.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "full_name": full_name,
                        "role": role,
                    }
                }
            })

            if res.user:
                # Insert profile into user_profiles table
                try:
                    self.sb.table("user_profiles").insert({
                        "id": res.user.id,
                        "email": email,
                        "full_name": full_name,
                        "first_name": profile_data.get("first_name", ""),
                        "last_name": profile_data.get("last_name", ""),
                        "role": role,
                        "student_id": profile_data.get("student_id", ""),
                        "department": profile_data.get("department", ""),
                        "academic_year": profile_data.get("academic_year"),
                        "employee_id": profile_data.get("employee_id", ""),
                        "position": profile_data.get("position", ""),
                        "academic_position": profile_data.get("academic_position", ""),
                    }).execute()
                except Exception as profile_err:
                    print(f"Warning: Profile insert failed for {email}: {profile_err}")

                return {
                    "success": True,
                    "user": {
                        "id": res.user.id,
                        "email": res.user.email,
                        "full_name": full_name,
                        "role": role,
                    }
                }
            return {"success": False, "error": "Sign-up failed. Please try again."}

        except Exception as e:
            error_msg = str(e)
            if "already registered" in error_msg.lower() or "already been registered" in error_msg.lower():
                return {"success": False, "error": "This email is already registered. Please log in."}
            return {"success": False, "error": error_msg}

    # ═══════════════════════════════════════════
    # SIGN IN
    # ═══════════════════════════════════════════
    def sign_in(self, email: str, password: str) -> dict:
        try:
            res = self.sb.auth.sign_in_with_password({
                "email": email,
                "password": password,
            })

            if res.user and res.session:
                meta = res.user.user_metadata or {}
                return {
                    "success": True,
                    "user": {
                        "id": res.user.id,
                        "email": res.user.email,
                        "full_name": meta.get("full_name", "User"),
                        "role": meta.get("role", "student"),
                    },
                    "access_token": res.session.access_token,
                }
            return {"success": False, "error": "Invalid credentials."}

        except Exception as e:
            error_msg = str(e)
            if "invalid" in error_msg.lower() or "credentials" in error_msg.lower():
                return {"success": False, "error": "Invalid email or password."}
            return {"success": False, "error": error_msg}

    # ═══════════════════════════════════════════
    # PROFILE CRUD
    # ═══════════════════════════════════════════
    def get_profile(self, user_id: str) -> dict | None:
        """Get a single user's profile."""
        try:
            res = self.sb.table("user_profiles").select("*").eq("id", str(user_id)).execute()
            if res.data and len(res.data) > 0:
                return res.data[0]
        except Exception as e:
            print(f"Profile fetch error: {e}")
        return None

    def get_all_profiles(self, role: str = None) -> list[dict]:
        """Get all profiles, optionally filtered by role."""
        try:
            query = self.sb.table("user_profiles").select("*").order("created_at", desc=True)
            if role:
                query = query.eq("role", role)
            res = query.execute()
            return res.data or []
        except Exception as e:
            print(f"Profiles fetch error: {e}")
            return []

    def get_user_stats(self) -> dict:
        """Aggregate stats from user_profiles for admin dashboards."""
        try:
            res = self.sb.table("user_profiles").select(
                "role, department, academic_year, position, academic_position, created_at"
            ).execute()
            data = res.data or []
        except Exception as e:
            print(f"User stats error: {e}")
            data = []

        stats = {
            "total_users": len(data),
            "by_role": {},
            "students_by_department": {},
            "students_by_year": {},
            "staff_by_position": {},
            "recent_signups": 0,
        }

        from datetime import datetime, timedelta, timezone
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)

        for p in data:
            role = p.get("role", "unknown")
            stats["by_role"][role] = stats["by_role"].get(role, 0) + 1

            # Count recent signups
            created = p.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if dt > week_ago:
                        stats["recent_signups"] += 1
                except Exception:
                    pass

            if role == "student":
                dept = p.get("department") or "Undeclared"
                if dept:
                    stats["students_by_department"][dept] = stats["students_by_department"].get(dept, 0) + 1
                year = p.get("academic_year")
                if year:
                    key = f"Year {year}"
                    stats["students_by_year"][key] = stats["students_by_year"].get(key, 0) + 1

            if role in ("ta", "lecturer"):
                pos = p.get("position") or "Unknown"
                if pos:
                    stats["staff_by_position"][pos] = stats["staff_by_position"].get(pos, 0) + 1

        return stats

    # ═══════════════════════════════════════════
    # GET USER / SIGN OUT
    # ═══════════════════════════════════════════
    def get_user(self, access_token: str) -> dict | None:
        try:
            res = self.sb.auth.get_user(access_token)
            if res.user:
                meta = res.user.user_metadata or {}
                return {
                    "id": res.user.id,
                    "email": res.user.email,
                    "full_name": meta.get("full_name", "User"),
                    "role": meta.get("role", "student"),
                }
        except Exception:
            pass
        return None

    def sign_out(self, access_token: str):
        try:
            self.sb.auth._headers["Authorization"] = f"Bearer {access_token}"
            self.sb.auth.sign_out()
        except Exception:
            pass