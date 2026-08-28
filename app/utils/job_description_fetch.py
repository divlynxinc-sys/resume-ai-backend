"""
Fetch a job-posting URL and extract its visible text.

Used only by `POST /interviews/fetch-job-description` (the AI Interviews setup
form's "add a link" option, alongside pasting). This makes an outbound request
to a URL the user supplies, which is an SSRF surface: `_ensure_public_host`
resolves and rejects loopback/private/link-local/reserved addresses before
every connection AND every redirect hop; the response is never returned raw,
only extracted, truncated text.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Tuple

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException, status

MAX_RESPONSE_BYTES = 3_000_000  # HTML pages are rarely this large; stop reading past it
MAX_REDIRECTS = 5
FETCH_TIMEOUT_SECONDS = 10.0
MIN_EXTRACTED_CHARS = 100
# A generic browser UA: some ATS/job-board pages 403 on a bare "python-httpx" UA.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Common containers for the actual posting text, tried before falling back to
# the whole page. Order matters: more specific first.
_CONTENT_TAGS = ("article", "main")
_CONTENT_CLASS_HINTS = (
    "job-description", "jobdescription", "job_description",
    "job-details", "jobdetails", "posting-body", "description",
)


def _ensure_public_host(url: httpx.URL) -> None:
    if url.scheme not in ("http", "https") or not url.host:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Enter a valid http or https link.")
    try:
        infos = socket.getaddrinfo(url.host, None)
    except socket.gaierror:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That link's address could not be found.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That link can't be used.")


def _looks_like_content_class(value) -> bool:  # bs4 passes str | list[str] | None
    if not value:
        return False
    classes = " ".join(value).lower() if isinstance(value, list) else str(value).lower()
    return any(hint in classes for hint in _CONTENT_CLASS_HINTS)


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "header", "footer"]):
        tag.decompose()

    for name in _CONTENT_TAGS:
        node = soup.find(name)
        if node is not None:
            text = node.get_text(separator="\n", strip=True)
            if len(text) >= MIN_EXTRACTED_CHARS:
                return text

    node = soup.find(attrs={"class": _looks_like_content_class}) or soup.find(attrs={"id": _looks_like_content_class})
    if node is not None:
        text = node.get_text(separator="\n", strip=True)
        if len(text) >= MIN_EXTRACTED_CHARS:
            return text

    return soup.get_text(separator="\n", strip=True)


def fetch_job_description_from_url(url: str) -> Tuple[str, str]:
    """Returns (extracted_text, final_url). Raises HTTPException on any failure."""
    current = httpx.URL(url.strip())
    _ensure_public_host(current)

    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain"},
        ) as client:
            response = None
            for _ in range(MAX_REDIRECTS + 1):
                response = client.get(current)
                if not response.is_redirect:
                    break
                location = response.headers.get("location")
                if not location:
                    break
                current = current.join(location)
                _ensure_public_host(current)
            else:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="That link redirected too many times.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="That page took too long to load.")
    except httpx.HTTPError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="We couldn't reach that link.")

    if response is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="We couldn't reach that link.")
    if response.status_code in (401, 403, 429):
        # Some sites (LinkedIn, Indeed, and others with aggressive bot protection)
        # block any non-browser client regardless of User-Agent — not fixable here.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That site doesn't allow this kind of access. Try pasting the description instead.",
        )
    if response.is_error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="We couldn't reach that link.")

    content_type = response.headers.get("content-type", "")
    if not any(t in content_type for t in ("text/html", "application/xhtml", "text/plain")):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="That link doesn't point to a readable page.")

    raw = response.content[:MAX_RESPONSE_BYTES].decode(response.encoding or "utf-8", errors="replace")
    text = raw if "text/plain" in content_type else _extract_text(raw)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())

    if len(text) < MIN_EXTRACTED_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="We couldn't find enough text on that page. Try pasting the description instead.",
        )
    return text, str(response.url)
