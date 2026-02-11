from django.test import TestCase
from django.db import IntegrityError
from decimal import Decimal
from datetime import date
from books_core.models import Book, Chapter, Prompt, Summary, UsageTracking, ProcessingJob, Settings


class PromptModelTest(TestCase):
    """
    Focused tests for Prompt model.
    Testing critical functionality: is_fabric flag, template rendering, unique name constraint.
    """

    def test_prompt_is_fabric_flag(self):
        """Test that is_fabric flag distinguishes Fabric prompts from custom prompts."""
        fabric_prompt = Prompt.objects.create(
            name="extract_wisdom",
            template_text="Extract wisdom from: {content}",
            is_fabric=True,
            is_custom=False
        )
        custom_prompt = Prompt.objects.create(
            name="my_custom_prompt",
            template_text="Analyze: {content}",
            is_fabric=False,
            is_custom=True
        )

        self.assertTrue(fabric_prompt.is_fabric)
        self.assertFalse(fabric_prompt.is_custom)
        self.assertFalse(custom_prompt.is_fabric)
        self.assertTrue(custom_prompt.is_custom)

    def test_prompt_template_rendering(self):
        """Test that render_template method correctly substitutes variables."""
        prompt = Prompt.objects.create(
            name="test_prompt",
            template_text="Title: {title}\nContent: {content}",
            variables_required=["title", "content"]
        )

        rendered = prompt.render_template({
            "title": "Chapter 1",
            "content": "Test content here"
        })

        expected = "Title: Chapter 1\nContent: Test content here"
        self.assertEqual(rendered, expected)


class SummaryModelTest(TestCase):
    """
    Focused tests for Summary model.
    Testing critical functionality: version uniqueness constraint, version linking.
    """

    def setUp(self):
        """Create test book and chapter for summary tests."""
        self.book = Book.objects.create(
            title="Test Book",
            author="Test Author"
        )
        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            content="Test content",
            title="Chapter 1"
        )
        self.prompt = Prompt.objects.create(
            name="test_prompt",
            template_text="Summarize: {content}"
        )

    def test_summary_unique_version_constraint(self):
        """Test that unique constraint on (chapter, prompt, version) is enforced."""
        # Create first summary
        Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            version=1,
            model_used="gpt-4o-mini",
            content_json={"summary": "First version"}
        )

        # Attempting to create duplicate version should raise IntegrityError
        with self.assertRaises(IntegrityError):
            Summary.objects.create(
                chapter=self.chapter,
                prompt=self.prompt,
                version=1,  # Same version
                model_used="gpt-4o-mini",
                content_json={"summary": "Duplicate version"}
            )

    def test_summary_version_linking(self):
        """Test that previous_version FK correctly links summary versions."""
        # Create version 1
        v1 = Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            version=1,
            model_used="gpt-4o-mini",
            content_json={"summary": "Version 1"}
        )

        # Create version 2 linked to v1
        v2 = Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            version=2,
            previous_version=v1,
            model_used="gpt-4o-mini",
            content_json={"summary": "Version 2"}
        )

        # Create version 3 linked to v2
        v3 = Summary.objects.create(
            chapter=self.chapter,
            prompt=self.prompt,
            version=3,
            previous_version=v2,
            model_used="gpt-4o-mini",
            content_json={"summary": "Version 3"}
        )

        # Verify linking
        self.assertEqual(v2.previous_version, v1)
        self.assertEqual(v3.previous_version, v2)
        self.assertIsNone(v1.previous_version)

        # Verify reverse relationship
        self.assertEqual(v1.next_versions.first(), v2)
        self.assertEqual(v2.next_versions.first(), v3)


