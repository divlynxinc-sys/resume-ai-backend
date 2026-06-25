"""Verify the new export pipeline: HTML -> PDF (Chromium) with real text + links
+ clean pagination. Renders each template PDF and dumps page PNGs for visual diff."""
import os
import sys
import glob

sys.path.insert(0, os.path.dirname(__file__))
from app.utils.pdf_renderer import render_html_to_pdf  # noqa: E402

HTML_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "resume-ai-frontend", "scripts", "output", "_verify"))
OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "_verify_out"))
os.makedirs(OUT_DIR, exist_ok=True)


def check_pdf(path):
    from pypdf import PdfReader
    r = PdfReader(path)
    pages = len(r.pages)
    text = "\n".join((p.extract_text() or "") for p in r.pages)
    links = []
    for p in r.pages:
        annots = p.get("/Annots")
        if not annots:
            continue
        for a in annots:
            o = a.get_object()
            uri = o.get("/A", {}).get("/URI")
            if uri:
                links.append(uri)
    has_november = "November 2024" in text
    has_garble = "N - vember" in text or "N – vember" in text
    return pages, len(text), links, has_november, has_garble


def to_png(path, slug):
    import fitz
    doc = fitz.open(path)
    outs = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=110)
        out = os.path.join(OUT_DIR, f"{slug}__p{i+1}.png")
        pix.save(out)
        outs.append(out)
    doc.close()
    return outs


def main():
    htmls = sorted(glob.glob(os.path.join(HTML_DIR, "*.html")))
    if not htmls:
        print("No HTML found in", HTML_DIR)
        return
    for h in htmls:
        slug = os.path.splitext(os.path.basename(h))[0]
        html = open(h, encoding="utf-8").read()
        try:
            pdf = render_html_to_pdf(html)
        except Exception as e:
            print(f"[{slug}] RENDER FAILED: {e}")
            continue
        pdf_path = os.path.join(OUT_DIR, f"{slug}.pdf")
        open(pdf_path, "wb").write(pdf)
        pages, tlen, links, has_nov, garble = check_pdf(pdf_path)
        pngs = to_png(pdf_path, slug)
        print(f"[{slug}] pages={pages} text_len={tlen} links={len(links)} "
              f"November_ok={has_nov} garbled={garble}")
        print(f"    links: {links}")
    print("\nPNGs + PDFs in:", OUT_DIR)


if __name__ == "__main__":
    main()
