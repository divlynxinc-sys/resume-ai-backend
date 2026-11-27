"""Read the visible structure of resume PDFs and DOCX paragraphs."""

from dataclasses import dataclass
from io import BytesIO
from statistics import median

from app.utils import resume_parser


@dataclass(frozen=True)
class Line:
    text: str
    bold: str = ""
    bullet: bool = False
    x: float = 0
    y: float = 0
    size: float = 0
    page: int = 0
    wrap_gap: float = 0
    compact_spacing: bool = False
    skill_badges: bool = False
    spaced_words: tuple[str, ...] = ()
    links: tuple[tuple[str, str], ...] = ()


def _is_bold(char: dict) -> bool:
    font = char.get("fontname", "").lower()
    return "semibold" not in font and any(weight in font for weight in ("bold", "black", "demi"))


def _pdf_column_divider(page) -> float | None:
    words = [word for word in page.extract_words() if word["top"] > page.height * 0.12]
    if len(words) < 24:
        return None
    options = []
    for point in range(int(page.width * 0.25), int(page.width * 0.68), 4):
        left = [word for word in words if word["x1"] < point - 3]
        right = [word for word in words if word["x0"] > point + 3]
        crossings = len(words) - len(left) - len(right)
        if min(len(left), len(right)) < 12 or crossings > max(2, len(words) * 0.017):
            continue
        left_rows = {round(word["top"] / 4) for word in left}
        right_rows = {round(word["top"] / 4) for word in right}
        if min(len(left_rows), len(right_rows)) < 5:
            continue
        if len(left_rows & right_rows) < 3:
            continue
        options.append((crossings, abs(point - page.width * 0.4), point))
    return min(options)[2] if options else None


def _pdf_lines(page, source, page_number: int, split: bool) -> list[Line]:
    from pdfplumber.utils import extract_text

    result = []
    annotations = page.hyperlinks
    for raw in source.extract_text_lines(return_chars=True):
        text = raw["text"].strip()
        if not text:
            continue
        chars = sorted(raw["chars"], key=lambda char: char["x0"])
        lead = []
        for char in chars:
            if not _is_bold(char) and char["text"].strip():
                break
            lead.append(char)
        prefix = extract_text(lead, x_tolerance=1).strip() if lead else ""
        if prefix and not text.startswith(prefix):
            compact = "".join(prefix.split())
            matched = 0
            for index, char in enumerate(text):
                if char.isspace():
                    continue
                if char != compact[matched]:
                    break
                matched += 1
                if matched == len(compact):
                    prefix = text[:index + 1].strip()
                    break
        font = chars[0]["fontname"].lower()
        spacing = 1.5 if "georgia" in font or ("segoe" in font and not split) else 1.35
        word_parts: list[list[dict]] = []
        for char in chars:
            if not char["text"].strip():
                continue
            if not word_parts or char["x0"] - word_parts[-1][-1]["x1"] > 4.5:
                word_parts.append([])
            word_parts[-1].append(char)
        links = []
        for link in annotations:
            if link["bottom"] < raw["top"] or link["top"] > raw["bottom"]:
                continue
            if link["x1"] <= raw["x0"] or link["x0"] >= raw["x1"]:
                continue
            link_chars = [c for c in chars if link["x0"] - 0.7 <= (c["x0"] + c["x1"]) / 2 <= link["x1"] + 0.7]
            label = extract_text(link_chars).strip() if link_chars else ""
            if label and link.get("uri"):
                links.append((label, link["uri"]))
        size = median(char["size"] for char in chars)
        result.append(Line(
            text=text, bold=prefix, x=raw["x0"], y=raw["top"], page=page_number,
            size=size, wrap_gap=size * spacing + 0.35,
            compact_spacing="arial" in font, skill_badges="segoe" in font,
            spaced_words=tuple(extract_text(part, x_tolerance=1).strip() for part in word_parts),
            links=tuple(links),
        ))
    return result


def pdf_lines(file_bytes: bytes) -> list[Line]:
    import pdfplumber

    if b"%PDF-" not in file_bytes[:1024]:
        raise ValueError("invalid PDF")
    streams: tuple[list[Line], list[Line]] = ([], [])
    total = 0
    sidebar = False
    with pdfplumber.open(BytesIO(file_bytes)) as document:
        if document.doc.encryption is not None:
            raise ValueError("protected PDF")
        if len(document.pages) > resume_parser.MAX_PDF_PAGES:
            raise resume_parser.DocumentLimitError()
        first_divider = _pdf_column_divider(document.pages[0]) if document.pages else None
        for index, page in enumerate(document.pages):
            try:
                divider = first_divider
                if divider is None:
                    columns = (_pdf_lines(page, page, index, False), [])
                else:
                    x0, top, x1, bottom = page.bbox
                    columns = (
                        _pdf_lines(page, page.crop((x0, top, divider, bottom)), index, True),
                        _pdf_lines(page, page.crop((divider, top, x1, bottom)), index, True),
                    )
                left_contact = any(line.text.casefold() == "contact" for line in columns[0])
                if index == 0:
                    sidebar = divider is not None and left_contact
                for column, lines in enumerate(columns):
                    if index and not column and sidebar and left_contact:
                        header = []
                        for line in lines:
                            if line.text.casefold() == "contact":
                                break
                            if line.bold and line.text.casefold() != "resume":
                                header.append(line)
                        if header and not any(existing.text == header[0].text for existing in streams[0]):
                            streams[0][0:0] = header
                        continue
                    for line in lines:
                        resume_parser._check_text(line.text)
                        total += len(line.text) + 1
                        if total > resume_parser.MAX_TEXT_LENGTH:
                            raise resume_parser.DocumentLimitError()
                    streams[column].extend(lines)
            finally:
                page.close()
    return streams[0] + streams[1]


def docx_lines(file_bytes: bytes) -> list[Line]:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    resume_parser._validate_docx(file_bytes)
    document = Document(BytesIO(file_bytes))
    seen = set()

    def paragraphs(container, parent):
        for child in container.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, parent)
            elif child.tag == qn("w:tbl"):
                for row in Table(child, parent).rows:
                    for cell in row.cells:
                        if cell._tc not in seen:
                            seen.add(cell._tc)
                            yield from paragraphs(cell._tc, cell)

    lines = []
    total = 0
    for paragraph in paragraphs(document.element.body, document):
        style = (paragraph.style.name if paragraph.style else "").lower()
        bullet = style.startswith(("list bullet", "list number")) or paragraph._p.xpath("w:pPr/w:numPr") != []
        text_parts = []
        links = []
        for child in paragraph._p:
            if child.tag not in {qn("w:r"), qn("w:hyperlink")}:
                continue
            text = "".join(
                (node.text or "") if node.tag == qn("w:t") else " "
                for node in child.iter()
                if node.tag in {qn("w:t"), qn("w:br"), qn("w:tab")}
            )
            text_parts.append(text)
            if child.tag == qn("w:hyperlink"):
                relation = paragraph.part.rels.get(child.get(qn("r:id")))
                if relation is not None and relation.is_external:
                    links.append((text, relation.target_ref))
        text = "".join(text_parts).strip()
        if not text:
            continue
        total += len(text) + 1
        resume_parser._check_text(text)
        if total > resume_parser.MAX_TEXT_LENGTH:
            raise resume_parser.DocumentLimitError()
        leading = []
        for run in paragraph.runs:
            if not run.bold:
                break
            leading.append(run.text)
        lines.append(Line(text, bold="".join(leading).strip(), bullet=bullet, links=tuple(links)))
    return lines
