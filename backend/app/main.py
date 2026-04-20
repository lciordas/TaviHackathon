"""FastAPI application entrypoint for Tavi intake."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import intake, places

app = FastAPI(title="Tavi Intake")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(intake.router, prefix="/intake", tags=["intake"])
app.include_router(places.router, prefix="/intake/places", tags=["places"])


@app.get("/health")
def health() -> dict:
    return {"ok": True}
