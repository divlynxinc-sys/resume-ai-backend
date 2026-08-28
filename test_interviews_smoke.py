"""
Smoke test for the AI Interviews router (app/routers/interviews.py).

Requirement under test:
  * Setup is validated, the résumé is snapshotted WITHOUT contact details, and a
    free user is blocked with 402 while a paid user gets a session.
  * Ownership is enforced with 404 (never 403) and bad transitions with 409.
  * `POST /{id}/start` mints a real LiveKit token scoped to one room + one identity
    with the interviewer dispatch attached, is idempotent, and consumes exactly one
    `ai_interviews` usage slot per interview (429 once the cap is reached).
  * The worker-only routes need the shared secret, and `finalize` is idempotent,
    routes an empty interview to `abandoned`, and otherwise produces a report whose
    overall score is the weighted formula (the AI service is stubbed).
  * A failing report lands in `failed` and `retry` recovers it.
  * Stale sessions self-heal, and delete drops the transcript + snapshot.

Runs the REAL FastAPI app over an in-memory SQLite DB (JSONB compiled as JSON) —
no Postgres, no LiveKit connection, no AI service. Only the outbound AI call is
stubbed; the LiveKit token is genuinely signed and decoded back.

Run:  python test_interviews_smoke.py
Exit code 0 = all assertions passed.
"""
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# Env must be set BEFORE app.core.config is imported (it reads os.getenv at import).
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["LIVEKIT_URL"] = "wss://smoke-test.livekit.cloud"
os.environ["LIVEKIT_API_KEY"] = "APIsmoketestkey"
os.environ["LIVEKIT_API_SECRET"] = "smoke-test-secret-value-at-least-32-chars-long"
os.environ["INTERVIEW_AGENT_NAME"] = "jobsynk-interviewer"
os.environ["INTERVIEW_AGENT_SECRET"] = "agent-shared-secret"
os.environ["USAGE_LIMIT_AI_INTERVIEWS"] = "2"
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
    """SQLite has no JSONB; the JSON type round-trips the same Python values."""
    return "JSON"


from app.core.config import SubscriptionState, UsageFeature                # noqa: E402
from app.database.connection import Base, get_db                           # noqa: E402
from app.main import app                                                   # noqa: E402
from app.models.ai_usage import AIUsageEvent                               # noqa: E402
from app.models.interview import InterviewSession, InterviewStatus         # noqa: E402
from app.models.pricing_plan import PricingPlan                            # noqa: E402
from app.models.resume import Resume                                       # noqa: E402
from app.models.user import User                                           # noqa: E402
from app.models import ats_score as _ats                                   # noqa: E402,F401
from app.models import help_article as _help                               # noqa: E402,F401
from app.models import juno_prompt as _juno                                # noqa: E402,F401
from app.models import session_tracking as _sessions                       # noqa: E402,F401
from app.models import template as _template                               # noqa: E402,F401
from app.models import user_settings as _settings                          # noqa: E402,F401
from app.core.security import get_current_user                             # noqa: E402
from app.utils import interviews as interviews_util                        # noqa: E402
from app.utils.interviews import SCORE_WEIGHTS                             # noqa: E402
from app.utils.usage_limits import weekly_limit                            # noqa: E402

AGENT_HEADERS = {"X-Interview-Agent-Key": "agent-shared-secret"}
BAD_AGENT_HEADERS = {"X-Interview-Agent-Key": "wrong"}

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
Base.metadata.create_all(engine)

# The background report job opens its own session; point it at the test engine.
interviews_util.SessionLocal = TestSession

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


# --- fixtures -------------------------------------------------------------------

