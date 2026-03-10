"""Parse resume text from PDF/DOCX into structured ResumeContent."""
import re
from io import BytesIO
from typing import Any

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


def _extract_linkedin(text: str) -> str | None:
    match = re.search(r"linkedin\.com/in/[a-zA-Z0-9_-]+", text, re.IGNORECASE)
    if match:
        return "https://" + match.group(0)
    return None


def _extract_website(text: str) -> str | None:
    match = re.search(r"https?://(?!linkedin)[^\s\)\]]+", text)
    return match.group(0).rstrip(".,)") if match else None


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

            # Try to split role / company from the text before or after the date
            role, company, location = "", "", ""
            combined = (before + " " + after).strip()
            for sep in (" at ", " | ", " - ", " @ ", ", "):
                if sep in combined:
                    role, company = combined.split(sep, 1)
                    role = role.strip(); company = company.strip()
                    break
            if not role:
                role = combined

            # Collect description lines
            desc_lines: list[str] = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if _DATE_PAT.search(nxt):
                    break
                # Stop if a new entry looks like a title line (short, title-cased)
                if len(nxt) < 80 and re.match(r"^[A-Z][a-zA-Z]+(?:\s+[A-Za-z]+){0,5}$", nxt) and i + 1 < len(lines) and _DATE_PAT.search(lines[i + 1] if i + 1 < len(lines) else ""):
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

    email    = _extract_email(text)
    phone    = _extract_phone(text)
    linkedin = _extract_linkedin(text)
    website  = _extract_website(text)
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
        "custom":          {},
    }
    try:
        return ResumeContent.model_validate(content).model_dump()
    except Exception:
        content["info"]["email"] = None
        return content


def parse_uploaded_resume(file_bytes: bytes, filename: str) -> dict:
    text = extract_text(file_bytes, filename)
    return parse_resume_content(text, filename)
