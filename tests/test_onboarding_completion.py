from types import SimpleNamespace

from app.routers.profile import complete_onboarding


def test_completion_is_idempotent_and_applies_to_the_current_account():
    contact_details = dict(phone=None, location=None, linkedin_url=None, portfolio_url=None, credits_remaining=0)
    user = SimpleNamespace(id=1, email="first@example.com", name="First", onboarding_completed=False,
                           role="user", **contact_details)
    other = SimpleNamespace(id=2, onboarding_completed=False)
    commits = []
    db = SimpleNamespace(commit=lambda: commits.append(True))

    assert complete_onboarding(db, user).onboarding_completed
    assert complete_onboarding(db, user).onboarding_completed
    assert not other.onboarding_completed
    assert commits == [True]
