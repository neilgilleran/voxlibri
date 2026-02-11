from django.test import TestCase
from django.db import models as django_models
from books_core.models import Book, Chapter


class BookModelTest(TestCase):
    """
    Focused tests for Book model.
    Testing only critical functionality: creation, relationships, and constraints.
    """

    def test_book_creation_with_required_fields(self):
        """Test creating a book with only required fields (title and author)."""
        book = Book.objects.create(
            title="Test Book",
            author="Test Author"
        )
        self.assertEqual(book.title, "Test Book")
        self.assertEqual(book.author, "Test Author")
        self.assertEqual(book.status, 'uploaded')  # Default status
        self.assertEqual(book.word_count, 0)  # Default word count

    def test_book_status_choices_validation(self):
        """Test that book status field accepts valid choices."""
        valid_statuses = ['uploaded', 'processing', 'completed', 'failed']
        for status in valid_statuses:
            book = Book.objects.create(
                title=f"Book {status}",
                author="Test Author",
                status=status
            )
            self.assertEqual(book.status, status)

    def test_cascade_delete_behavior(self):
        """Test that deleting a book deletes all associated chapters."""
        book = Book.objects.create(
            title="Test Book",
            author="Test Author"
        )
        # Create chapters
        Chapter.objects.create(
            book=book,
            chapter_number=1,
            content="Chapter 1 content",
            title="Chapter 1"
        )
        Chapter.objects.create(
            book=book,
            chapter_number=2,
            content="Chapter 2 content",
            title="Chapter 2"
        )

        # Verify chapters exist
        self.assertEqual(book.chapters.count(), 2)

        # Delete book
        book_id = book.id
        book.delete()

        # Verify chapters were deleted
        self.assertEqual(Chapter.objects.filter(book_id=book_id).count(), 0)

    def test_word_count_defaults_to_zero(self):
        """Test that word_count defaults to 0."""
        book = Book.objects.create(
            title="Test Book",
            author="Test Author"
        )
        self.assertEqual(book.word_count, 0)

    def test_isbn_index_exists(self):
        """Test that ISBN field has a database index."""
        # Get the Book model's meta information
        isbn_field = Book._meta.get_field('isbn')
        self.assertTrue(isbn_field.db_index)
