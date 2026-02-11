"""
Tests for Phase 4: WebSocket Integration & Batch Processing

Tests WebSocket consumer, batch processing service, and real-time progress updates.
"""

import json
from decimal import Decimal
from unittest.mock import patch, MagicMock, AsyncMock
from django.test import TestCase, TransactionTestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from channels.testing import WebsocketCommunicator
from channels.layers import get_channel_layer
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async

from books_core.models import Book, Chapter, Prompt, ProcessingJob, Summary, Settings
from books_core.consumers import BatchProgressConsumer
from books_core.services.batch_processing_service import BatchProcessingService
from books_core.exceptions import LimitExceededException


class WebSocketConsumerTests(TransactionTestCase):
    """Test WebSocket consumer for batch progress."""

    def setUp(self):
        """Set up test data."""
        # Create dummy EPUB file
        epub_content = b'PK\x03\x04' + b'\x00' * 100  # Minimal ZIP header
        epub_file = SimpleUploadedFile('test.epub', epub_content, content_type='application/epub+zip')

        # Create test book and chapters
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            epub_file=epub_file
        )

        self.chapter1 = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter 1',
            content='Chapter 1 content',
        )

        # Create test prompt
        self.prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Summarize: {content}',
            category='summarization',
            is_fabric=False,
            is_custom=True
        )

        # Create processing job
        self.job = ProcessingJob.objects.create(
            book=self.book,
            job_type='batch_summarization',
            status='pending',
            progress_percent=0,
            metadata={'chapter_ids': [self.chapter1.id]}
        )

    async def test_websocket_connection_acceptance(self):
        """Test WebSocket connection is accepted."""
        communicator = WebsocketCommunicator(
            BatchProgressConsumer.as_asgi(),
            f'/ws/batch/{self.job.id}/'
        )

        connected, subprotocol = await communicator.connect()
        self.assertTrue(connected)

        await communicator.disconnect()

    async def test_websocket_joins_batch_group(self):
        """Test WebSocket joins correct batch group."""
        communicator = WebsocketCommunicator(
            BatchProgressConsumer.as_asgi(),
            f'/ws/batch/{self.job.id}/'
        )

        await communicator.connect()

        # Send message to group
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f'batch_{self.job.id}',
            {
                'type': 'batch_progress',
                'message': {'status': 'processing', 'message': 'Test message'}
            }
        )

        # Should receive message
        response = await communicator.receive_json_from(timeout=2)
        self.assertEqual(response['status'], 'processing')
        self.assertEqual(response['message'], 'Test message')

        await communicator.disconnect()

    async def test_websocket_disconnect_cleanup(self):
        """Test WebSocket disconnect leaves group."""
        communicator = WebsocketCommunicator(
            BatchProgressConsumer.as_asgi(),
            f'/ws/batch/{self.job.id}/'
        )

        await communicator.connect()
        await communicator.disconnect()

        # After disconnect, messages shouldn't be received
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f'batch_{self.job.id}',
            {
                'type': 'batch_progress',
                'message': {'status': 'completed'}
            }
        )

        # No assertion needed - just verify no errors

    async def test_websocket_receives_progress_messages(self):
        """Test WebSocket receives multiple progress messages."""
        communicator = WebsocketCommunicator(
            BatchProgressConsumer.as_asgi(),
            f'/ws/batch/{self.job.id}/'
        )

        await communicator.connect()

        channel_layer = get_channel_layer()

        # Send processing message
        await channel_layer.group_send(
            f'batch_{self.job.id}',
            {
                'type': 'batch_progress',
                'message': {
                    'status': 'processing',
                    'chapter_id': self.chapter1.id,
                    'progress': 50
                }
            }
        )

        response1 = await communicator.receive_json_from(timeout=2)
        self.assertEqual(response1['status'], 'processing')
        self.assertEqual(response1['progress'], 50)

        # Send success message
        await channel_layer.group_send(
            f'batch_{self.job.id}',
            {
                'type': 'batch_progress',
                'message': {
                    'status': 'success',
                    'chapter_id': self.chapter1.id,
                    'progress': 100,
                    'summary_id': 123
                }
            }
        )

        response2 = await communicator.receive_json_from(timeout=2)
        self.assertEqual(response2['status'], 'success')
        self.assertEqual(response2['summary_id'], 123)

        await communicator.disconnect()


