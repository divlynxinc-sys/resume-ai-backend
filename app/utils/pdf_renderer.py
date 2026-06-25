"""
HTML -> PDF rendering via headless Chromium (Playwright).

This replaces the old client-side html2canvas pipeline, which rasterized the
whole resume into a JPEG. That produced PDFs with:
  - no selectable / ATS-readable text,
  - no clickable links (everything was an image),
  - blind page breaks that sliced through cards and text near page boundaries,
  - html2canvas text-shaping artifacts (e.g. "November" -> "N - vember").

Chromium renders the exact same template HTML the user previews, with real
text, real <a> link annotations, and proper CSS pagination (`@page`,
`page-break-inside: avoid`).

Security: the HTML is built from the user's own resume data + our templates,
but we still treat it as untrusted. The page is rendered with JavaScript
disabled and ALL network egress blocked (only `data:`/`about:`/`blob:` URLs are
allowed), so injected markup cannot exfiltrate data or perform SSRF.
"""
from __future__ import annotations

import threading

# Playwright is an optional/heavy dependency. Import lazily so the rest of the
# app (and test collection) still works on machines where the browser binaries
# are not installed.
try:  # pragma: no cover - import guard
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    sync_playwright = None  # type: ignore
    _PLAYWRIGHT_IMPORT_ERROR = exc


# Chromium's sync API objects are not safe to share across threads, and FastAPI
# runs sync endpoints in a threadpool. We therefore launch + close a browser per
# request, serialized by a lock to bound memory under concurrency.
_RENDER_LOCK = threading.Lock()

# Try the bundled Chromium first (what we install in the container via
# `playwright install chromium`), then fall back to a system browser so local
# dev works without the ~150MB download.
_LAUNCH_CANDIDATES = (
    {},                       # bundled chromium (prod)
    {"channel": "chrome"},    # system Chrome (local dev)
    {"channel": "msedge"},    # system Edge (local dev fallback)
)

_ALLOWED_URL_PREFIXES = ("data:", "about:", "blob:")

MAX_HTML_BYTES = 5 * 1024 * 1024  # 5 MB guardrail on input size


class PdfRenderError(RuntimeError):
    """Raised when the HTML could not be rendered to a PDF."""


def playwright_available() -> bool:
    return sync_playwright is not None


def _launch(pw):
    attempts = []
    for kwargs in _LAUNCH_CANDIDATES:
        label = kwargs.get("channel", "bundled chromium")
        try:
            return pw.chromium.launch(headless=True, args=["--no-sandbox"], **kwargs)
        except Exception as exc:  # try next candidate
            first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            attempts.append(f"{label}: {first_line}")
    # Lead with the real remedy (install Chromium); list every attempt so the
    # bundled-chromium failure is visible, not just the last channel fallback.
    raise PdfRenderError(
        "Could not launch a browser for PDF export — no Chromium is installed in "
        "the backend. In Docker, build the backend from the Playwright image "
        "(mcr.microsoft.com/playwright/python); otherwise run "
        "`python -m playwright install --with-deps chromium`. Tried — "
        + " | ".join(attempts)
    )


def render_html_to_pdf(html: str) -> bytes:
    """Render a full HTML document to PDF bytes. Raises PdfRenderError on failure."""
    if sync_playwright is None:
        raise PdfRenderError(
            f"Playwright is not available: {_PLAYWRIGHT_IMPORT_ERROR}"
        )
    if not html or not html.strip():
        raise PdfRenderError("Empty HTML supplied to PDF renderer.")
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise PdfRenderError("Resume HTML is too large to render.")

    with _RENDER_LOCK:
        with sync_playwright() as pw:
            browser = _launch(pw)
            try:
                # JS disabled: templates are static HTML+CSS; this removes a
                # whole class of injection risk.
                context = browser.new_context(java_script_enabled=False)
                page = context.new_page()

                # Block every network request except in-document data URIs.
                def _guard(route):
                    url = route.request.url or ""
                    if url.startswith(_ALLOWED_URL_PREFIXES):
                        route.continue_()
                    else:
                        route.abort()

                page.route("**/*", _guard)

                page.set_content(html, wait_until="load", timeout=20000)
                # Let web fonts settle so text metrics are final.
                try:
                    page.evaluate_handle("document.fonts && document.fonts.ready")
                except Exception:
                    pass

                # NB: we deliberately do NOT pass `margin=` here. Passing it
                # would override the template's CSS `@page { margin }`, which is
                # what gives EVERY page (not just page 1) identical margins.
                pdf_bytes = page.pdf(
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                )
                return pdf_bytes
            finally:
                browser.close()
