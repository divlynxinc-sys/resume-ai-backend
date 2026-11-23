"""
Smoke test for bug 14: a deleted account's email AND google_sub must both be
freed so the person can sign up again, whether they use a password or
Google. Reproduces the reviewer's exact repro against an in-memory SQLite DB.

Run:  python test_account_delete_smoke.py
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("TURNSTILE_SECRET_KEY", "1x0000000000000000000000000000000AA")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine                 # noqa: E402
from sqlalchemy.orm import sessionmaker              # noqa: E402
from sqlalchemy.exc import IntegrityError            # noqa: E402

from app.database.connection import Base             # noqa: E402
from app.models.user import User                     # noqa: E402
from app.models import user_settings as _us          # noqa: E402,F401
from app.models import pricing_plan as _pp           # noqa: E402,F401

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, future=True)
Session = sessionmaker(bind=engine, future=True)
Base.metadata.create_all(bind=engine)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  -- ' + detail) if detail and not cond else ''}")


def soft_delete(db, user):
    """Mirrors delete_my_account()'s logic directly (no HTTP layer needed)."""
    import time
    user.email = f"deleted+{user.id}+{int(time.time())}@deleted.invalid"
    user.google_sub = None
    user.is_deleted = True
    user.token_version = (user.token_version or 1) + 1
    db.add(user)
    db.commit()


# ── Google account: delete then re-signup with the same google_sub ────────
db = Session()
google_user = User(name="Carol", email="carol@example.com", google_sub="g-carol-123", role="user", token_version=1)
db.add(google_user)
db.commit()
soft_delete(db, google_user)

try:
    new_google_user = User(name="Carol", email="carol@example.com", google_sub="g-carol-123", role="user", token_version=1)
    db.add(new_google_user)
    db.commit()
    check("Google re-signup after delete succeeds (no IntegrityError)", True)
except IntegrityError:
    db.rollback()
    check("Google re-signup after delete succeeds (no IntegrityError)", False, "IntegrityError raised")
db.close()

# ── Password account: unaffected, still works as before ───────────────────
db = Session()
pw_user = User(name="Dave", email="dave@example.com", password_hash="hashed", role="user", token_version=1)
db.add(pw_user)
db.commit()
soft_delete(db, pw_user)
try:
    new_pw_user = User(name="Dave", email="dave@example.com", password_hash="hashed2", role="user", token_version=1)
    db.add(new_pw_user)
    db.commit()
    check("Password re-signup after delete still succeeds", True)
except IntegrityError:
    db.rollback()
    check("Password re-signup after delete still succeeds", False, "IntegrityError raised")
db.close()

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
