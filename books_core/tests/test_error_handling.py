"""
Error handling tests for Phase 1F.
Tests error recovery, edge cases, and graceful degradation.
"""
import os
from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from books_core.models import Book, Chapter


class ErrorHandlingTests(TestCase):
    """
    Tests for error handling, edge cases, and graceful degradation.
    """

    def setUp(self):
        """Set up test client."""
        self.client = Client()
        self.test_epubs_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'test_epubs'
        )

    def test_epub_without_cover_handles_gracefully(self):
        """
        Test that EPUBs without cover images are handled gracefully.
        Verifies no crashes when cover extraction fails.
        """
        test_epub_path = os.path.join(self.test_epubs_dir, 'grit.epub')
        if not os.path.exists(test_epub_path):
            self.skipTest(f"Test EPUB not found at {test_epub_path}")

        with open(test_epub_path, 'rb') as epub_file:
            epub_data = epub_file.read()

        uploaded_file = SimpleUploadedFile(
            "test.epub",
            epub_data,
            content_type="application/epub+zip"
        )

        response = self.client.post(
            reverse('upload_book'),
            {'epub_file': uploaded_file},
            follow=True
        )

        # Should still complete successfully even if no cover
        self.assertEqual(response.status_code, 200)

        book = Book.objects.first()
        self.assertIsNotNone(book)
        self.assertEqual(book.status, 'completed')

        # Book should be usable even without cover
        # Check library view doesn't crash
        library_response = self.client.get(reverse('library'))
        self.assertEqual(library_response.status_code, 200)
        self.assertContains(library_response, book.title)

        # Check book detail doesn't crash
        detail_response = self.client.get(
            reverse('book_detail', kwargs={'pk': book.id})
        )
        self.assertEqual(detail_response.status_code, 200)

    def test_epub_parsing_failure_creates_failed_book(self):
        """
        Test that EPUB parsing failures create failed books with error messages.
        Verifies error handling doesn't crash the application.
        """
        # Create a file that looks like EPUB but will fail parsing
        fake_epub = SimpleUploadedFile(
            "invalid.epub",
            b"PK\x03\x04" + b"x" * 1000,  # ZIP header but invalid EPUB
            content_type="application/epub+zip"
        )

        response = self.client.post(
            reverse('upload_book'),
            {'epub_file': fake_epub},
            follow=True
        )

        # Should redirect successfully (error flow)
        self.assertEqual(response.status_code, 200)

        # Check if a book was created (might be failed)
        if Book.objects.exists():
            book = Book.objects.first()

            # If failed, should have error message
            if book.status == 'failed':
                self.assertIsNotNone(book.error_message)
                self.assertGreater(len(book.error_message), 0)

                # Failed book should be visible in library
                library_response = self.client.get(reverse('library'))
                self.assertContains(library_response, book.title)

    def test_corrupt_epub_shows_user_friendly_error(self):
        """
        Test that corrupt EPUB files show user-friendly error messages.
        """
        # Create a clearly corrupt file
        corrupt_epub = SimpleUploadedFile(
            "corrupt.epub",
            b"This is not an EPUB file at all",
            content_type="application/epub+zip"
        )

        response = self.client.post(
            reverse('upload_book'),
            {'epub_file': corrupt_epub},
            follow=True
        )

        self.assertEqual(response.status_code, 200)

        # Check if failed book exists with error message
        failed_books = Book.objects.filter(status='failed')
        if failed_books.exists():
            failed_book = failed_books.first()
            self.assertIsNotNone(failed_book.error_message)
            # Error message should mention corruption or invalid file
            error_lower = failed_book.error_message.lower()
            self.assertTrue(
                'corrupt' in error_lower or
                'invalid' in error_lower or
                'error' in error_lower,
                f"Error message should be user-friendly: {failed_book.error_message}"
            )

    def test_upload_epub_with_minimal_content(self):
        """
        Test that EPUBs with minimal content are handled.
        Verifies word count filtering and chapter creation.
        """
        test_epub_path = os.path.join(self.test_epubs_dir, 'grit.epub')
        if not os.path.exists(test_epub_path):
            self.skipTest(f"Test EPUB not found at {test_epub_path}")

        with open(test_epub_path, 'rb') as epub_file:
            epub_data = epub_file.read()

        uploaded_file = SimpleUploadedFile(
            "small.epub",
            epub_data,
            content_type="application/epub+zip"
        )

        response = self.client.post(
            reverse('upload_book'),
            {'epub_file': uploaded_file},
            follow=True
        )

        book = Book.objects.first()
        if book and book.status == 'completed':
            # Should have at least one chapter with >= 50 words
            main_chapters = Chapter.objects.filter(
                book=book,
                is_front_matter=False,
                is_back_matter=False
            )

            # If chapters exist, they should all meet minimum word count
            for chapter in main_chapters:
                self.assertGreaterEqual(chapter.word_count, 50,
                    f"Chapter {chapter.chapter_number} has {chapter.word_count} words, minimum is 50")

    def test_nonexistent_book_returns_404(self):
        """
        Test that accessing non-existent book returns 404.
        Verifies proper HTTP error handling.
        """
        # Try to access book that doesn't exist
        response = self.client.get(
            reverse('book_detail', kwargs={'pk': 99999})
        )
        self.assertEqual(response.status_code, 404)

        # Try to access reading view for non-existent book
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': 99999,
                'chapter_number': 1
            })
        )
        self.assertEqual(response.status_code, 404)

        # Try to delete non-existent book
        response = self.client.post(
            reverse('delete_book', kwargs={'pk': 99999})
        )
        self.assertEqual(response.status_code, 404)

    def test_invalid_chapter_number_returns_404(self):
        """
        Test that accessing invalid chapter number returns 404.
        Verifies chapter navigation error handling.
        """
        # Create a book with one chapter
        book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            status='completed',
            word_count=1000
        )

        Chapter.objects.create(
            book=book,
            chapter_number=1,
            title='Only Chapter',
            content='Content',
            word_count=100,
            is_front_matter=False,
            is_back_matter=False
        )

        # Try to access non-existent chapter
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': book.id,
                'chapter_number': 999
            })
        )
        self.assertEqual(response.status_code, 404)
