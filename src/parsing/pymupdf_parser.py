from pathlib import Path
import hashlib
import logging
import re

logger = logging.getLogger(__name__)
TABLE_PARSER_VERSION = 'table-aware-v2-unicode'


def normalize_pdf_text(text: str) -> str:
    """Join valid UTF-16 pairs; mark isolated surrogate glyphs as U+FFFD."""
    if not re.search(r'[\ud800-\udfff]',text):return text
    return text.encode('utf-16-le','surrogatepass').decode('utf-16-le','replace')


def extract_text(pdf_path: Path) -> str:
    import fitz
    with fitz.open(str(pdf_path)) as doc:
        return normalize_pdf_text('\n'.join(page.get_text() for page in doc))


def extract_markdown_like_text(pdf_path: Path) -> str:
    import fitz
    parts, tables = [], []
    with fitz.open(str(pdf_path)) as doc:
        for i, page in enumerate(doc):
            parts.append(f"## Page {i + 1}\n\n{page.get_text()}")
            try:
                found = page.find_tables().tables
                if not found:
                    found = page.find_tables(strategy='text', min_words_vertical=3).tables
                for index, table in enumerate(found):
                    rows = table.extract()
                    if table.col_count < 2 or table.row_count < 2:
                        continue
                    if sum(any(char.isdigit() for cell in row if cell for char in cell) for row in rows) < 2:
                        continue
                    # Do not fill merged/empty cells with a neighbouring value.
                    markdown = table.to_markdown(fill_empty=False)
                    tables.append(f"### PDF table on page {i + 1}, region {index + 1}\n\n{markdown}")
            except Exception:
                logger.warning('Table recognition failed on page %s; retaining page text', i+1)
    # Put table rows before prose so long papers do not lose results at the input cap.
    return normalize_pdf_text('\n\n'.join(tables + parts))


def cached_table_view(pdf_path: Path, workspace: Path) -> Path:
    """Create a versioned derivative; historical full.md and PDF remain untouched."""
    from src.runtime.settings import atomic_write
    root = workspace.resolve()
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_relative_to(root) or not pdf_path.is_file():
        raise ValueError('PDF must be a file inside the current workspace')
    with pdf_path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    output = root/'papers/parsed/_table_views'/f'{TABLE_PARSER_VERSION}-{digest}.md'
    if not output.resolve().is_relative_to(root):
        raise ValueError('Table cache escapes current workspace')
    if not output.is_file():
        atomic_write(output, extract_markdown_like_text(pdf_path))
    return output