RESUME_CONTENT = {
    "info": {
        "full_name": "Ayesha Khan",
        "email": "ayesha@example.com",
        "phone": "+92 300 1234567",
        "linkedin_url": "https://linkedin.com/in/ayesha",
    },
    "summary": "Frontend developer with four years of React and TypeScript experience.",
    "experience": [
        {
            "role": "Frontend Developer",
            "company": "Nimbus Labs",
            "start_date": "2023",
            "end_date": "Present",
            "description": "Led the Flightdeck dashboard rebuild\nCut load time from six seconds to under two",
        }
    ],
    "education": [{"school": "University of Karachi", "degree": "BSc", "field_of_study": "Computer Science"}],
    "skills": ["React", "TypeScript"],
    "job_description": {},
    "custom": {},
}

db = TestSession()
plan = PricingPlan(name="Monthly", slug="monthly", price=35.99, credits=0, is_active=True)
db.add(plan)
db.commit()

paid_user = User(
    name="Ayesha Khan",
    email="paid@example.com",
    password_hash="x",
    role="user",
    plan_id=plan.id,
    subscription_state=SubscriptionState.active,
)
free_user = User(name="Free User", email="free@example.com", password_hash="x", role="user")
other_user = User(
    name="Other User",
    email="other@example.com",
    password_hash="x",
    role="user",
    plan_id=plan.id,
    subscription_state=SubscriptionState.active,
)
db.add_all([paid_user, free_user, other_user])
db.commit()
db.add(Resume(user_id=paid_user.id, user_resume_id=1, title="Frontend résumé", content=RESUME_CONTENT))
db.add(Resume(user_id=other_user.id, user_resume_id=1, title="Someone else's résumé", content=RESUME_CONTENT))
db.commit()
PAID_ID, FREE_ID, OTHER_ID = paid_user.id, free_user.id, other_user.id
db.close()

current_user_id = PAID_ID


def override_get_db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def override_current_user():
    session = TestSession()
    try:
        return session.get(User, current_user_id)
    finally:
        session.close()


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_user] = override_current_user
client = TestClient(app)


def as_user(user_id):
    global current_user_id
    current_user_id = user_id


# --- stub the AI service --------------------------------------------------------

AI_REPORT = {
    "questions": [
        {"id": "q1", "prompt": "Tell me about yourself.", "category": "warm_up", "is_follow_up": False},
        {"id": "q2", "prompt": "Walk me through the Flightdeck rebuild.", "category": "project", "is_follow_up": False},
    ],
    "answers": [
        {
            "question_id": "q1",
            "transcript": "I'm a frontend developer with four years of experience.",
            "evaluation": {
                "scores": {"relevance": 80, "evidence": 70, "structure": 75, "role_alignment": 82, "communication": 190},
                "evidence": "States four years of React experience.",
                "worked": ["Clear and concise"],
                "improvements": ["Add a headline achievement"],
                "improved_outline": ["Open with the role", "Name one result"],
            },
        },
        {
            "question_id": "q2",
            "transcript": "I led the rebuild and cut load time from six seconds to under two.",
            "evaluation": {
                "scores": {"relevance": 90, "evidence": 88, "structure": 80, "role_alignment": 85, "communication": 84},
                "evidence": "Gives the before and after load times.",
                "worked": ["Concrete metric"],
                "improvements": ["Name the trade-off"],
                "improved_outline": ["Set the problem", "Explain the fix", "Close with the metric"],
            },
        },
        {"question_id": "ghost", "transcript": "Answer to a question that does not exist.", "evaluation": {}},
    ],
    "report": {
        "scores": {"relevance": 85, "evidence": 79, "structure": 78, "role_alignment": 84, "communication": 87},
        "summary": "Solid, evidence-backed answers.",
        "strengths": ["Concrete metrics", "Clear delivery"],
        "improvements": ["Name trade-offs"],
        "action_plan": ["Prepare three stories", "Add a metric to each"],
        "overall_score": 3,  # deliberately wrong: the backend must recompute it
    },
    "evaluation_version": "interview-eval-v1",
}

ai_calls = []
ai_should_fail = False


def fake_post_json(url, payload, **kwargs):
    ai_calls.append({"url": url, "payload": payload})
    if ai_should_fail:
        raise RuntimeError("AI service unreachable: simulated outage")
    return AI_REPORT


