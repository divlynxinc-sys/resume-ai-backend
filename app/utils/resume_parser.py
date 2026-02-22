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
    """Extract raw text from PDF or DOCX file."""
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
    # Match various phone formats
    patterns = [
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
    # Look for portfolio, personal site (excluding linkedin)
    match = re.search(r"https?://(?!linkedin)[^\s\)]+", text)
    return match.group(0).rstrip(".,)") if match else None


def _split_sections(text: str) -> dict[str, str]:
    """Split text into sections by common resume headers."""
    section_headers = [
        r"^(?:PROFESSIONAL\s+)?SUMMARY|^(?:EXECUTIVE\s+)?PROFILE|^OBJECTIVE",
        r"^(?:WORK\s+)?EXPERIENCE|^EMPLOYMENT|^PROFESSIONAL\s+EXPERIENCE|^EXPERIENCE",
        r"^EDUCATION|^ACADEMIC",
        r"^SKILLS?|^TECHNICAL\s+SKILLS|^KEY\s+SKILLS|^CORE\s+COMPETENCIES|^COMPETENCIES|^EXPERTISE|^AREAS?\s+OF\s+EXPERTISE|^TECHNOLOGIES|^TOOLS|^LANGUAGES",
        r"^PROJECTS",
        r"^CERTIFICATIONS|^CERTIFICATES",
        r"^AWARDS|^HONORS",
    ]
    pattern = "|".join(f"({h})" for h in section_headers)
    parts = re.split(pattern, text, flags=re.MULTILINE | re.IGNORECASE)
    sections: dict[str, str] = {}
    current_header = "header"  # text before first section
    for i, part in enumerate(parts):
        if not part or not part.strip():
            continue
        part = part.strip()
        if i % 2 == 1 and re.match(pattern, part, re.IGNORECASE):
            current_header = re.sub(r"\s+", "_", part.upper())[:30]
        else:
            if current_header not in sections:
                sections[current_header] = ""
            sections[current_header] += "\n" + part
    return sections


def _parse_experience_block(block: str) -> list[dict[str, Any]]:
    """Parse experience section into list of ExperienceItem-like dicts."""
    items = []
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    date_pattern = re.compile(
        r"(\d{1,2}/\d{4}|\d{4}|\w+\s+\d{4})\s*[-–—to]\s*(\d{1,2}/\d{4}|\d{4}|Present|\w+\s+\d{4})",
        re.IGNORECASE,
    )
    i = 0
    while i < len(lines):
        line = lines[i]
        date_match = date_pattern.search(line)
        if date_match:
            start_date = date_match.group(1)
            end_date = date_match.group(2)
            before = line[: date_match.start()].strip()
            if " at " in before:
                role, company = before.split(" at ", 1)
            elif " - " in before:
                role, company = before.split(" - ", 1)
            elif " | " in before:
                role, company = before.split(" | ", 1)
            else:
                role = before
                company = ""
            desc_lines = []
            i += 1
            while i < len(lines) and not date_pattern.search(lines[i]) and not re.match(r"^[A-Z][a-z]+\s+[A-Z]", lines[i]):
                if lines[i].startswith("•") or lines[i].startswith("-"):
                    desc_lines.append(lines[i][1:].strip())
                else:
                    desc_lines.append(lines[i])
                i += 1
            items.append({
                "role": role or None,
                "company": company or None,
                "start_date": start_date,
                "end_date": end_date,
                "description": "\n".join(desc_lines) if desc_lines else None,
            })
        else:
            i += 1
    if not items and block.strip():
        items.append({"role": None, "company": None, "start_date": None, "end_date": None, "description": block.strip()})
    return items


def _parse_education_block(block: str) -> list[dict[str, Any]]:
    """Parse education section into list of EducationItem-like dicts."""
    items = []
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    date_pattern = re.compile(r"(\d{4})\s*[-–—]\s*(\d{4}|\w+)", re.IGNORECASE)
    for i, line in enumerate(lines):
        date_match = date_pattern.search(line)
        if date_match:
            start_date = date_match.group(1)
            end_date = date_match.group(2)
            before = line[: date_match.start()].strip()
            parts = re.split(r"\s*[,\|]\s*|-\s*", before, maxsplit=1)
            degree = parts[0].strip() if parts else ""
            school = parts[1].strip() if len(parts) > 1 else ""
            items.append({
                "school": school or "Unknown",
                "degree": degree or "Unknown",
                "start_date": start_date,
                "end_date": end_date,
            })
    if not items and block.strip():
        items.append({"school": block.split("\n")[0][:200], "degree": "", "start_date": "", "end_date": ""})
    return items


def _parse_skills_block(block: str) -> list[str]:
    """Extract skills from text. Handles: bullets, commas, pipes, semicolons, newlines, colons."""
    seen: set[str] = set()
    skills: list[str] = []

    def add_skill(s: str) -> None:
        s = s.strip()
        if not s or len(s) < 2 or len(s) > 60:
            return
        # Skip headers, dates, descriptions
        if re.match(r"^(years?|proficient|expert|beginner|intermediate)$", s, re.I):
            return
        if re.search(r"\d{4}|\d+\s*years?", s):
            return
        s_clean = s.strip("•\-–—·").strip()
        if s_clean and s_clean.lower() not in seen:
            seen.add(s_clean.lower())
            skills.append(s_clean)

    # Split by newlines first - each line may have multiple skills
    lines = [l.strip() for l in block.split("\n") if l.strip()]

    for line in lines:
        # Skip section-like headers (short caps lines)
        if len(line) < 30 and re.match(r"^[A-Z\s]+$", line):
            continue
        # Handle "Category: skill1, skill2" or "Category - skill1, skill2"
        if ":" in line:
            parts = line.split(":", 1)
            if len(parts) == 2 and len(parts[0]) < 40:
                line = parts[1]  # take content after category
        if " - " in line and len(line.split(" - ")[0]) < 25:
            line = " - ".join(line.split(" - ")[1:])  # drop category prefix

        # Split by comma, pipe, semicolon, bullet
        for chunk in re.split(r"[,;|]|\s*[•\-–—·]\s*", line):
            add_skill(chunk)

    # Also try whole-block: comma/pipe/semicolon separated on same line
    if not skills:
        flat = block.replace("\n", ",").replace("•", ",").replace("|", ",").replace(";", ",")
        for s in re.split(r",", flat):
            add_skill(s)

    return skills[:80]


def _guess_name_from_text(text: str) -> str:
    """Heuristic: name often in first few lines, before email."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines[:5]:
        if not _extract_email(line) and not _extract_phone(line) and len(line) < 80:
            if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+$", line) or re.match(r"^[A-Z][A-Z\s]+$", line):
                return line
    return ""


def parse_resume_content(text: str, filename: str = "") -> dict:
    """Parse raw resume text into ResumeContent-compatible dict."""
    text = re.sub(r"[ \t]+", " ", text)  # normalize spaces
    text = re.sub(r"\n{3,}", "\n\n", text)  # collapse excess newlines
    text = text.strip()
    if not text:
        return ResumeContent().model_dump()

    email = _extract_email(text)
    phone = _extract_phone(text)
    linkedin = _extract_linkedin(text)
    website = _extract_website(text)
    full_name = _guess_name_from_text(text)

    info = {
        "full_name": full_name or "Parsed from resume",
        "email": email,
        "phone": phone or "",
        "location": None,
        "linkedin_url": linkedin,
        "portfolio_url": website,
    }

    sections = _split_sections(text)
    header_text = sections.get("header", "")
    if header_text and not full_name:
        first_line = header_text.split("\n")[0].strip()
        if first_line and len(first_line) < 60:
            info["full_name"] = first_line

    experience: list[dict] = []
    for key in sections:
        if "EXPERIENCE" in key.upper() and "EDUCATION" not in key.upper():
            experience = _parse_experience_block(sections[key])
            break

    education: list[dict] = []
    for key in sections:
        if "EDUCATION" in key.upper():
            education = _parse_education_block(sections[key])
            break

    skills: list[str] = []
    skill_keys = ("SKILL", "COMPETENC", "EXPERTISE", "TECHNOLOG", "TOOLS", "LANGUAGES")
    for key in sections:
        k = key.upper()
        if any(sk in k for sk in skill_keys):
            parsed = _parse_skills_block(sections[key])
            if parsed:
                skills = parsed
                break

    # Fallback: if section split missed it, search for Skills heading + following content
    if not skills:
        for pattern in [
            r"(?:SKILLS?|TECHNICAL\s+SKILLS|KEY\s+SKILLS|EXPERTISE|TECHNOLOGIES)[\s:]*\n([^\n]+(?:\n[^\n]+){0,50})",
            r"(?:SKILLS?|TECHNICAL\s+SKILLS)[\s:]*([\s\S]{20,2000}?)(?=\n\n[A-Z]|\n(?:EXPERIENCE|EDUCATION|PROJECTS)|\Z)",
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                skills = _parse_skills_block(m.group(1).strip())
                if skills:
                    break

    summary = ""
    for key in sections:
        if "SUMMARY" in key.upper() or "PROFILE" in key.upper() or "OBJECTIVE" in key.upper():
            summary = sections[key].strip()[:2000]
            break

    exp_items = []
    for e in experience:
        try:
            exp_items.append(ExperienceItem(**{k: v for k, v in e.items() if v is not None}).model_dump())
        except Exception:
            exp_items.append({"role": e.get("role"), "company": e.get("company"), "start_date": e.get("start_date"), "end_date": e.get("end_date"), "location": None, "description": e.get("description")})

    edu_items = []
    for e in education:
        try:
            edu_items.append(EducationItem(school=e.get("school", ""), degree=e.get("degree", ""), start_date=e.get("start_date", ""), end_date=e.get("end_date", ""), location=e.get("location"), field_of_study=e.get("field_of_study")).model_dump())
        except Exception:
            edu_items.append({"school": str(e.get("school", ""))[:200], "degree": str(e.get("degree", "")), "start_date": "", "end_date": "", "location": None, "field_of_study": None})

    if email and "@" not in email:
        email = None

    info["email"] = email
    content = {
        "info": info,
        "experience": exp_items,
        "education": edu_items,
        "skills": skills,
        "summary": summary,
        "job_description": JobDescription().model_dump(),
        "custom": {},
    }
    try:
        return ResumeContent.model_validate(content).model_dump()
    except Exception:
        content["info"]["email"] = None
        return content


def parse_uploaded_resume(file_bytes: bytes, filename: str) -> dict:
    """Extract text from file and parse into ResumeContent dict."""
    text = extract_text(file_bytes, filename)
    return parse_resume_content(text, filename)
