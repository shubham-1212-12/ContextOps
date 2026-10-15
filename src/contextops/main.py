"""Minimal ContextOps application entry point."""

from fastapi import FastAPI

from contextops import __version__

app = FastAPI(title="ContextOps", version=__version__)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "contextops"}