interviews_util.post_json = fake_post_json

TRANSCRIPT = [
    {"role": "assistant", "text": "Tell me about yourself.", "at": 1.0},
    {"role": "user", "text": "I'm a frontend developer with four years of React and TypeScript experience.", "at": 9.0},
    {"role": "assistant", "text": "Walk me through the Flightdeck rebuild.", "at": 14.0},
    {"role": "user", "text": "I led the rebuild and cut load time from six seconds to under two seconds.", "at": 40.0},
]

VALID_SETUP = {
    "interview_type": "behavioural",
    "role_title": "Frontend Developer",
    "seniority": "mid",
    "duration_minutes": 15,
    "resume_id": 1,
    "job_description": "Own the React design system.",
}


def usage_count(user_id):
    session = TestSession()
    try:
        return (
            session.query(AIUsageEvent)
            .filter(AIUsageEvent.user_id == user_id, AIUsageEvent.feature == UsageFeature.ai_interviews)
            .count()
        )
    finally:
        session.close()


def row(session_id):
    session = TestSession()
    try:
        return session.get(InterviewSession, session_id)
    finally:
        session.close()


# --- 1. create + validation + gating ---------------------------------------------

section("1. Create, validation and plan gating")

as_user(FREE_ID)
r = client.post("/interviews", json=VALID_SETUP)
check(r.status_code == 402 and r.json()["detail"]["code"] == "requires_plan", "free user gets 402 requires_plan")

as_user(PAID_ID)
r = client.post("/interviews", json={**VALID_SETUP, "duration_minutes": 12})
check(r.status_code == 422, "duration outside 10/15/20 is rejected")
r = client.post("/interviews", json={**VALID_SETUP, "seniority": "principal"})
check(r.status_code == 422, "unknown seniority is rejected")
r = client.post("/interviews", json={**VALID_SETUP, "job_description": "x" * 8001})
check(r.status_code == 422, "job description over 8,000 chars is rejected")
r = client.post("/interviews", json={**VALID_SETUP, "resume_id": 99})
check(r.status_code == 404, "a résumé the user does not own is 404")

r = client.post("/interviews", json=VALID_SETUP)
check(r.status_code == 201, "paid user creates an interview (201)")
created = r.json()
SID = created["id"]
check(created["status"] == InterviewStatus.ready, "new interview is 'ready'")
check(created["question_target"] == 4, "15 minutes targets 4 questions")
check(created["resume_title"] == "Frontend résumé", "résumé title is copied for display")

snapshot = row(SID).resume_snapshot
blob = json.dumps(snapshot)
check("Nimbus Labs" in blob and "Flightdeck" in blob, "snapshot keeps experience and bullets")
check("ayesha@example.com" not in blob, "snapshot strips the email")
check("+92 300 1234567" not in blob, "snapshot strips the phone")
check("linkedin.com/in/ayesha" not in blob, "snapshot strips the LinkedIn URL")
check("resume_snapshot" not in created, "the snapshot is never returned to the browser")

# --- 2. ownership ------------------------------------------------------------------

section("2. Ownership")

as_user(OTHER_ID)
check(client.get(f"/interviews/{SID}").status_code == 404, "another user reading the interview gets 404, not 403")
check(client.post(f"/interviews/{SID}/start").status_code == 404, "another user cannot start it")
check(client.delete(f"/interviews/{SID}").status_code == 404, "another user cannot delete it")
check(client.get("/interviews").json()["total"] == 0, "history only lists your own interviews")

# --- 3. start: token, idempotency, usage cap ----------------------------------------

section("3. Start — LiveKit token, idempotency, usage cap")

as_user(PAID_ID)
r = client.post(f"/interviews/{SID}/start")
check(r.status_code == 200, "start returns 200")
body = r.json()
check(body["session"]["status"] == InterviewStatus.in_progress, "start moves the session to in_progress")

