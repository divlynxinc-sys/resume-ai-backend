# 🧠 Resumen AI Backend

Resumen AI helps users create professional resumes from scratch or enhance existing ones using AI.  
This backend is built with **FastAPI** and **PostgreSQL** for scalability and performance.

---

## 🚀 Tech Stack

- **Framework:** FastAPI
- **Database:** PostgreSQL (cloud - TBD)
- **ORM:** SQLAlchemy + Alembic
- **Auth:** JWT (coming soon)
- **Deployment:** Docker / Render / Railway (TBD)

---

## ⚙️ Installation

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/resumen-ai-backend.git
cd resumen-ai-backend
```

python -m venv venv
source venv/bin/activate # for mac/linux
venv\Scripts\activate # for windows

pip install -r requirements.txt

### Skip the database part for now. It is under review (not finalised)

Create a .env file in the root folder:
DATABASE_URL=postgresql://postgres:password@localhost/resumen_ai
SECRET_KEY=your_secret_key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

### --------------------------------------------------------------------

### Run the Server

uvicorn app.main:app --reload

### Project Structure

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

### Collaboration Guide

- **Create a new branch for each feature:**
  git checkout -b feature/resume-upload
- **Push and create a pull request:**
  git push origin feature/resume-upload

- **Use .env.example for shared configs.**

### Future Plans

- AI Resume Enhancer using OpenAI API
- Resume Template Generator (PDF)
- Cloud Storage Integration
- User Dashboard with Analytics

### For swagger, add "/docs" in the url e.g : http://127.0.0.1:8000/docs#/

#
#

### To deactivate the VENV (Virtual ENVironment) run this command in terminal : deactivate
