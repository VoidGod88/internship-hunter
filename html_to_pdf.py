"""
html_to_pdf.py
Convert HTML CV to high-quality PDF (using Playwright / Chromium)
Usage: python html_to_pdf.py [input.html] [output.pdf]
Defaults: cv.html → CV.pdf
"""

from pathlib import Path
import sys

HTML_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("cv.html")
PDF_PATH = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("CV.pdf")


def convert_with_playwright():
    from playwright.sync_api import sync_playwright

    abs_html = HTML_PATH.resolve().as_uri()   # file:///C:/.../cv.html
    print(f"[HTML] Rendering: {HTML_PATH.name}")
    print(f"   Path: {abs_html}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(abs_html, wait_until="networkidle", timeout=15_000)
        page.evaluate("document.fonts.ready")
        page.pdf(
            path=str(PDF_PATH),
            format="A4",
            print_background=True,
            margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"},
        )
        browser.close()

    size_kb = PDF_PATH.stat().st_size // 1024
    print(f"[OK] PDF generated: {PDF_PATH}")
    print(f"   File size: {size_kb} KB")
    return True


def convert_with_pdfkit():
    """Fallback: pdfkit + wkhtmltopdf"""
    import pdfkit
    options = {
        "page-size": "A4",
        "margin-top": "0mm",
        "margin-bottom": "0mm",
        "margin-left": "0mm",
        "margin-right": "0mm",
        "encoding": "UTF-8",
        "print-media-type": True,
        "enable-local-file-access": True,
    }
    pdfkit.from_file(str(HTML_PATH), str(PDF_PATH), options=options)
    print(f"✅ PDF generated (pdfkit): {PDF_PATH}")
    return True


if __name__ == "__main__":
    if not HTML_PATH.exists():
        print(f"[ERROR] HTML file not found: {HTML_PATH}")
        print("Usage: python html_to_pdf.py [input.html] [output.pdf]")
        sys.exit(1)

    print("=" * 50)
    print("[CONVERT] HTML → PDF")
    print("=" * 50)

    try:
        ok = convert_with_playwright()
    except Exception as e:
        print(f"[WARN] Playwright failed: {e}")
        print("   Trying fallback (pdfkit)...")
        try:
            ok = convert_with_pdfkit()
        except Exception as e2:
            print(f"[ERROR] pdfkit also failed: {e2}")
            print("\nPlease open the HTML file in browser → Ctrl+P → Save as PDF")
            sys.exit(1)

    print("\nDone!")
