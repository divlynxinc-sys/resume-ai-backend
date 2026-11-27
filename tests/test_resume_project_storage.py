from types import SimpleNamespace

from app.routers import resumes
from app.schemas.resume_schema import ResumeSection
from app.utils.resume_ai_adapter import ai_optimized_resume_to_backend_content


def test_builder_custom_merge_keeps_other_keys_and_can_clear_projects(monkeypatch):
    resume = SimpleNamespace(content={"custom": {
        "projects": [{"title": "Existing project"}],
        "sections": [{"title": "Existing project"}],
        "social_urls": {"github": ["https://github.com/example"]},
    }})
    monkeypatch.setattr(resumes, "_get_owned_resume", lambda *_: resume)
    db = SimpleNamespace(add=lambda _resume: None, commit=lambda: None)
    response = resumes.patch_content(
        resume_id=5, section=ResumeSection.custom, merge_custom=True,
        payload={"projects": [], "sections": []}, db=db, user=SimpleNamespace(id=1),
    )
    assert response["content"] == {
        "projects": [], "sections": [], "social_urls": {"github": ["https://github.com/example"]},
    }


def test_ai_result_mirrors_projects_with_existing_labels():
    prior = {"custom": {
        "projects": [{"title": "Portfolio", "link": "https://example.com", "link_label": "View portfolio"}],
        "sections": [{"title": "Portfolio", "content": "Old bullet"}],
    }}
    updated = ai_optimized_resume_to_backend_content(
        prior, None, {"projects": [{"title": "Portfolio", "bullets": ["New bullet"]}]},
    )
    assert updated["custom"]["sections"] == [{
        "title": "Portfolio", "content": "New bullet", "link": "https://example.com", "linkLabel": "View portfolio",
    }]
    assert prior["custom"]["sections"][0]["content"] == "Old bullet"
    assert ai_optimized_resume_to_backend_content(prior, None, {})["custom"] == prior["custom"]
    assert ai_optimized_resume_to_backend_content(prior, None, {"projects": []})["custom"] == prior["custom"]
