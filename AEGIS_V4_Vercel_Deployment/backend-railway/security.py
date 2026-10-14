"""Optional localhost administrator session. Enable with AEGIS_ADMIN_PASSWORD.
Runs without authentication only in deliberately local/competition mode on 127.0.0.1.
"""
import os
import secrets
import time
from collections import deque

PASSWORD = os.environ.get('AEGIS_ADMIN_PASSWORD', '')
REVIEWER_PASSWORD = os.environ.get('AEGIS_REVIEWER_PASSWORD', '')
SESSIONS = {}  # ephemeral process-local; no tokens in localStorage or database
ATTEMPTS = deque(maxlen=50)
COOKIE = 'aegis_session'


def protected():
    return bool(PASSWORD)


def authenticated(request):
    if not protected():
        return True
    token = request.cookies.get(COOKIE, '')
    session = SESSIONS.get(token)
    return bool(session and session[0] > time.time())


def login(password, client='local'):
    current = time.time()
    recent = [x for x in ATTEMPTS if x[0] > current - 60 and x[1] == client]
    if len(recent) >= 8:
        raise ValueError('Too many attempts. Try again in one minute.')
    role = ('admin' if secrets.compare_digest(password, PASSWORD) and bool(PASSWORD)
            else 'reviewer' if REVIEWER_PASSWORD and secrets.compare_digest(password, REVIEWER_PASSWORD)
            else None)
    if role is None:
        ATTEMPTS.append((current, client))
        raise ValueError('Invalid operator password.')
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = (current + 8 * 3600,role)
    return token


def logout(token):
    SESSIONS.pop(token, None)


def role(request):
    if not protected():return 'admin'
    token=request.cookies.get(COOKIE,'')
    value=SESSIONS.get(token)
    return value[1] if value and value[0]>time.time() else None
