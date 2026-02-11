"""
Content splitting service for creating flat chapter structure.

Handles:
- Converting parsed EPUB chapters to Chapter model instances
- Smart front/back matter detection and filtering
- Word count calculation
- Chapter title extraction
"""

import re
import logging
from typing import List, Dict

from books_core.models import Book, Chapter

logger = logging.getLogger(__name__)


class ContentSplitter:
    """Service for creating flat chapter structure from parsed EPUB data."""

    # Front matter patterns to detect (word boundary matching)
    FRONT_MATTER_PATTERNS = [
        r'\btable of contents\b',
        r'\btoc\b',
        r'\bcontents\b',
        r'\bcopyright\b',
        r'\bdedication\b',
        r'\bforeword\b',
        r'\bforward\b',  # Common misspelling
        r'\bpreface\b',
        r'\backnowledgments?\b',
        r'\backnowledgements?\b',
        r'\bintroduction\b',
    ]

    # Back matter patterns to detect (word boundary matching)
    BACK_MATTER_PATTERNS = [
        r'\babout the author\b',
        r'\babout author\b',
        r'\balso by\b',
        r'\badvertisements?\b',
        r'\bepilogue\b',
        r'\bafterword\b',
        r'\bappendix\b',
        r'\bappendices\b',
        r'\bglossary\b',
        r'\bindex\b',
        r'\bendnotes?\b',
        r'\bnotes\b',
        r'\bbibliography\b',
        r'\breferences\b',
    ]

    # Minimum word count threshold (chapters below this are skipped)
    MIN_WORD_COUNT = 50

    def __init__(self):
        """Initialize the content splitter."""
        pass

    def split_chapters(self, book: Book, chapters_data: List[Dict]) -> List[Chapter]:
        """
        Split chapters and create flat Chapter objects.

        Args:
            book: Book model instance
            chapters_data: List of chapter dictionaries from EPUBParserService

        Returns:
            List of created Chapter model instances
        """
        from books_core.services.markdown_converter import MarkdownConverter

        converter = MarkdownConverter()
        created_chapters = []
        chapter_number = 1

        total_chapters = len(chapters_data)
        logger.info(f"Processing {total_chapters} chapters for book {book.id}")

        for index, chapter_data in enumerate(chapters_data):
            # Extract data
            html_content = chapter_data.get('html_content', '')
            title = chapter_data.get('title', '')

            # Convert HTML to Markdown
            markdown_content = converter.convert_html_to_markdown(html_content)

            # Calculate word count
            word_count = self._count_words(markdown_content)

            # Skip chapters with very low word count
            if word_count < self.MIN_WORD_COUNT:
                logger.debug(
                    f"Skipping chapter '{title}' with only {word_count} words (< {self.MIN_WORD_COUNT})"
                )
                continue

            # Detect front/back matter
            is_front_matter = self._is_front_matter(title, index, total_chapters)
            is_back_matter = self._is_back_matter(title, index, total_chapters)

            # Create Chapter instance
            chapter = Chapter.objects.create(
                book=book,
                chapter_number=chapter_number,
                title=title if title else None,
                content=markdown_content,
                word_count=word_count,
                is_front_matter=is_front_matter,
                is_back_matter=is_back_matter
            )

            created_chapters.append(chapter)
            logger.debug(
                f"Created chapter {chapter_number}: '{title}' "
                f"({word_count} words, front={is_front_matter}, back={is_back_matter})"
            )

            chapter_number += 1

        logger.info(
            f"Created {len(created_chapters)} chapters for book {book.id} "
            f"(skipped {total_chapters - len(created_chapters)} chapters with < {self.MIN_WORD_COUNT} words)"
        )

        return created_chapters

    def _is_front_matter(self, title: str, index: int, total_chapters: int) -> bool:
        """
        Detect if chapter is front matter.

        Args:
            title: Chapter title
            index: Chapter index in spine (0-based)
            total_chapters: Total number of chapters

        Returns:
            True if chapter is front matter
        """
        # Check title against patterns using regex word boundaries
        title_lower = title.lower()
        for pattern in self.FRONT_MATTER_PATTERNS:
            if re.search(pattern, title_lower, re.IGNORECASE):
                logger.debug(f"Front matter detected: '{title}' matches pattern '{pattern}'")
                return True

        return False

    def _is_back_matter(self, title: str, index: int, total_chapters: int) -> bool:
        """
        Detect if chapter is back matter.

        Args:
            title: Chapter title
            index: Chapter index in spine (0-based)
            total_chapters: Total number of chapters

        Returns:
            True if chapter is back matter
        """
        # Check title against patterns using regex word boundaries
        title_lower = title.lower()
        for pattern in self.BACK_MATTER_PATTERNS:
            if re.search(pattern, title_lower, re.IGNORECASE):
                logger.debug(f"Back matter detected: '{title}' matches pattern '{pattern}'")
                return True

        return False

    def _count_words(self, text: str) -> int:
        """
        Count words in text.

        Args:
            text: Text string

        Returns:
            Word count
        """
        # Remove markdown syntax for accurate count
        clean_text = re.sub(r'[#*_\[\]()>`~-]', '', text)
        clean_text = re.sub(r'\s+', ' ', clean_text)
        words = clean_text.split()
        return len(words)
