"""Single-administrator authentication and in-memory session management."""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


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


@dataclass
class FailedLogin:
    failures: int
    blocked_until: float
    last_seen: float


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
    """Thread-safe, bounded in-memory authentication state for one process."""

    def __init__(self, config: AuthConfig) -> None:
        self.config = config
        self._sessions: dict[str, Session] = {}
        self._login_csrf_tokens: dict[str, tuple[float, str]] = {}
        self._failed_logins: dict[str, FailedLogin] = {}
        self._lock = threading.RLock()

    def _prune_expired_locked(self, now: float) -> None:
        expired_sessions = [
            token
            for token, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for token in expired_sessions:
            self._sessions.pop(token, None)

        expired_csrf = [
            token
            for token, (expires_at, _destination) in self._login_csrf_tokens.items()
            if expires_at <= now
        ]
        for token in expired_csrf:
            self._login_csrf_tokens.pop(token, None)

        stale_clients = [
            client
            for client, record in self._failed_logins.items()
            if record.last_seen + self.config.session_ttl_seconds <= now
        ]
        for client in stale_clients:
            self._failed_logins.pop(client, None)

    def _bound_mapping_locked(self, mapping: dict[str, object], limit: int) -> None:
        while len(mapping) > limit:
            oldest = next(iter(mapping))
            mapping.pop(oldest, None)

    def issue_login_csrf(self, destination: str = "/") -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._prune_expired_locked(now)
            self._login_csrf_tokens[token] = (
                now + self.config.session_ttl_seconds,
                destination,
            )
            self._bound_mapping_locked(
                self._login_csrf_tokens, MAX_TRACKED_CSRF_TOKENS
            )
        return token

    def validate_login_csrf(self, token: str | None, cookie_token: str | None) -> bool:
        if not token or not cookie_token:
            return False
        now = time.monotonic()
        with self._lock:
            self._prune_expired_locked(now)
            record = self._login_csrf_tokens.get(cookie_token)
            return bool(
                record
                and record[0] > now
                and hmac.compare_digest(token, cookie_token)
            )

    def login_csrf_destination(self, cookie_token: str | None) -> str | None:
        if not cookie_token:
            return None
        with self._lock:
            record = self._login_csrf_tokens.get(cookie_token)
            return record[1] if record else None

    def consume_login_csrf(self, cookie_token: str | None) -> None:
        if not cookie_token:
            return
        with self._lock:
            self._login_csrf_tokens.pop(cookie_token, None)

    def get_session(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = time.monotonic()
        with self._lock:
            self._prune_expired_locked(now)
            session = self._sessions.get(token)
            if session is None or session.expires_at <= now:
                self._sessions.pop(token, None)
                return None
            return session

    def validate_csrf(self, session: Session, token: str | None) -> bool:
        return bool(token and hmac.compare_digest(session.csrf_token, token))

    def login_allowed(self, client_ip: str) -> float:
        now = time.monotonic()
        with self._lock:
            self._prune_expired_locked(now)
            record = self._failed_logins.get(client_ip)
            if record is None:
                return 0.0
            return max(0.0, record.blocked_until - now)

    def record_failed_login(self, client_ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune_expired_locked(now)
            record = self._failed_logins.get(client_ip)
            failures = (record.failures if record else 0) + 1
            # Permit a couple of ordinary typos, then apply a short exponential
            # backoff capped to keep the in-memory throttle lightweight.
            delay = min(8.0, 2 ** (failures - 3)) if failures >= 3 else 0.0
            self._failed_logins[client_ip] = FailedLogin(
                failures=failures,
                blocked_until=now + delay,
                last_seen=now,
            )
            self._bound_mapping_locked(self._failed_logins, MAX_TRACKED_CLIENTS)

    def clear_failed_logins(self, client_ip: str) -> None:
        with self._lock:
            self._failed_logins.pop(client_ip, None)

    def authenticate(self, username: str, password: str) -> bool:
        username_matches = hmac.compare_digest(username, self.config.username)
        password_matches = hmac.compare_digest(password, self.config.password)
        return username_matches and password_matches

    def create_session(self, previous_token: str | None = None) -> Session:
        now = time.monotonic()
        session = Session(
            token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=now + self.config.session_ttl_seconds,
        )
        with self._lock:
            self._prune_expired_locked(now)
            if previous_token:
                self._sessions.pop(previous_token, None)
            self._sessions[session.token] = session
        return session

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)
