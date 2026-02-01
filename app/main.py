from fastapi import FastAPI
from app.routers.auth import router as auth_router
from app.routers.profile import router as profile_router
from app.routers.admin import router as admin_router
from app.routers.dashboard import router as dashboard_router
from app.routers.resumes import router as resumes_router
from app.routers.templates import router as templates_router
from app.routers.pricing import router as pricing_router
from app.routers.settings import router as settings_router
from app.routers.help_center import router as help_router
from app.middleware.session import UserSessionMiddleware
from app.core.swagger import setup_swagger

app = FastAPI(
    title="ResumeAI Backend",
    version="1.0.0",
    description="Backend APIs for the AI-powered resume builder.",
)

setup_swagger(app)

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

@app.get("/")
def root():
    return {"message": "ResumeAI backend is running 🚀"}
