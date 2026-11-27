from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.auth import router


def test_legacy_login_otp_routes_are_removed():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    paths = app.openapi()["paths"]
    for path in ("/auth/login/otp/start", "/auth/login/otp/verify"):
        assert path not in paths
        assert client.post(path).status_code == 404

    assert "post" in paths["/auth/login"]
    assert "post" in paths["/auth/google"]
    assert "post" in paths["/auth/signup/send-otp"]
    assert "post" in paths["/auth/signup/verify-otp"]
