"""
app/main.py — FastAPI application entry point.

Registers all API routers and configures CORS, lifespan events, and
the WebSocket route. The application instance is imported by uvicorn:
    uvicorn app.main:app
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1 import auth, users, meetings, websocket_audio


def _parse_allowed_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: nothing to initialise explicitly (SQLAlchemy engine is lazy).
    yield
    # Shutdown: close the async engine connection pool.
    from app.core.db_session import engine
    await engine.dispose()


app = FastAPI(
    title="Intelligent Meeting Memory",
    description="Multi-tenant SaaS MOM generation with RAG memory loop.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_allowed_origins(settings.ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routers
app.include_router(auth.router,     prefix="/api/v1/auth",     tags=["auth"])
app.include_router(users.router,    prefix="/api/v1/users",    tags=["users"])
app.include_router(meetings.router, prefix="/api/v1/meetings", tags=["meetings"])

# WebSocket route (no /api/v1 prefix — standard WS path convention)
app.include_router(websocket_audio.router, tags=["websocket"])


@app.get("/healthz", tags=["health"])
async def health_check():
    return {"status": "ok"}
