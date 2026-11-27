import re
from collections import defaultdict
from urllib.parse import urlparse

from pydantic import ValidationError

from app.schemas.resume_schema import ResumeContent
from app.utils import resume_parser
from app.utils.resume_layout import Line, docx_lines, pdf_lines

HEADINGS = {
    "contact": ("contact", "contact information"),
    "summary": ("summary", "professional summary", "profile", "objective", "about me", "executive profile"),
    "experience": ("experience", "work experience", "professional experience", "employment", "work history"),
    "education": ("education", "academic background", "qualifications"),
    "projects": ("project", "projects", "personal projects", "notable projects"),
    "skills": ("skills", "technical skills", "key skills", "core competencies", "competencies", "expertise"),
}
SECTION = {label: section for section, labels in HEADINGS.items() for label in labels}
OTHER_HEADINGS = {"certifications", "courses", "awards", "languages", "references", "interests", "volunteering"}
MONTHS = r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
DATE = rf"(?:(?:{MONTHS})\.?\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}}|Present|Current|Now|Ongoing)"
RANGE = re.compile(fr"\b({DATE})\s*(?:[-–—]|\bto\b)\s*({DATE})\b", re.I)
END_DATE = re.compile(fr"\b({DATE})\s*$", re.I)
DEGREE = re.compile(
    r"(?i)\b(bachelor|master|phd|degree|diploma|associate|bsc|msc|mba|beng|meng|ba|bs|ma|ms|llb|llm|ged|high school)s?\b"
)
BULLET = re.compile(r"^\s*(?:[•◦●▪‣*]|\d+\.|[-–])\s+")


def _heading(text: str) -> str | None:
    key = " ".join(text.casefold().rstrip(" :").split())
    return SECTION.get(key) or ("other" if key in OTHER_HEADINGS else None)


def _groups(lines: list[Line]):
    groups = defaultdict(list)
    current = "header"
    for line in lines:
        next_section = _heading(line.text)
        if current == "skills" and line.text == "Languages":
            next_section = None
        if next_section:
            current = next_section
            if current == "other":
                groups[current].append(line)
        else:
            groups[current].append(line)
    return groups


def _split_date(line: str, *, single: bool = False) -> tuple[str, str, str, str]:
    match = RANGE.search(line)
    if match:
        start, end = match.groups()
        if start.casefold() in {"present", "current", "now", "ongoing"}:
            start = ""
        return line[:match.start()].strip(" |·•,–-"), start, end, line[match.end():].strip(" |·•,–-")
    if single:
        match = END_DATE.search(line)
        if match:
            return line[:match.start()].strip(" |·•,–-"), "", match[1], ""
    return line.strip(), "", "", ""


def _company_location(value: str) -> tuple[str, str]:
    for sep in (" · ", " • ", " | ", ", "):
        if sep in value:
            company, location = value.split(sep, 1)
            return company.strip(), location.strip()
    return value, ""


def _is_bullet(line: Line) -> bool:
    return line.bullet or BULLET.match(line.text) is not None


def _short_label(line: Line) -> bool:
    return not _is_bullet(line) and len(line.text) <= 140 and not line.text.endswith(".")


def _bullet_text(lines: list[Line]) -> list[str]:
    result: list[str] = []
    previous = None
    labeled = any(_is_bullet(line) for line in lines)
    for line in lines:
        text = BULLET.sub("", line.text).strip()
        if not text:
            continue
        wrapped_prefix = not text[0].isupper()
        open_ending = previous is not None and previous.text.casefold().endswith(
            ("-", ",", "/", " a", " an", " the", " and", " on", " of", " for", " to", " with")
        )
        continuation = previous is not None and not _is_bullet(line) and (
            labeled
            or (line.wrap_gap and previous.page == line.page and 0 < line.y - previous.y <= line.wrap_gap
                and (line.wrap_style == "standard" or wrapped_prefix or open_ending))
        )
        if continuation:
            result[-1] += ("" if result[-1].endswith("-") else " ") + text
        else:
            result.append(text)
        previous = line
    return result


