from pathlib import Path


def extract_text(pdf_path: Path) -> str:
    import fitz
    doc = fitz.open(str(pdf_path))
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text


def extract_markdown_like_text(pdf_path: Path) -> str:
    import fitz
    doc = fitz.open(str(pdf_path))
    parts = []
    for i, page in enumerate(doc):
        parts.append(f"## Page {i + 1}\n\n{page.get_text()}")
    doc.close()
    return "\n\n".join(parts)
