import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # UniTime API
    UNITIME_BASE_URL: str = os.getenv("UNITIME_BASE_URL", "http://localhost:8080/UniTime/api")
    UNITIME_USERNAME: str = os.getenv("UNITIME_USERNAME", "admin")
    UNITIME_PASSWORD: str = os.getenv("UNITIME_PASSWORD", "admin")

    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

    # Session
    SECRET_KEY: str = os.getenv("SECRET_KEY", "super-secret-change-me")

    # UniTime Database
    UNITIME_DB_HOST: str = os.getenv("UNITIME_DB_HOST", "localhost")
    UNITIME_DB_PORT: int = int(os.getenv("UNITIME_DB_PORT", "3306"))
    UNITIME_DB_USER: str = os.getenv("UNITIME_DB_USER", "root")
    UNITIME_DB_PASSWORD: str = os.getenv("UNITIME_DB_PASSWORD", "")
    UNITIME_DB_NAME: str = os.getenv("UNITIME_DB_NAME", "timetable")

    # ══════════════════════════════════════════════
    # Fal 2010 Session Constants (from database)
    # ══════════════════════════════════════════════
    UNITIME_SESSION_ID: int = int(os.getenv("UNITIME_SESSION_ID", "231379"))
    UNITIME_DEPARTMENT_ID: int = int(os.getenv("UNITIME_DEPARTMENT_ID", "231383"))
    UNITIME_DATE_PATTERN_ID: int = int(os.getenv("UNITIME_DATE_PATTERN_ID", "853"))
    UNITIME_API_TERM: str = os.getenv("UNITIME_API_TERM", "Fal2010woebegon")


settings = Settings()