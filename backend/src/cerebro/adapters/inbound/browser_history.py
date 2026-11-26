"""Bounded private owner history. The browser authentication middleware enforces the private listener."""

from dataclasses import asdict

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from cerebro.application.history import ChangeUnavailable, HistoryRequest
from cerebro.application.use_cases.history import OwnerHistory


def install_browser_history(app: FastAPI, history: OwnerHistory) -> None:
    @app.get("/api/changes")
    def list_changes(request: Request) -> JSONResponse:
        return JSONResponse({"changes": [asdict(row) for row in history.list(request.state.ctx)]})

    @app.get("/api/changes/{revision}")
    def change_detail(revision: str, request: Request) -> JSONResponse:
        try:
            result = history.detail(HistoryRequest(revision), request.state.ctx)
        except ChangeUnavailable:
            return JSONResponse({"error": "Change unavailable."}, status_code=404)
        return JSONResponse({**asdict(result.change), "diff": result.diff, "truncated": result.truncated})
