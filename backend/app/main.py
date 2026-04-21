"""FastAPI application entrypoint for Tavi backend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import admin, discovery, intake, negotiations, places

app = FastAPI(title="Tavi Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(intake.router, prefix="/intake", tags=["intake"])
app.include_router(places.router, prefix="/intake/places", tags=["places"])
app.include_router(discovery.router, prefix="/discovery", tags=["discovery"])
app.include_router(negotiations.router, prefix="/negotiations", tags=["negotiations"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])


@app.get("/health")
def health() -> dict:
    return {"ok": True}