class UsageTrackingModelTest(TestCase):
    """
    Focused tests for UsageTracking model.
    Testing critical functionality: daily/monthly counters, unique date constraint.
    """

    def test_usage_tracking_atomic_updates(self):
        """Test that daily and monthly counters update correctly."""
        today = date.today()
        month_year = today.strftime('%Y-%m')

        usage = UsageTracking.objects.create(
            date=today,
            month_year=month_year,
            daily_summaries_count=5,
            daily_tokens_used=1000,
            daily_cost_usd=Decimal('0.005000'),
            monthly_summaries_count=10,
            monthly_tokens_used=2000,
            monthly_cost_usd=Decimal('0.010000')
        )

        # Verify values stored correctly
        self.assertEqual(usage.daily_summaries_count, 5)
        self.assertEqual(usage.daily_tokens_used, 1000)
        self.assertEqual(usage.daily_cost_usd, Decimal('0.005000'))
        self.assertEqual(usage.monthly_summaries_count, 10)
        self.assertEqual(usage.monthly_tokens_used, 2000)
        self.assertEqual(usage.monthly_cost_usd, Decimal('0.010000'))

    def test_usage_tracking_unique_date(self):
        """Test that unique constraint on date field is enforced."""
        today = date.today()
        month_year = today.strftime('%Y-%m')

        # Create first record for today
        UsageTracking.objects.create(
            date=today,
            month_year=month_year
        )

        # Attempting to create duplicate date should raise IntegrityError
        with self.assertRaises(IntegrityError):
            UsageTracking.objects.create(
                date=today,
                month_year=month_year
            )


class SettingsModelTest(TestCase):
    """
    Focused tests for Settings model.
    Testing critical functionality: singleton pattern, default values.
    """

    def test_settings_default_values(self):
        """Test that Settings model has correct default values."""
        settings = Settings.objects.create()

        self.assertEqual(settings.monthly_limit_usd, Decimal('5.00'))
        self.assertEqual(settings.daily_summary_limit, 100)
        self.assertTrue(settings.ai_features_enabled)
        self.assertEqual(settings.default_model, 'gpt-4o-mini')

    def test_settings_singleton_pattern(self):
        """Test that only one Settings instance can exist."""
        # Create first settings instance
        Settings.objects.create()

        # Attempting to create second instance should raise ValueError
        with self.assertRaises(ValueError):
            Settings.objects.create()

    def test_settings_get_settings_method(self):
        """Test that get_settings class method creates or retrieves singleton."""
        # First call should create settings
        settings1 = Settings.get_settings()
        self.assertIsNotNone(settings1)
        self.assertEqual(settings1.pk, 1)

        # Second call should retrieve same instance
        settings2 = Settings.get_settings()
        self.assertEqual(settings1.pk, settings2.pk)

        # Verify only one record exists
        self.assertEqual(Settings.objects.count(), 1)


class ChapterHasSummaryFieldTest(TestCase):
    """
    Test the new has_summary field on Chapter model.
    """

    def test_chapter_has_summary_defaults_to_false(self):
        """Test that has_summary field defaults to False."""
        book = Book.objects.create(
            title="Test Book",
            author="Test Author"
        )
        chapter = Chapter.objects.create(
            book=book,
            chapter_number=1,
            content="Test content"
        )

        self.assertFalse(chapter.has_summary)

    def test_chapter_summary_count_property(self):
        """Test that summary_count property returns correct count."""
        book = Book.objects.create(
            title="Test Book",
            author="Test Author"
        )
        chapter = Chapter.objects.create(
            book=book,
            chapter_number=1,
            content="Test content"
        )
        prompt = Prompt.objects.create(
            name="test_prompt",
            template_text="Summarize: {content}"
        )

        # Initially zero summaries
        self.assertEqual(chapter.summary_count, 0)

        # Create summaries
        Summary.objects.create(
            chapter=chapter,
            prompt=prompt,
            version=1,
            model_used="gpt-4o-mini",
            content_json={"summary": "Version 1"}
        )
        Summary.objects.create(
            chapter=chapter,
            prompt=prompt,
            version=2,
            model_used="gpt-4o-mini",
            content_json={"summary": "Version 2"}
        )

        # Should have 2 summaries
        self.assertEqual(chapter.summary_count, 2)