conn = body["connection"]
check(conn["url"] == "wss://smoke-test.livekit.cloud", "connection carries the LiveKit URL")
check(conn["room_name"] == f"interview-{SID}", "room is interview-<id>")
check(conn["participant_identity"] == f"user-{PAID_ID}", "identity is user-<id>")

claims = json.loads(base64.urlsafe_b64decode(conn["token"].split(".")[1] + "=="))
video = claims["video"]
check(video["room"] == f"interview-{SID}" and video["roomJoin"] is True, "token grants join on exactly one room")
check(video.get("canPublishSources") == ["microphone"], "token is microphone-only (never camera)")
check(claims["sub"] == f"user-{PAID_ID}", "token identity matches the participant")
agents = (claims.get("roomConfig") or {}).get("agents") or []
check(
    len(agents) == 1 and agents[0]["agentName"] == "jobsynk-interviewer",
    "token dispatches the jobsynk-interviewer worker",
)
check(json.loads(agents[0]["metadata"])["session_id"] == SID, "dispatch metadata carries the session id")
check(0 < claims["exp"] - claims["nbf"] <= 45 * 60, "token expires within the configured TTL")

check(usage_count(PAID_ID) == 1, "starting consumed exactly one usage slot")
r2 = client.post(f"/interviews/{SID}/start")
check(r2.status_code == 200 and r2.json()["session"]["status"] == InterviewStatus.in_progress, "re-start is idempotent")
check(usage_count(PAID_ID) == 1, "re-starting does NOT consume a second usage slot")
check(r2.json()["connection"]["token"] != "", "re-start issues a fresh token (refresh recovery)")

# --- 4. worker routes ----------------------------------------------------------------

section("4. Worker routes — auth and briefing")

check(client.get(f"/internal/interviews/{SID}/context").status_code == 401, "context without the secret is 401")
check(
    client.get(f"/internal/interviews/{SID}/context", headers=BAD_AGENT_HEADERS).status_code == 401,
    "context with a wrong secret is 401",
)
r = client.get(f"/internal/interviews/{SID}/context", headers=AGENT_HEADERS)
check(r.status_code == 200, "context with the shared secret is 200")
ctx = r.json()
check(ctx["role_title"] == "Frontend Developer" and ctx["question_target"] == 4, "briefing carries the setup")
check(ctx["candidate_name"] == "Ayesha", "briefing passes only the candidate's first name")
check("ayesha@example.com" not in json.dumps(ctx), "briefing carries no contact details")
check(
    client.post(f"/internal/interviews/{SID}/finalize", json={"transcript": [], "ended_reason": "completed"}).status_code == 401,
    "finalize without the secret is 401",
)

# --- 5. finalize + report -------------------------------------------------------------

section("5. Finalize and report generation")

r = client.post(
    f"/internal/interviews/{SID}/finalize",
    headers=AGENT_HEADERS,
    json={"transcript": TRANSCRIPT, "ended_reason": "completed"},
)
check(r.status_code == 202, "finalize is accepted (202)")
check(len(ai_calls) == 1 and ai_calls[0]["url"].endswith("/interview/report"), "the AI service was called once")
sent = ai_calls[0]["payload"]
check([t["role"] for t in sent["transcript"]] == ["assistant", "user", "assistant", "user"], "the full transcript is sent")
check(sent["role_title"] == "Frontend Developer" and sent["seniority"] == "mid", "role context is sent for calibration")

as_user(PAID_ID)
session_json = client.get(f"/interviews/{SID}").json()
check(session_json["status"] == InterviewStatus.report_ready, "the session reaches report_ready")

