"""
Smoke test for the shared job-description-from-a-link endpoint
(app/routers/job_description.py), used by cover letter, recruiter outreach,
interview answers, the ATS checker, the résumé builder, and AI Interviews.

Requirement under test:
  * Requires login, but NOT a paid plan (the ATS checker and résumé builder
    are free-tier, and this makes no LLM call, so it isn't gated like an AI
    generation is).
  * The SSRF guard rejects loopback/private/link-local/reserved hosts and
    non-http(s) schemes before any outbound request is made.
  * A real public URL is fetched, extracted, and truncated at 8,000 chars.

Runs the REAL FastAPI app over an in-memory SQLite DB — no Postgres. The SSRF
checks make no network call (rejected at DNS/IP validation); the happy-path
checks call the real extractor against real public test pages (example.com,
httpbin.org), matching the standalone verification already done for
app/utils/job_description_fetch.py.

Run:  python test_job_description_smoke.py
Exit code 0 = all assertions passed.
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["RUN_MIGRATIONS_ON_STARTUP"] = "false"
os.environ["JWT_SECRET_KEY"] = "smoke-test-jwt-key"

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient                                  # noqa: E402
from sqlalchemy import create_engine                                       # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB                           # noqa: E402
from sqlalchemy.ext.compiler import compiles                               # noqa: E402
from sqlalchemy.orm import sessionmaker                                    # noqa: E402
from sqlalchemy.pool import StaticPool                                     # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


from app.database.connection import Base, get_db                           # noqa: E402
from app.main import app                                                   # noqa: E402
from app.models.user import User                                           # noqa: E402
from app.models import user_settings as _settings                          # noqa: E402,F401
from app.core.security import get_current_user                             # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
Base.metadata.create_all(engine)

failures = []
checks = 0


def check(condition, label):
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}")


def section(title):
    print(f"\n{title}")


db = TestSession()
free_user = User(name="Free User", email="free@example.com", password_hash="x", role="user")
db.add(free_user)
db.commit()
FREE_ID = free_user.id
db.close()


def override_get_db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def override_current_user():
    session = TestSession()
    try:
        return session.get(User, FREE_ID)
    finally:
        session.close()


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_user] = override_current_user
client = TestClient(app)

# --- 1. requires login only, not a paid plan ---------------------------------------

section("1. Gating")

del app.dependency_overrides[get_current_user]
r = client.post("/job-description/from-url", json={"url": "https://example.com"})
check(r.status_code == 401, "an unauthenticated request is rejected")
app.dependency_overrides[get_current_user] = override_current_user

r = client.post("/job-description/from-url", json={"url": "https://example.com/"})
check(r.status_code == 200, "a FREE (unpaid) user can use it — this is not an AI generation")

# --- 2. SSRF guard -----------------------------------------------------------------

section("2. SSRF guard (no network call needed — rejected at DNS/IP validation)")

for bad_url in [
    "http://127.0.0.1:8010/interviews",
    "http://localhost/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://[::1]/",
    "ftp://example.com/x",
    "not a url",
    "",
]:
    r = client.post("/job-description/from-url", json={"url": bad_url})
    check(r.status_code in (400, 422), f"{bad_url!r} is rejected (got {r.status_code})")

# --- 3. happy path against real public pages ----------------------------------------

section("3. Real fetch + extraction")

r = client.post("/job-description/from-url", json={"url": "https://example.com/"})
check(r.status_code == 200, "example.com is fetched")
body = r.json()
check("Example Domain" in body["job_description"], "extracted text contains the real page content")
check(body["source_url"] == "https://example.com/", "source_url reflects the final URL")
check(body["truncated"] is False, "a short page is not marked truncated")

r = client.post("/job-description/from-url", json={"url": "https://httpbin.org/redirect-to?url=https://httpbin.org/html"})
check(r.status_code == 200, "a redirect is followed")
body = r.json()
check("Moby-Dick" in body["job_description"], "content is extracted from the article after the redirect")
check(body["source_url"] == "https://httpbin.org/html", "source_url reflects the post-redirect URL, not the original")

r = client.post("/job-description/from-url", json={"url": "https://en.wikipedia.org/wiki/Software_engineering"})
check(r.status_code == 422, "a bot-protected site returns a friendly 422, not a raw 403")
check("try pasting" in r.json()["detail"].lower(), "the message tells the user to fall back to paste")

# --- summary -------------------------------------------------------------------------

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("\nFAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("Job description smoke test: OK")