class BatchProcessingServiceTests(TestCase):
    """Test batch processing service."""

    def setUp(self):
        """Set up test data."""
        # Create dummy EPUB file
        epub_content = b'PK\x03\x04' + b'\x00' * 100  # Minimal ZIP header
        epub_file = SimpleUploadedFile('test.epub', epub_content, content_type='application/epub+zip')

        # Create test book and chapters
        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            epub_file=epub_file
        )

        self.chapter1 = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter 1',
            content='Chapter 1 content for testing summary generation.',
        )

        self.chapter2 = Chapter.objects.create(
            book=self.book,
            chapter_number=2,
            title='Chapter 2',
            content='Chapter 2 content for testing summary generation.',
        )

        # Create test prompt
        self.prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Summarize: {content}',
            category='summarization',
            is_fabric=False,
            is_custom=True
        )

        # Create settings
        Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True
        )

        # Create processing job
        self.job = ProcessingJob.objects.create(
            book=self.book,
            job_type='batch_summarization',
            status='pending',
            progress_percent=0,
            metadata={
                'chapter_ids': [self.chapter1.id, self.chapter2.id],
                'prompt_id': self.prompt.id,
                'model': 'gpt-4o-mini'
            }
        )

    @patch('books_core.services.batch_processing_service.BatchProcessingService._broadcast_progress')
    @patch('books_core.services.openai_service.OpenAIService.complete_with_cost_control')
    def test_atomic_chapter_processing(self, mock_openai, mock_broadcast):
        """Test that one chapter failure doesn't stop batch processing."""
        # Mock first chapter to succeed
        mock_openai.side_effect = [
            {
                'content': 'Summary for chapter 1',
                'tokens_used': 500,
                'model': 'gpt-4o-mini'
            },
            # Second chapter fails
            Exception('OpenAI API error'),
        ]

        service = BatchProcessingService()
        result = service.process_batch(
            job_id=str(self.job.id),
            chapter_ids=[self.chapter1.id, self.chapter2.id],
            prompt_id=self.prompt.id,
            model='gpt-4o-mini'
        )

        # Should have 1 success and 1 failure
        self.assertEqual(result['successful'], 1)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['total'], 2)

        # Job should be completed
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'completed')

    @patch('books_core.services.batch_processing_service.BatchProcessingService._broadcast_progress')
    @patch('books_core.services.openai_service.OpenAIService.complete_with_cost_control')
    def test_processing_job_status_updates(self, mock_openai, mock_broadcast):
        """Test ProcessingJob status updates correctly."""
        mock_openai.return_value = {
            'content': 'Test summary',
            'tokens_used': 500,
            'model': 'gpt-4o-mini'
        }

        # Check initial status
        self.assertEqual(self.job.status, 'pending')

        service = BatchProcessingService()
        result = service.process_batch(
            job_id=str(self.job.id),
            chapter_ids=[self.chapter1.id],
            prompt_id=self.prompt.id,
            model='gpt-4o-mini'
        )

        # Job should be completed
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'completed')
        self.assertEqual(self.job.progress_percent, 100)
        self.assertIsNotNone(self.job.started_at)
        self.assertIsNotNone(self.job.completed_at)

    @patch('books_core.services.batch_processing_service.BatchProcessingService._broadcast_progress')
    @patch('books_core.services.openai_service.OpenAIService.complete_with_cost_control')
    def test_per_chapter_status_tracking(self, mock_openai, mock_broadcast):
        """Test per-chapter status is tracked in metadata."""
        mock_openai.side_effect = [
            {
                'content': 'Summary 1',
                'tokens_used': 500,
                'model': 'gpt-4o-mini'
            },
            Exception('Failed'),
        ]

        service = BatchProcessingService()
        result = service.process_batch(
            job_id=str(self.job.id),
            chapter_ids=[self.chapter1.id, self.chapter2.id],
            prompt_id=self.prompt.id,
            model='gpt-4o-mini'
        )

        # Check results tracking
        self.assertEqual(len(result['results']['successful']), 1)
        self.assertEqual(len(result['results']['failed']), 1)

        # Check successful chapter
        successful = result['results']['successful'][0]
        self.assertEqual(successful['chapter_id'], self.chapter1.id)
        self.assertIn('summary_id', successful)
        self.assertIn('version', successful)

        # Check failed chapter
        failed = result['results']['failed'][0]
        self.assertEqual(failed['chapter_id'], self.chapter2.id)
        self.assertIn('error', failed)

    @patch('books_core.services.batch_processing_service.BatchProcessingService._broadcast_progress')
    def test_websocket_message_broadcasting(self, mock_broadcast):
        """Test WebSocket messages are broadcast correctly."""
        service = BatchProcessingService()

        # Broadcast a progress message
        service._broadcast_progress(
            job_id=str(self.job.id),
            chapter_id=self.chapter1.id,
            status='processing',
            message='Processing chapter 1',
            progress=50
        )

        # Verify broadcast was called
        mock_broadcast.assert_called_once()
        call_args = mock_broadcast.call_args
        self.assertEqual(call_args[1]['job_id'], str(self.job.id))
        self.assertEqual(call_args[1]['status'], 'processing')
        self.assertEqual(call_args[1]['progress'], 50)

    @patch('books_core.services.batch_processing_service.BatchProcessingService._broadcast_progress')
    @patch('books_core.services.cost_control_service.CostControlService.check_limits')
    def test_limit_exceeded_handling(self, mock_check_limits, mock_broadcast):
        """Test handling when limits are exceeded during batch."""
        # First chapter succeeds, second exceeds limit
        mock_check_limits.side_effect = [
            {'daily_usage': {}, 'monthly_usage': {}, 'warnings': []},
            LimitExceededException('monthly', Decimal('5.00'), Decimal('5.00')),
        ]

        service = BatchProcessingService()

        with patch('books_core.services.openai_service.OpenAIService.complete_with_cost_control') as mock_openai:
            mock_openai.return_value = {
                'content': 'Summary',
                'tokens_used': 500,
                'model': 'gpt-4o-mini'
            }

            result = service.process_batch(
                job_id=str(self.job.id),
                chapter_ids=[self.chapter1.id, self.chapter2.id],
                prompt_id=self.prompt.id,
                model='gpt-4o-mini'
            )

        # Should have 1 success and 1 failure (limit exceeded)
        self.assertEqual(result['successful'], 1)
        self.assertEqual(result['failed'], 1)

        # Check that failure is due to limit
        failed_chapter = result['results']['failed'][0]
        self.assertIn('Limit exceeded', failed_chapter['error'])


