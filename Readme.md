# 🧠 ResumeAI Backend

FastAPI + PostgreSQL backend for an AI resume builder. Includes JWT auth (access + refresh), RBAC, per-user resume IDs, dashboard summary, session logging, and sectioned resume editing APIs.

---

## 🚀 Tech Stack
- FastAPI + Starlette
- SQLAlchemy 2.x + Alembic
- PostgreSQL
- JWT (python-jose) + Passlib (PBKDF2-SHA256)

---

## ⚙️ Setup (Windows PowerShell)

1) Installation

```bash
git clone https://github.com/yourusername/resumen-ai-backend.git
cd resumen-ai-backend


python -m venv venv
source venv/bin/activate # for mac/linux
venv\Scripts\activate # for windows

pip install -r requirements.txt
```

2) Env configuration
- The app auto-loads `.envs/.env.<APP_ENV>` then `.env`. OS envs take precedence.
- Recommended local file: create `.envs/.env.local`:
```bash
APP_ENV=local
DB_NAME=resume_ai
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432
JWT_SECRET_KEY=dev-super-secret-key
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_MINUTES=43200
```

3) Start PostgreSQL and create DB (choose one)
- Local PG service → create DB:
```powershell
psql -h localhost -U postgres -c "CREATE DATABASE resume_ai;"
```
- Or Docker:
```powershell
docker run -d --name resumeai-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
psql -h localhost -U postgres -c "CREATE DATABASE resume_ai;"
```

4) Migrations
```powershell
$env:APP_ENV="local"
python -m alembic upgrade head
```

5) Run
```powershell
uvicorn app.main:app --reload
```
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

Authorize in Swagger: click “Authorize”, paste only the access token (no “Bearer ”).

---

## 🔐 Auth Quickstart
1) Signup
```http
POST /auth/signup
{ "name": "Alex", "email": "alex@example.com", "password": "SecurePass123!" }
```
2) Login → get access_token + refresh_token
```http
POST /auth/login
{ "email": "alex@example.com", "password": "SecurePass123!" }
```
3) Refresh
```http
POST /auth/refresh
{ "refresh_token": "<REFRESH_TOKEN>" }
```

---

## 📄 Resumes and Dashboard
- Per-user IDs: every resume has a local `id` that starts at 1 for each user.
- Create:
```http
POST /resumes?mode=scratch
{ "title": "My Resume" }
```
- List (returns `{items, total}`):
```http
GET /resumes?limit=50&offset=0
```
- Section updates (raw JSON bodies):
```http
PATCH /resumes/{id}/content?section=info
{ "full_name":"Alex Doe","email":"alex@example.com","phone":"+1 555 555" }
```
- Dashboard recents:
```http
GET /dashboard/summary
```

---

## 🧭 Swagger tags
- Auth, Profile, Admin, Dashboard, Resumes, Templates, Pricing, Settings, Help Center, Juno AI Assistant

---

## 📋 Feature API Reference

