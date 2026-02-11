"""
HTML to Markdown conversion service.

Handles:
- Converting EPUB HTML content to clean Markdown
- Removing HTML artifacts and inline styles
- Normalizing heading levels
- Preserving important formatting
"""

import re
import logging
from typing import Optional
from bs4 import BeautifulSoup
import html2text

logger = logging.getLogger(__name__)


class MarkdownConverter:
    """Service for converting HTML content to clean Markdown."""

    def __init__(self):
        """Initialize the converter with optimal settings."""
        self.h2t = html2text.HTML2Text()

        # Configure html2text settings
        self.h2t.ignore_links = False
        self.h2t.ignore_images = False
        self.h2t.ignore_emphasis = False
        self.h2t.ignore_tables = False
        self.h2t.body_width = 0  # Don't wrap lines
        self.h2t.unicode_snob = True  # Use unicode
        self.h2t.protect_links = False  # Fixed: Don't wrap URLs in angle brackets to prevent removal by HTML tag regex
        self.h2t.wrap_links = False
        self.h2t.wrap_list_items = True
        self.h2t.ul_item_mark = '-'  # Use dash for unordered lists
        self.h2t.emphasis_mark = '*'  # Use asterisk for emphasis
        self.h2t.strong_mark = '**'  # Use double asterisk for strong

        # Skip converting these tags
        self.h2t.skip_internal_links = False
        self.h2t.ignore_anchors = True

    def convert_html_to_markdown(self, html_content: str, normalize_headings: bool = True) -> str:
        """
        Convert HTML content to clean Markdown.

        Args:
            html_content: HTML string to convert
            normalize_headings: Whether to normalize heading levels

        Returns:
            Clean Markdown string
        """
        if not html_content:
            return ""

        try:
            # Pre-process HTML
            cleaned_html = self._preprocess_html(html_content)

            # Convert to Markdown
            markdown = self.h2t.handle(cleaned_html)

            # Post-process Markdown
            markdown = self._postprocess_markdown(markdown)

            # Normalize headings if requested
            if normalize_headings:
                markdown = self._normalize_heading_levels(markdown)

            return markdown

        except Exception as e:
            logger.error(f"Failed to convert HTML to Markdown: {str(e)}")
            # Return basic text extraction as fallback
            return self._extract_plain_text(html_content)

    def _preprocess_html(self, html_content: str) -> str:
        """
        Clean and prepare HTML before conversion.

        Args:
            html_content: Raw HTML string

        Returns:
            Cleaned HTML string
        """
        # Parse HTML with BeautifulSoup (lxml is faster than html.parser)
        soup = BeautifulSoup(html_content, 'lxml')

        # Remove script and style tags completely
        for tag in soup(['script', 'style', 'meta', 'link']):
            tag.decompose()

        # Remove all image tags
        for tag in soup.find_all('img'):
            tag.decompose()

        # Remove empty divs and spans
        for tag in soup.find_all(['div', 'span']):
            if not tag.text.strip() and not tag.find_all(['img', 'a']):
                tag.decompose()

        # Remove all inline styles
        for tag in soup.find_all(style=True):
            del tag['style']

        # Remove all class attributes (keep id for anchors)
        for tag in soup.find_all(class_=True):
            del tag['class']

        # Clean up nested formatting tags
        self._clean_nested_formatting(soup)

        # Remove excessive whitespace between tags
        html_str = str(soup)
        html_str = re.sub(r'>\s+<', '><', html_str)

        return html_str

    def _clean_nested_formatting(self, soup):
        """
        Clean up excessively nested formatting tags.

        Args:
            soup: BeautifulSoup object
        """
        # Unwrap unnecessary nested tags
        for tag_name in ['b', 'i', 'em', 'strong']:
            for tag in soup.find_all(tag_name):
                # If parent is the same tag type, unwrap
                if tag.parent and tag.parent.name == tag_name:
                    tag.unwrap()

        # Remove empty formatting tags
        for tag_name in ['b', 'i', 'em', 'strong', 'u', 'strike']:
            for tag in soup.find_all(tag_name):
                if not tag.text.strip():
                    tag.decompose()

    def _postprocess_markdown(self, markdown: str) -> str:
        """
        Clean up Markdown after conversion.

        Args:
            markdown: Raw converted Markdown

        Returns:
            Cleaned Markdown string
        """
        # Remove excessive blank lines (more than 2)
        markdown = re.sub(r'\n{3,}', '\n\n', markdown)

        # Remove trailing whitespace on lines
        markdown = '\n'.join(line.rstrip() for line in markdown.split('\n'))

        # Fix spacing around headings
        markdown = re.sub(r'(^|\n)(#{1,6})\s*([^\n]+)', r'\1\n\2 \3\n', markdown)

        # Remove HTML comments
        markdown = re.sub(r'<!--.*?-->', '', markdown, flags=re.DOTALL)

        # Remove markdown images
        markdown = re.sub(r'!\[([^\]]*)\]\([^)]+\)', '', markdown)

        # Clean up link formatting
        markdown = self._clean_link_formatting(markdown)

        # Remove escaped characters that don't need escaping
        markdown = self._clean_escaped_characters(markdown)

        # Fix list formatting
        markdown = self._fix_list_formatting(markdown)

        # Remove any remaining HTML tags
        markdown = re.sub(r'<[^>]+>', '', markdown)

        # Final cleanup of multiple blank lines
        markdown = re.sub(r'\n{3,}', '\n\n', markdown)

        # Ensure document ends with single newline
        markdown = markdown.rstrip() + '\n'

        return markdown

    def _normalize_heading_levels(self, markdown: str) -> str:
        """
        Normalize heading levels to ensure proper hierarchy.

        Rules:
        - Chapter titles should be H1
        - Main sections should be H2
        - Subsections should be H3, etc.

        Args:
            markdown: Markdown content

        Returns:
            Markdown with normalized headings
        """
        lines = markdown.split('\n')
        normalized = []

        # Track heading levels
        first_heading_found = False
        min_heading_level = 6

        # First pass: find minimum heading level
        for line in lines:
            heading_match = re.match(r'^(#{1,6})\s+', line)
            if heading_match:
                level = len(heading_match.group(1))
                min_heading_level = min(min_heading_level, level)

        # Second pass: normalize headings
        for line in lines:
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if heading_match:
                current_level = len(heading_match.group(1))
                heading_text = heading_match.group(2)

                # Adjust level (ensure first heading is H1)
                if not first_heading_found:
                    new_level = 1
                    first_heading_found = True
                else:
                    # Maintain relative hierarchy
                    level_diff = current_level - min_heading_level
                    new_level = min(1 + level_diff, 6)

                normalized.append(f"{'#' * new_level} {heading_text}")
            else:
                normalized.append(line)

        return '\n'.join(normalized)

    def _clean_link_formatting(self, markdown: str) -> str:
        """
        Clean up link formatting in Markdown.

        Args:
            markdown: Markdown content

        Returns:
            Markdown with cleaned links
        """
        # Fix broken link formatting (space between text and URL)
        markdown = re.sub(r'\[([^\]]+)\]\s+\(([^)]+)\)', r'[\1](\2)', markdown)

        # Remove empty links (links with no URL)
        markdown = re.sub(r'\[([^\]]+)\]\(\s*\)', r'\1', markdown)

        # Remove links with empty text
        markdown = re.sub(r'\[\s*\]\([^)]+\)', '', markdown)

        # Clean up reference-style links
        markdown = re.sub(r'\[([^\]]+)\]\[\s*\]', r'[\1]', markdown)

        return markdown

    def _clean_escaped_characters(self, markdown: str) -> str:
        """
        Remove unnecessary escaped characters.

        Args:
            markdown: Markdown content

        Returns:
            Markdown with cleaned escaping
        """
        # Characters that don't need escaping in most contexts
        unnecessary_escapes = [
            (r'\\\.', '.'),  # Period
            (r'\\,', ','),   # Comma
            (r'\\;', ';'),   # Semicolon
            (r'\\:', ':'),   # Colon
            (r'\\"', '"'),   # Quote
            (r"\\'", "'"),   # Apostrophe
        ]

        for pattern, replacement in unnecessary_escapes:
            markdown = re.sub(pattern, replacement, markdown)

        return markdown

    def _fix_list_formatting(self, markdown: str) -> str:
        """
        Fix list formatting issues.

        Args:
            markdown: Markdown content

        Returns:
            Markdown with fixed lists
        """
        lines = markdown.split('\n')
        fixed = []
        in_list = False
        list_indent = 0

        for i, line in enumerate(lines):
            # Check if line is a list item
            list_match = re.match(r'^(\s*)([*\-+]|\d+\.)\s+(.+)$', line)

            if list_match:
                indent = len(list_match.group(1))
                marker = list_match.group(2)
                content = list_match.group(3)

                # Normalize indent (use 2 spaces per level)
                normalized_indent = (indent // 2) * 2

                # Reconstruct list item
                fixed_line = f"{' ' * normalized_indent}{marker} {content}"
                fixed.append(fixed_line)
                in_list = True
                list_indent = normalized_indent

            elif in_list and line.strip() == '':
                # Blank line in list - preserve it
                fixed.append('')

            elif in_list and line.strip() and not line[0].isspace():
                # Non-indented text after list - end list
                fixed.append('')  # Add blank line after list
                fixed.append(line)
                in_list = False

            else:
                fixed.append(line)
                if not line.strip():
                    in_list = False

        return '\n'.join(fixed)

    def _extract_plain_text(self, html_content: str) -> str:
        """
        Fallback method to extract plain text from HTML.

        Args:
            html_content: HTML string

        Returns:
            Plain text string
        """
        soup = BeautifulSoup(html_content, 'lxml')

        # Get text and preserve some structure
        text_parts = []

        for element in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li']):
            text = element.get_text().strip()
            if text:
                if element.name.startswith('h'):
                    # Add heading markers
                    level = int(element.name[1])
                    text = f"{'#' * level} {text}"
                elif element.name == 'li':
                    text = f"- {text}"

                text_parts.append(text)

        return '\n\n'.join(text_parts)

    def convert_chapter_to_markdown(self, chapter_html: str, chapter_title: str = None) -> str:
        """
        Convert a single chapter's HTML to Markdown.

        Args:
            chapter_html: HTML content of the chapter
            chapter_title: Optional chapter title to use as H1

        Returns:
            Markdown string for the chapter
        """
        # Convert HTML to Markdown
        markdown = self.convert_html_to_markdown(chapter_html)

        # Add chapter title as H1 if provided and not already present
        if chapter_title:
            if not markdown.startswith('# '):
                markdown = f"# {chapter_title}\n\n{markdown}"

        return markdown

    def estimate_word_count(self, markdown: str) -> int:
        """
        Estimate word count from Markdown content.

        Args:
            markdown: Markdown string

        Returns:
            Estimated word count
        """
        # Remove Markdown syntax for more accurate count
        text = re.sub(r'[#*_\[\]()>`~-]', '', markdown)
        text = re.sub(r'\s+', ' ', text)

        # Count words
        words = text.split()
        return len(words)
