import os
import subprocess
import sys
from typing import List

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.auth import router as auth_router
from app.routers.profile import router as profile_router
from app.routers.admin import router as admin_router
from app.routers.dashboard import router as dashboard_router
from app.routers.resumes import router as resumes_router
from app.routers.templates import router as templates_router
from app.routers.pricing import router as pricing_router
from app.routers.settings import router as settings_router
from app.routers.help_center import router as help_router
from app.routers.juno import router as juno_router
from app.middleware.session import UserSessionMiddleware
from app.core.swagger import setup_swagger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run DB migrations on startup (enables free-tier deploys where Shell/Release Command aren't available)
    if os.getenv("RUN_MIGRATIONS_ON_STARTUP", "true").lower() in ("1", "true", "yes"):
        subprocess.run(
            # Some projects end up with multiple Alembic "heads".
            # `upgrade heads` applies all terminal heads instead of failing on ambiguity.
            [sys.executable, "-m", "alembic", "upgrade", "heads"],
            check=True,
            capture_output=False,
        )
    yield


app = FastAPI(
    title="ResumeAI Backend",
    version="1.0.0",
    description="Backend APIs for the AI-powered resume builder.",
    lifespan=lifespan,
)

setup_swagger(app)

_raw_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
cors_origins: List[str] = [o.strip() for o in _raw_cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    UserSessionMiddleware,
    ignored_paths={
        "/auth/login/",
        "/auth/refresh/",
        "/auth/logout-all/",
    },
    idle_threshold_minutes=1,
)
app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(admin_router)
app.include_router(dashboard_router)
app.include_router(resumes_router)
app.include_router(templates_router)
app.include_router(pricing_router)
app.include_router(settings_router)
app.include_router(help_router)
app.include_router(juno_router)

@app.get("/")
def root():
    return {"message": "ResumeAI backend is running 🚀"}
