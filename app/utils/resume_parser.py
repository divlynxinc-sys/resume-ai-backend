"""Parse resume text from PDF/DOCX into structured ResumeContent."""
import re
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from app.schemas.resume_schema import (
    EducationItem,
    ExperienceItem,
    JobDescription,
    PersonalInfo,
    ResumeContent,
)


def _extract_text_from_pdf(file_bytes: bytes) -> str:
    import pdfplumber
    text_parts = []
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t)
    return "\n\n".join(text_parts) if text_parts else ""


def _extract_text_from_docx(file_bytes: bytes) -> str:
    from docx import Document
    doc = Document(BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_text(file_bytes: bytes, filename: str) -> str:
    fn = (filename or "").lower()
    if fn.endswith(".pdf"):
        return _extract_text_from_pdf(file_bytes)
    if fn.endswith(".docx") or fn.endswith(".doc"):
        return _extract_text_from_docx(file_bytes)
    raise ValueError("Unsupported format. Use PDF or DOCX.")


def _extract_email(text: str) -> str | None:
    match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    return match.group(0) if match else None


def _extract_phone(text: str) -> str | None:
    patterns = [
        r"\+?\d{1,3}[-.\s]?\d{3}[-.\s]?\d{7,8}",   # +92 3xx xxxxxxx
        r"\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",  # US
        r"\+?\d{1,3}[-.\s]?\d{2,4}[-.\s]?\d{2,4}[-.\s]?\d{2,4}",  # International
        r"\d{3}[-.\s]\d{3}[-.\s]\d{4}",
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            return match.group(0).strip()
    return None


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item:
            continue
        key = item.strip()
        if not key:
            continue
        if key.lower() in seen:
            continue
        seen.add(key.lower())
        out.append(key)
    return out


def _normalize_url(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    # Clean common trailing punctuation extracted from PDFs.
    s = s.rstrip(".,;:)]}>'\"")

    if s.lower().startswith("www."):
        s = "https://" + s

    if not re.match(r"^https?://", s, re.IGNORECASE):
        s = "https://" + s

    return s


def _hostname_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


# Social URL extractors (tolerate missing scheme and trailing slash).
_LINKEDIN_URL_PAT = re.compile(
    r"(?i)\b(?:https?://)?(?:www\.)?linkedin\.com/(?:in|company|pub)/[a-z0-9_-]+/?(?:\?[^\s\)]+)?"
)
_GITHUB_URL_PAT = re.compile(
    r"(?i)\b(?:https?://)?(?:www\.)?github\.com/[a-z0-9_.-]+(?:/[a-z0-9_.-]+)?/?(?:\?[^\s\)]+)?"
)
_TWITTER_URL_PAT = re.compile(
    r"(?i)\b(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/[a-z0-9_]+/?(?:\?[^\s\)]+)?"
)

# Generic URL candidates for portfolio selection.
_URL_WITH_SCHEME_PAT = re.compile(r"(?i)\bhttps?://[^\s\)\]\}>,\"']+")
_WWW_URL_PAT = re.compile(r"(?i)\bwww\.[^\s\)\]\}>,\"']+")
# Bare domain with a path (e.g. `example.com/resume`).
_BARE_DOMAIN_WITH_PATH_PAT = re.compile(r"(?i)\b((?:[a-z0-9-]+\.)+[a-z]{2,})/[^\s\)\]\}>,\"']+")


def _extract_url_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for pat in (_URL_WITH_SCHEME_PAT, _WWW_URL_PAT, _BARE_DOMAIN_WITH_PATH_PAT):
        for m in pat.finditer(text):
            candidates.append(m.group(0))
    normalized = [_normalize_url(c) for c in candidates]
    return _dedupe_preserve_order([u for u in normalized if u])


def _extract_social_urls(text: str) -> dict[str, list[str]]:
    linkedin_urls: list[str] = []
    github_urls: list[str] = []
    twitter_urls: list[str] = []

    for m in _LINKEDIN_URL_PAT.finditer(text):
        val = _normalize_url(m.group(0))
        if val:
            linkedin_urls.append(val)
    for m in _GITHUB_URL_PAT.finditer(text):
        val = _normalize_url(m.group(0))
        if val:
            github_urls.append(val)
    for m in _TWITTER_URL_PAT.finditer(text):
        val = _normalize_url(m.group(0))
        if val:
            twitter_urls.append(val)

    linkedin_urls = _dedupe_preserve_order(linkedin_urls)
    github_urls = _dedupe_preserve_order(github_urls)
    twitter_urls = _dedupe_preserve_order(twitter_urls)

    return {
        "linkedin": linkedin_urls,
        "github": github_urls,
        "twitter_x": twitter_urls,
    }


def _extract_portfolio_url(text: str, social_urls: dict[str, list[str]]) -> str | None:
    # Prefer GitHub when present (common portfolio fallback for dev resumes).
    github_urls = social_urls.get("github") or []
    if isinstance(github_urls, list) and github_urls:
        return github_urls[0]

    # Otherwise pick a non-linkedin website candidate.
    candidates = _extract_url_candidates(text)
    for u in candidates:
        host = _hostname_from_url(u)
        if not host:
            continue
        if host.endswith("linkedin.com"):
            continue
        if host.endswith("twitter.com") or host == "x.com" or host.endswith("x.com"):
            continue
        return u
    return None


def _split_sections(text: str) -> dict[str, str]:
    """Split resume text into labelled sections by common headers."""
    section_headers = [
        r"^(?:PROFESSIONAL\s+)?SUMMARY|^(?:EXECUTIVE\s+)?PROFILE|^OBJECTIVE|^ABOUT\s+ME",
        r"^(?:WORK\s+)?EXPERIENCE|^EMPLOYMENT|^PROFESSIONAL\s+EXPERIENCE|^EXPERIENCE",
        r"^EDUCATION|^ACADEMIC\s+BACKGROUND|^ACADEMIC",
        r"^TECHNICAL\s+SKILLS|^KEY\s+SKILLS|^CORE\s+COMPETENCIES|^COMPETENCIES|^SKILLS",
        r"^PROJECTS|^PERSONAL\s+PROJECTS|^NOTABLE\s+PROJECTS",
        r"^CERTIFICATIONS?|^CERTIFICATES?|^COURSES?",
        r"^AWARDS?|^HONORS?|^ACHIEVEMENTS?",
        r"^LANGUAGES?",
    ]
    pattern = "|".join(f"({h})" for h in section_headers)
    parts = re.split(pattern, text, flags=re.MULTILINE | re.IGNORECASE)
    sections: dict[str, str] = {}
    current_header = "header"
    for i, part in enumerate(parts):
        if not part or not part.strip():
            continue
        part = part.strip()
        if re.match(pattern, part, re.IGNORECASE):
            current_header = re.sub(r"\s+", "_", part.upper())[:30]
        else:
            if current_header not in sections:
                sections[current_header] = ""
            sections[current_header] += "\n" + part
    return sections


# ── Date pattern: matches many resume date formats ───────────────────────────
_DATE_PAT = re.compile(
    r"(?:"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s*\d{4}"              # Month YYYY
    r"|\d{1,2}/\d{4}"          # MM/YYYY
    r"|\d{4}"                   # YYYY
    r")"
    r"\s*[-–—to]+\s*"
    r"(?:"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s*\d{4}"
    r"|\d{1,2}/\d{4}"
    r"|\d{4}"
    r"|Present|Current|Now|Ongoing"
    r")",
    re.IGNORECASE,
)

# Standalone year e.g. "2021" or "2019 - 2023"
_YEAR_RANGE_PAT = re.compile(r"(\d{4})\s*[-–—]\s*(\d{4}|Present|Current)", re.IGNORECASE)
_YEAR_ONLY_PAT = re.compile(r"\b(20\d{2}|19\d{2})\b")


def _parse_experience_block(block: str) -> list[dict[str, Any]]:
    items: list[dict] = []
    lines = [l.strip() for l in block.split("\n") if l.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]
        date_match = _DATE_PAT.search(line)
        if date_match:
            date_str = date_match.group(0)
            # Split date into start/end
            parts = re.split(r"\s*[-–—to]+\s*", date_str, maxsplit=1, flags=re.IGNORECASE)
            start_date = parts[0].strip() if parts else ""
            end_date = parts[1].strip() if len(parts) > 1 else ""

            before = line[: date_match.start()].strip().rstrip(",-|")
            after  = line[date_match.end():].strip().lstrip(",-|")

            # Many resumes have a multi-column layout; PDF extraction may place
            # dates on a separate line from the role/company. Try to find the
            # best "header" candidate around the date line.
            combined = (before + " " + after).strip()
            header_candidates: list[tuple[str, str]] = [("same", combined)]
            if i > 0:
                header_candidates.append(("prev", lines[i - 1].strip()))
            if i + 1 < len(lines):
                header_candidates.append(("next", lines[i + 1].strip()))

            def header_score(s: str) -> int:
                s = (s or "").strip()
                if not s:
                    return -10_000
                # Penalize things that look like pure dates.
                if _DATE_PAT.search(s):
                    return -5_000
                # Prefer strings that contain common separators.
                has_sep = any(sep.strip() in s for sep in ("|", "-", " at ", " @ ", ","))
                # Prefer role/company lines: short and title-like.
                title_like = bool(re.match(r"^[A-Z][A-Za-z]+(?:\s+[A-Za-z][A-Za-z0-9&./]+){0,6}$", s))

                # Prefer moderate length (role/company lines are usually short).
                length_score = 60 - abs(len(s) - 45)

                # Penalize bullet-only or description-like sentences.
                if re.match(r"^[•·\-–—]\s*", s):
                    length_score -= 40
                if re.search(
                    r"(?i)\b(responsible|developed|worked|managed|led|achievement|impact|built|designed|implemented|created|improved)\b",
                    s,
                ):
                    length_score -= 60
                if "." in s:
                    length_score -= 10
                return (30 if has_sep else 0) + length_score

            header_source, header_text = max(header_candidates, key=lambda t: header_score(t[1]))

            role, company, location = "", "", ""
            # Try to split role / company from the header candidate
            header_parts = header_text
            for sep in (" at ", " | ", " - ", " @ ", ", "):
                if sep in header_parts:
                    left, right = header_parts.split(sep, 1)
                    role = left.strip()
                    company = right.strip()
                    break
            if not role:
                role = header_parts.strip()

            # Collect description lines (skip the "next" line if we used it as header).
            desc_lines: list[str] = []
            i = i + 1 if header_source != "next" else i + 2
            while i < len(lines):
                nxt = lines[i]
                if _DATE_PAT.search(nxt):
                    break
                # Stop if a new entry looks like a title line (short, title-cased)
                if (
                    len(nxt) < 80
                    and re.match(r"^[A-Z][a-zA-Z]+(?:\s+[A-Za-z]+){0,5}$", nxt)
                    and i + 1 < len(lines)
                    and _DATE_PAT.search(lines[i + 1] if i + 1 < len(lines) else "")
                    # Avoid breaking on real description sentences.
                    and not re.search(r"(?i)\b(responsible|developed|worked|managed|led|achievement|impact)\b", nxt)
                ):
                    break
                desc_lines.append(nxt.lstrip("•-–—·").strip())
                i += 1

            items.append({
                "role":        role or None,
                "company":     company or None,
                "start_date":  start_date,
                "end_date":    end_date,
                "location":    location or None,
                "description": "\n".join(desc_lines) if desc_lines else None,
            })
        else:
            i += 1

    # Fallback: couldn't find dates — store whole block as one item
    if not items and block.strip():
        first_line = block.strip().split("\n")[0]
        items.append({
            "role": first_line[:120] if first_line else None,
            "company": None,
            "start_date": None,
            "end_date": None,
            "location": None,
            "description": block.strip(),
        })
    return items


def _parse_education_block(block: str) -> list[dict[str, Any]]:
    items: list[dict] = []
    lines = [l.strip() for l in block.split("\n") if l.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]
        # Try full date range first
        date_match = _DATE_PAT.search(line) or _YEAR_RANGE_PAT.search(line)
        if date_match:
            date_str = date_match.group(0)
            parts = re.split(r"\s*[-–—to]+\s*", date_str, maxsplit=1, flags=re.IGNORECASE)
            start_date = parts[0].strip() if parts else ""
            end_date = parts[1].strip() if len(parts) > 1 else ""

            before = line[: date_match.start()].strip().rstrip(",-|")

            # Look one line ahead for school/degree if not in current line
            lookahead = lines[i - 1] if i > 0 else ""

            # Split into degree and school
            degree, school, field = "", "", ""
            for sep in (" | ", " - ", ", ", " at "):
                if sep in before:
                    halves = before.split(sep, 1)
                    degree = halves[0].strip()
                    school = halves[1].strip()
                    break
            if not degree and not school:
                # Try the previous line
                if lookahead and not _DATE_PAT.search(lookahead) and not _YEAR_RANGE_PAT.search(lookahead):
                    degree = lookahead
                    school = before
                else:
                    degree = before

            # Field of study: look for "in <field>" in degree
            fos_match = re.search(r"\bin\s+(.+)$", degree, re.IGNORECASE)
            if fos_match:
                field = fos_match.group(1).strip()
                degree = degree[: fos_match.start()].strip()

            items.append({
                "school":       school or degree or "Unknown",
                "degree":       degree or "Unknown",
                "start_date":   start_date,
                "end_date":     end_date,
                "field_of_study": field or None,
                "location":     None,
            })
            i += 1
        else:
            # Line has no date — check if it might be a school + year-only
            year_match = _YEAR_ONLY_PAT.search(line)
            if year_match:
                school_part = line[: year_match.start()].strip().rstrip(",-|")
                items.append({
                    "school":   school_part or line,
                    "degree":   lines[i - 1] if i > 0 else "",
                    "start_date": "",
                    "end_date": year_match.group(0),
                    "field_of_study": None,
                    "location": None,
                })
            i += 1

    # Final fallback — return first line as school
    if not items and block.strip():
        block_lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
        items.append({
            "school":       block_lines[0][:200] if block_lines else "",
            "degree":       block_lines[1][:200] if len(block_lines) > 1 else "",
            "start_date":   "",
            "end_date":     "",
            "field_of_study": None,
            "location":     None,
        })
    return items


# Category header words that prefix skill lists
_SKILL_CATEGORY_PAT = re.compile(
    r"^(?:PROGRAMMING\s+LANGUAGES?|FRONTEND|BACK[- ]?END|DATABASES?|"
    r"DEV\s*OPS|TOOLS?|FRAMEWORKS?|LIBRARIES|CLOUD|TECHNOLOGIES|"
    r"SOFT\s+SKILLS?|LANGUAGES?|OTHERS?|TECHNICAL|AREAS?\s+OF\s+EXPERTISE)"
    r"[\s:&/]*",
    re.IGNORECASE,
)


def _parse_skills_block(block: str) -> list[str]:
    seen: set[str] = set()
    skills: list[str] = []

    def add_skill(s: str) -> None:
        s = s.strip().strip("•–—·").strip()
        if not s or len(s) < 2 or len(s) > 80:
            return
        # Skip noise words
        if re.match(r"^(years?|proficient|expert|beginner|intermediate|advanced|basic)$", s, re.I):
            return
        if re.search(r"\d{4}", s):  # skip year-containing strings
            return
        if s.lower() not in seen:
            seen.add(s.lower())
            skills.append(s)

    lines = [l.strip() for l in block.split("\n") if l.strip()]

    for line in lines:
        # Strip leading category header from the line
        # e.g. "PROGRAMMING LANGUAGES JavaScript, Python" → "JavaScript, Python"
        # e.g. "FRONTEND React.js, Next.js" → "React.js, Next.js"
        line = _SKILL_CATEGORY_PAT.sub("", line).strip()

        # Skip if line is now empty or is just a short all-caps header
        if not line:
            continue
        if len(line) < 35 and re.match(r"^[A-Z\s&/]+$", line):
            continue

        # Handle "Category: skill1, skill2"
        if ":" in line:
            colon_parts = line.split(":", 1)
            if len(colon_parts[0]) < 35:
                line = colon_parts[1].strip()

        # Split on commas, pipes, semicolons — NOT bare hyphens (to preserve "Scikit-learn")
        # Only split on " - " (hyphen with spaces) as a delimiter
        chunks = re.split(r"[,;|]|\s+[-–—]\s+", line)
        for chunk in chunks:
            # Further split on bullets
            for sub in re.split(r"\s*[•·]\s*", chunk):
                add_skill(sub)

    return skills[:80]


def _guess_name_from_text(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines[:6]:
        if _extract_email(line) or _extract_phone(line):
            continue
        if len(line) > 80 or len(line) < 3:
            continue
        # Match "Firstname Lastname" or "FIRSTNAME LASTNAME"
        if re.match(r"^[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,3}$", line):
            return line
        if re.match(r"^[A-Z]{2,}\s+[A-Z]{2,}$", line):
            return line.title()
    return ""


def parse_resume_content(text: str, filename: str = "") -> dict:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if not text:
        return ResumeContent().model_dump()

    # For contact/social extraction, tolerate newlines inserted mid-URL by PDF extractors.
    scan_text = re.sub(r"\s+", " ", text)

    email = _extract_email(scan_text)
    phone = _extract_phone(scan_text)

    social_urls = _extract_social_urls(scan_text)
    linkedin_urls = social_urls.get("linkedin") or []
    linkedin = linkedin_urls[0] if isinstance(linkedin_urls, list) and linkedin_urls else None

    website = _extract_portfolio_url(scan_text, social_urls)
    name     = _guess_name_from_text(text)

    info: dict[str, Any] = {
        "full_name":     name or "Parsed from resume",
        "email":         email,
        "phone":         phone or "",
        "location":      None,
        "linkedin_url":  linkedin,
        "portfolio_url": website,
    }

    sections = _split_sections(text)

    # Fallback name from header block
    header_text = sections.get("header", "")
    if header_text and not name:
        first_line = header_text.strip().split("\n")[0].strip()
        if first_line and len(first_line) < 60:
            info["full_name"] = first_line

    # ── Experience ────────────────────────────────────────────────────────────
    experience: list[dict] = []
    for key in sections:
        if "EXPERIENCE" in key.upper() and "EDUCATION" not in key.upper():
            experience = _parse_experience_block(sections[key])
            break

    # ── Education ─────────────────────────────────────────────────────────────
    education: list[dict] = []
    for key in sections:
        if "EDUCATION" in key.upper() or "ACADEMIC" in key.upper():
            education = _parse_education_block(sections[key])
            break

    # ── Skills ────────────────────────────────────────────────────────────────
    skills: list[str] = []
    skill_keys = ("SKILL", "COMPETENC", "EXPERTISE", "TECHNOLOG", "TOOLS")
    for key in sections:
        k = key.upper()
        if any(sk in k for sk in skill_keys):
            parsed = _parse_skills_block(sections[key])
            if parsed:
                skills = parsed
                break

    if not skills:
        for pattern in [
            r"(?:TECHNICAL\s+SKILLS?|KEY\s+SKILLS?|SKILLS?)[\s:]*\n([\s\S]{10,2000}?)(?=\n\n[A-Z]|\Z)",
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                skills = _parse_skills_block(m.group(1).strip())
                if skills:
                    break

    # ── Summary ───────────────────────────────────────────────────────────────
    summary = ""
    for key in sections:
        if any(k in key.upper() for k in ("SUMMARY", "PROFILE", "OBJECTIVE", "ABOUT")):
            summary = sections[key].strip()[:2000]
            break

    # ── Build output ──────────────────────────────────────────────────────────
    exp_items = []
    for e in experience:
        try:
            exp_items.append(ExperienceItem(**{k: v for k, v in e.items() if v is not None}).model_dump())
        except Exception:
            exp_items.append({
                "role": e.get("role"), "company": e.get("company"),
                "start_date": e.get("start_date"), "end_date": e.get("end_date"),
                "location": None, "description": e.get("description"),
            })

    edu_items = []
    for e in education:
        try:
            edu_items.append(EducationItem(
                school=e.get("school", ""),
                degree=e.get("degree", ""),
                start_date=e.get("start_date", ""),
                end_date=e.get("end_date", ""),
                location=e.get("location"),
                field_of_study=e.get("field_of_study"),
            ).model_dump())
        except Exception:
            edu_items.append({
                "school": str(e.get("school", ""))[:200],
                "degree": str(e.get("degree", "")),
                "start_date": "", "end_date": "",
                "location": None, "field_of_study": None,
            })

    if email and "@" not in email:
        email = None
    info["email"] = email

    content = {
        "info":            info,
        "experience":      exp_items,
        "education":       edu_items,
        "skills":          skills,
        "summary":         summary,
        "job_description": JobDescription().model_dump(),
        "custom":          {"social_urls": social_urls},
    }
    try:
        return ResumeContent.model_validate(content).model_dump()
    except Exception:
        content["info"]["email"] = None
        return content


def parse_uploaded_resume(file_bytes: bytes, filename: str) -> dict:
    text = extract_text(file_bytes, filename)
    return parse_resume_content(text, filename)
