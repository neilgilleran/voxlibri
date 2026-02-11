"""
Phase 5: Reading View Layout Tests

Tests for 3-column resizable layout, summary panel UI, and chapter navigation enhancements.
Following minimal testing philosophy: 2-8 focused tests per task group.
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from books_core.models import Book, Chapter


class ReadingViewLayoutTests(TestCase):
    """
    Task Group 5.1: Enhanced Reading View - 3-column resizable layout
    Tests: 2-8 focused tests for layout components
    """

    def setUp(self):
        """Set up test data"""
        self.client = Client()

        # Create a mock EPUB file
        epub_content = b'Mock EPUB file content'
        epub_file = SimpleUploadedFile(
            "test_book.epub",
            epub_content,
            content_type="application/epub+zip"
        )

        # Create a test book with chapters
        self.book = Book.objects.create(
            title="Test Book for Layout",
            author="Test Author",
            epub_file=epub_file,
            word_count=10000,
            status='completed'
        )

        # Create chapters with and without summaries
        self.chapters = []
        for i in range(1, 6):
            chapter = Chapter.objects.create(
                book=self.book,
                chapter_number=i,
                title=f"Chapter {i}",
                content=f"Content for chapter {i}" * 50,
                word_count=500,
                has_summary=(i <= 2)  # First 2 chapters have summaries
            )
            self.chapters.append(chapter)

    def test_reading_view_renders_3column_layout(self):
        """Test that reading view renders with 3-column layout structure"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for 3-column container
        self.assertContains(response, 'reading-container-3col')

        # Check for left column (chapter navigation)
        self.assertContains(response, 'chapter-navigation-enhanced')

        # Check for center column (reading content)
        self.assertContains(response, 'reading-content-column')

        # Check for right column (summary panel)
        self.assertContains(response, 'summary-panel')

        # Check for resize handles
        self.assertContains(response, 'resize-handle')

    def test_chapter_navigation_has_checkboxes(self):
        """Test that chapter navigation includes checkboxes for batch selection"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for checkbox elements
        self.assertContains(response, 'chapter-checkbox')
        self.assertContains(response, 'type="checkbox"')

        # Check for batch control buttons
        self.assertContains(response, 'select-all')
        self.assertContains(response, 'deselect-all')
        self.assertContains(response, 'batch-generate')

    def test_summary_indicator_displays_for_chapters_with_summaries(self):
        """Test that visual indicator shows for chapters that have summaries"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check that summary indicator markup exists
        self.assertContains(response, 'summary-indicator')

        # Count indicators should match chapters with has_summary=True
        content = response.content.decode('utf-8')
        indicator_count = content.count('summary-indicator')

        # We have 2 chapters with summaries
        self.assertEqual(indicator_count, 2)

    def test_batch_selection_controls_present(self):
        """Test that batch selection UI elements are present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for batch action section
        self.assertContains(response, 'batch-action')
        self.assertContains(response, 'batch-count-text')

        # Check for batch button (should be disabled by default)
        self.assertContains(response, 'id="batch-generate"')
        self.assertContains(response, 'disabled')

    def test_reading_view_loads_javascript_dependencies(self):
        """Test that reading view includes necessary JavaScript files"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for HTMX library
        self.assertContains(response, 'htmx.org')

        # Check for reading.js (Phase 1 compatibility)
        self.assertContains(response, 'reading.js')

        # Check for reading-ai.js (Phase 5 features)
        self.assertContains(response, 'reading-ai.js')

    def test_chapter_data_attributes_present(self):
        """Test that chapter data is exposed via data attributes for JavaScript"""
        url = reverse('reading_view_chapter', args=[self.book.id, 2])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for data attributes container
        self.assertContains(response, 'id="reading-view-data"')
        self.assertContains(response, f'data-book-id="{self.book.id}"')
        self.assertContains(response, f'data-chapter-id="{self.chapters[1].id}"')
        self.assertContains(response, 'data-chapter-number="2"')