report = session_json["report"]
expected = round(sum(report["scores"][k] * w for k, w in SCORE_WEIGHTS.items()))
check(report["overall_score"] == expected, f"overall score is the weighted formula ({expected}), not the model's number")
check(report["scores"]["communication"] <= 100, "out-of-range model scores are clamped")
check(len(session_json["answers"]) == 2, "the answer for an unknown question id is dropped")
check(
    session_json["answers"][0]["evaluation"]["scores"]["communication"] == 100,
    "a 190 sub-score is clamped to 100",
)
check(len(session_json["questions"]) == 2, "questions are stored")
check(len(session_json.get("transcript") or []) == 4, "the transcript is returned once the report is ready")

before = len(ai_calls)
r = client.post(
    f"/internal/interviews/{SID}/finalize",
    headers=AGENT_HEADERS,
    json={"transcript": TRANSCRIPT, "ended_reason": "completed"},
)
check(r.status_code == 202 and len(ai_calls) == before, "a duplicate finalize does not re-run the report")
check(client.post(f"/interviews/{SID}/start").status_code == 409, "starting a finished interview is 409 state_conflict")

# --- 6. abandoned path -----------------------------------------------------------------

section("6. Interview with nothing to score")

r = client.post("/interviews", json={**VALID_SETUP, "resume_id": None})
EMPTY_SID = r.json()["id"]
client.post(f"/interviews/{EMPTY_SID}/start")
before = len(ai_calls)
client.post(
    f"/internal/interviews/{EMPTY_SID}/finalize",
    headers=AGENT_HEADERS,
    json={
        "transcript": [
            {"role": "assistant", "text": "Tell me about yourself."},
            {"role": "user", "text": "sorry, bye"},
        ],
        "ended_reason": "candidate_left",
    },
)
state = client.get(f"/interviews/{EMPTY_SID}").json()
check(state["status"] == InterviewStatus.abandoned, "an interview with no real answer becomes 'abandoned'")
check(len(ai_calls) == before, "no report is generated for an abandoned interview")
check(client.post("/interviews", json=VALID_SETUP).status_code == 201, "an abandoned interview frees the open-session slot")

# --- 7. usage cap ------------------------------------------------------------------------

section("7. Concurrent-session cap and weekly usage cap")


def open_sessions():
    session = TestSession()
    try:
        return (
            session.query(InterviewSession)
            .filter(
                InterviewSession.user_id == PAID_ID,
                InterviewSession.status.in_([InterviewStatus.ready, InterviewStatus.in_progress]),
            )
            .count()
        )
    finally:
        session.close()


# Fill up to the concurrent-session cap (MAX_OPEN_SESSIONS = 2), then expect 409.
open_ids = []
while open_sessions() < 2:
    created_r = client.post("/interviews", json={**VALID_SETUP, "resume_id": None})
    check(created_r.status_code == 201, "creating an interview under the open-session cap succeeds")
    open_ids.append(created_r.json()["id"])
r = client.post("/interviews", json={**VALID_SETUP, "resume_id": None})
check(
    r.status_code == 409 and r.json()["detail"]["code"] == "too_many_open_interviews",
    "a third simultaneously-open interview is 409 too_many_open_interviews",
)

# Effective cap = base (USAGE_LIMIT_AI_INTERVIEWS=2) x the plan multiplier, so a
# monthly subscriber gets 4. Derive it rather than hardcoding, then fill it up.
cap = weekly_limit(UsageFeature.ai_interviews, "monthly")
check(cap == 4, f"the monthly plan doubles the base cap of 2 (effective {cap})")
check(usage_count(PAID_ID) == 2, "two starts consumed so far")

session = TestSession()
try:
    for _ in range(cap - usage_count(PAID_ID)):
        session.add(AIUsageEvent(user_id=PAID_ID, feature=UsageFeature.ai_interviews))
    session.commit()
finally:
    session.close()
check(usage_count(PAID_ID) == cap, "the weekly cap is now fully consumed")

r = client.post(f"/interviews/{open_ids[-1]}/start")
check(r.status_code == 429, "starting past the weekly cap is 429")
detail = r.json().get("detail", {})
check(detail.get("code") == "usage_limit_reached", "429 carries the usage_limit_reached code")
check(detail.get("feature") == UsageFeature.ai_interviews, "429 names the ai_interviews feature")
check("resets_at" in detail, "429 tells the frontend when the cap resets")

