"""
Smoke test for the forgot-password / reset-password flow (app.routers.auth).

Exercises the REAL endpoints through FastAPI's TestClient against an
in-memory SQLite DB -- no Postgres, no live email provider, no Cloudflare
call (verify_turnstile is monkeypatched to a no-op; Turnstile itself is
already covered by tests/test_turnstile.py).

Run:  python test_forgot_password_smoke.py
Exit code 0 = all assertions passed.
"""
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("TURNSTILE_SECRET_KEY", "1x0000000000000000000000000000000AA")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient                           # noqa: E402
from sqlalchemy import create_engine                                 # noqa: E402
from sqlalchemy.orm import sessionmaker                              # noqa: E402
from sqlalchemy.pool import StaticPool                               # noqa: E402

from app.database.connection import Base, get_db                     # noqa: E402
from app.models.user import User                                     # noqa: E402
from app.models import user_settings as _user_settings               # noqa: E402,F401
import app.utils.turnstile as turnstile_mod                          # noqa: E402

# Never actually call Cloudflare in a smoke test -- that's test_turnstile.py's job.
turnstile_mod.verify_turnstile = lambda *a, **kw: None

from app.utils import auth_utils                                     # noqa: E402
import app.routers.auth as auth_mod                                  # noqa: E402
auth_mod.verify_turnstile = lambda *a, **kw: None

sent_emails = []
auth_mod.send_password_reset_email = lambda to, link: sent_emails.append(("reset", to, link))
auth_mod.send_password_changed_email = lambda to: sent_emails.append(("changed", to))

from app.main import app                                             # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  -- ' + detail) if detail and not cond else ''}")


def make_user(email, password="OldPass123", google_only=False):
    db = TestSession()
    user = User(
        name="Test",
        email=email,
        password_hash=None if google_only else auth_utils.hash_password(password),
        google_sub=f"g-{email}" if google_only else None,
        role="user",
        token_version=1,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user.id


# ── 1. Unknown email gets the identical response as a known one ────────────────────
r_unknown = client.post("/auth/forgot-password", json={"email": "ghost@example.com", "turnstile_token": "t"})
known_id = make_user("alice@example.com")
r_known = client.post("/auth/forgot-password", json={"email": "alice@example.com", "turnstile_token": "t"})
check("unknown email: 200", r_unknown.status_code == 200)
check("known email: 200", r_known.status_code == 200)
check("identical response body", r_unknown.json() == r_known.json(), f"{r_unknown.json()} vs {r_known.json()}")
check("no email sent for unknown address", not any(e[1] == "ghost@example.com" for e in sent_emails if e[0] == "reset"))
check("email sent for known address", any(e[1] == "alice@example.com" for e in sent_emails if e[0] == "reset"))

reset_link = next(e[2] for e in sent_emails if e[0] == "reset" and e[1] == "alice@example.com")
raw_token = reset_link.split("token=")[1]

# ── 2. Per-account cooldown: a second request within 60s sends no new email ─────────
sent_before = len(sent_emails)
client.post("/auth/forgot-password", json={"email": "alice@example.com", "turnstile_token": "t"})
check("cooldown: no new email within 60s", len(sent_emails) == sent_before)

# ── 3. Weak password rejected (422, before any DB lookup) ───────────────────────────
r_weak = client.post("/auth/reset-password", json={"token": raw_token, "new_password": "short"})
check("weak password rejected with 422", r_weak.status_code == 422)

# ── 4. Successful reset with a valid token ──────────────────────────────────────────
r_reset = client.post("/auth/reset-password", json={"token": raw_token, "new_password": "BrandNewPass1"})
check("valid reset returns 200", r_reset.status_code == 200, r_reset.text)
check("password-changed email sent", any(e[0] == "changed" and e[1] == "alice@example.com" for e in sent_emails))

db = TestSession()
alice = db.query(User).filter(User.id == known_id).first()
check("token cleared after use", alice.password_reset_token_hash is None)
check("token_version bumped (old sessions logged out)", alice.token_version == 2)
check("new password actually works", auth_utils.verify_password("BrandNewPass1", alice.password_hash))
db.close()

# ── 5. Reusing the same (now-cleared) token fails ───────────────────────────────────
r_reuse = client.post("/auth/reset-password", json={"token": raw_token, "new_password": "AnotherPass1"})
check("reused token rejected", r_reuse.status_code == 400)

# ── 6. Expired token is rejected ────────────────────────────────────────────────────
expired_user_id = make_user("bob@example.com")
db = TestSession()
bob = db.query(User).filter(User.id == expired_user_id).first()
bob.password_reset_token_hash = hashlib.sha256(b"expired-raw-token").hexdigest()
bob.password_reset_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
db.add(bob)
db.commit()
db.close()
r_expired = client.post("/auth/reset-password", json={"token": "expired-raw-token", "new_password": "ValidPass1"})
check("expired token rejected", r_expired.status_code == 400)

# ── 7. Garbage token rejected with the same generic error (no enumeration) ─────────
r_garbage = client.post("/auth/reset-password", json={"token": "not-a-real-token", "new_password": "ValidPass1"})
check("garbage token rejected", r_garbage.status_code == 400)
check("same error message as expired token", r_garbage.json()["detail"] == r_expired.json()["detail"])

# ── 8. Google-only account (no password_hash) can still reset ──────────────────────
google_id = make_user("carol@example.com", google_only=True)
r_fp_google = client.post("/auth/forgot-password", json={"email": "carol@example.com", "turnstile_token": "t"})
check("forgot-password works for Google-only account", r_fp_google.status_code == 200)
google_link = next(e[2] for e in sent_emails if e[0] == "reset" and e[1] == "carol@example.com")
google_token = google_link.split("token=")[1]
r_reset_google = client.post("/auth/reset-password", json={"token": google_token, "new_password": "GoogleUserPass1"})
check("Google-only account reset succeeds", r_reset_google.status_code == 200, r_reset_google.text)
db = TestSession()
carol = db.query(User).filter(User.id == google_id).first()
check("Google-only account now has a password", carol.password_hash is not None)
db.close()


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
sys.exit(0)
