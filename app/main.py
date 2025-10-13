from fastapi import FastAPI
from app.routers import user_routes

app = FastAPI(title="Resumen AI Backend")

app.include_router(user_routes.router)

@app.get("/")
def root():
    return {"message": "Resumen AI backend is running 🚀"}
