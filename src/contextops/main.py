"""FastAPI entry point and server-rendered review routes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from contextops import __version__
from contextops.database import Database
from contextops.service import ContextOpsService


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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
    app = FastAPI(title="ContextOps", version=__version__)
    path = database_path or os.getenv("DATABASE_PATH", "data/contextops.db")
    app.state.service = ContextOpsService(Database(path))
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "contextops"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Any:
        service: ContextOpsService = request.app.state.service
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"cases": _review_cards(service)},
        )

    @app.get("/rules", response_class=HTMLResponse)
    def rules_page(request: Request) -> Any:
        service: ContextOpsService = request.app.state.service
        return templates.TemplateResponse(
            request=request,
            name="rules.html",
            context={"rules": service.list_rules()},
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