# --- 8. failure + retry --------------------------------------------------------------------

section("8. Report failure and retry")

session = TestSession()
try:
    for s in session.query(InterviewSession).all():
        if s.id not in (SID, EMPTY_SID):
            session.delete(s)
    session.query(AIUsageEvent).delete()
    session.commit()
finally:
    session.close()

r = client.post("/interviews", json={**VALID_SETUP, "resume_id": None})
FAIL_SID = r.json()["id"]
client.post(f"/interviews/{FAIL_SID}/start")
ai_should_fail = True
client.post(
    f"/internal/interviews/{FAIL_SID}/finalize",
    headers=AGENT_HEADERS,
    json={"transcript": TRANSCRIPT, "ended_reason": "completed"},
)
state = client.get(f"/interviews/{FAIL_SID}").json()
check(state["status"] == InterviewStatus.failed, "an AI outage lands the session in 'failed'")
check(state["error"] and "unreachable" not in state["error"].lower(), "the user-facing error hides the provider detail")
check(row(FAIL_SID).transcript is not None, "the transcript survives a failed report")

ai_should_fail = False
r = client.post(f"/interviews/{FAIL_SID}/retry")
check(r.status_code == 200, "retry is accepted")
state = client.get(f"/interviews/{FAIL_SID}").json()
check(state["status"] == InterviewStatus.report_ready, "retry rebuilds the report from the saved transcript")
check(client.post(f"/interviews/{FAIL_SID}/retry").status_code == 409, "retrying a healthy interview is 409")

# --- 9. stale reconciliation ------------------------------------------------------------------

section("9. Stale-session self-healing")

r = client.post("/interviews", json={**VALID_SETUP, "resume_id": None})
STALE_SID = r.json()["id"]
client.post(f"/interviews/{STALE_SID}/start")
client.post(f"/interviews/{STALE_SID}/complete")
check(client.get(f"/interviews/{STALE_SID}").json()["status"] == InterviewStatus.processing, "complete moves to processing")

session = TestSession()
try:
    s = session.get(InterviewSession, STALE_SID)
    s.processing_started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    session.commit()
finally:
    session.close()
state = client.get(f"/interviews/{STALE_SID}").json()
check(state["status"] == InterviewStatus.failed, "processing with no transcript for >4 min self-heals to 'failed'")
check(client.post(f"/interviews/{STALE_SID}/retry").status_code == 409, "a session with no transcript cannot be retried")

session = TestSession()
try:
    s = session.get(InterviewSession, STALE_SID)
    s.status = InterviewStatus.in_progress
    s.transcript = None
    s.started_at = datetime.now(timezone.utc) - timedelta(minutes=90)
    session.commit()
finally:
    session.close()
check(
    client.get(f"/interviews/{STALE_SID}").json()["status"] == InterviewStatus.abandoned,
    "an in_progress session far past its duration self-heals to 'abandoned'",
)

# --- 10. delete ---------------------------------------------------------------------------------

section("10. Delete")

check(client.delete(f"/interviews/{SID}").status_code == 204, "delete returns 204")
check(client.get(f"/interviews/{SID}").status_code == 404, "a deleted interview is 404 afterwards")
deleted = row(SID)
check(deleted.status == InterviewStatus.deleted and deleted.deleted_at is not None, "delete is a soft delete")
check(deleted.transcript is None and deleted.resume_snapshot is None, "delete drops the transcript and the snapshot")
check(all(item["id"] != SID for item in client.get("/interviews").json()["items"]), "deleted interviews leave the history")

# Job-description-from-a-link moved to its own shared endpoint
# (POST /job-description/from-url, not paid-gated) — see test_job_description_smoke.py.

# --- summary -------------------------------------------------------------------------------------

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("\nFAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("AI Interviews smoke test: OK")
