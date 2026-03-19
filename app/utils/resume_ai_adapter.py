"""
Adapter between `resumeai-backend` resume content schema and `resumeai-AI`
(`/generate_resume`) request/response schemas.

This keeps the integration logic localized and makes the router thin.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


def _clean_bullets_from_description(description: Any) -> List[str]:
    if not description:
        return []
    text = str(description).strip()
    if not text:
        return []
    # Split by newlines and strip common bullet prefixes.
    bullets: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^([-*•]|\\d+\\.)\\s*", "", s).strip()
        if s:
            bullets.append(s)
    return bullets


def backend_content_to_ai_request(resume_content: Dict[str, Any]) -> Dict[str, Any]:
    info = resume_content.get("info") or {}
    job_description_obj = resume_content.get("job_description") or {}
    custom = resume_content.get("custom") or {}

    job_title = (job_description_obj.get("job_title") or "").strip()
    company = (job_description_obj.get("company") or "").strip()
    location = (job_description_obj.get("location") or "").strip()
    description = (job_description_obj.get("description") or "").strip()

    # `resumeai-AI` expects a single string.
    job_description_text = " ".join([p for p in [job_title, company] if p]).strip()
    if location:
        job_description_text = f"{job_description_text} ({location})".strip()
    if description:
        job_description_text = f"{job_description_text}\n\n{description}".strip() if job_description_text else description

    name = (info.get("full_name") or " ").strip()
    email = (info.get("email") or "").strip()
    phone = (info.get("phone") or "").strip()
    linkedin = (info.get("linkedin_url") or "").strip()
    portfolio = (info.get("portfolio_url") or "").strip()

    experiences_in = resume_content.get("experience") or []
    experiences: List[Dict[str, Any]] = []
    for e in experiences_in:
        if not isinstance(e, dict):
            continue
        experiences.append(
            {
                "role": (e.get("role") or "").strip(),
                "company": (e.get("company") or "").strip(),
                "location": e.get("location"),
                "startDate": (e.get("start_date") or "").strip(),
                "endDate": (e.get("end_date") or "").strip(),
                "bullets": _clean_bullets_from_description(e.get("description")),
            }
        )

    education_in = resume_content.get("education") or []
    education: List[Dict[str, Any]] = []
    for ed in education_in:
        if not isinstance(ed, dict):
            continue
        education.append(
            {
                "school": (ed.get("school") or "").strip(),
                "degree": (ed.get("degree") or "").strip(),
                "field": (ed.get("field_of_study") or "").strip(),
                "location": ed.get("location"),
                "endDate": (ed.get("end_date") or "").strip(),
            }
        )

    skills_in = resume_content.get("skills") or []
    skills_flat: List[str] = [s.strip() for s in skills_in if isinstance(s, str) and s.strip()]
    skills = [{"category": "Technical", "skills": skills_flat}]

    summary = (resume_content.get("summary") or "").strip()

    # Backend doesn't model projects directly; allow `custom.projects` to feed AI.
    projects_out: List[Dict[str, Any]] = []
    if isinstance(custom, dict):
        projects_in = custom.get("projects") or []
        if isinstance(projects_in, list):
            for p in projects_in:
                if not isinstance(p, dict):
                    continue
                bullets = p.get("bullets")
                if not isinstance(bullets, list):
                    bullets = _clean_bullets_from_description(p.get("description") or p.get("bullets"))
                bullets = [str(b).strip() for b in bullets if str(b).strip()]
                projects_out.append(
                    {
                        "title": (p.get("title") or p.get("name") or "").strip(),
                        "link": (p.get("link") or p.get("url") or "").strip(),
                        "bullets": bullets,
                    }
                )

    return {
        "name": name or "",
        "email": email,
        "phone": phone,
        "linkedin": linkedin,
        "portfolio": portfolio,
        "summary": summary,
        "experiences": experiences,
        "projects": projects_out,
        "education": education,
        "skills": skills,
        "job_description": job_description_text,
    }


def ai_optimized_resume_to_backend_content(
    existing_resume_content: Dict[str, Any],
    ai_candidate_info: Dict[str, Any] | None,
    ai_resume: Dict[str, Any],
) -> Dict[str, Any]:
    updated: Dict[str, Any] = dict(existing_resume_content or {})

    existing_experiences_in = existing_resume_content.get("experience") or []
    existing_education_in = existing_resume_content.get("education") or []

    info = (updated.get("info") or {}).copy()
    if isinstance(ai_candidate_info, dict) and ai_candidate_info:
        info["full_name"] = ai_candidate_info.get("name") or info.get("full_name") or ""
        info["email"] = ai_candidate_info.get("email") or info.get("email") or None
        info["phone"] = ai_candidate_info.get("phone") or info.get("phone") or ""
        info["linkedin_url"] = ai_candidate_info.get("linkedin") or info.get("linkedin_url") or None
        info["portfolio_url"] = ai_candidate_info.get("portfolio") or info.get("portfolio_url") or None

    updated["info"] = info
    updated["summary"] = ai_resume.get("summary") or updated.get("summary") or ""

    ai_experiences = ai_resume.get("experiences") or []
    experiences_out: List[Dict[str, Any]] = []
    for idx, e in enumerate(ai_experiences):
        if not isinstance(e, dict):
            continue
        bullets = e.get("bullets") or []
        if not isinstance(bullets, list):
            bullets = []
        description = "\n".join([str(b).strip() for b in bullets if str(b).strip()])

        existing_at_idx: Dict[str, Any] = {}
        if idx < len(existing_experiences_in) and isinstance(existing_experiences_in[idx], dict):
            existing_at_idx = existing_experiences_in[idx]

        start_date = e.get("startDate") or existing_at_idx.get("start_date") or ""
        end_date = e.get("endDate") or existing_at_idx.get("end_date") or ""
        location = e.get("location") if e.get("location") is not None else existing_at_idx.get("location")
        experiences_out.append(
            {
                "role": e.get("role") or "",
                "company": e.get("company") or "",
                "start_date": start_date,
                "end_date": end_date,
                "location": location,
                "description": description,
            }
        )
    updated["experience"] = experiences_out

    ai_education = ai_resume.get("education") or []
    education_out: List[Dict[str, Any]] = []
    for idx, ed in enumerate(ai_education):
        if not isinstance(ed, dict):
            continue

        existing_at_idx: Dict[str, Any] = {}
        if idx < len(existing_education_in) and isinstance(existing_education_in[idx], dict):
            existing_at_idx = existing_education_in[idx]

        start_date = existing_at_idx.get("start_date") or ""
        end_date = ed.get("endDate") or existing_at_idx.get("end_date") or ""
        location = ed.get("location") if ed.get("location") is not None else existing_at_idx.get("location")

        education_out.append(
            {
                "school": ed.get("school") or "",
                "degree": ed.get("degree") or "",
                "start_date": start_date,
                "end_date": end_date,
                "location": location,
                "field_of_study": ed.get("field") or "",
            }
        )
    updated["education"] = education_out

    ai_skills = ai_resume.get("skills") or []
    skills_flat: List[str] = []
    for cat in ai_skills:
        if not isinstance(cat, dict):
            continue
        items = cat.get("skills") or []
        if isinstance(items, list):
            skills_flat.extend([str(s).strip() for s in items if str(s).strip()])
    # Deduplicate while preserving order.
    seen: set[str] = set()
    deduped: List[str] = []
    for s in skills_flat:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)
    updated["skills"] = deduped

    # Backend doesn't have a projects section; keep it in `custom` so we don't lose it.
    ai_projects = ai_resume.get("projects") or []
    if isinstance(ai_projects, list):
        custom = updated.get("custom") or {}
        if not isinstance(custom, dict):
            custom = {}
        custom["projects"] = ai_projects
        updated["custom"] = custom

    return updated


def map_ai_ats_to_backend_payload(ai_response: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Returns:
      - (payload_for_resume_ats_score, ats_summary_for_response)
    """
    ai_resume = ai_response.get("resume") or {}
    ats_report = ai_resume.get("ats_report") or {}
    ats_final = ai_response.get("ats_final_result") or {}

    overall_score = (
        ats_final.get("final_ats_score")
        or ats_report.get("coverage_percent")
        or 0
    )
    try:
        overall_score_int = int(float(overall_score))
    except Exception:
        overall_score_int = 0

    max_score = 100
    recommendations = ats_report.get("notes") or []
    if not isinstance(recommendations, list):
        recommendations = []
    keywords_covered = ats_report.get("keywords_covered") or []
    keywords_missing = ats_report.get("keywords_missing") or []
    if not isinstance(keywords_covered, list):
        keywords_covered = []
    if not isinstance(keywords_missing, list):
        keywords_missing = []

    category_scores = {
        "keyword_match": {
            "keywords_covered": keywords_covered,
            "keywords_missing": keywords_missing,
        }
    }

    payload_for_db = {
        "overall_score": overall_score_int,
        "max_score": max_score,
        "category_scores": category_scores,
        "recommendations": [str(n) for n in recommendations if str(n).strip()],
    }

    ats_summary_for_response = {
        "final_ats_score": overall_score_int,
        "keywords_found": ats_final.get("keywords_found") or keywords_covered,
        "keywords_missing": ats_final.get("keywords_missing") or keywords_missing,
        "iterations_needed": ats_final.get("iterations_needed"),
    }
    return payload_for_db, ats_summary_for_response