def _experiences(lines: list[Line]) -> tuple[list[dict], list[Line]]:
    entries = []
    lost = []
    current = None
    details: list[Line] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        following = lines[i + 1] if i + 1 < len(lines) else None
        role, start, end, location = _split_date(line.text)
        company = ""
        header = False
        if not _is_bullet(line):
            if " — " in role and (end or (line.bold and not current)):
                role, employer = role.split(" — ", 1)
                company, inferred_location = _company_location(employer)
                location = location or inferred_location
                header = True
            elif end and role:
                header = True
                if line.text[:RANGE.search(line.text).start()].strip().endswith("|"):
                    company, role = role, ""
                    if following and _short_label(following):
                        role = following.text
                        i += 1
                elif (following and _short_label(following) and not following.bold
                      and (not line.size or abs(following.x - line.x) < 5)):
                    company, inferred_location = _company_location(following.text)
                    location = location or inferred_location
                    i += 1
            elif following and _short_label(line) and _short_label(following):
                employer, employer_start, employer_end, employer_location = _split_date(following.text)
                if line.bold and employer_end and employer:
                    if not following.text[:RANGE.search(following.text).start()].strip().endswith("|"):
                        company, inferred_location = _company_location(employer)
                        start, end, location = employer_start, employer_end, employer_location or inferred_location
                        header = True
                        i += 1
            if not header and not current and line.bold and _short_label(line):
                header = True
        if header:
            if current:
                current["description"] = "\n".join(_bullet_text(details))
            current = {"role": role, "company": company, "start_date": start, "end_date": end, "location": location}
            entries.append(current)
            details = []
        elif current:
            details.append(line)
        else:
            lost.append(line)
        i += 1
    if current:
        current["description"] = "\n".join(_bullet_text(details))
    return entries, lost


def _education(lines: list[Line]) -> tuple[list[dict], list[Line]]:
    entries = []
    lost = []
    i = 0
    while i < len(lines):
        line = lines[i]
        heading, start, end, _ = _split_date(line.text, single=True)
        degree = school = location = field = ""
        if " — " in heading:
            degree, school = heading.split(" — ", 1)
        elif DEGREE.search(heading):
            degree = heading
            if i + 1 < len(lines) and _short_label(lines[i + 1]) and not DEGREE.search(lines[i + 1].text):
                school, next_start, next_end, _ = _split_date(lines[i + 1].text, single=True)
                start, end = start or next_start, end or next_end
                i += 1
        elif end and heading:
            school = heading
        else:
            lost.append(line)
        if school:
            school, location = _company_location(school)
        if degree:
            degree, separator, field = degree.partition(" in ")
            if not separator and degree.endswith(" in"):
                degree = degree[:-3]
        if (degree or school) and not end and i + 1 < len(lines):
            date_only, _, next_end, _ = _split_date(lines[i + 1].text, single=True)
            if next_end and not date_only:
                i += 1
                end = next_end
        if degree or school:
            entries.append({"degree": degree.strip(), "school": school, "location": location,
                            "field_of_study": field.strip(), "start_date": start, "end_date": end})
        i += 1
    return entries, lost


def _web_link(url: str) -> str:
    try:
        parsed = urlparse(url)
        return url if parsed.scheme in {"http", "https"} and parsed.hostname else ""
    except ValueError:
        return ""


def _project_title_and_label(text: str, label: str) -> tuple[str, str]:
    if not label:
        return text, label
    remaining = "".join(label.split())
    if not remaining:
        return text, ""
    matched = len(remaining) - 1
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if char.isspace():
            continue
        if char != remaining[matched]:
            break
        matched -= 1
        if matched < 0:
            return text[:index].strip(" |·"), text[index:].strip()
    return text, label


def _projects(lines: list[Line]) -> tuple[list[dict], list[dict], list[Line]]:
    sections = []
    projects = []
    lost = []
    current = None
    details: list[Line] = []

    def save():
        if current is None:
            return
        bullets = _bullet_text(details)
        sections.append({**current, "content": "\n".join(bullets)})
        projects.append({"title": current["title"], "link": current["link"],
                         "link_label": current["linkLabel"], "bullets": bullets})

    for line in lines:
        label, url = next(((label, url) for label, uri in line.links if (url := _web_link(uri))), ("", ""))
        title, label = _project_title_and_label(line.text, label)
        if (line.bold or (not current and _short_label(line))) and title and not _is_bullet(line):
            save()
            current = {"title": title, "link": url, "linkLabel": label}
            details = []
        elif current and url:
            current["link"] = current["link"] or url
            current["linkLabel"] = current["linkLabel"] or label
        elif current:
            details.append(line)
        else:
            lost.append(line)
    save()
    return sections, projects, lost