| Feature | Endpoint | Description |
|---------|----------|-------------|
| **My Resumes** | `GET /resumes` | List resumes created by current user |
| **Upload (new)** | `POST /resumes/from-upload` | Upload PDF/DOCX, create resume with parsed data |
| **Upload (existing)** | `PATCH /resumes/{id}/from-upload` | Upload PDF/DOCX, merge into existing resume |
| **Parse Preview** | `POST /resumes/parse-upload` | Parse file, return content without saving |
| **Pricing** | `GET /pricing/plans` | Public list of pricing plans |
| **Pricing (Admin CRUD)** | `GET/POST/PATCH/DELETE /pricing/admin/plans` | Customizable plans |
| **Choose Plan** | `POST /pricing/plans/{id}/choose` | User selects a plan |
| **Templates** | `GET /templates?style=&industry=` | List with Style/Industry filters |
| **Recent Activity** | `GET /dashboard/recent-activity` | Last edited resumes |
| **Profile** | `GET /profile/me`, `PATCH /profile/me` | User CRUD – returns updated profile |
| **Change Password** | `POST /profile/change-password` | Requires old_password, new_password, confirm_password |
| **Sync from Resume** | `POST /profile/sync-from-resume/{id}` | Copy resume info → profile |
| **Logout** | `POST /auth/logout-all` | Revoke all sessions |
| **Settings** | `GET/PATCH /settings/preferences` | Theme, notifications, 2FA |
| **Account Summary** | `GET /settings/account/summary` | Plan, credits |
| **Export Data** | `GET /settings/account/export` | Download all user data as JSON |
| **Delete Account** | `DELETE /settings/account` | Soft delete account |
| **Help Center** | `GET /help/topics` | Browse by topic |
| **Help Search** | `GET /help/articles?q=` | Search for answers |
| **Featured Articles** | `GET /help/articles/featured` | Featured articles |
| **FAQs** | `GET /help/articles/faqs` | FAQs list |
| **Article Detail** | `GET /help/articles/{slug}` | Full article (Read More) |
| **Help Admin** | `GET/POST/PATCH/DELETE /help/admin/topics` | Topics CRUD |
| **Help Admin** | `GET/POST/PATCH/DELETE /help/admin/articles` | Articles CRUD |
| **ATS Score (save)** | `POST /resumes/{id}/ats-score` | Store ATS data from AI |
| **ATS Score (get)** | `GET /resumes/{id}/ats-score` | Get latest ATS score |
| **Juno - Example Prompts** | `GET /juno/prompts` | List example prompts for AI assistant |
| **Juno Admin CRUD** | `GET/POST/PATCH/DELETE /juno/admin/prompts` | Manage example prompts |

---

## 🧪 Postman
Import the collection and environment from `postman/`:
- **Collection:** `postman/ResumeAI-Backend.postman_collection.json`
- **Environment:** `postman/ResumeAI-Local.postman_environment.json` (optional)

1. Import the collection → File → Import → select the `.postman_collection.json`
2. (Optional) Import the environment and select it in the top-right dropdown
3. Run **Auth → Login** to get tokens (auto-saved to collection variables)
4. Variables: `base_url`, `access_token`, `refresh_token`, `resume_id`

---

## 🧱 Migrations in Git
Commit `alembic/versions/*.py`, `alembic/env.py`, `alembic.ini`. On new envs run:
```powershell
python -m alembic upgrade head
```

---

## 🤝 Contributing
- Create feature branches; open PRs.
- Don’t commit `.env*`, `.venv`, `__pycache__`.

---

## 🩺 Troubleshooting
- “alembic not recognized”: use `python -m alembic ...`.
- “driver://” in alembic: we override from env automatically.
- bcrypt errors: we use PBKDF2-SHA256 (passlib) by default.

Deactivate venv: `deactivate`

### Project Structure

```bash
resumen-ai-backend/
│
├── app/
│ ├── main.py
│ ├── models/
│ │ └── user.py
│ ├── routes/
│ │ └── user_routes.py
│ ├── schemas/
│ │ └── user_schema.py
│ ├── services/
│ │ └── resume_ai.py
│ └── database/
│ └── connection.py
│
├── scripts/
│ └── create_db.py
├── requirements.txt
├── .env
└── README.md
```

### Collaboration Guide

- **Create a new branch for each feature: AND NEVER WORK/PUSH YOUR WORK IN MAIN OR DEV DIRECTLY!**

```bash

  git checkout -b feature/resume-upload
```

- **Push and create a pull request:**

```bash
  git push origin feature/resume-upload
```

- **Use .env.example for shared configs.**

### Future Plans

- AI Resume Enhancer using OpenAI API
- Resume Template Generator (PDF)
- Cloud Storage Integration
- User Dashboard with Analytics

### For swagger, add "/docs" in the url e.g : http://127.0.0.1:8000/docs#/

#
