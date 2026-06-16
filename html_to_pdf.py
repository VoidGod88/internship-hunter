"""
html_to_pdf.py
将 HTML CV 转换为高质量 PDF（使用 Playwright / Chromium）
用法: python html_to_pdf.py
"""

from pathlib import Path
import sys

HTML_PATH = Path(__file__).parent.parent / "cv_yipfungming.html"
PDF_PATH  = Path(__file__).parent.parent / "CV_YipFungMing_AI.pdf"

def convert_with_playwright():
    from playwright.sync_api import sync_playwright

    abs_html = HTML_PATH.resolve().as_uri()   # file:///C:/.../cv_yipfungming.html
    print(f"[HTML] 正在渲染: {HTML_PATH.name}")
    print(f"   路径: {abs_html}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(abs_html, wait_until="networkidle", timeout=15_000)
        # 等待字体加载完成
        page.evaluate("document.fonts.ready")
        page.pdf(
            path=str(PDF_PATH),
            format="A4",
            print_background=True,
            margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"},
        )
        browser.close()

    size_kb = PDF_PATH.stat().st_size // 1024
    print(f"[OK] PDF 已生成: {PDF_PATH}")
    print(f"   文件大小: {size_kb} KB")
    return True


def convert_with_pdfkit():
    """备用方案: pdfkit + wkhtmltopdf"""
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
    print(f"✅ PDF 已生成 (pdfkit): {PDF_PATH}")
    return True


if __name__ == "__main__":
    if not HTML_PATH.exists():
        print(f"[ERROR] 找不到 HTML 文件: {HTML_PATH}")
        sys.exit(1)

    print("=" * 50)
    print("[CONVERT] HTML → PDF 转换")
    print("=" * 50)

    try:
        ok = convert_with_playwright()
    except Exception as e:
        print(f"[WARN] Playwright 失败: {e}")
        print("   尝试备用方案 (pdfkit)...")
        try:
            ok = convert_with_pdfkit()
        except Exception as e2:
            print(f"[ERROR] pdfkit 也失败: {e2}")
            print("\n请手动用浏览器打开 HTML 文件 → Ctrl+P → 另存为 PDF")
            sys.exit(1)

    print("\n完成！")
