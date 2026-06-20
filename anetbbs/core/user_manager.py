# anetbbs/core/user_manager.py
"""
Telnet/SSH/rlogin user manager — talks to the SAME database the Flask web app uses.

Previously this was a JSON-file UserManager (data/users.json) that was completely
disconnected from the web's `User` table. Result: a user registered on the web
couldn't log in via telnet, and vice-versa. This module now uses raw SQLAlchemy
(no Flask app context required) against the same User table, with the same
Werkzeug password hash format the web app uses, so accounts are unified.

The public dict shape returned by `authenticate()` is preserved for compatibility
with `core/session.py`.
"""
import os
from datetime import datetime
from typing import Optional, Dict
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash


def _resolve_db_uri() -> str:
    """Resolve the database URI the same way the Flask app does."""
    # Prefer the env var (which is what the systemd EnvironmentFile loads)
    uri = os.environ.get('DATABASE_URL')
    if uri:
        return uri
    # Fall back to whatever the Flask config picks for the active env
    try:
        from anetbbs.config import get_config
        cfg = get_config(os.environ.get('FLASK_ENV', 'production'))
        return cfg.SQLALCHEMY_DATABASE_URI
    except Exception:
        return 'sqlite:///data/anetbbs.db'


# Module-level engine — one connection pool shared across all sessions
_DB_URI = _resolve_db_uri()
_engine = create_engine(_DB_URI, future=True)
_Session = sessionmaker(bind=_engine, future=True, expire_on_commit=False)


class UserManager:
    """SQLAlchemy-backed user manager. Same User table as the web app."""

    def __init__(self, data_dir: str = "data"):
        # data_dir kept for backward-compat with the old signature; unused now
        self.data_dir = data_dir

    @staticmethod
    def _user_to_dict(user) -> Dict:
        """Convert a User row into the dict shape session.py expects."""
        return {
            "id": user.id,
            "username": user.username,
            "display_name": getattr(user, 'display_name', None) or '',
            "location": getattr(user, 'location', None) or '',
            "email": user.email,
            "is_admin": bool(user.is_admin),
            "is_active": bool(user.is_active),
            "is_locked": bool(getattr(user, 'is_locked', False)),
            "access_level": getattr(user, 'access_level', None) or 10,
            "codepage": getattr(user, 'codepage', None) or 'cp437',
            "language": getattr(user, 'language', None) or 'en',
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "login_count": user.login_count or 0,
        }

    def username_exists(self, username: str) -> bool:
        """Case-insensitive check for an existing username."""
        from anetbbs.models import User
        with _Session() as s:
            row = s.execute(
                select(User).where(func.lower(User.username) == username.lower())
            ).scalar_one_or_none()
            return row is not None

    def email_exists(self, email: str) -> bool:
        """Case-insensitive check for an existing email."""
        from anetbbs.models import User
        with _Session() as s:
            row = s.execute(
                select(User).where(func.lower(User.email) == email.lower())
            ).scalar_one_or_none()
            return row is not None

    def create_user(self, username: str, password: str, email: str) -> str:
        """Create a new user. Returns 'ok', 'username_taken', or 'email_taken'."""
        from anetbbs.models import User

        with _Session() as s:
            if s.execute(
                select(User).where(func.lower(User.username) == username.lower())
            ).scalar_one_or_none() is not None:
                return 'username_taken'
            if s.execute(
                select(User).where(func.lower(User.email) == email.lower())
            ).scalar_one_or_none() is not None:
                return 'email_taken'
            user = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                is_active=True,
                is_admin=False,
                created_at=datetime.utcnow(),
                login_count=0,
            )
            s.add(user)
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                return 'email_taken'
            return 'ok'

    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        """Return user dict on success, None on failure."""
        from anetbbs.models import User

        with _Session() as s:
            user = s.execute(
                select(User).where(func.lower(User.username) == username.lower())
            ).scalar_one_or_none()
            if user is None:
                return None
            if not user.is_active:
                return None
            if not check_password_hash(user.password_hash, password):
                return None
            # Update login bookkeeping (same fields the web app maintains)
            user.last_login = datetime.utcnow()
            user.login_count = (user.login_count or 0) + 1
            s.commit()
            return self._user_to_dict(user)

    def save_security_answers(self, user_id: int, qa_pairs: list) -> None:
        """Persist password-recovery security Q&A. qa_pairs: list of (question, answer) tuples."""
        from anetbbs.models import UserSecurityAnswer
        with _Session() as s:
            for question, answer in qa_pairs:
                s.add(UserSecurityAnswer(
                    user_id=user_id,
                    question=question,
                    answer_hash=UserSecurityAnswer.hash_answer(answer),
                ))
            s.commit()

    def get_user(self, username: str) -> Optional[Dict]:
        """Look up a user by username without authentication."""
        from anetbbs.models import User
        with _Session() as s:
            user = s.execute(
                select(User).where(func.lower(User.username) == username.lower())
            ).scalar_one_or_none()
            return self._user_to_dict(user) if user else None
