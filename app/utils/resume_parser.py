"""Parse resume text from PDF/DOCX into structured ResumeContent."""
import re
from zipfile import ZipFile
from io import BytesIO
from urllib.parse import urlparse

MAX_TEXT_LENGTH = 100_000
MAX_LINE_LENGTH = 4096
MAX_PDF_PAGES = 50
MAX_DOCX_TOTAL = 32 * 1024 * 1024
MAX_DOCX_MEMBER = 16 * 1024 * 1024
MAX_DOCX_ENTRIES = 1000


class DocumentLimitError(ValueError):
    pass


class EmptyDocumentError(ValueError):
    pass


def _check_text(text: str) -> None:
    if len(text) > MAX_TEXT_LENGTH or any(len(line) > MAX_LINE_LENGTH for line in text.splitlines()):
        raise DocumentLimitError()


def _validate_docx(file_bytes: bytes) -> None:
    with ZipFile(BytesIO(file_bytes)) as archive:
        entries = archive.infolist()
        if (len(entries) > MAX_DOCX_ENTRIES or sum(entry.file_size for entry in entries) > MAX_DOCX_TOTAL
                or any(entry.file_size > MAX_DOCX_MEMBER for entry in entries)):
            raise DocumentLimitError()
        if any(entry.flag_bits & 1 for entry in entries) or "[Content_Types].xml" not in archive.namelist():
            raise ValueError("protected or invalid DOCX")


def _extract_email(text: str) -> str | None:
    for token in re.split(r"""[\s<>()\[\]{},;:|"']+""", text):
        candidate = token.rstrip(".")
        if "@" in candidate and re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", candidate):
            return candidate
    return None


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
_BARE_DOMAIN_WITH_PATH_PAT = re.compile(r"(?i)(?:[a-z0-9-]{1,63}\.)+[a-z]{2,63}/[^\s\)\]\}>,\"']+")


def _extract_url_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for pat in (_URL_WITH_SCHEME_PAT, _WWW_URL_PAT):
        for m in pat.finditer(text):
            candidates.append(m.group(0))
    for token in re.split(r"""[\s<>()\[\]{},|"']+""", text):
        if "/" in token and _BARE_DOMAIN_WITH_PATH_PAT.fullmatch(token):
            candidates.append(token)
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


def parse_uploaded_resume(file_bytes: bytes, filename: str, *, include_review: bool = False) -> dict:
    from app.utils.resume_import import import_resume

    extension = "." + filename.rsplit(".", 1)[-1].lower()
    if extension not in {".pdf", ".docx"}:
        raise ValueError("Unsupported format. Use PDF or DOCX.")
    result = import_resume(file_bytes, extension)
    return result if include_review else result["content"]
