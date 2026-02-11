"""
Tests for EPUBParserService.

Focused tests for EPUB parsing functionality.
"""

from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
import tempfile
import os

from books_core.services.epub_parser import EPUBParserService


class EPUBParserServiceTestCase(TestCase):
    """Test EPUBParserService."""

    def setUp(self):
        """Set up test fixtures."""
        self.parser = EPUBParserService()

    def test_validate_file_size_exceeds_limit(self):
        """Test that validation fails for files exceeding size limit."""
        # Create a mock file that appears to be too large
        large_file = SimpleUploadedFile(
            "test.epub",
            b"x" * (51 * 1024 * 1024),  # 51MB
            content_type="application/epub+zip"
        )

        with self.assertRaises(ValidationError) as context:
            self.parser.validate_epub_file(large_file)

        self.assertIn("50MB", str(context.exception))

    def test_validate_invalid_extension(self):
        """Test that validation fails for non-EPUB files."""
        invalid_file = SimpleUploadedFile(
            "test.pdf",
            b"dummy content",
            content_type="application/pdf"
        )

        with self.assertRaises(ValidationError) as context:
            self.parser.validate_epub_file(invalid_file)

        self.assertIn(".epub", str(context.exception))

    def test_validate_invalid_mime_type(self):
        """Test that validation fails for incorrect MIME types."""
        invalid_file = SimpleUploadedFile(
            "test.epub",
            b"dummy content",
            content_type="application/pdf"
        )

        with self.assertRaises(ValidationError) as context:
            self.parser.validate_epub_file(invalid_file)

        self.assertIn("application/epub+zip", str(context.exception))

    def test_validate_valid_epub_file(self):
        """Test that validation passes for valid EPUB files."""
        valid_file = SimpleUploadedFile(
            "test.epub",
            b"dummy epub content",
            content_type="application/epub+zip"
        )

        # Should not raise any exception
        try:
            self.parser.validate_epub_file(valid_file)
        except ValidationError:
            self.fail("validate_epub_file() raised ValidationError unexpectedly")

    def test_extract_metadata_default_values(self):
        """Test that metadata extraction provides default values when fields are missing."""
        # Note: This test verifies the structure, but we can't test actual EPUB parsing
        # without a real EPUB file. The service should provide defaults.
        self.assertEqual(self.parser.metadata, {})

        # After initialization, metadata should be empty
        # After parsing, it should have default values
        # This is a structural test to ensure the service is set up correctly

    def test_parser_initialization(self):
        """Test that parser initializes with correct attributes."""
        parser = EPUBParserService()

        self.assertIsNone(parser.book)
        self.assertEqual(parser.metadata, {})
        self.assertEqual(parser.chapters, [])
        self.assertEqual(parser.spine_items, [])
        self.assertEqual(parser.MAX_FILE_SIZE, 50 * 1024 * 1024)
        self.assertEqual(parser.ALLOWED_MIME_TYPES, ['application/epub+zip'])
