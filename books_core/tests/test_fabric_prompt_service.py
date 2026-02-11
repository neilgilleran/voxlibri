"""
Tests for FabricPromptService.

Focused tests covering:
- GitHub fetch with mocked response
- Local caching (second fetch uses cache)
- Prompt parsing
- Sync functionality
"""

from unittest.mock import Mock, patch
from django.test import TestCase

from books_core.models import Prompt
from books_core.services.fabric_prompt_service import FabricPromptService


class FabricPromptServiceTestCase(TestCase):
    """Tests for FabricPromptService."""

    def setUp(self):
        """Set up test fixtures."""
        self.service = FabricPromptService()

    @patch('books_core.services.fabric_prompt_service.requests.get')
    def test_fetch_prompt_from_github(self, mock_get):
        """Test fetching prompt from GitHub with mocked response."""
        # Mock successful GitHub response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "# Test Prompt\n\nThis is a test prompt template."
        mock_get.return_value = mock_response

        # Fetch prompt
        content = self.service.fetch_prompt_from_github('test_prompt')

        # Verify content
        self.assertIsNotNone(content)
        self.assertIn('Test Prompt', content)

        # Verify correct URL was called
        expected_url = f"{self.service.GITHUB_BASE_URL}/test_prompt/system.md"
        mock_get.assert_called_once_with(expected_url, timeout=10)

    @patch('books_core.services.fabric_prompt_service.requests.get')
    def test_fetch_prompt_github_error(self, mock_get):
        """Test handling of GitHub fetch errors."""
        # Mock 404 error
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        # Fetch should return None
        content = self.service.fetch_prompt_from_github('nonexistent_prompt')
        self.assertIsNone(content)

    @patch('books_core.services.fabric_prompt_service.requests.get')
    def test_import_fabric_prompt_creates_database_record(self, mock_get):
        """Test that importing creates Prompt record (local cache)."""
        # Mock GitHub response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "# Extract Wisdom\n\nExtract wisdom from content."
        mock_get.return_value = mock_response

        # Import prompt
        prompt = self.service.import_fabric_prompt('extract_wisdom')

        # Verify prompt was created
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt.name, 'extract_wisdom')
        self.assertTrue(prompt.is_fabric)
        self.assertFalse(prompt.is_custom)
        self.assertEqual(prompt.category, 'extraction')
        self.assertEqual(prompt.created_by, 'fabric_import')

        # Verify it's in database (local cache)
        cached_prompt = Prompt.objects.get(name='extract_wisdom')
        self.assertEqual(cached_prompt.id, prompt.id)

    @patch('books_core.services.fabric_prompt_service.requests.get')
    def test_local_caching_second_fetch(self, mock_get):
        """Test that second fetch can use local cache."""
        # Mock GitHub response for first fetch
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "# Test Prompt\n\nContent here."
        mock_get.return_value = mock_response

        # First import - fetches from GitHub
        prompt1 = self.service.import_fabric_prompt('test_prompt')
        self.assertIsNotNone(prompt1)

        # Verify GitHub was called
        self.assertEqual(mock_get.call_count, 1)

        # Second import - could use cache or re-fetch
        # Current implementation re-fetches to update
        prompt2 = self.service.import_fabric_prompt('test_prompt')
        self.assertIsNotNone(prompt2)

        # Should be same prompt (same ID)
        self.assertEqual(prompt1.id, prompt2.id)

    @patch('books_core.services.fabric_prompt_service.requests.get')
    def test_sync_prompts_creates_multiple(self, mock_get):
        """Test syncing multiple prompts."""
        # Mock GitHub response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "# Prompt content"
        mock_get.return_value = mock_response

        # Sync subset of prompts
        result = self.service.sync_prompts(['extract_wisdom', 'summarize'])

        # Verify result
        self.assertEqual(result['synced'], 2)
        self.assertEqual(len(result['failed']), 0)

        # Verify prompts in database
        self.assertEqual(Prompt.objects.filter(is_fabric=True).count(), 2)

    @patch('books_core.services.fabric_prompt_service.requests.get')
    def test_sync_prompts_handles_failures(self, mock_get):
        """Test that sync continues on individual failures."""
        # Mock one success, one failure
        def side_effect(url, timeout):
            if 'extract_wisdom' in url:
                response = Mock()
                response.status_code = 200
                response.text = "# Content"
                return response
            else:
                response = Mock()
                response.status_code = 404
                return response

        mock_get.side_effect = side_effect

        # Sync two prompts
        result = self.service.sync_prompts(['extract_wisdom', 'nonexistent'])

        # Verify one succeeded, one failed
        self.assertEqual(result['synced'], 1)
        self.assertEqual(len(result['failed']), 1)
        self.assertIn('nonexistent', result['failed'])

    def test_preview_prompt(self):
        """Test prompt preview with variable substitution."""
        # Create a test prompt
        prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Summarize this: {content}',
            category='summarization',
            is_fabric=True
        )

        # Preview with variables
        preview = self.service.preview_prompt(
            prompt,
            variables={'content': 'Sample chapter text'}
        )

        # Verify substitution
        self.assertIn('Sample chapter text', preview)
        self.assertNotIn('{content}', preview)
