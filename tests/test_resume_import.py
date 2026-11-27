from io import BytesIO

from docx import Document

from app.utils.docx_builder import build_resume_docx
from app.utils.resume_import import _bullet_text, _project_title_and_label
from app.utils.resume_layout import Line
from app.utils.resume_parser import parse_uploaded_resume


def test_pdf_link_label_spacing_does_not_change_the_visible_project_title():
    assert _project_title_and_label("Portfolio Website View portfolio", "Viewportfolio") == (
        "Portfolio Website", "View portfolio",
    )


def test_sidebar_wrapped_phrase_keeps_its_line_and_the_next_bullet_stays_separate():
    lines = [
        Line("Grouped candidates into Watch / On", y=10, wrap_gap=12, wrap_style="sidebar"),
        Line("Track, enabling better follow-up", y=22, wrap_gap=12, wrap_style="sidebar"),
        Line("Scraped, cleaned and mapped data", y=34, wrap_gap=12, wrap_style="sidebar"),
    ]
    assert _bullet_text(lines) == [
        "Grouped candidates into Watch / On Track, enabling better follow-up",
        "Scraped, cleaned and mapped data",
    ]


def test_word_export_recovers_independent_education_experience_and_project_fields():
    original = {
        "candidate_info": {"name": "Synthetic Candidate", "email": "candidate@example.com"},
        "resume": {
            "summary": "Experience with payment systems.",
            "experiences": [{
                "role": "Platform Engineer", "company": "Example Labs", "location": "Springfield",
                "startDate": "2021-03", "endDate": "2024", "bullets": ["Built the API", "Led a team"],
            }],
            "education": [{
                "school": "Example University", "degree": "BSc", "field": "Computer Science",
                "endDate": "2020",
            }],
            "projects": [{
                "title": "Portfolio Website", "link": {"url": "https://example.com/portfolio", "label": "View portfolio"},
                "bullets": ["Published a site", "Improved navigation"],
            }],
            "skills": [{"category": "Languages", "skills": ["C++", "C#"]}],
        },
    }
    review = parse_uploaded_resume(build_resume_docx(original), "own.docx", include_review=True)
    content = review["content"]
    assert content["info"]["full_name"] == "Synthetic Candidate"
    assert content["experience"][0] == {
        "role": "Platform Engineer", "company": "Example Labs", "location": "Springfield",
        "start_date": "March 2021", "end_date": "2024", "description": "Built the API\nLed a team",
    }
    assert content["education"][0]["school"] == "Example University"
    assert content["education"][0]["degree"] == "BSc"
    assert content["education"][0]["field_of_study"] == "Computer Science"
    assert content["custom"]["projects"][0]["link_label"] == "View portfolio"
    assert content["custom"]["projects"][0]["link"] == "https://example.com/portfolio"
    assert content["skills"] == ["C++", "C#"]


def test_table_contents_and_unassigned_sections_appear_in_review():
    document = Document()
    document.add_paragraph("Synthetic Candidate")
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.add_paragraph("Skills")
    cell.add_paragraph("C++ | C#")
    document.add_heading("Certifications")
    document.add_paragraph("Fixture Credential")
    output = BytesIO()
    document.save(output)
    review = parse_uploaded_resume(output.getvalue(), "table.docx", include_review=True)
    assert review["content"]["skills"] == ["C++", "C#"]
    assert {"section": "other", "text": "Fixture Credential"} in review["unassigned"]
    assert "Fixture Credential" in review["source_text"]
