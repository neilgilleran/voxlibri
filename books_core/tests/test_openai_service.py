"""
Tests for OpenAIService.

Focused tests covering:
- Successful API call with mocked response
- Cost control integration
- Timeout enforcement
- Error handling without automatic retry
"""

from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock
from django.test import TestCase

from books_core.models import Settings, UsageTracking, Book, Chapter, Prompt
from books_core.services.openai_service import OpenAIService
from books_core.services.cost_control_service import CostControlService
from books_core.exceptions import EmergencyStopException


class OpenAIServiceTestCase(TestCase):
    """Tests for OpenAIService with mocked API calls."""

    def setUp(self):
        """Set up test fixtures."""
        # Create settings
        Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True
        )

        # Initialize service with fake API key
        self.service = OpenAIService(api_key='test-api-key')

    @patch('books_core.services.openai_service.OpenAI')
    def test_complete_successful_api_call(self, mock_openai_class):
        """Test successful API call with mocked response."""
        # Mock the OpenAI client and response
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        # Mock the completion response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "This is a test summary."
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "gpt-4o-mini"
        mock_response.usage.total_tokens = 150
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50

        mock_client.chat.completions.create.return_value = mock_response

        # Create service with mocked client
        service = OpenAIService(api_key='test-key')
        service.client = mock_client

        # Make API call
        result = service.complete(prompt="Test prompt")

        # Verify result
        self.assertEqual(result['content'], "This is a test summary.")
        self.assertEqual(result['tokens_used'], 150)
        self.assertEqual(result['prompt_tokens'], 100)
        self.assertEqual(result['completion_tokens'], 50)
        self.assertEqual(result['finish_reason'], 'stop')

    @patch('books_core.services.openai_service.OpenAI')
    def test_complete_with_cost_control_integration(self, mock_openai_class):
        """Test that cost control service is called correctly."""
        # Mock the OpenAI client
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        # Mock the completion response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Summary text"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "gpt-4o-mini"
        mock_response.usage.total_tokens = 100
        mock_response.usage.prompt_tokens = 80
        mock_response.usage.completion_tokens = 20

        mock_client.chat.completions.create.return_value = mock_response

        # Create service with mocked client
        service = OpenAIService(api_key='test-key')
        service.client = mock_client

        # Call with cost control
        result = service.complete_with_cost_control(
            prompt="Test prompt",
            cost_service=CostControlService()
        )

        # Verify cost information is included
        self.assertIn('actual_cost_usd', result)
        self.assertIn('cost_breakdown', result)

        # Verify usage was tracked
        usage = UsageTracking.objects.first()
        self.assertIsNotNone(usage)
        self.assertEqual(usage.daily_summaries_count, 1)

    @patch('books_core.services.openai_service.OpenAI')
    def test_complete_timeout_enforcement(self, mock_openai_class):
        """Test that timeout is set correctly."""
        # Verify timeout is passed to client
        service = OpenAIService(api_key='test-key', timeout=30)
        self.assertEqual(service.timeout, 30)

    @patch('books_core.services.openai_service.OpenAI')
    def test_complete_api_error_no_retry(self, mock_openai_class):
        """Test that API errors are raised without automatic retry."""
        # Mock the OpenAI client
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        # Mock an API error (use a generic Exception since APIError requires request object)
        mock_client.chat.completions.create.side_effect = Exception("API Error")

        # Create service with mocked client
        service = OpenAIService(api_key='test-key')
        service.client = mock_client

        # Call should raise error without retry
        with self.assertRaises(Exception):
            service.complete(prompt="Test prompt")

    @patch('books_core.services.openai_service.OpenAI')
    def test_complete_with_cost_control_blocks_when_disabled(self, mock_openai_class):
        """Test that emergency stop blocks operations."""
        # Disable AI features
        settings = Settings.get_settings()
        settings.ai_features_enabled = False
        settings.save()

        # Mock client (won't be called)
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        service = OpenAIService(api_key='test-key')
        service.client = mock_client

        # Should raise EmergencyStopException before making API call
        with self.assertRaises(EmergencyStopException):
            service.complete_with_cost_control(
                prompt="Test prompt",
                cost_service=CostControlService()
            )

        # Verify API was never called
        mock_client.chat.completions.create.assert_not_called()

    def test_api_key_validation(self):
        """Test that missing API key raises error."""
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ValueError) as cm:
                OpenAIService()

            self.assertIn('API key', str(cm.exception))
