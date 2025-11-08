from fastapi import FastAPI
from app.routers.user_routes import router as user_router
from app.routers.auth import router as auth_router
from app.routers.profile import router as profile_router
from app.routers.admin import router as admin_router
from app.middleware.session import UserSessionMiddleware

app = FastAPI(title="ResumeAI Backend")

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
app.include_router(user_router)

@app.get("/")
def root():
    return {"message": "ResumeAI backend is running 🚀"}
