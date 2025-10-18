from fastapi import FastAPI
from app.routers.user_routes import router as user_router

app = FastAPI(title="ResumeAI Backend")

app.include_router(user_router)

@app.get("/")
def root():
    return {"message": "ResumeAI backend is running 🚀"}
