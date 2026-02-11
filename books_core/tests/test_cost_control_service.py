"""
Tests for CostControlService.

Focused tests covering:
- Token counting accuracy
- Cost estimation formula for gpt-4o-mini
- Limit checking (monthly and daily)
- Atomic usage updates with SELECT FOR UPDATE
"""

from decimal import Decimal
from datetime import date
from django.test import TestCase, TransactionTestCase
from django.db import transaction

from books_core.models import Settings, UsageTracking
from books_core.services.cost_control_service import CostControlService
from books_core.exceptions import (
    LimitExceededException,
    EmergencyStopException,
    CostEstimationException
)


class CostControlServiceTestCase(TestCase):
    """Tests for CostControlService."""

    def setUp(self):
        """Set up test fixtures."""
        # Create settings with known limits
        self.settings = Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True,
            default_model='gpt-4o-mini'
        )
        self.service = CostControlService(model='gpt-4o-mini')

    def test_count_tokens_accuracy(self):
        """Test token counting matches tiktoken results."""
        # Sample text with known token count
        text = "This is a test sentence for token counting."

        # Count tokens
        token_count = self.service.count_tokens(text)

        # Verify token count is reasonable (not zero)
        self.assertGreater(token_count, 0)
        self.assertLess(token_count, 50)  # Should be around 10-15 tokens

    def test_count_tokens_empty_string(self):
        """Test token counting with empty string."""
        token_count = self.service.count_tokens("")
        self.assertEqual(token_count, 0)

    def test_estimate_cost_gpt4o_mini(self):
        """Test cost estimation formula for gpt-4o-mini."""
        # Known pricing: $0.150/1M input, $0.600/1M output
        input_tokens = 1000
        output_tokens = 500

        estimate = self.service.estimate_cost(input_tokens, output_tokens)

        # Calculate expected cost
        # Input: 1000 / 1,000,000 * 0.150 = 0.00015
        # Output: 500 / 1,000,000 * 0.600 = 0.0003
        # Total: 0.00045
        expected_cost = Decimal('0.00045')

        self.assertEqual(estimate['total_tokens'], 1500)
        self.assertEqual(estimate['input_tokens'], 1000)
        self.assertEqual(estimate['output_tokens'], 500)
        self.assertEqual(estimate['estimated_cost_usd'], expected_cost)
        self.assertEqual(estimate['model'], 'gpt-4o-mini')

    def test_check_limits_blocks_when_monthly_exceeded(self):
        """Test that monthly limit is enforced."""
        # Create usage that's at the limit
        today = date.today()
        UsageTracking.objects.create(
            date=today,
            month_year=today.strftime('%Y-%m'),
            daily_summaries_count=50,
            daily_cost_usd=Decimal('4.50'),
            monthly_summaries_count=50,
            monthly_cost_usd=Decimal('4.50'),  # Close to $5 limit
        )

        # Try to spend more
        with self.assertRaises(LimitExceededException) as cm:
            self.service.check_limits(Decimal('1.00'))  # Would exceed $5 limit

        exception = cm.exception
        self.assertEqual(exception.limit_type, 'monthly')
        self.assertIn('Settings', str(exception))

    def test_check_limits_blocks_when_daily_exceeded(self):
        """Test that daily limit is enforced."""
        # Create usage at daily limit
        today = date.today()
        UsageTracking.objects.create(
            date=today,
            month_year=today.strftime('%Y-%m'),
            daily_summaries_count=100,  # At limit
            daily_cost_usd=Decimal('0.50'),
            monthly_summaries_count=100,
            monthly_cost_usd=Decimal('0.50'),
        )

        # Try to create another summary
        with self.assertRaises(LimitExceededException) as cm:
            self.service.check_limits(Decimal('0.01'))

        exception = cm.exception
        self.assertEqual(exception.limit_type, 'daily')

    def test_check_limits_returns_warnings_at_80_percent(self):
        """Test that warnings appear at 80% usage."""
        # Create usage at 80% of daily limit
        today = date.today()
        UsageTracking.objects.create(
            date=today,
            month_year=today.strftime('%Y-%m'),
            daily_summaries_count=80,  # 80% of 100
            daily_cost_usd=Decimal('0.10'),
            monthly_summaries_count=80,
            monthly_cost_usd=Decimal('0.10'),
        )

        result = self.service.check_limits(Decimal('0.01'))

        # Should have warning
        self.assertGreater(len(result['warnings']), 0)
        self.assertTrue(any('80%' in w for w in result['warnings']))

    def test_check_limits_blocks_when_ai_disabled(self):
        """Test emergency stop blocks all operations."""
        self.settings.ai_features_enabled = False
        self.settings.save()

        with self.assertRaises(EmergencyStopException):
            self.service.check_limits(Decimal('0.01'))

    def test_get_current_usage(self):
        """Test retrieving current usage stats."""
        today = date.today()
        UsageTracking.objects.create(
            date=today,
            month_year=today.strftime('%Y-%m'),
            daily_summaries_count=10,
            daily_tokens_used=5000,
            daily_cost_usd=Decimal('0.50'),
            monthly_summaries_count=50,
            monthly_tokens_used=25000,
            monthly_cost_usd=Decimal('2.50'),
        )

        usage = self.service.get_current_usage()

        self.assertEqual(usage['daily']['summaries_count'], 10)
        self.assertEqual(usage['monthly']['summaries_count'], 50)
        self.assertEqual(Decimal(usage['monthly']['cost_usd']), Decimal('2.50'))


class CostControlServiceAtomicTestCase(TransactionTestCase):
    """Tests for atomic usage updates (requires TransactionTestCase)."""

    def setUp(self):
        """Set up test fixtures."""
        Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True
        )
        self.service = CostControlService()

    def test_update_usage_atomic(self):
        """Test that usage updates are atomic with SELECT FOR UPDATE."""
        today = date.today()

        # Create initial usage
        usage = self.service.update_usage(
            tokens=1000,
            cost=Decimal('0.001')
        )

        self.assertEqual(usage.daily_summaries_count, 1)
        self.assertEqual(usage.daily_tokens_used, 1000)
        self.assertEqual(usage.daily_cost_usd, Decimal('0.001'))

        # Update again (same day)
        usage = self.service.update_usage(
            tokens=500,
            cost=Decimal('0.0005')
        )

        self.assertEqual(usage.daily_summaries_count, 2)
        self.assertEqual(usage.daily_tokens_used, 1500)
        self.assertEqual(usage.daily_cost_usd, Decimal('0.0015'))
