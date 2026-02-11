"""
PDF parsing service using PyMuPDF (fitz).

Handles:
- PDF file validation and metadata extraction
- Chapter detection via bookmarks, headings, or page-based splitting
- Text content extraction for conversion to markdown
- Cover page extraction (first page as image)
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


class PDFParserService:
    """Service for parsing and extracting content from PDF files."""

    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
    DEFAULT_PAGES_PER_CHAPTER = 30

    # Chapter heading patterns
    CHAPTER_PATTERNS = [
        r'^chapter\s+(\d+|[ivxlcdm]+)',
        r'^part\s+(\d+|[ivxlcdm]+)',
        r'^section\s+(\d+)',
        r'^\d+\.\s+[A-Z]',
    ]

    def __init__(self):
        self.document = None
        self.metadata = {}
        self.chapters = []
        self.extraction_warnings = []

    def validate_pdf_file(self, file) -> None:
        """
        Validate PDF file size and type.

        Args:
            file: Uploaded file object

        Raises:
            ValidationError: If file is invalid
        """
        if file.size > self.MAX_FILE_SIZE:
            raise ValidationError(
                "File size exceeds 50MB limit. Please upload a smaller file."
            )
        if not file.name.lower().endswith('.pdf'):
            raise ValidationError(
                "Invalid file extension. Please upload a .pdf file."
            )

    def parse_pdf(self, pdf_path: str) -> Dict:
        """
        Parse PDF file and extract metadata and content.

        Args:
            pdf_path: Path to PDF file in storage

        Returns:
            Dictionary containing metadata and chapters (same format as EPUBParserService)
        """
        full_path = default_storage.path(pdf_path)

        try:
            self.extraction_warnings = []
            self.document = fitz.open(full_path)

            # Check if PDF has text (not just images)
            if not self._has_extractable_text():
                raise ValidationError(
                    "PDF appears to be image-based (scanned). "
                    "Only text-based PDFs are currently supported."
                )

            # Extract metadata
            self.metadata = self._extract_metadata()

            # Detect and extract chapters
            self.chapters = self._extract_chapters()

            # Extract cover image (first page)
            cover_image_data = self._extract_cover_image()

            return {
                'metadata': self.metadata,
                'chapters': self.chapters,
                'total_chapters': len(self.chapters),
                'cover_image': cover_image_data,
                'warnings': self.extraction_warnings
            }

        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Failed to parse PDF: {str(e)}", exc_info=True)
            raise ValidationError(
                "PDF appears to be corrupted or password-protected. "
                "Please try a different file."
            )
        finally:
            if self.document:
                self.document.close()

    def _has_extractable_text(self) -> bool:
        """Check if PDF has extractable text (not just images)."""
        # Sample first few pages for text
        pages_to_check = min(5, self.document.page_count)
        total_chars = 0

        for page_num in range(pages_to_check):
            page = self.document[page_num]
            text = page.get_text()
            total_chars += len(text.strip())

        # If we have at least 100 chars per page on average, it's text-based
        return total_chars > (pages_to_check * 100)

    def _extract_metadata(self) -> Dict:
        """Extract document metadata from PDF."""
        metadata = {
            'title': 'Untitled',
            'author': 'Unknown Author',
            'isbn': '',
            'publication_date': None,
            'language': 'en',
            'publisher': '',
            'description': ''
        }

        if self.document.metadata:
            pdf_meta = self.document.metadata

            if pdf_meta.get('title'):
                metadata['title'] = pdf_meta['title']

            if pdf_meta.get('author'):
                metadata['author'] = pdf_meta['author']

            if pdf_meta.get('creationDate'):
                # PDF dates are in format: D:YYYYMMDDHHmmSS
                date_str = pdf_meta['creationDate']
                if date_str.startswith('D:'):
                    date_str = date_str[2:10]  # Extract YYYYMMDD
                metadata['publication_date'] = date_str

            if pdf_meta.get('producer'):
                metadata['publisher'] = pdf_meta['producer']

            if pdf_meta.get('subject'):
                metadata['description'] = pdf_meta['subject']

        # If no title in metadata, try to extract from first page
        if metadata['title'] == 'Untitled':
            metadata['title'] = self._extract_title_from_first_page()

        logger.info(f"Extracted PDF metadata: {metadata['title']} by {metadata['author']}")
        return metadata

    def _extract_title_from_first_page(self) -> str:
        """Try to extract title from first page content."""
        if self.document.page_count == 0:
            return 'Untitled'

        page = self.document[0]
        blocks = page.get_text("dict")["blocks"]

        # Look for large text at top of page
        candidates = []
        for block in blocks:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    size = span["size"]
                    # Large text (likely title)
                    if size >= 16 and len(text) > 3 and len(text) < 200:
                        candidates.append((size, text))

        if candidates:
            # Return largest text
            candidates.sort(reverse=True)
            return candidates[0][1]

        return 'Untitled'

    def _extract_chapters(self) -> List[Dict]:
        """Extract chapters using best available method."""
        # Try bookmark-based splitting first (most reliable)
        toc = self.document.get_toc()
        if toc:
            logger.info(f"Found {len(toc)} TOC entries, using bookmark-based splitting")
            return self._split_by_bookmarks(toc)

        # Try heading detection (only for smaller PDFs - scanning is slow)
        if self.document.page_count <= 100:
            chapters = self._detect_chapter_headings()
            if chapters:
                logger.info(f"Detected {len(chapters)} chapters via heading patterns")
                return chapters

        # Fall back to page-based splitting
        self.extraction_warnings.append(
            "No bookmarks or chapter headings detected. Using page-based splitting."
        )
        logger.info("No structure detected, using page-based splitting")
        return self._split_by_pages()

    def _split_by_bookmarks(self, toc: List) -> List[Dict]:
        """
        Split PDF by bookmark/outline entries.

        Args:
            toc: List of [level, title, page_number] from PyMuPDF

        Returns:
            List of chapter dicts
        """
        chapters = []

        # Filter to top-level entries (level 1)
        top_level = [(title, page) for level, title, page in toc if level == 1]

        if not top_level:
            # If no level-1 entries, use all entries
            top_level = [(title, page) for level, title, page in toc]

        for i, (title, start_page) in enumerate(top_level):
            # Determine end page
            if i + 1 < len(top_level):
                end_page = top_level[i + 1][1] - 1
            else:
                end_page = self.document.page_count

            # Extract text from page range (pages are 1-indexed in TOC, 0-indexed in API)
            text_content = self._extract_text_range(start_page - 1, end_page - 1)

            if text_content.strip():
                chapters.append({
                    'id': f'chapter_{i + 1}',
                    'title': title,
                    'html_content': self._text_to_html(text_content),
                    'order_index': i,
                    'file_name': f'pages_{start_page}-{end_page}'
                })

        return chapters

    def _detect_chapter_headings(self) -> List[Dict]:
        """
        Detect chapters by analyzing text patterns and formatting.

        Returns:
            List of chapter dicts, or empty list if no patterns found
        """
        chapter_breaks = []

        for page_num in range(self.document.page_count):
            page = self.document[page_num]
            blocks = page.get_text("dict")["blocks"]

            for block in blocks:
                if "lines" not in block:
                    continue

                for line in block["lines"]:
                    text = "".join([span["text"] for span in line["spans"]]).strip()
                    if not text:
                        continue

                    font_size = max([span["size"] for span in line["spans"]], default=12)

                    # Check for chapter patterns
                    for pattern in self.CHAPTER_PATTERNS:
                        if re.match(pattern, text, re.IGNORECASE):
                            chapter_breaks.append({
                                'page': page_num,
                                'title': text,
                                'font_size': font_size
                            })
                            break

        if len(chapter_breaks) < 2:
            return []

        return self._build_chapters_from_breaks(chapter_breaks)

    def _build_chapters_from_breaks(self, breaks: List[Dict]) -> List[Dict]:
        """
        Build chapter list from detected break points.

        Args:
            breaks: List of {'page': int, 'title': str, 'font_size': float}

        Returns:
            List of chapter dicts
        """
        chapters = []

        for i, break_info in enumerate(breaks):
            start_page = break_info['page']

            # Determine end page
            if i + 1 < len(breaks):
                end_page = breaks[i + 1]['page'] - 1
            else:
                end_page = self.document.page_count - 1

            if end_page < start_page:
                end_page = start_page

            # Extract text
            text_content = self._extract_text_range(start_page, end_page)

            if text_content.strip():
                chapters.append({
                    'id': f'chapter_{i + 1}',
                    'title': break_info['title'],
                    'html_content': self._text_to_html(text_content),
                    'order_index': i,
                    'file_name': f'pages_{start_page + 1}-{end_page + 1}'
                })

        return chapters

    def _split_by_pages(self) -> List[Dict]:
        """
        Split PDF into chapters by page count.

        Returns:
            List of chapter dicts
        """
        chapters = []
        total_pages = self.document.page_count
        pages_per_chapter = self.DEFAULT_PAGES_PER_CHAPTER

        for start in range(0, total_pages, pages_per_chapter):
            end = min(start + pages_per_chapter - 1, total_pages - 1)

            text_content = self._extract_text_range(start, end)

            if text_content.strip():
                chapters.append({
                    'id': f'pages_{start + 1}_{end + 1}',
                    'title': f'Pages {start + 1}-{end + 1}',
                    'html_content': self._text_to_html(text_content),
                    'order_index': len(chapters),
                    'file_name': f'pages_{start + 1}-{end + 1}'
                })

        return chapters

    def _extract_text_range(self, start_page: int, end_page: int) -> str:
        """
        Extract text from a range of pages.

        Args:
            start_page: Starting page index (0-based)
            end_page: Ending page index (0-based, inclusive)

        Returns:
            Combined text from all pages
        """
        text_parts = []

        for page_num in range(start_page, min(end_page + 1, self.document.page_count)):
            page = self.document[page_num]
            text = page.get_text()
            text_parts.append(text)

        return '\n\n'.join(text_parts)

    def _text_to_html(self, text: str) -> str:
        """
        Convert plain text to simple HTML for markdown converter.

        Args:
            text: Plain text content

        Returns:
            HTML string with paragraphs wrapped
        """
        # Split into paragraphs (double newline)
        paragraphs = re.split(r'\n\s*\n', text)

        html_parts = []
        for p in paragraphs:
            p = p.strip()
            if p:
                # Escape HTML entities
                p = p.replace('&', '&amp;')
                p = p.replace('<', '&lt;')
                p = p.replace('>', '&gt;')
                # Preserve single line breaks within paragraphs
                p = p.replace('\n', '<br/>')
                html_parts.append(f'<p>{p}</p>')

        return '\n'.join(html_parts)

    def _extract_cover_image(self) -> Optional[bytes]:
        """
        Extract first page as cover image.

        Returns:
            JPEG image bytes or None
        """
        try:
            if self.document.page_count > 0:
                page = self.document[0]
                # Render at 2x zoom for better quality
                mat = fitz.Matrix(2, 2)
                pix = page.get_pixmap(matrix=mat)
                return pix.tobytes("jpeg")
        except Exception as e:
            logger.warning(f"Could not extract cover: {e}")

        return None
