"""
Smoke test for the hidden weekly AI usage caps (app.utils.usage_limits).

Requirement under test:
  * A user may run at most `limit` AI generations of a feature per rolling window.
  * Exceeding it raises HTTP 429 with a structured {code, message, feature, resets_at} detail.
  * Limits scale by plan tier (multiplier); admins bypass entirely; a feature whose
    base limit is 0 is uncapped; events older than the window don't count.

This exercises the REAL enforce_usage_limit() against an in-memory SQLite DB with
tiny caps injected via env vars — no Postgres and no AI service required.

Run:  python test_usage_limits_smoke.py
Exit code 0 = all assertions passed.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

# Inject small, deterministic caps BEFORE importing config (it reads env at import).
os.environ["DATABASE_URL"] = "sqlite://"            # harmless; we build our own engine
os.environ["USAGE_WINDOW_DAYS"] = "7"
os.environ["USAGE_LIMIT_QA_ANSWERS"] = "3"          # base cap for the main tests
os.environ["USAGE_LIMIT_RESUME_AI"] = "2"           # used by the window test
os.environ["USAGE_LIMIT_HR_EMAIL"] = "0"            # 0 => uncapped
os.environ["USAGE_MULT_MONTHLY"] = "2"              # monthly plan doubles the cap

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import HTTPException                                   # noqa: E402
from sqlalchemy import create_engine, text                         # noqa: E402
from sqlalchemy.orm import sessionmaker                            # noqa: E402

from app.core.config import UsageFeature                            # noqa: E402
from app.database.connection import Base                            # noqa: E402
from app.models.user import User                                    # noqa: E402
from app.models import user_settings as _user_settings              # noqa: E402,F401  (registers UserSettings for the mapper)
from app.models.ai_usage import AIUsageEvent                        # noqa: E402
from app.utils.usage_limits import enforce_usage_limit, weekly_limit  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")


# ── DB setup ──────────────────────────────────────────────────────────────────────
# expire_on_commit=False so accessing user.id after commit doesn't trigger a refresh
# (which, via User's joined `settings` relationship, would hit a table we didn't create).
engine = create_engine("sqlite://", future=True)
Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)

# Minimal pricing_plans table (only the columns _plan_slug_for actually selects).
with engine.begin() as conn:
    conn.execute(text("CREATE TABLE pricing_plans (id INTEGER PRIMARY KEY, slug VARCHAR)"))
    conn.execute(text("INSERT INTO pricing_plans (id, slug) VALUES (1, 'monthly')"))

Base.metadata.create_all(engine, tables=[User.__table__, AIUsageEvent.__table__])

db = Session()
_uid = [0]


def new_user(role="user", plan_id=None):
    _uid[0] += 1
    u = User(name=f"u{_uid[0]}", email=f"u{_uid[0]}@t.co", role=role, plan_id=plan_id)
    db.add(u)
    db.commit()
    return u


def event_count(user_id, feature):
    return db.query(AIUsageEvent).filter(
        AIUsageEvent.user_id == user_id, AIUsageEvent.feature == feature
    ).count()


def raises_429(user, feature):
    try:
        enforce_usage_limit(db, user, feature)
        return None
    except HTTPException as e:
        return e


# ── Tests ─────────────────────────────────────────────────────────────────────────
def test_under_limit_records_and_blocks():
    print("\n-- free/no-plan user, qa_answers cap = 3 --")
    u = new_user()
    for i in range(3):
        err = raises_429(u, UsageFeature.qa_answers)
        check(f"call {i + 1}/3 allowed", err is None, f"unexpected 429: {err}")
    check("3 events recorded", event_count(u.id, UsageFeature.qa_answers) == 3,
          f"count={event_count(u.id, UsageFeature.qa_answers)}")

    err = raises_429(u, UsageFeature.qa_answers)
    check("4th call is blocked", err is not None and err.status_code == 429,
          f"got {err.status_code if err else 'no error'}")
    d = err.detail if err else {}
    check("429 detail.code == usage_limit_reached", isinstance(d, dict) and d.get("code") == "usage_limit_reached", str(d))
    check("429 detail.feature == qa_answers", isinstance(d, dict) and d.get("feature") == "qa_answers", str(d))
    ok_reset = False
    if isinstance(d, dict) and d.get("resets_at"):
        try:
            ok_reset = datetime.fromisoformat(d["resets_at"]) > datetime.now(timezone.utc)
        except Exception:
            ok_reset = False
    check("429 detail.resets_at is a future ISO timestamp", ok_reset, str(d.get("resets_at") if isinstance(d, dict) else d))
    check("blocked call did NOT record an extra event", event_count(u.id, UsageFeature.qa_answers) == 3,
          f"count={event_count(u.id, UsageFeature.qa_answers)}")


def test_admin_bypass():
    print("\n-- admin bypasses the cap --")
    a = new_user(role="admin")
    blocked = False
    for _ in range(10):                                   # well over the cap of 3
        if raises_429(a, UsageFeature.qa_answers) is not None:
            blocked = True
            break
    check("admin never blocked over 10 calls", not blocked)
    check("admin records no usage events", event_count(a.id, UsageFeature.qa_answers) == 0,
          f"count={event_count(a.id, UsageFeature.qa_answers)}")


def test_plan_scaling():
    print("\n-- monthly plan doubles the cap (3 -> 6) --")
    check("weekly_limit(qa, None) == 3", weekly_limit(UsageFeature.qa_answers, None) == 3)
    check("weekly_limit(qa, 'monthly') == 6", weekly_limit(UsageFeature.qa_answers, "monthly") == 6)
    u = new_user(plan_id=1)                               # plan_id 1 -> slug 'monthly'
    allowed = 0
    for _ in range(6):
        if raises_429(u, UsageFeature.qa_answers) is None:
            allowed += 1
    check("monthly user allowed 6 calls", allowed == 6, f"allowed={allowed}")
    check("7th call blocked", raises_429(u, UsageFeature.qa_answers) is not None)


def test_rolling_window_excludes_old_events():
    print("\n-- events older than the window don't count (resume_ai cap = 2) --")
    u = new_user()
    old = datetime.now(timezone.utc) - timedelta(days=10)   # outside the 7-day window
    for _ in range(5):                                       # 5 stale events
        db.add(AIUsageEvent(user_id=u.id, feature=UsageFeature.resume_ai, created_at=old))
    db.commit()
    # In-window count is 0, so the first 2 calls must still be allowed.
    a1 = raises_429(u, UsageFeature.resume_ai)
    a2 = raises_429(u, UsageFeature.resume_ai)
    check("stale events ignored: 2 fresh calls allowed", a1 is None and a2 is None,
          f"a1={a1}, a2={a2}")
    check("3rd fresh call blocked", raises_429(u, UsageFeature.resume_ai) is not None)


def test_uncapped_feature():
    print("\n-- hr_email base=0 => uncapped --")
    check("weekly_limit(hr_email, None) is None", weekly_limit(UsageFeature.hr_email, None) is None)
    u = new_user()
    blocked = False
    for _ in range(25):
        if raises_429(u, UsageFeature.hr_email) is not None:
            blocked = True
            break
    check("uncapped feature never blocks (25 calls)", not blocked)
    check("uncapped feature records no events", event_count(u.id, UsageFeature.hr_email) == 0,
          f"count={event_count(u.id, UsageFeature.hr_email)}")


def main():
    print("== AI usage-limit smoke tests ==")
    test_under_limit_records_and_blocks()
    test_admin_bypass()
    test_plan_scaling()
    test_rolling_window_excludes_old_events()
    test_uncapped_feature()

    total = len(PASS) + len(FAIL)
    print(f"\n==== RESULT: {len(PASS)}/{total} passed, {len(FAIL)} failed ====")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
