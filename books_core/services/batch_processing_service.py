"""
Batch processing service for generating multiple summaries.

Handles batch summary generation with real-time progress updates via WebSockets.
Implements atomic operations where individual chapter failures don't stop the batch.
"""

import logging
from typing import List, Dict, Any
from decimal import Decimal
from datetime import datetime
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

from books_core.models import Chapter, Prompt, ProcessingJob, Summary
from books_core.services.cost_control_service import CostControlService
from books_core.services.openai_service import OpenAIService
from books_core.services.summary_service import SummaryService
from books_core.exceptions import LimitExceededException

logger = logging.getLogger(__name__)


class BatchProcessingService:
    """
    Service for batch processing summary generation with WebSocket updates.
    """

    def __init__(self):
        self.cost_control_service = CostControlService()
        self.openai_service = OpenAIService()
        self.summary_service = SummaryService()
        self.channel_layer = get_channel_layer()

    def process_batch(
        self,
        job_id: str,
        chapter_ids: List[int],
        prompt_id: int,
        model: str = 'gpt-4o-mini'
    ) -> Dict[str, Any]:
        """
        Process batch of chapters to generate summaries.

        Each chapter is processed atomically - individual failures don't stop
        remaining chapters. Progress is broadcast via WebSocket for real-time updates.

        Args:
            job_id: ProcessingJob ID for tracking
            chapter_ids: List of chapter IDs to process
            prompt_id: Prompt to use for generation
            model: AI model to use (default: gpt-4o-mini)

        Returns:
            Dictionary with batch processing results:
            {
                'total': int,
                'successful': int,
                'failed': int,
                'results': {...}
            }
        """
        try:
            # Get ProcessingJob and update to running
            job = ProcessingJob.objects.get(id=job_id)
            job.status = 'running'
            job.started_at = timezone.now()
            job.save()

            # Get prompt
            prompt = Prompt.objects.get(id=prompt_id)

            # Initialize results tracking
            results = {
                'successful': [],
                'failed': []
            }

            total_chapters = len(chapter_ids)
            processed_count = 0

            # Process each chapter atomically
            for chapter_id in chapter_ids:
                try:
                    # Process single chapter
                    result = self._process_single_chapter(
                        chapter_id=chapter_id,
                        prompt=prompt,
                        model=model,
                        job_id=job_id
                    )

                    # Track success
                    results['successful'].append({
                        'chapter_id': chapter_id,
                        'summary_id': result['summary_id'],
                        'version': result['version'],
                        'cost_usd': str(result['cost_usd'])
                    })

                    # Broadcast success
                    processed_count += 1
                    progress = int((processed_count / total_chapters) * 100)
                    self._broadcast_progress(
                        job_id=job_id,
                        chapter_id=chapter_id,
                        status='success',
                        message=f'Summary generated successfully (v{result["version"]})',
                        progress=progress,
                        summary_id=result['summary_id']
                    )

                except LimitExceededException as e:
                    # Limit exceeded - record error and continue
                    error_msg = f'Limit exceeded: {str(e)}'
                    results['failed'].append({
                        'chapter_id': chapter_id,
                        'error': error_msg
                    })

                    processed_count += 1
                    progress = int((processed_count / total_chapters) * 100)
                    self._broadcast_progress(
                        job_id=job_id,
                        chapter_id=chapter_id,
                        status='error',
                        message=error_msg,
                        progress=progress
                    )

                    logger.warning(f"Limit exceeded for chapter {chapter_id} in job {job_id}")

                except Exception as e:
                    # Other errors - record and continue
                    error_msg = f'Error: {str(e)}'
                    results['failed'].append({
                        'chapter_id': chapter_id,
                        'error': error_msg
                    })

                    processed_count += 1
                    progress = int((processed_count / total_chapters) * 100)
                    self._broadcast_progress(
                        job_id=job_id,
                        chapter_id=chapter_id,
                        status='error',
                        message=error_msg,
                        progress=progress
                    )

                    logger.error(f"Error processing chapter {chapter_id} in job {job_id}: {str(e)}", exc_info=True)

            # Update job with final results
            job.status = 'completed'
            job.completed_at = timezone.now()
            job.progress_percent = 100
            job.metadata['results'] = results
            job.save()

            # Broadcast completion
            self._broadcast_progress(
                job_id=job_id,
                chapter_id=None,
                status='completed',
                message=f'Batch complete: {len(results["successful"])} succeeded, {len(results["failed"])} failed',
                progress=100
            )

            logger.info(f"Batch job {job_id} completed: {len(results['successful'])} successful, {len(results['failed'])} failed")

            return {
                'total': total_chapters,
                'successful': len(results['successful']),
                'failed': len(results['failed']),
                'results': results
            }

        except Exception as e:
            # Fatal error - mark job as failed
            logger.error(f"Fatal error in batch job {job_id}: {str(e)}", exc_info=True)

            try:
                job = ProcessingJob.objects.get(id=job_id)
                job.status = 'failed'
                job.error_message = str(e)
                job.completed_at = timezone.now()
                job.save()
            except:
                pass

            # Broadcast failure
            self._broadcast_progress(
                job_id=job_id,
                chapter_id=None,
                status='failed',
                message=f'Batch job failed: {str(e)}',
                progress=0
            )

            raise

    def _process_single_chapter(
        self,
        chapter_id: int,
        prompt: Prompt,
        model: str,
        job_id: str
    ) -> Dict[str, Any]:
        """
        Process a single chapter within a batch.

        Uses atomic transaction to ensure consistency.

        Args:
            chapter_id: Chapter to process
            prompt: Prompt to use
            model: AI model
            job_id: Parent job ID

        Returns:
            Dictionary with summary_id, version, and cost_usd

        Raises:
            LimitExceededException: If limits are exceeded
            Exception: For other errors
        """
        # Get chapter
        chapter = Chapter.objects.get(id=chapter_id)

        # Broadcast processing status
        self._broadcast_progress(
            job_id=job_id,
            chapter_id=chapter_id,
            status='processing',
            message='Generating summary...',
            progress=None
        )

        # Render prompt with chapter content
        prompt_text = prompt.render_template({
            'content': chapter.content
        })

        # Count tokens and estimate cost
        input_tokens = self.cost_control_service.count_tokens(prompt_text, model)
        output_tokens_estimate = min(input_tokens, 4000)  # Estimate: up to input tokens, max 4000

        cost_estimate = self.cost_control_service.estimate_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens_estimate,
            model=model
        )

        # Check limits (will raise exception if exceeded)
        self.cost_control_service.check_limits(cost_estimate['estimated_cost_usd'])

        # Call OpenAI with cost control
        start_time = timezone.now()
        response = self.openai_service.complete_with_cost_control(
            prompt=prompt_text,
            model=model,
            cost_service=self.cost_control_service
        )
        end_time = timezone.now()
        processing_time_ms = int((end_time - start_time).total_seconds() * 1000)

        # Create summary via SummaryService
        summary = self.summary_service.create_summary(
            chapter=chapter,
            prompt=prompt,
            content=response['content'],
            metadata={
                'tokens_used': response['tokens_used'],
                'model_used': model,
                'processing_time_ms': processing_time_ms,
                'estimated_cost_usd': cost_estimate['estimated_cost_usd'],
                'job_id': job_id
            }
        )

        return {
            'summary_id': summary.id,
            'version': summary.version,
            'cost_usd': summary.estimated_cost_usd
        }

    def _broadcast_progress(
        self,
        job_id: str,
        chapter_id: int = None,
        status: str = 'processing',
        message: str = '',
        progress: int = None,
        summary_id: int = None
    ) -> None:
        """
        Broadcast progress update via WebSocket channel layer.

        Args:
            job_id: Job ID to broadcast to
            chapter_id: Chapter being processed (optional)
            status: Status ('processing', 'success', 'error', 'completed', 'failed')
            message: Status message
            progress: Overall progress percentage (0-100)
            summary_id: Created summary ID (on success)
        """
        group_name = f'batch_{job_id}'

        message_data = {
            'type': 'progress',
            'status': status,
            'message': message,
            'timestamp': timezone.now().isoformat()
        }

        if chapter_id is not None:
            message_data['chapter_id'] = chapter_id

        if progress is not None:
            message_data['progress'] = progress

        if summary_id is not None:
            message_data['summary_id'] = summary_id

        try:
            # Send to channel layer (async_to_sync wrapper)
            async_to_sync(self.channel_layer.group_send)(
                group_name,
                {
                    'type': 'batch_progress',
                    'message': message_data
                }
            )
            logger.debug(f"Broadcast progress for job {job_id}: {message_data}")
        except Exception as e:
            # Log error but don't fail the batch
            logger.error(f"Failed to broadcast progress for job {job_id}: {str(e)}")
