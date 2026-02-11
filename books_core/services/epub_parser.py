"""
EPUB parsing service using ebooklib.

Handles:
- EPUB file validation and metadata extraction
- Chapter parsing from spine items
- HTML content extraction for conversion
- Cover image extraction
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional
import uuid

import ebooklib
from ebooklib import epub
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)


class EPUBParserService:
    """Service for parsing and extracting content from EPUB files."""

    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
    ALLOWED_MIME_TYPES = ['application/epub+zip']

    def __init__(self):
        self.book = None
        self.metadata = {}
        self.chapters = []
        self.spine_items = []
        self.toc_mapping = {}  # filename -> title from TOC
        self.extraction_warnings = []  # Track issues during parsing

    def validate_epub_file(self, file) -> None:
        """
        Validate EPUB file size and type.

        Args:
            file: Uploaded file object

        Raises:
            ValidationError: If file is invalid
        """
        # Check file size
        if file.size > self.MAX_FILE_SIZE:
            raise ValidationError(
                f"File size exceeds 50MB limit. Please upload a smaller file."
            )

        # Check file extension
        if not file.name.lower().endswith('.epub'):
            raise ValidationError(
                "Invalid file extension. Please upload an .epub file."
            )

        # Check MIME type
        if hasattr(file, 'content_type'):
            if file.content_type not in self.ALLOWED_MIME_TYPES:
                raise ValidationError(
                    f"Invalid file type. Expected EPUB file (application/epub+zip)."
                )

    def save_epub_file(self, file, book_id: int) -> str:
        """
        Save EPUB file to media storage.

        Args:
            file: Uploaded file object
            book_id: Book database ID

        Returns:
            Relative path to saved file
        """
        # Generate unique filename
        file_uuid = uuid.uuid4().hex[:8]
        filename = f"{file_uuid}_{file.name}"

        # Create path: books/[book_id]/[filename]
        file_path = f"books/{book_id}/{filename}"

        # Save using Django's storage system
        saved_path = default_storage.save(file_path, ContentFile(file.read()))

        logger.info(f"Saved EPUB file to: {saved_path}")
        return saved_path

    def parse_epub(self, epub_path: str) -> Dict:
        """
        Parse EPUB file and extract metadata and content.

        Args:
            epub_path: Path to EPUB file in storage

        Returns:
            Dictionary containing metadata and chapters
        """
        # Get full file path
        full_path = default_storage.path(epub_path)

        try:
            # Reset warnings for this parse
            self.extraction_warnings = []

            # Read EPUB book
            self.book = epub.read_epub(full_path)

            # Extract metadata
            self.metadata = self._extract_metadata()

            # Extract spine items (ordered chapter list)
            self.spine_items = self._extract_spine_items()

            # Extract chapters with content
            self.chapters = self._extract_chapters()

            # Extract cover image if exists (returns bytes, not path)
            cover_image_data = self._extract_cover_image()

            return {
                'metadata': self.metadata,
                'chapters': self.chapters,
                'total_chapters': len(self.chapters),
                'cover_image': cover_image_data,  # Include cover bytes in return
                'warnings': self.extraction_warnings  # Include any parsing issues
            }

        except Exception as e:
            logger.error(f"Failed to parse EPUB: {str(e)}", exc_info=True)
            raise ValidationError(
                f"File appears to be corrupted or invalid. Please try re-downloading the EPUB."
            )

    def _extract_metadata(self) -> Dict:
        """Extract book metadata from EPUB."""
        metadata = {
            'title': 'Untitled',
            'author': 'Unknown Author',
            'isbn': '',
            'publication_date': None,
            'language': 'en',
            'publisher': '',
            'description': ''
        }

        # Extract title
        title = self.book.get_metadata('DC', 'title')
        if title:
            metadata['title'] = title[0][0]

        # Extract author(s)
        creators = self.book.get_metadata('DC', 'creator')
        if creators:
            authors = [creator[0] for creator in creators]
            metadata['author'] = ', '.join(authors)

        # Extract ISBN
        identifiers = self.book.get_metadata('DC', 'identifier')
        for identifier in identifiers:
            if 'isbn' in str(identifier[1]).lower():
                metadata['isbn'] = identifier[0]
                break

        # Extract publication date
        dates = self.book.get_metadata('DC', 'date')
        if dates:
            # Parse date string (format varies, handle gracefully)
            date_str = dates[0][0]
            metadata['publication_date'] = date_str  # Will be parsed in the model

        # Extract language
        languages = self.book.get_metadata('DC', 'language')
        if languages:
            metadata['language'] = languages[0][0]

        # Extract publisher
        publishers = self.book.get_metadata('DC', 'publisher')
        if publishers:
            metadata['publisher'] = publishers[0][0]

        # Extract description
        descriptions = self.book.get_metadata('DC', 'description')
        if descriptions:
            metadata['description'] = descriptions[0][0]

        logger.info(f"Extracted metadata: {metadata['title']} by {metadata['author']}")
        return metadata

    def _extract_spine_items(self) -> List:
        """Extract ordered spine items (reading order)."""
        spine_items = []

        logger.debug(f"Processing spine with {len(self.book.spine)} items")

        for spine_item in self.book.spine:
            item_id, linear = spine_item
            item = self.book.get_item_with_id(item_id)

            if not item:
                logger.warning(f"Spine item {item_id} not found")
                continue

            # Skip non-linear items (navigation, footnotes, ancillary content)
            if linear == 'no' or linear is False:
                logger.debug(f"Skipping non-linear item: {item_id}")
                continue

            # Check if item is a document/HTML item
            item_type = item.get_type() if hasattr(item, 'get_type') else None
            is_html = isinstance(item, epub.EpubHtml)

            logger.debug(f"Item {item_id}: type={item_type}, is_EpubHtml={is_html}")

            # Skip non-HTML items
            if not (is_html or (item_type and item_type == ebooklib.ITEM_DOCUMENT)):
                # Try content-based check as fallback
                try:
                    content = item.get_content() if hasattr(item, 'get_content') else None
                    if not (content and b'<' in content):
                        logger.debug(f"Skipping non-document item: {item_id}")
                        continue
                except Exception as e:
                    logger.warning(f"Could not check content for item {item_id}: {str(e)}")
                    continue

            # Add to spine items (we'll filter front/back matter in ContentSplitter)
            spine_items.append({
                'id': item_id,
                'item': item,
                'linear': linear
            })

        logger.info(f"Found {len(spine_items)} spine items")
        return spine_items

    def _build_toc_mapping(self) -> Dict[str, str]:
        """
        Build a mapping from filename to chapter title using the EPUB's TOC.
        Also builds anchor-based mapping for files with multiple TOC entries.

        Returns:
            Dict mapping file paths to titles (without anchors, first entry wins)
        """
        mapping = {}
        # Also track full href -> title for anchor-based splitting
        self.toc_anchor_mapping = {}  # (filename, anchor) -> title
        self.toc_entries_by_file = {}  # filename -> [(anchor, title), ...]

        def process_toc_item(item, depth=0):
            """Recursively process TOC items (can be Link or tuple of Section + children)."""
            if hasattr(item, 'href') and hasattr(item, 'title'):
                # It's a Link object
                href = item.href
                anchor = None
                filename = href

                if '#' in href:
                    filename, anchor = href.split('#', 1)

                if item.title:
                    # Store anchor-based mapping
                    self.toc_anchor_mapping[(filename, anchor)] = item.title

                    # Track entries by file for anchor-based splitting
                    if filename not in self.toc_entries_by_file:
                        self.toc_entries_by_file[filename] = []
                    self.toc_entries_by_file[filename].append((anchor, item.title, depth))

                    # First entry for a file (without anchor) goes into simple mapping
                    if filename not in mapping:
                        mapping[filename] = item.title

            elif isinstance(item, tuple) and len(item) >= 2:
                # It's a Section: (Section/title, [children])
                section = item[0]
                # Process the section itself if it has href
                if hasattr(section, 'href') and hasattr(section, 'title'):
                    process_toc_item(section, depth)
                # Process children
                children = item[1] if len(item) > 1 else []
                for child in children:
                    process_toc_item(child, depth + 1)
            elif isinstance(item, list):
                # List of items
                for child in item:
                    process_toc_item(child, depth)

        try:
            if self.book and hasattr(self.book, 'toc'):
                for item in self.book.toc:
                    process_toc_item(item)
                logger.info(f"Built TOC mapping with {len(mapping)} file entries, "
                           f"{len(self.toc_anchor_mapping)} anchor entries")
        except Exception as e:
            logger.warning(f"Could not build TOC mapping: {str(e)}")

        return mapping

    def _extract_chapters(self) -> List[Dict]:
        """Extract chapters with HTML content, using TOC anchors when available."""
        # Build TOC mapping first for title lookup
        self.toc_mapping = self._build_toc_mapping()

        chapters = []
        order_index = 0

        for spine_info in self.spine_items:
            item = spine_info['item']

            try:
                file_name = item.get_name()
                html_content = item.get_content().decode('utf-8', errors='replace')

                # Check if this file has multiple top-level TOC entries with anchors
                toc_entries = self.toc_entries_by_file.get(file_name, [])

                # Filter to top-level entries (depth 0) that have anchors
                top_level_anchored = [(a, t) for a, t, d in toc_entries if a is not None and d == 0]

                # Only split by anchors if:
                # 1. There are 2-10 anchored entries (too many suggests granular TOC, not chapters)
                # 2. The file is large enough to warrant splitting (rough estimate)
                file_word_count = len(html_content.split()) // 2  # Rough word estimate
                should_split = (
                    2 <= len(top_level_anchored) <= 10 and
                    file_word_count >= 1000  # At least 1000 words to consider splitting
                )

                if should_split:
                    # This file contains multiple chapters - split by anchors
                    anchor_chapters = self._split_by_anchors(
                        html_content, top_level_anchored, file_name, spine_info['id']
                    )
                    for ch in anchor_chapters:
                        ch['order_index'] = order_index
                        chapters.append(ch)
                        order_index += 1
                    logger.info(f"Split {file_name} into {len(anchor_chapters)} chapters by anchors")
                else:
                    if len(top_level_anchored) > 10:
                        logger.info(f"Skipping anchor split for {file_name}: {len(top_level_anchored)} TOC entries (too granular)")
                    # Single chapter file - use standard extraction
                    title = self._extract_chapter_title(item, html_content)
                    chapters.append({
                        'id': spine_info['id'],
                        'title': title,
                        'html_content': html_content,
                        'order_index': order_index,
                        'file_name': file_name
                    })
                    order_index += 1

            except Exception as e:
                warning = f"Failed to extract chapter '{spine_info['id']}': {str(e)}"
                logger.warning(warning)
                self.extraction_warnings.append(warning)
                continue

        logger.info(f"Extracted {len(chapters)} chapters")
        return chapters

    def _split_by_anchors(self, html_content: str, anchors: List[tuple],
                          file_name: str, item_id: str) -> List[Dict]:
        """
        Split HTML content into chapters based on anchor IDs from TOC.

        Args:
            html_content: Full HTML content of the file
            anchors: List of (anchor_id, title) tuples from TOC
            file_name: Original file name
            item_id: Spine item ID

        Returns:
            List of chapter dicts
        """
        from bs4 import BeautifulSoup
        import re

        chapters = []

        # Parse HTML
        soup = BeautifulSoup(html_content, 'lxml')

        # Find all anchor elements and their positions
        anchor_elements = []
        for anchor_id, title in anchors:
            # Look for element with this ID
            element = soup.find(id=anchor_id)
            if element:
                anchor_elements.append((element, anchor_id, title))
            else:
                logger.debug(f"Anchor {anchor_id} not found in {file_name}")

        if not anchor_elements:
            # No anchors found - return whole content as single chapter
            return [{
                'id': item_id,
                'title': anchors[0][1] if anchors else 'Chapter',
                'html_content': html_content,
                'file_name': file_name
            }]

        # Extract content between anchor points
        body = soup.find('body')
        if not body:
            body = soup

        # Get all elements in document order
        all_elements = list(body.descendants)

        for i, (element, anchor_id, title) in enumerate(anchor_elements):
            # Find the next anchor element (or end of document)
            next_element = anchor_elements[i + 1][0] if i + 1 < len(anchor_elements) else None

            # Extract content from this anchor to the next
            chapter_html = self._extract_content_between(soup, element, next_element)

            if chapter_html.strip():
                chapters.append({
                    'id': f"{item_id}_{anchor_id}",
                    'title': title,
                    'html_content': chapter_html,
                    'file_name': f"{file_name}#{anchor_id}"
                })

        return chapters if chapters else [{
            'id': item_id,
            'title': anchors[0][1] if anchors else 'Chapter',
            'html_content': html_content,
            'file_name': file_name
        }]

    def _extract_content_between(self, soup, start_element, end_element) -> str:
        """
        Extract HTML content from start_element up to (but not including) end_element.

        Args:
            soup: BeautifulSoup object
            start_element: Starting element (included)
            end_element: Ending element (excluded), or None for end of document

        Returns:
            HTML string of the content
        """
        from bs4 import NavigableString, Tag

        collected = []
        collecting = False
        start_found = False

        def is_tag(el):
            """Check if element is a Tag (not NavigableString)."""
            return isinstance(el, Tag)

        def should_stop(el):
            """Check if we've reached the end element."""
            if end_element is None:
                return False
            # Check if el is or contains the end element
            if el is end_element:
                return True
            # Only check find on Tag elements
            if is_tag(el):
                end_id = end_element.get('id') if hasattr(end_element, 'get') else None
                if end_id and el.find(id=end_id):
                    return True
            return False

        # Walk through body's children
        body = soup.find('body') or soup

        for element in body.children:
            if not start_found:
                # Check if this element contains the start anchor
                if element is start_element:
                    start_found = True
                    collecting = True
                    collected.append(str(element))
                elif is_tag(element):
                    # Only search in Tag elements
                    start_id = start_element.get('id') if hasattr(start_element, 'get') else None
                    found = element.find(id=start_id) if start_id else None
                    if found:
                        start_found = True
                        collecting = True
                        collected.append(str(element))
            elif collecting:
                if should_stop(element):
                    break
                collected.append(str(element))

        return ''.join(collected)

    def _parse_split_filename(self, file_name: str) -> tuple:
        """
        Parse a filename to detect split file pattern.

        Args:
            file_name: File path like 'text/part0005_split_001.html'

        Returns:
            Tuple of (base_name, split_index) or (None, None) if not a split file
        """
        import re
        # Match patterns like: part0005_split_001.html or chapter1_split_002.xhtml
        match = re.search(r'(.+)_split_(\d+)\.(x?html?)$', file_name, re.IGNORECASE)
        if match:
            base_name = match.group(1)
            split_index = int(match.group(2))
            return base_name, split_index
        return None, None

    def _extract_chapter_title(self, item, html_content: str) -> str:
        """
        Extract chapter title from item or HTML content.

        Args:
            item: EPUB item
            html_content: HTML content string

        Returns:
            Chapter title
        """
        # Try TOC mapping first (most reliable - uses book's table of contents)
        file_name = item.get_name() if hasattr(item, 'get_name') else None
        if file_name and file_name in self.toc_mapping:
            return self.toc_mapping[file_name]

        # Try to get title from item metadata
        if hasattr(item, 'title') and item.title:
            return item.title

        # Try to extract from HTML
        from bs4 import BeautifulSoup
        import re
        soup = BeautifulSoup(html_content, 'lxml')

        def clean_title(text: str) -> str:
            """Remove image markdown and clean up title."""
            text = re.sub(r'!\[Image\]\([^)]+\)', '', text)
            text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
            # Remove excessive whitespace
            text = ' '.join(text.split())
            return text.strip()

        def is_valid_title(tag) -> bool:
            """Check if heading tag is likely a real title (not nav/header/footer)."""
            if not tag or not tag.text.strip():
                return False
            # Skip if inside navigation, header, or footer
            for parent in tag.parents:
                if parent.name in ('nav', 'header', 'footer', 'aside'):
                    return False
                # Skip if parent has nav-like class/id
                parent_class = ' '.join(parent.get('class', []))
                parent_id = parent.get('id', '')
                if any(nav_word in (parent_class + parent_id).lower()
                       for nav_word in ['nav', 'menu', 'sidebar', 'toc', 'header', 'footer']):
                    return False
            # Skip very short titles that are likely page numbers or decorations
            text = tag.text.strip()
            if len(text) < 2 or text.isdigit():
                return False
            return True

        # Look for h1 first (skip invalid ones)
        for h1 in soup.find_all('h1'):
            if is_valid_title(h1):
                return clean_title(h1.text)

        # Then h2
        for h2 in soup.find_all('h2'):
            if is_valid_title(h2):
                return clean_title(h2.text)

        # Check for title tag
        title_tag = soup.find('title')
        if title_tag and title_tag.text.strip():
            title_text = clean_title(title_tag.text)
            # Skip generic title tags
            if title_text.lower() not in ('untitled', 'chapter', 'section'):
                return title_text

        # Generate descriptive fallback based on filename
        filename = item.get_name()
        if filename:
            stem = Path(filename).stem
            # Try to extract chapter number from filename
            chapter_match = re.search(r'(?:ch(?:apter)?|part)?[_\-\s]*(\d+)', stem, re.IGNORECASE)
            if chapter_match:
                # Strip leading zeros by converting to int
                chapter_num = int(chapter_match.group(1))
                return f"Chapter {chapter_num}"
            # Clean up filename
            clean_name = stem.replace('_', ' ').replace('-', ' ')
            clean_name = ' '.join(clean_name.split())
            if clean_name:
                return clean_name.title()

        return f"Chapter {len(self.chapters) + 1}"

    def _extract_cover_image(self) -> Optional[bytes]:
        """
        Extract cover image from EPUB if available.

        Returns:
            Cover image bytes or None
        """
        try:
            # Strategy 1: Look for ITEM_COVER type (proper cover image marker)
            for item in self.book.get_items():
                if item.get_type() == ebooklib.ITEM_COVER:
                    cover_data = item.get_content()
                    logger.info(f"Extracted cover image (ITEM_COVER): {item.get_name()}")
                    return cover_data

            # Strategy 2: Get all image items for fallback approaches
            images = []
            for item in self.book.get_items():
                if item.get_type() == ebooklib.ITEM_IMAGE:
                    images.append(item)

            # Strategy 3: Look for cover image by filename
            cover_item = None
            for img in images:
                img_name = img.get_name().lower()
                if 'cover' in img_name:
                    cover_item = img
                    logger.info(f"Found cover by filename: {img.get_name()}")
                    break

            # Strategy 4: Check metadata for cover reference
            if not cover_item:
                try:
                    cover_id = self.book.get_metadata('OPF', 'cover')
                    if cover_id:
                        cover_ref = cover_id[0][0]
                        logger.debug(f"Cover metadata reference: {cover_ref}")
                        for img in images:
                            if img.id == cover_ref:
                                cover_item = img
                                logger.info(f"Found cover by metadata: {img.get_name()}")
                                break
                except Exception as e:
                    logger.debug(f"Could not check cover metadata: {str(e)}")

            # Strategy 5: Use first image as fallback
            if not cover_item and images:
                cover_item = images[0]
                logger.info(f"Using first image as cover: {cover_item.get_name()}")

            # Return cover image bytes if found
            if cover_item:
                cover_data = cover_item.get_content()
                return cover_data
            else:
                logger.warning("No cover image found in EPUB")

        except Exception as e:
            logger.warning(f"Could not extract cover image: {str(e)}", exc_info=True)

        return None