def _parse_skills(lines: list[Line]) -> tuple[list[str], list[dict]]:
    by_category: dict[str, list[str]] = {}
    category = "Skills"
    previous = None
    for line in lines:
        values = line.text
        if line.bold:
            if line.bold == line.text and len(line.bold) < 40 and ":" not in line.bold:
                category = line.bold
                previous = None
                continue
            if line.text.startswith(line.bold):
                category = line.bold.strip(" :")
                values = line.text[len(line.bold):].lstrip(" :")
        groups = line.spaced_words if len(line.spaced_words) > 1 and not line.bold and line.skill_badges else (values,)
        parts = [part.strip() for group in groups for part in re.split(r"\s*[|,•●▪]\s*", group) if part.strip()]
        if parts:
            items = by_category.setdefault(category, [])
            if previous and items and not line.bold and line.x > previous.x + 8 and not previous.text.endswith(","):
                items[-1] += " " + parts.pop(0)
            items.extend(parts)
        previous = line
    categories = [{"category": key, "skills": resume_parser._dedupe_preserve_order(values)} for key, values in by_category.items()]
    skills = resume_parser._dedupe_preserve_order([skill for category in categories for skill in category["skills"]])
    return skills, categories


def _contact(lines: list[Line]) -> dict:
    text = " ".join(line.text for line in lines)
    email = resume_parser._extract_email(text)
    social = resume_parser._extract_social_urls(text)
    linkedin = next(iter(social["linkedin"]), "")
    portfolio = resume_parser._extract_portfolio_url(text, social)
    for line in lines:
        for _, uri in line.links:
            if "linkedin.com/" in uri.lower():
                linkedin = _web_link(uri) or linkedin
            elif _web_link(uri):
                portfolio = _web_link(uri)
    name = next((line.text for line in lines if line.bold and _short_label(line)
                 and "@" not in line.text and line.text.casefold() != "resume"), "")
    if not name:
        name = resume_parser._guess_name_from_text("\n".join(line.text for line in lines)) or ""
    if name.casefold() == "resume" or _heading(name):
        name = ""
    return {"full_name": name, "email": email, "phone": resume_parser._extract_phone(text) or "",
            "linkedin_url": linkedin or None, "portfolio_url": portfolio}


def import_resume(file_bytes: bytes, extension: str) -> dict:
    lines = pdf_lines(file_bytes) if extension == ".pdf" else docx_lines(file_bytes)
    if not lines:
        raise resume_parser.EmptyDocumentError()
    groups = _groups(lines)
    experience, missed_work = _experiences(groups["experience"])
    education, missed_school = _education(groups["education"])
    sections, projects, missed_projects = _projects(groups["projects"])
    skills, categories = _parse_skills(groups["skills"])
    info = _contact(groups["header"] + groups["contact"])
    data = {"info": info, "summary": " ".join(line.text for line in groups["summary"]),
            "experience": experience, "education": education, "skills": skills,
            "custom": {"sections": sections, "projects": projects, "skillCategories": categories,
                       "social_urls": resume_parser._extract_social_urls(" ".join(line.text for line in groups["header"] + groups["contact"]))}}
    try:
        content = ResumeContent.model_validate(data)
    except ValidationError:
        info["email"] = None
        content = ResumeContent.model_validate(data)
    unassigned = []
    warnings = []
    for section, remaining in (
        ("experience", missed_work), ("education", missed_school), ("projects", missed_projects),
        ("other", groups["other"]),
    ):
        if remaining:
            warnings.append({"code": "unassigned_text", "section": section,
                             "message": "Some source text needs to be assigned during review."})
            unassigned.extend({"section": section, "text": line.text} for line in remaining)
    if not any(groups[section] for section in ("experience", "education", "summary", "projects", "skills")):
        warnings.append({"code": "sections_unrecognized", "section": "general",
                         "message": "Check the extracted text and fill the appropriate fields."})
        unassigned.extend({"section": "general", "text": line.text} for line in groups["header"])
    if not info["full_name"]:
        warnings.append({"code": "missing_name", "section": "contact", "message": "Check the name on the resume."})
    return {"content": content.model_dump(mode="json"), "warnings": warnings, "unassigned": unassigned,
            "source_text": "\n".join(line.text for line in lines)}
