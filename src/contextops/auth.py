"""Single-administrator authentication and SQLite-backed session management."""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from contextops.database import Database


SESSION_COOKIE_NAME = "contextops_session"
LOGIN_CSRF_COOKIE_NAME = "contextops_login_csrf"
DEFAULT_SESSION_TTL_SECONDS = 28_800
MAX_TRACKED_CLIENTS = 1_024
MAX_TRACKED_CSRF_TOKENS = 1_024


@dataclass(frozen=True)
class AuthConfig:
    """Configuration loaded from the environment for one app instance."""

    username: str
    password: str
    session_ttl_seconds: int
    cookie_secure: bool


@dataclass
class Session:
    token: str
    csrf_token: str
    expires_at: float


def _required_environment_value(name: str, missing: list[str]) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        missing.append(name)
        return ""
    return value


def _read_positive_integer(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a positive integer") from error
    if parsed <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return parsed


def _read_boolean(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean (true/false)")


def load_auth_config() -> AuthConfig:
    """Load authentication settings without ever echoing configured secrets."""

    missing: list[str] = []
    username = _required_environment_value("CONTEXTOPS_ADMIN_USERNAME", missing)
    password = _required_environment_value("CONTEXTOPS_ADMIN_PASSWORD", missing)
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(
            "ContextOps administrator credentials are not configured; set "
            f"{names} before creating the application."
        )
    return AuthConfig(
        username=username,
        password=password,
        session_ttl_seconds=_read_positive_integer(
            "CONTEXTOPS_SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS
        ),
        cookie_secure=_read_boolean("CONTEXTOPS_COOKIE_SECURE", False),
    )


def safe_local_redirect(destination: str | None) -> str:
    """Return a local redirect target, falling back safely for external input."""

    if not destination:
        return "/"
    decoded = unquote(destination)
    if (
        not decoded.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(character) < 0x20 for character in decoded)
    ):
        return "/"
    parsed = urlsplit(decoded)
    if parsed.scheme or parsed.netloc:
        return "/"
    return decoded


class Authentication:
    """Thread-safe authentication backed by the shared application database."""

    def __init__(self, config: AuthConfig, database: Database) -> None:
        self.config = config
        self.database = database
        self._lock = threading.RLock()

    def _prune_expired_locked(self, now: float) -> None:
        with self.database.session() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            connection.execute("DELETE FROM auth_login_csrf WHERE expires_at <= ?", (now,))
            connection.execute(
                """
                DELETE FROM auth_failed_logins
                WHERE last_seen + ? <= ?
                """,
                (self.config.session_ttl_seconds, now),
            )

    def issue_login_csrf(self, destination: str = "/") -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._prune_expired_locked(now)
            with self.database.session() as connection:
                connection.execute(
                    """
                    INSERT INTO auth_login_csrf (token, destination, expires_at)
                    VALUES (?, ?, ?)
                    """,
                    (token, destination, now + self.config.session_ttl_seconds),
                )
                connection.execute(
                    """
                    DELETE FROM auth_login_csrf
                    WHERE token IN (
                        SELECT token FROM auth_login_csrf
                        ORDER BY rowid ASC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (MAX_TRACKED_CSRF_TOKENS,),
                )
        return token

    def validate_login_csrf(self, token: str | None, cookie_token: str | None) -> bool:
        if not token or not cookie_token:
            return False
        now = time.time()
        with self._lock:
            with self.database.session() as connection:
                record = connection.execute(
                    """
                    SELECT expires_at FROM auth_login_csrf
                    WHERE token = ? AND expires_at > ?
                    """,
                    (cookie_token, now),
                ).fetchone()
            return bool(
                record
                and hmac.compare_digest(token, cookie_token)
            )

    def login_csrf_destination(self, cookie_token: str | None) -> str | None:
        if not cookie_token:
            return None
        now = time.time()
        with self._lock:
            with self.database.session() as connection:
                record = connection.execute(
                    """
                    SELECT destination FROM auth_login_csrf
                    WHERE token = ? AND expires_at > ?
                    """,
                    (cookie_token, now),
                ).fetchone()
            return record["destination"] if record else None

    def consume_login_csrf(self, cookie_token: str | None) -> bool:
        if not cookie_token:
            return False
        now = time.time()
        with self._lock:
            with self.database.session() as connection:
                result = connection.execute(
                    """
                    DELETE FROM auth_login_csrf
                    WHERE token = ? AND expires_at > ?
                    """,
                    (cookie_token, now),
                )
            return result.rowcount == 1

    def get_session(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = time.time()
        with self._lock:
            with self.database.session() as connection:
                row = connection.execute(
                    """
                    SELECT token, csrf_token, expires_at FROM auth_sessions
                    WHERE token = ? AND expires_at > ?
                    """,
                    (token, now),
                ).fetchone()
            if row is None:
                return None
            return Session(
                token=row["token"],
                csrf_token=row["csrf_token"],
                expires_at=row["expires_at"],
            )

    def validate_csrf(self, session: Session, token: str | None) -> bool:
        return bool(token and hmac.compare_digest(session.csrf_token, token))

    def login_allowed(self, client_ip: str) -> float:
        now = time.time()
        with self._lock:
            with self.database.session() as connection:
                record = connection.execute(
                    """
                    SELECT blocked_until FROM auth_failed_logins
                    WHERE client_key = ? AND last_seen + ? > ?
                    """,
                    (client_ip, self.config.session_ttl_seconds, now),
                ).fetchone()
            if record is None:
                return 0.0
            return max(0.0, record["blocked_until"] - now)

    def record_failed_login(self, client_ip: str) -> float:
        now = time.time()
        with self._lock:
            self._prune_expired_locked(now)
            with self.database.session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                record = connection.execute(
                    """
                    SELECT failures, blocked_until FROM auth_failed_logins
                    WHERE client_key = ?
                    """,
                    (client_ip,),
                ).fetchone()
                if record and record["blocked_until"] > now:
                    return record["blocked_until"] - now
                failures = (record["failures"] if record else 0) + 1
                # Permit a couple of ordinary typos, then apply a short
                # exponential backoff capped to keep the shared throttle bounded.
                delay = min(8.0, 2 ** (failures - 3)) if failures >= 3 else 0.0
                connection.execute(
                    """
                    INSERT INTO auth_failed_logins
                        (client_key, failures, blocked_until, last_seen)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(client_key) DO UPDATE SET
                        failures = excluded.failures,
                        blocked_until = excluded.blocked_until,
                        last_seen = excluded.last_seen
                    """,
                    (client_ip, failures, now + delay, now),
                )
                connection.execute(
                    """
                    DELETE FROM auth_failed_logins
                    WHERE client_key IN (
                        SELECT client_key FROM auth_failed_logins
                        ORDER BY last_seen ASC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (MAX_TRACKED_CLIENTS,),
                )
                return 0.0

    def clear_failed_logins(self, client_ip: str) -> None:
        with self._lock:
            with self.database.session() as connection:
                connection.execute(
                    "DELETE FROM auth_failed_logins WHERE client_key = ?", (client_ip,)
                )

    def authenticate(self, username: str, password: str) -> bool:
        username_matches = hmac.compare_digest(username, self.config.username)
        password_matches = hmac.compare_digest(password, self.config.password)
        return username_matches and password_matches

    def create_session(self, previous_token: str | None = None) -> Session:
        now = time.time()
        session = Session(
            token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=now + self.config.session_ttl_seconds,
        )
        with self._lock:
            self._prune_expired_locked(now)
            with self.database.session() as connection:
                if previous_token:
                    connection.execute(
                        "DELETE FROM auth_sessions WHERE token = ?", (previous_token,)
                    )
                connection.execute(
                    """
                    INSERT INTO auth_sessions (token, csrf_token, expires_at)
                    VALUES (?, ?, ?)
                    """,
                    (session.token, session.csrf_token, session.expires_at),
                )
        return session

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock:
                with self.database.session() as connection:
                    connection.execute(
                        "DELETE FROM auth_sessions WHERE token = ?", (token,)
                    )
