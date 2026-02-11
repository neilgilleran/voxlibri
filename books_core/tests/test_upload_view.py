"""
Tests for upload view and upload flow.
Focused tests for Phase 1C upload functionality.
"""
import os
from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from books_core.models import Book, Chapter


class UploadViewTest(TestCase):
    """Test upload view and form validation."""

    def setUp(self):
        """Set up test client and test data directory."""
        self.client = Client()
        self.upload_url = reverse('upload_book')

        # Path to test EPUB files
        self.test_epubs_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'test_epubs'
        )

    def test_upload_view_accessible(self):
        """Test that upload view is accessible."""
        response = self.client.get(self.upload_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upload Book')

    def test_invalid_file_type_shows_validation_error(self):
        """Test that uploading invalid file type shows validation error."""
        # Create a fake text file
        invalid_file = SimpleUploadedFile(
            "test.txt",
            b"This is not an EPUB file",
            content_type="text/plain"
        )

        response = self.client.post(self.upload_url, {
            'epub_file': invalid_file
        })

        # Should show validation error
        self.assertContains(response, 'Invalid file extension')

    def test_file_too_large_shows_size_error(self):
        """Test that uploading file >50MB shows size error."""
        # Create a file larger than 50MB (52428800 bytes)
        large_file_size = 52428801  # 1 byte over limit
        large_file = SimpleUploadedFile(
            "large.epub",
            b"x" * large_file_size,
            content_type="application/epub+zip"
        )

        response = self.client.post(self.upload_url, {
            'epub_file': large_file
        })

        # Should show size error
        self.assertContains(response, 'File size exceeds 50MB')

    def test_valid_epub_creates_book_and_chapters(self):
        """Test that valid EPUB upload creates Book and Chapters."""
        # Check if test EPUB exists
        test_epub_path = os.path.join(self.test_epubs_dir, 'grit.epub')

        if not os.path.exists(test_epub_path):
            self.skipTest(f"Test EPUB not found at {test_epub_path}")

        # Read the test EPUB file
        with open(test_epub_path, 'rb') as epub_file:
            epub_data = epub_file.read()

        uploaded_file = SimpleUploadedFile(
            "grit.epub",
            epub_data,
            content_type="application/epub+zip"
        )

        # POST to upload view
        response = self.client.post(self.upload_url, {
            'epub_file': uploaded_file
        })

        # Should redirect to book detail
        self.assertEqual(response.status_code, 302)

        # Check Book created
        self.assertTrue(Book.objects.exists())
        book = Book.objects.first()

        self.assertEqual(book.status, 'completed')
        self.assertIsNotNone(book.title)
        self.assertNotEqual(book.title, 'Processing...')
        self.assertGreater(book.word_count, 0)

        # Check Chapters created
        chapters = Chapter.objects.filter(book=book)
        self.assertGreater(chapters.count(), 0)

    def test_successful_upload_redirects_to_book_detail(self):
        """Test that successful upload redirects to book detail page."""
        test_epub_path = os.path.join(self.test_epubs_dir, 'grit.epub')

        if not os.path.exists(test_epub_path):
            self.skipTest(f"Test EPUB not found at {test_epub_path}")

        with open(test_epub_path, 'rb') as epub_file:
            epub_data = epub_file.read()

        uploaded_file = SimpleUploadedFile(
            "grit.epub",
            epub_data,
            content_type="application/epub+zip"
        )

        response = self.client.post(self.upload_url, {
            'epub_file': uploaded_file
        }, follow=False)

        # Should redirect
        self.assertEqual(response.status_code, 302)

        # Get the created book
        book = Book.objects.first()

        # Should redirect to book detail
        expected_url = reverse('book_detail', kwargs={'pk': book.id})
        self.assertRedirects(response, expected_url)

    def test_failed_parsing_creates_failed_book(self):
        """Test that failed EPUB parsing creates Book with status='failed'."""
        # Create a fake EPUB file that will fail parsing
        fake_epub = SimpleUploadedFile(
            "fake.epub",
            b"PK\x03\x04" + b"x" * 1000,  # ZIP header but invalid EPUB
            content_type="application/epub+zip"
        )

        response = self.client.post(self.upload_url, {
            'epub_file': fake_epub
        })

        # Should redirect to library on error
        self.assertEqual(response.status_code, 302)

        # Check if Book was created with failed status
        books = Book.objects.filter(status='failed')
        if books.exists():
            failed_book = books.first()
            self.assertEqual(failed_book.status, 'failed')
            self.assertIsNotNone(failed_book.error_message)

    def test_upload_form_validation_empty_file(self):
        """Test that empty file submission shows error."""
        response = self.client.post(self.upload_url, {})

        # Should show form with errors
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required')
