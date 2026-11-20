"""
Smoke test for bug 10: /docs, /redoc, /openapi.json must be gone (404) in
production and available (200) locally. Tests via real HTTP requests
(TestClient), not by inspecting app.docs_url -- FastAPI registers these
routes at construction time, so mutating the attribute afterward doesn't
remove them (this is exactly the bug the first attempt had).

Run:  python test_swagger_production_smoke.py
"""
import os
import sys

os.environ.setdefault("TURNSTILE_SECRET_KEY", "1x0000000000000000000000000000000AA")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_NAME", "x")
os.environ.setdefault("DB_USER", "x")
os.environ.setdefault("DB_PASSWORD", "x")
os.environ.setdefault("DB_PORT", "5432")

sys.path.insert(0, os.path.dirname(__file__))

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


os.environ["APP_ENV"] = "production"
import importlib
import app.main as main_mod
importlib.reload(main_mod)
from fastapi.testclient import TestClient

client = TestClient(main_mod.app, raise_server_exceptions=False)
for path in ("/docs", "/redoc", "/openapi.json"):
    check(f"production: {path} returns 404", client.get(path).status_code == 404)

os.environ.pop("APP_ENV")
importlib.reload(main_mod)
client = TestClient(main_mod.app, raise_server_exceptions=False)
for path in ("/docs", "/redoc", "/openapi.json"):
    check(f"local: {path} returns 200", client.get(path).status_code == 200)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