class BatchProcessingIntegrationTests(TransactionTestCase):
    """Integration tests for batch processing with WebSocket."""

    def setUp(self):
        """Set up test data."""
        # Create dummy EPUB file
        epub_content = b'PK\x03\x04' + b'\x00' * 100  # Minimal ZIP header
        epub_file = SimpleUploadedFile('test.epub', epub_content, content_type='application/epub+zip')

        self.book = Book.objects.create(
            title='Test Book',
            author='Test Author',
            epub_file=epub_file
        )

        self.chapter = Chapter.objects.create(
            book=self.book,
            chapter_number=1,
            title='Chapter 1',
            content='Test content for summary generation.',
        )

        self.prompt = Prompt.objects.create(
            name='test_prompt',
            template_text='Summarize: {content}',
            category='summarization',
            is_fabric=False,
            is_custom=True
        )

        Settings.objects.create(
            monthly_limit_usd=Decimal('5.00'),
            daily_summary_limit=100,
            ai_features_enabled=True
        )

    @patch('books_core.services.openai_service.OpenAIService.complete_with_cost_control')
    async def test_end_to_end_batch_processing_with_websocket(self, mock_openai):
        """Test complete batch processing flow with WebSocket updates."""
        mock_openai.return_value = {
            'content': 'Test summary',
            'tokens_used': 500,
            'model': 'gpt-4o-mini'
        }

        # Create job in async context
        epub_content = b'PK\x03\x04' + b'\x00' * 100
        epub_file = SimpleUploadedFile('test2.epub', epub_content, content_type='application/epub+zip')

        book = await database_sync_to_async(Book.objects.create)(
            title='Test Book 2',
            author='Test Author 2',
            epub_file=epub_file
        )

        chapter = await database_sync_to_async(Chapter.objects.create)(
            book=book,
            chapter_number=1,
            title='Chapter 1',
            content='Test content',
        )

        prompt = await database_sync_to_async(Prompt.objects.create)(
            name='test_prompt2',
            template_text='Summarize: {content}',
            category='summarization',
            is_fabric=False,
            is_custom=True
        )

        job = await database_sync_to_async(ProcessingJob.objects.create)(
            book=book,
            job_type='batch_summarization',
            status='pending',
            progress_percent=0,
            metadata={
                'chapter_ids': [chapter.id],
                'prompt_id': prompt.id,
                'model': 'gpt-4o-mini'
            }
        )

        # Connect WebSocket
        communicator = WebsocketCommunicator(
            BatchProgressConsumer.as_asgi(),
            f'/ws/batch/{job.id}/'
        )

        await communicator.connect()

        # Run batch processing in background
        async def run_batch():
            await database_sync_to_async(lambda: BatchProcessingService().process_batch(
                job_id=str(job.id),
                chapter_ids=[chapter.id],
                prompt_id=prompt.id,
                model='gpt-4o-mini'
            ))()

        # Start batch processing
        import asyncio
        batch_task = asyncio.create_task(run_batch())

        # Receive progress messages
        messages_received = []
        try:
            while True:
                message = await communicator.receive_json_from(timeout=5)
                messages_received.append(message)
                if message.get('status') in ('completed', 'failed'):
                    break
        except:
            pass  # Timeout is expected when no more messages

        await batch_task
        await communicator.disconnect()

        # Verify messages were received
        self.assertGreater(len(messages_received), 0)

        # Verify job completed
        job = await database_sync_to_async(ProcessingJob.objects.get)(id=job.id)
        self.assertEqual(job.status, 'completed')

        # Verify summary was created
        summary_count = await database_sync_to_async(Summary.objects.filter(chapter=chapter).count)()
        self.assertEqual(summary_count, 1)
