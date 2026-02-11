"""
Tests for display views (library, book detail, reading view).
"""
from django.test import TestCase, Client
from django.urls import reverse
from books_core.models import Book, Chapter


class LibraryViewTests(TestCase):
    """Tests for the LibraryView."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

        # Create test books
        self.book1 = Book.objects.create(
            title='Test Book 1',
            author='Author One',
            status='completed',
            word_count=1000
        )
        self.book2 = Book.objects.create(
            title='Another Book',
            author='Author Two',
            status='completed',
            word_count=2000
        )
        self.book3 = Book.objects.create(
            title='Book Three',
            author='Author One',
            status='processing',
            word_count=0
        )

    def test_library_view_displays_books(self):
        """Test that library view displays all books."""
        response = self.client.get(reverse('library'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Book 1')
        self.assertContains(response, 'Another Book')
        self.assertContains(response, 'Book Three')
        self.assertContains(response, '3 books in your collection')

    def test_library_view_sort_newest_first(self):
        """Test library view with newest first sorting (default)."""
        response = self.client.get(reverse('library'))

        books = response.context['books']
        self.assertEqual(books[0].id, self.book3.id)  # Most recently created

    def test_library_view_sort_oldest_first(self):
        """Test library view with oldest first sorting."""
        response = self.client.get(reverse('library') + '?sort=oldest')

        books = response.context['books']
        self.assertEqual(books[0].id, self.book1.id)  # First created

    def test_library_view_sort_title_az(self):
        """Test library view with title A-Z sorting."""
        response = self.client.get(reverse('library') + '?sort=title_az')

        books = list(response.context['books'])
        self.assertEqual(books[0].title, 'Another Book')
        self.assertEqual(books[1].title, 'Book Three')
        self.assertEqual(books[2].title, 'Test Book 1')

    def test_library_view_sort_author_az(self):
        """Test library view with author A-Z sorting."""
        response = self.client.get(reverse('library') + '?sort=author_az')

        books = list(response.context['books'])
        # Should sort by author first, then title
        self.assertEqual(books[0].author, 'Author One')
        self.assertEqual(books[2].author, 'Author Two')

    def test_library_view_empty_state(self):
        """Test library view shows empty state when no books."""
        Book.objects.all().delete()
        response = self.client.get(reverse('library'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No books in your library yet')
        self.assertContains(response, '0 books in your collection')


class BookDetailViewTests(TestCase):
    """Tests for the BookDetailView."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

        # Create test book
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            status='completed',
            word_count=5000
        )

        # Create test chapters
        self.chapter1 = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter One',
            content='# Chapter One\n\nThis is chapter one content.',
            word_count=500,
            is_front_matter=False,
            is_back_matter=False
        )
        self.chapter2 = Chapter.objects.create(
            book=self.book,
            chapter_number=2,
            title='Chapter Two',
            content='# Chapter Two\n\nThis is chapter two content.',
            word_count=600,
            is_front_matter=False,
            is_back_matter=False
        )
        # Create front matter chapter (should be filtered out)
        self.front_matter = Chapter.objects.create(
            book=self.book,
            chapter_number=0,
            title='Table of Contents',
            content='TOC content',
            word_count=100,
            is_front_matter=True,
            is_back_matter=False
        )

    def test_book_detail_view_displays_metadata(self):
        """Test that book detail view displays book metadata."""
        response = self.client.get(reverse('book_detail', kwargs={'pk': self.book.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Book')
        self.assertContains(response, 'Test Author')
        self.assertContains(response, '5,000')  # Word count with comma

    def test_book_detail_view_displays_chapters(self):
        """Test that book detail view displays chapters list."""
        response = self.client.get(reverse('book_detail', kwargs={'pk': self.book.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Chapter One')
        self.assertContains(response, 'Chapter Two')
        self.assertContains(response, 'Chapters (2)')

    def test_book_detail_view_filters_front_matter(self):
        """Test that front matter is not displayed in chapters list."""
        response = self.client.get(reverse('book_detail', kwargs={'pk': self.book.id}))

        chapters = response.context['chapters']
        self.assertEqual(chapters.count(), 2)
        self.assertNotIn(self.front_matter, chapters)

    def test_book_detail_view_failed_status(self):
        """Test book detail view displays error for failed books."""
        failed_book = Book.objects.create(
            title='Failed Book',
            author='Author',
            status='failed',
            error_message='Test error message'
        )

        response = self.client.get(reverse('book_detail', kwargs={'pk': failed_book.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processing Error')
        self.assertContains(response, 'Test error message')

    def test_book_detail_view_404_for_nonexistent_book(self):
        """Test book detail view returns 404 for non-existent book."""
        response = self.client.get(reverse('book_detail', kwargs={'pk': 9999}))

        self.assertEqual(response.status_code, 404)


class ReadingViewTests(TestCase):
    """Tests for the ReadingView."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

        # Create test book
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            status='completed',
            word_count=3000
        )

        # Create test chapters
        self.chapter1 = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='First Chapter',
            content='# First Chapter\n\nThis is the first chapter.',
            word_count=1000,
            is_front_matter=False,
            is_back_matter=False
        )
        self.chapter2 = Chapter.objects.create(
            book=self.book,
            chapter_number=2,
            title='Second Chapter',
            content='# Second Chapter\n\nThis is the second chapter.',
            word_count=1500,
            is_front_matter=False,
            is_back_matter=False
        )
        self.chapter3 = Chapter.objects.create(
            book=self.book,
            chapter_number=3,
            title='Third Chapter',
            content='# Third Chapter\n\nThis is the third chapter.',
            word_count=500,
            is_front_matter=False,
            is_back_matter=False
        )

    def test_reading_view_displays_chapter_content(self):
        """Test that reading view displays chapter content."""
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': self.book.id,
                'chapter_number': 1
            })
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'First Chapter')
        self.assertContains(response, 'This is the first chapter')

    def test_reading_view_shows_navigation(self):
        """Test that reading view shows chapter navigation."""
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': self.book.id,
                'chapter_number': 2
            })
        )

        self.assertEqual(response.status_code, 200)
        # Should have both prev and next
        self.assertContains(response, 'Previous Chapter')
        self.assertContains(response, 'Next Chapter')

    def test_reading_view_prev_navigation(self):
        """Test that reading view prev button links correctly."""
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': self.book.id,
                'chapter_number': 2
            })
        )

        # Should have prev_chapter in context
        prev_chapter = response.context['prev_chapter']
        self.assertEqual(prev_chapter.chapter_number, 1)

    def test_reading_view_next_navigation(self):
        """Test that reading view next button links correctly."""
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': self.book.id,
                'chapter_number': 1
            })
        )

        # Should have next_chapter in context
        next_chapter = response.context['next_chapter']
        self.assertEqual(next_chapter.chapter_number, 2)

    def test_reading_view_first_chapter_no_prev(self):
        """Test that first chapter has no previous button."""
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': self.book.id,
                'chapter_number': 1
            })
        )

        # Should NOT have prev_chapter
        self.assertIsNone(response.context['prev_chapter'])

    def test_reading_view_last_chapter_no_next(self):
        """Test that last chapter has no next button."""
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': self.book.id,
                'chapter_number': 3
            })
        )

        # Should NOT have next_chapter
        self.assertIsNone(response.context['next_chapter'])

    def test_reading_view_default_chapter(self):
        """Test that reading view defaults to chapter 1."""
        response = self.client.get(
            reverse('reading_view', kwargs={'book_id': self.book.id})
        )

        self.assertEqual(response.status_code, 200)
        chapter = response.context['chapter']
        self.assertEqual(chapter.chapter_number, 1)

    def test_reading_view_shows_chapter_progress(self):
        """Test that reading view shows chapter progress."""
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': self.book.id,
                'chapter_number': 2
            })
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Chapter 2 of 3')

    def test_reading_view_404_for_invalid_chapter(self):
        """Test reading view returns 404 for non-existent chapter."""
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': self.book.id,
                'chapter_number': 999
            })
        )

        self.assertEqual(response.status_code, 404)


class DeleteBookViewTests(TestCase):
    """Tests for the DeleteBookView."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

        # Create test book
        self.book = Book.objects.create(
            title='Book to Delete',
            author='Test Author',
            status='completed',
            word_count=1000
        )

        # Create test chapter
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter One',
            content='Content',
            word_count=1000
        )

    def test_delete_book_removes_book(self):
        """Test that deleting a book removes it from database."""
        response = self.client.post(
            reverse('delete_book', kwargs={'pk': self.book.id}),
            follow=True
        )

        # Should redirect to library
        self.assertRedirects(response, reverse('library'))

        # Book should be deleted
        self.assertFalse(Book.objects.filter(pk=self.book.id).exists())

    def test_delete_book_cascades_to_chapters(self):
        """Test that deleting a book cascades to delete chapters."""
        chapter_id = self.chapter.id

        self.client.post(
            reverse('delete_book', kwargs={'pk': self.book.id})
        )

        # Chapter should be deleted (cascade)
        self.assertFalse(Chapter.objects.filter(pk=chapter_id).exists())

    def test_delete_book_shows_success_message(self):
        """Test that deleting a book shows success message."""
        response = self.client.post(
            reverse('delete_book', kwargs={'pk': self.book.id}),
            follow=True
        )

        # Should show success message
        messages = list(response.context['messages'])
        self.assertEqual(len(messages), 1)
        self.assertIn('deleted successfully', str(messages[0]))
