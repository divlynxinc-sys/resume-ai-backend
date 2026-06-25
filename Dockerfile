# Backend image. Based on the official Playwright Python image, which ships
# Chromium + every shared library it needs, version-matched to the Playwright
# pip package. This is what makes the HTML->PDF export endpoint work in
# production (the previous python:3.11-slim image had no browser, so /resumes/
# export/pdf failed while the pure-Python DOCX export worked).
#
# IMPORTANT: keep this tag in sync with `playwright==X.Y.Z` in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

WORKDIR /srv/backend

# Browsers are pre-installed in the base image here.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway/Render inject $PORT; default to 8000 for local/compose.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
