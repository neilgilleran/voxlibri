"""
Template rendering tests for Phase 1F.
Tests template tag functionality and content rendering.
"""
from django.test import TestCase, Client
from django.urls import reverse

from books_core.models import Book, Chapter


class TemplateRenderingTests(TestCase):
    """
    Tests for template rendering, markdown conversion, and display.
    """

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

    def test_markdown_rendering_in_reading_view(self):
        """
        Test that markdown content is properly rendered to HTML in reading view.
        Verifies the markdown template tag functionality.
        """
        # Create chapter with markdown content
        chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Test Chapter',
            content="""# Chapter Heading

This is a paragraph with **bold text** and *italic text*.

## Subheading

- List item 1
- List item 2
- List item 3

> This is a blockquote.

1. Numbered item 1
2. Numbered item 2
""",
            word_count=100,
            is_front_matter=False,
            is_back_matter=False
        )

        # Get reading view
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': self.book.id,
                'chapter_number': chapter.chapter_number
            })
        )

        self.assertEqual(response.status_code, 200)

        # Verify markdown was rendered to HTML
        content = response.content.decode('utf-8')

        # Check for HTML tags (markdown should be converted)
        self.assertIn('<h1>Chapter Heading</h1>', content)
        self.assertIn('<h2>Subheading</h2>', content)
        self.assertIn('<strong>bold text</strong>', content)
        self.assertIn('<em>italic text</em>', content)
        self.assertIn('<ul>', content)
        self.assertIn('<li>List item 1</li>', content)
        self.assertIn('<blockquote>', content)
        self.assertIn('<ol>', content)

        # Should NOT contain raw markdown syntax
        self.assertNotIn('**bold text**', content)
        self.assertNotIn('*italic text*', content)

    def test_library_displays_book_cards_correctly(self):
        """
        Test that library view displays book cards with all required elements.
        Verifies card rendering and status badge display.
        """
        # Create books with different statuses
        completed_book = self.book  # Already created in setUp
        processing_book = Book.objects.create(
            title='Processing Book',
            author='Author Two',
            status='processing',
            word_count=0
        )
        failed_book = Book.objects.create(
            title='Failed Book',
            author='Author Three',
            status='failed',
            error_message='Test error',
            word_count=0
        )

        # Get library view
        response = self.client.get(reverse('library'))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode('utf-8')

        # Verify all books are displayed
        self.assertContains(response, 'Test Book')
        self.assertContains(response, 'Processing Book')
        self.assertContains(response, 'Failed Book')

        # Verify author names displayed
        self.assertContains(response, 'Test Author')
        self.assertContains(response, 'Author Two')
        self.assertContains(response, 'Author Three')

        # Verify status badges for non-completed books
        # Processing book should show status badge
        self.assertIn('Processing', content)

        # Failed book should show status badge
        self.assertIn('Failed', content)

        # Verify book count displayed
        self.assertContains(response, '3 books in your collection')

        # Verify View buttons present for all books
        self.assertEqual(content.count('>View</a>'), 3)  # One View button per book

        # Verify word counts displayed for completed book
        self.assertContains(response, '5,000')

    def test_book_detail_displays_chapters_with_titles(self):
        """
        Test that book detail page displays chapters with proper titles.
        Verifies chapter list rendering and title fallback logic.
        """
        # Create chapters with different title scenarios
        Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter with Title',
            content='Content for chapter 1',
            word_count=100,
            is_front_matter=False,
            is_back_matter=False
        )

        Chapter.objects.create(
            book=self.book,
            chapter_number=2,
            title=None,  # No title - should use fallback
            content='Content for chapter 2',
            word_count=150,
            is_front_matter=False,
            is_back_matter=False
        )

        Chapter.objects.create(
            book=self.book,
            chapter_number=3,
            title='',  # Empty title - should use fallback
            content='Content for chapter 3',
            word_count=200,
            is_front_matter=False,
            is_back_matter=False
        )

        # Get book detail view
        response = self.client.get(
            reverse('book_detail', kwargs={'pk': self.book.id})
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Verify chapter with title displays correctly
        self.assertContains(response, 'Chapter with Title')

        # Verify chapters without titles use fallback
        self.assertContains(response, 'Chapter 2')
        self.assertContains(response, 'Chapter 3')

        # Verify word counts displayed
        self.assertContains(response, '100 words')
        self.assertContains(response, '150 words')
        self.assertContains(response, '200 words')

        # Verify Read links present (one per chapter)
        self.assertEqual(content.count('class="btn btn-sm">Read</a>'), 3)

        # Verify chapter count in heading
        self.assertContains(response, 'Chapters (3)')

    def test_reading_view_displays_chapter_progress(self):
        """
        Test that reading view displays chapter progress indicator.
        Verifies "Chapter X of Y" display.
        """
        # Create 3 chapters
        for i in range(1, 4):
            Chapter.objects.create(
                book=self.book,
                chapter_number=i,
                title=f'Chapter {i}',
                content=f'Content for chapter {i}',
                word_count=100,
                is_front_matter=False,
                is_back_matter=False
            )

        # View chapter 2
        response = self.client.get(
            reverse('reading_view_chapter', kwargs={
                'book_id': self.book.id,
                'chapter_number': 2
            })
        )

        self.assertEqual(response.status_code, 200)

        # Verify progress indicator shows "Chapter 2 of 3"
        self.assertContains(response, 'Chapter 2 of 3')

        # Verify both prev and next buttons present
        self.assertContains(response, 'Previous Chapter')
        self.assertContains(response, 'Next Chapter')
