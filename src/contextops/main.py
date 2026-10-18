"""FastAPI entry point and server-rendered review routes."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from contextops import __version__
from contextops.auth import (
    LOGIN_CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    Authentication,
    LoginCsrfDiagnostics,
    classify_login_csrf_reason,
    load_auth_config,
    safe_local_redirect,
)
from contextops.database import Database
from contextops.service import ContextOpsService


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
logger = logging.getLogger(__name__)


def _form_value(form: dict[str, list[str]], name: str) -> str:
    values = form.get(name, [])
    return values[0] if values else ""


def _log_login_csrf_failure(
    diagnostics: LoginCsrfDiagnostics,
    *,
    stage: Literal["initial_validation", "atomic_consumption"],
    atomic_consumption_result: bool | None,
) -> None:
    """Log only booleans and reason codes for a rejected login CSRF check."""

    consumption_code = (
        "not_attempted"
        if atomic_consumption_result is None
        else str(atomic_consumption_result).lower()
    )
    reason = classify_login_csrf_reason(
        form_token_present=diagnostics.form_token_present,
        cookie_present=diagnostics.cookie_present,
        tokens_equal=diagnostics.tokens_equal,
        challenge_present=diagnostics.challenge_present,
        challenge_active=diagnostics.challenge_active,
        challenge_expired=diagnostics.challenge_expired,
        atomic_consumption_result=atomic_consumption_result,
    )
    logger.warning(
        "login_csrf_rejected stage=%s reason=%s form_token_present=%s cookie_present=%s "
        "tokens_equal=%s challenge_present=%s challenge_active=%s "
        "challenge_expired=%s atomic_consumption_result=%s",
        stage,
        reason,
        diagnostics.form_token_present,
        diagnostics.cookie_present,
        diagnostics.tokens_equal,
        diagnostics.challenge_present,
        diagnostics.challenge_active,
        diagnostics.challenge_expired,
        consumption_code,
        extra={
            "login_csrf_stage": stage,
            "login_csrf_reason": reason,
            "form_token_present": diagnostics.form_token_present,
            "cookie_present": diagnostics.cookie_present,
            "tokens_equal": diagnostics.tokens_equal,
            "challenge_present": diagnostics.challenge_present,
            "challenge_active": diagnostics.challenge_active,
            "challenge_expired": diagnostics.challenge_expired,
            "atomic_consumption_result": consumption_code,
        },
    )


async def _read_urlencoded_form(request: Request) -> dict[str, list[str]]:
    try:
        body = await request.body()
        return parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except (UnicodeDecodeError, ValueError):
        return {}


def _set_session_cookie(response: Response, authentication: Authentication, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=authentication.config.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        path="/",
        secure=authentication.config.cookie_secure,
    )


def _set_login_csrf_cookie(
    response: Response, authentication: Authentication, token: str
) -> None:
    response.set_cookie(
        key=LOGIN_CSRF_COOKIE_NAME,
        value=token,
        max_age=authentication.config.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        path="/",
        secure=authentication.config.cookie_secure,
    )


def _clear_auth_cookies(response: Response, authentication: Authentication) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=authentication.config.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=LOGIN_CSRF_COOKIE_NAME,
        path="/",
        secure=authentication.config.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _login_page(
    request: Request,
    *,
    csrf_token: str,
    next_destination: str,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    request.state.login_csrf_token = csrf_token
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next_destination": next_destination, "error": error},
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _unauthenticated_response(request: Request) -> Response:
    path = request.url.path
    if path == "/api" or path.startswith("/api/") or path == "/openapi.json":
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    next_destination = safe_local_redirect(path + (f"?{request.url.query}" if request.url.query else ""))
    return RedirectResponse(url=f"/login?next={quote(next_destination, safe='')}", status_code=303)


def _review_cards(service: ContextOpsService) -> list[dict[str, Any]]:
    """Build compact dashboard summaries without changing the case API."""

    cards: list[dict[str, Any]] = []
    for summary in service.list_cases():
        case = service.get_case(summary["id"])
        if case is None:
            continue
        issues = case["issues"]
        confirmed = [issue for issue in issues if issue["category"] == "confirmed_violation"]
        warnings = [issue for issue in issues if issue["category"] == "warning"]
        ambiguities = [
            issue for issue in issues if issue["category"] == "unresolved_ambiguity"
        ]
        latest_decision = case["decisions"][-1]["decision"] if case["decisions"] else None
        decision_labels = {
            "approve": "Approved",
            "accept": "Accepted",
            "edit": "Edited",
            "reject": "Rejected",
            "defer": "Deferred",
            "escalate": "Escalated",
        }
        cards.append(
            {
                **summary,
                "review_status": decision_labels.get(latest_decision, "Needs review"),
                "review_status_class": "status-reviewed" if latest_decision else "status-pending",
                "issue_headline": (
                    confirmed[0]["title"]
                    if confirmed
                    else "No confirmed violation detected"
                ),
                "confirmed_count": len(confirmed),
                "warning_count": len(warnings),
                "ambiguity_count": len(ambiguities),
            }
        )
    return cards


def create_app(database_path: str | None = None) -> FastAPI:
    auth_config = load_auth_config()
    app = FastAPI(title="ContextOps", version=__version__)
    path = database_path or os.getenv("DATABASE_PATH", "data/contextops.db")
    database = Database(path)
    app.state.service = ContextOpsService(database)
    authentication = Authentication(auth_config, database)
    app.state.authentication = authentication
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.middleware("http")
    async def authentication_guard(request: Request, call_next: Any) -> Response:
        path = request.url.path
        request.state.authenticated = False

        if path == "/health" or path == "/login" or path.startswith("/static/"):
            return await call_next(request)

        session_token = request.cookies.get(SESSION_COOKIE_NAME)
        session = authentication.get_session(session_token)
        if session is None:
            return _unauthenticated_response(request)

        request.state.authenticated = True
        request.state.session_token = session.token
        request.state.csrf_token = session.csrf_token
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and path != "/logout":
            if not authentication.validate_csrf(
                session, request.headers.get("x-csrf-token")
            ):
                return JSONResponse(
                    {"detail": "Invalid or missing CSRF token"}, status_code=403
                )
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "contextops"}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        next_destination = safe_local_redirect(request.query_params.get("next"))
        existing_token = request.cookies.get(LOGIN_CSRF_COOKIE_NAME)
        token = (
            existing_token
            if authentication.login_csrf_destination(existing_token) is not None
            else authentication.issue_login_csrf(next_destination)
        )
        response = _login_page(
            request,
            csrf_token=token,
            next_destination=next_destination,
        )
        if token != existing_token:
            _set_login_csrf_cookie(response, authentication, token)
        return response

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request) -> Response:
        form = await _read_urlencoded_form(request)
        csrf_token = _form_value(form, "csrf_token")
        cookie_token = request.cookies.get(LOGIN_CSRF_COOKIE_NAME)
        if not authentication.validate_login_csrf(csrf_token, cookie_token):
            if authentication.config.csrf_diagnostics_enabled:
                _log_login_csrf_failure(
                    authentication.login_csrf_diagnostics(csrf_token, cookie_token),
                    stage="initial_validation",
                    atomic_consumption_result=None,
                )
            return HTMLResponse("Invalid or missing CSRF token", status_code=403)

        requested_next = _form_value(form, "next")
        if not requested_next:
            requested_next = authentication.login_csrf_destination(cookie_token)
        next_destination = safe_local_redirect(requested_next)
        client_ip = request.client.host if request.client else "unknown"
        retry_after = authentication.login_allowed(client_ip)
        if retry_after:
            response = _login_page(
                request,
                csrf_token=csrf_token,
                next_destination=next_destination,
                error="Too many failed sign-in attempts. Try again shortly.",
                status_code=429,
            )
            response.headers["Retry-After"] = str(max(1, int(retry_after + 0.999)))
            return response

        username = _form_value(form, "username")
        password = _form_value(form, "password")
        if not authentication.authenticate(username, password):
            failed_retry_after = authentication.record_failed_login(client_ip)
            if failed_retry_after:
                response = _login_page(
                    request,
                    csrf_token=csrf_token,
                    next_destination=next_destination,
                    error="Too many failed sign-in attempts. Try again shortly.",
                    status_code=429,
                )
                response.headers["Retry-After"] = str(
                    max(1, int(failed_retry_after + 0.999))
                )
                return response
            return _login_page(
                request,
                csrf_token=csrf_token,
                next_destination=next_destination,
                error="Invalid username or password.",
                status_code=401,
            )

        authentication.clear_failed_logins(client_ip)
        consumed = authentication.consume_login_csrf(cookie_token)
        if not consumed:
            if authentication.config.csrf_diagnostics_enabled:
                _log_login_csrf_failure(
                    authentication.login_csrf_diagnostics(csrf_token, cookie_token),
                    stage="atomic_consumption",
                    atomic_consumption_result=consumed,
                )
            return HTMLResponse("Invalid or missing CSRF token", status_code=403)
        session = authentication.create_session(request.cookies.get(SESSION_COOKIE_NAME))
        response = RedirectResponse(url=next_destination, status_code=303)
        _set_session_cookie(response, authentication, session.token)
        response.delete_cookie(
            key=LOGIN_CSRF_COOKIE_NAME,
            path="/",
            secure=authentication.config.cookie_secure,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        session_token = request.cookies.get(SESSION_COOKIE_NAME)
        session = authentication.get_session(session_token)
        if session is None:
            response = RedirectResponse(url="/login", status_code=303)
            _clear_auth_cookies(response, authentication)
            return response

        form = await _read_urlencoded_form(request)
        supplied_token = request.headers.get("x-csrf-token") or _form_value(
            form, "csrf_token"
        )
        if not authentication.validate_csrf(session, supplied_token):
            return JSONResponse(
                {"detail": "Invalid or missing CSRF token"}, status_code=403
            )

        authentication.logout(session.token)
        response = RedirectResponse(url="/login", status_code=303)
        _clear_auth_cookies(response, authentication)
        return response

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Any:
        service: ContextOpsService = request.app.state.service
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "cases": _review_cards(service),
                "case_archived": request.query_params.get("archived") == "1",
            },
        )

    @app.get("/rules", response_class=HTMLResponse)
    def rules_page(request: Request) -> Any:
        service: ContextOpsService = request.app.state.service
        return_case_id = request.query_params.get("return_case_id")
        if return_case_id and service.get_case(return_case_id) is None:
            return_case_id = None
        return templates.TemplateResponse(
            request=request,
            name="rules.html",
            context={"rules": service.list_rules(), "return_case_id": return_case_id},
        )

    @app.get("/cases/new", response_class=HTMLResponse)
    def new_case_page(request: Request) -> Any:
        return templates.TemplateResponse(
            request=request,
            name="new_case.html",
            context={},
        )

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def case_page(request: Request, case_id: str) -> Any:
        service: ContextOpsService = request.app.state.service
        case = service.get_case(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        return templates.TemplateResponse(
            request=request,
            name="case.html",
            context={
                "case": case,
                "cases": service.list_cases(),
                "rules": service.list_rules(),
            },
        )

    @app.get("/api/cases/{case_id}")
    def case_data(request: Request, case_id: str) -> dict[str, Any]:
        case = request.app.state.service.get_case(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        return case

    @app.post("/api/cases/{case_id}/archive")
    def archive_case(request: Request, case_id: str) -> JSONResponse:
        try:
            result = request.app.state.service.archive_case(case_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="case not found") from error
        result["redirect"] = "/?archived=1"
        return JSONResponse(result)

    @app.get("/api/rules")
    def rules_data(request: Request) -> list[dict[str, Any]]:
        return request.app.state.service.list_rules()

    @app.post("/api/cases")
    async def create_case(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Request body must be an object")
        try:
            case = request.app.state.service.create_case(
                title=payload.get("title"),
                original_proposal=payload.get("original_proposal"),
                context=payload.get("context"),
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return JSONResponse(
            {"case_id": case["id"], "case": case},
            status_code=201,
        )

    @app.post("/api/cases/{case_id}/decision")
    async def record_decision(request: Request, case_id: str) -> JSONResponse:
        payload = await request.json()
        try:
            result = request.app.state.service.record_decision(
                case_id=case_id,
                decision=str(payload.get("decision", "")),
                rationale=str(payload.get("rationale", "")),
                edited_proposal=payload.get("edited_proposal"),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="case not found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return JSONResponse(result)

    @app.post("/api/cases/{case_id}/rules")
    async def draft_rule(request: Request, case_id: str) -> JSONResponse:
        payload = await request.json()
        try:
            result = request.app.state.service.create_rule_from_issue(
                case_id, str(payload.get("issue_id", ""))
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="issue not found") from error
        return JSONResponse(result)

    @app.post("/api/rules/{rule_id}/activate")
    def activate_rule(request: Request, rule_id: int) -> JSONResponse:
        try:
            result = request.app.state.service.activate_rule(rule_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="rule not found") from error
        return JSONResponse(result)

    @app.post("/api/rules/{rule_id}/disable")
    def disable_rule(request: Request, rule_id: int) -> JSONResponse:
        try:
            result = request.app.state.service.disable_rule(rule_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="rule not found") from error
        return JSONResponse(result)

    @app.post("/api/cases/{case_id}/evaluate-rules")
    def evaluate_rules(request: Request, case_id: str) -> JSONResponse:
        try:
            result = request.app.state.service.evaluate_active_rules(case_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="case not found") from error
        return JSONResponse({"evaluations": result})

    return app


app = create_app()