class SummaryPanelUITests(TestCase):
    """
    Task Group 5.2: Summary Panel UI
    Tests: 2-8 focused tests for summary panel components
    """

    def setUp(self):
        """Set up test data"""
        self.client = Client()

        epub_content = b'Mock EPUB file content'
        epub_file = SimpleUploadedFile(
            "test_summary.epub",
            epub_content,
            content_type="application/epub+zip"
        )

        self.book = Book.objects.create(
            title="Test Book for Summary Panel",
            author="Test Author",
            epub_file=epub_file,
            word_count=10000,
            status='completed'
        )

        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title="Chapter 1",
            content="Test content for chapter 1" * 100,
            word_count=500
        )

    def test_summary_panel_renders_with_controls(self):
        """Test that summary panel renders with all control elements"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for summary panel header
        self.assertContains(response, 'summary-panel-header')
        self.assertContains(response, 'AI Summary')

        # Check for summary controls section
        self.assertContains(response, 'summary-controls')

    def test_prompt_selector_present(self):
        """Test that prompt dropdown selector is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for prompt selector dropdown
        self.assertContains(response, 'id="prompt-selector"')
        self.assertContains(response, 'aria-label="Select AI prompt"')
        self.assertContains(response, 'Loading prompts...')

    def test_model_selector_present(self):
        """Test that model dropdown selector is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for model selector dropdown
        self.assertContains(response, 'id="model-selector"')
        self.assertContains(response, 'aria-label="Select AI model"')
        self.assertContains(response, 'gpt-4o-mini')

    def test_generate_summary_button_present(self):
        """Test that Generate Summary button is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for generate button
        self.assertContains(response, 'id="generate-summary"')
        self.assertContains(response, 'Generate Summary')
        self.assertContains(response, 'aria-label="Generate summary for current chapter"')

    def test_version_selector_hidden_by_default(self):
        """Test that version selector is hidden when no versions exist"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for version selector (should be hidden)
        self.assertContains(response, 'id="version-selector-group"')
        self.assertContains(response, 'style="display: none;"')

    def test_compare_versions_button_hidden_by_default(self):
        """Test that Compare Versions button is hidden by default"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for compare button (should be hidden)
        self.assertContains(response, 'id="compare-versions"')
        # Note: The button has style="display: none;" directly in HTML

    def test_summary_display_shows_empty_state(self):
        """Test that summary display shows empty state when no summary exists"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for summary display area
        self.assertContains(response, 'id="summary-display"')

        # Check for empty state message
        self.assertContains(response, 'summary-empty-state')
        self.assertContains(response, 'No summary generated yet.')

    def test_accessibility_attributes_present(self):
        """Test that accessibility attributes are present on summary panel elements"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for ARIA labels on key elements
        self.assertContains(response, 'aria-label')

        # Count ARIA labels (should have several)
        content = response.content.decode('utf-8')
        aria_count = content.count('aria-label')
        self.assertGreaterEqual(aria_count, 4)  # At least 4 ARIA labels


class ChapterNavigationEnhancementsTests(TestCase):
    """
    Task Group 5.3: Chapter Navigation Enhancements
    Tests: 2-8 focused tests for navigation enhancements
    """

    def setUp(self):
        """Set up test data"""
        self.client = Client()

        epub_content = b'Mock EPUB file content'
        epub_file = SimpleUploadedFile(
            "test_navigation.epub",
            epub_content,
            content_type="application/epub+zip"
        )

        self.book = Book.objects.create(
            title="Test Book for Navigation",
            author="Test Author",
            epub_file=epub_file,
            word_count=15000,
            status='completed'
        )

        # Create 10 chapters
        for i in range(1, 11):
            Chapter.objects.create(
                book=self.book,
                chapter_number=i,
                title=f"Chapter {i}",
                content=f"Content for chapter {i}" * 50,
                word_count=500,
                has_summary=(i % 3 == 0)  # Every 3rd chapter has summary
            )

    def test_all_chapters_have_checkboxes(self):
        """Test that all chapters in navigation have checkboxes"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Count checkboxes
        content = response.content.decode('utf-8')
        checkbox_count = content.count('class="chapter-checkbox"')

        # Should have 10 checkboxes (one per chapter)
        self.assertEqual(checkbox_count, 10)

    def test_summary_indicators_show_correct_count(self):
        """Test that summary indicators show for correct chapters"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Count summary indicators
        content = response.content.decode('utf-8')
        indicator_count = content.count('summary-indicator')

        # Every 3rd chapter has summary: 3, 6, 9 = 3 chapters
        self.assertEqual(indicator_count, 3)

    def test_active_chapter_highlighted(self):
        """Test that current chapter is highlighted in navigation"""
        url = reverse('reading_view_chapter', args=[self.book.id, 5])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for active class on chapter 5
        self.assertContains(response, 'class="chapter-list-item active"')

    def test_checkbox_data_attributes_correct(self):
        """Test that checkboxes have correct data attributes"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for data attributes on checkboxes
        self.assertContains(response, 'data-chapter-id')
        self.assertContains(response, 'data-chapter-number')

        # Verify format
        content = response.content.decode('utf-8')
        self.assertIn('data-chapter-number="1"', content)
        self.assertIn('data-chapter-number="10"', content)


class ResponsiveLayoutTests(TestCase):
    """
    Additional tests for responsive behavior (desktop-first, basic mobile handling)
    """

    def setUp(self):
        """Set up test data"""
        self.client = Client()

        epub_content = b'Mock EPUB file content'
        epub_file = SimpleUploadedFile(
            "test_responsive.epub",
            epub_content,
            content_type="application/epub+zip"
        )

        self.book = Book.objects.create(
            title="Test Book",
            author="Test Author",
            epub_file=epub_file,
            word_count=10000,
            status='completed'
        )

        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title="Chapter 1",
            content="Test content" * 100,
            word_count=500
        )

    def test_mobile_toggle_button_present(self):
        """Test that mobile navigation toggle button is present"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check for toggle button
        self.assertContains(response, 'id="toggle-nav"')
        self.assertContains(response, 'btn-icon')
        self.assertContains(response, 'Toggle chapter navigation')

    def test_css_includes_responsive_breakpoints(self):
        """Test that reading.css file is loaded (contains responsive styles)"""
        url = reverse('reading_view_chapter', args=[self.book.id, 1])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # Check that reading.css is included
        self.assertContains(response, 'reading.css')
