"""
Cookie-based session using itsdangerous signed cookies.
Stores: user id, email, full_name, role, access_token.
"""

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request
from fastapi.responses import Response
from app.config import settings

COOKIE_NAME = "session"
MAX_AGE = 60 * 60 * 24 * 7  # 7 days

_serializer = URLSafeTimedSerializer(settings.SECRET_KEY)


def create_session(response: Response, data: dict):
    """Sign data and set it as a cookie."""
    token = _serializer.dumps(data)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # set True in production with HTTPS
    )


def get_session(request: Request) -> dict | None:
    """Read and verify the session cookie. Returns data dict or None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=MAX_AGE)
        return data
    except (BadSignature, SignatureExpired):
        return None


def clear_session(response: Response):
    """Delete the session cookie."""
    response.delete_cookie(key=COOKIE_NAME)