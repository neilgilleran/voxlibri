"""
Book Analysis Service for generating comprehensive AI-powered analysis of entire books.

Handles:
- Content concatenation (excluding front/back matter)
- Cost estimation for 9-prompt batch analysis
- Batch processing with progress tracking
- Version tracking for regeneration
"""

import logging
import time
from typing import Dict, Any, List, Callable
from decimal import Decimal
from datetime import datetime
from django.db import transaction, models, OperationalError
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from django.conf import settings

from books_core.models import Book, Chapter, Prompt, Summary, ProcessingJob
from books_core.services.cost_control_service import CostControlService
from books_core.services.openai_service import OpenAIService
from books_core.exceptions import LimitExceededException

logger = logging.getLogger(__name__)


def get_available_prompts() -> list[str]:
    """
    Get list of available prompt names from the prompts/ directory.
    Falls back to default list if directory doesn't exist.
    """
    prompts_dir = settings.BASE_DIR / 'prompts'
    if prompts_dir.exists():
        return sorted([f.stem for f in prompts_dir.glob('*.md')])
    # Fallback to default list
    return [
        'rate_value',
        'create_summary',
        'extract_ideas',
        'extract_insights',
        'extract_predictions',
        'extract_primary_problem',
        'extract_recommendations',
        'extract_wisdom',
        'rate_content'
    ]


def retry_on_db_lock(func: Callable, max_retries: int = 5, base_delay: float = 0.1):
    """
    Retry a function if it raises OperationalError (database locked).
    Uses exponential backoff: 0.1s, 0.2s, 0.4s, 0.8s, 1.6s
    """
    for attempt in range(max_retries):
        try:
            return func()
        except OperationalError as e:
            if 'database is locked' in str(e).lower() and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Database locked, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                raise
    return func()  # Final attempt without catching


class BookAnalysisService:
    """
    Service for book-level analysis generation.
    Runs prompts from prompts/ directory against concatenated book content.
    """

    @property
    def PROMPT_NAMES(self) -> list[str]:
        """Get available prompt names from filesystem."""
        return get_available_prompts()

    def __init__(self):
        """Initialize services."""
        self.cost_control_service = CostControlService()
        self.openai_service = OpenAIService()
        self.channel_layer = get_channel_layer()

    def concatenate_book_content(self, book: Book) -> str:
        """
        Concatenate all chapter content for book-level analysis.

        Filters out front matter and back matter chapters based on the
        is_front_matter and is_back_matter flags set during EPUB parsing.

        Investigation findings:
        - ContentSplitter DOES mark chapters as front/back matter during parsing
        - Database query shows 5 front matter and 4 back matter chapters exist
        - Decision: EXCLUDE front/back matter to improve analysis quality

        Args:
            book: Book instance

        Returns:
            Concatenated markdown content as single string
        """
        # Query chapters excluding front/back matter, ordered by chapter_number
        chapters = book.chapters.filter(
            is_front_matter=False,
            is_back_matter=False
        ).order_by('chapter_number')

        if not chapters.exists():
            logger.warning(f"Book {book.id} has no content chapters (excluding front/back matter)")
            return ''

        content_parts = []
        for chapter in chapters:
            # Format: # Title\n\nContent\n\n
            title = chapter.title or f"Chapter {chapter.chapter_number}"
            content_parts.append(f"# {title}\n\n{chapter.content}\n\n")

        concatenated = "\n".join(content_parts)

        logger.info(
            f"Concatenated {chapters.count()} chapters for book {book.id} "
            f"(excluded front/back matter), total length: {len(concatenated)} chars"
        )

        return concatenated

    def estimate_cost(self, book: Book, model: str = 'gpt-4o-mini', prompt_names: List[str] = None) -> Dict[str, Any]:
        """
        Estimate cost for generating book analysis.

        Args:
            book: Book instance
            model: AI model to use
            prompt_names: Optional list of prompt names to estimate for (defaults to all prompts)

        Returns:
            Dictionary with cost estimate:
            {
                'input_tokens': int,
                'output_tokens': int,
                'total_tokens': int,
                'estimated_cost_usd': Decimal,
                'per_prompt_cost': Decimal,
                'num_prompts': int,
                'model': str
            }
        """
        # Use provided prompt names or default to all
        prompts_to_estimate = prompt_names if prompt_names is not None else self.PROMPT_NAMES
        num_prompts = len(prompts_to_estimate)

        # Concatenate book content
        content = self.concatenate_book_content(book)

        if not content:
            return {
                'input_tokens': 0,
                'output_tokens': 0,
                'total_tokens': 0,
                'estimated_cost_usd': Decimal('0'),
                'per_prompt_cost': Decimal('0'),
                'num_prompts': num_prompts,
                'model': model
            }

        # Initialize cost control service
        cost_service = CostControlService(model=model)

        # Count tokens in concatenated content
        input_tokens = cost_service.count_tokens(content, model)

        # Estimate output tokens per prompt (typically 1-2k tokens per analysis)
        output_tokens_per_prompt = min(input_tokens // 10, 2000)  # ~10% of input, max 2k

        # Calculate for selected number of prompts
        total_input_tokens = input_tokens * num_prompts
        total_output_tokens = output_tokens_per_prompt * num_prompts

        # Get cost estimate
        cost_estimate = cost_service.estimate_cost(
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            model=model
        )

        # Calculate per-prompt cost
        per_prompt_cost = cost_estimate['estimated_cost_usd'] / num_prompts if num_prompts > 0 else Decimal('0')

        logger.info(
            f"Cost estimate for book {book.id}: {total_input_tokens} input tokens, "
            f"{total_output_tokens} output tokens, ${cost_estimate['estimated_cost_usd']:.6f} total "
            f"({num_prompts} prompts)"
        )

        return {
            'input_tokens': input_tokens,
            'output_tokens': output_tokens_per_prompt,
            'total_tokens': cost_estimate['total_tokens'],
            'estimated_cost_usd': cost_estimate['estimated_cost_usd'],
            'per_prompt_cost': per_prompt_cost,
            'num_prompts': num_prompts,
            'model': model
        }

    def generate_book_analysis(
        self,
        book: Book,
        model: str = 'gpt-4o-mini'
    ) -> Dict[str, Any]:
        """
        Generate comprehensive book analysis by running 9 prompts.

        Creates ProcessingJob and executes batch processing with:
        - Cost limit checking before starting
        - Per-prompt progress updates via WebSocket
        - Individual prompt failure handling
        - Version tracking for regeneration

        Args:
            book: Book instance
            model: AI model to use

        Returns:
            Dictionary with results:
            {
                'job_id': int,
                'total_prompts': int,
                'successful': int,
                'failed': int,
                'summary_ids': List[int]
            }

        Raises:
            LimitExceededException: If cost limits would be exceeded
            Exception: For other errors
        """
        # Concatenate content
        content = self.concatenate_book_content(book)

        if not content:
            raise ValueError(f"Book {book.id} has no content to analyze")

        # Estimate cost and check limits
        cost_estimate = self.estimate_cost(book, model)
        cost_control = CostControlService(model=model)

        # Check limits BEFORE starting batch
        try:
            cost_control.check_limits(cost_estimate['estimated_cost_usd'])
        except LimitExceededException as e:
            logger.warning(f"Cost limit check failed for book {book.id}: {str(e)}")
            raise

        # Create ProcessingJob (with retry on database lock)
        def create_job():
            return ProcessingJob.objects.create(
                book=book,
                job_type='book_analysis',
                status='pending',
                metadata={
                    'model': model,
                    'num_prompts': len(self.PROMPT_NAMES),
                    'prompt_names': self.PROMPT_NAMES,
                    'cost_estimate': str(cost_estimate['estimated_cost_usd'])
                }
            )

        job = retry_on_db_lock(create_job)

        logger.info(f"Created ProcessingJob {job.id} for book {book.id} analysis")

        try:
            # Update job to running (with retry)
            def update_job_running():
                job.status = 'running'
                job.started_at = timezone.now()
                job.save()
                return None

            retry_on_db_lock(update_job_running)

            # Load prompts
            prompts = self._load_prompts()

            # Process each prompt
            results = {
                'successful': [],
                'failed': []
            }

            for index, prompt_name in enumerate(self.PROMPT_NAMES):
                prompt_num = index + 1

                try:
                    # Get prompt
                    prompt = prompts.get(prompt_name)
                    if not prompt:
                        raise ValueError(f"Prompt '{prompt_name}' not found")

                    # Process single prompt
                    result = self._process_single_prompt(
                        book=book,
                        prompt=prompt,
                        content=content,
                        model=model,
                        job=job,
                        prompt_num=prompt_num,
                        total_prompts=len(self.PROMPT_NAMES)
                    )

                    results['successful'].append({
                        'prompt_name': prompt_name,
                        'summary_id': result['summary_id'],
                        'version': result['version']
                    })

                    # Broadcast success
                    progress = int((prompt_num / len(self.PROMPT_NAMES)) * 100)
                    self._broadcast_progress(
                        job_id=job.id,
                        status='success',
                        message=f'Completed {prompt_num}/{len(self.PROMPT_NAMES)}: {prompt_name}',
                        progress=progress,
                        prompt_name=prompt_name
                    )

                except LimitExceededException as e:
                    # Limit exceeded - stop batch
                    error_msg = f"Limit exceeded at prompt {prompt_num}/{len(self.PROMPT_NAMES)}: {str(e)}"
                    logger.error(error_msg)
                    results['failed'].append({
                        'prompt_name': prompt_name,
                        'error': error_msg
                    })

                    # Mark job as failed and stop
                    job.status = 'failed'
                    job.error_message = error_msg
                    job.completed_at = timezone.now()
                    job.save()

                    self._broadcast_progress(
                        job_id=job.id,
                        status='failed',
                        message=error_msg,
                        progress=progress
                    )

                    raise

                except Exception as e:
                    # Individual prompt failure - log and continue
                    error_msg = f"Error processing {prompt_name}: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    results['failed'].append({
                        'prompt_name': prompt_name,
                        'error': error_msg
                    })

                    progress = int((prompt_num / len(self.PROMPT_NAMES)) * 100)
                    self._broadcast_progress(
                        job_id=job.id,
                        status='error',
                        message=error_msg,
                        progress=progress,
                        prompt_name=prompt_name
                    )

            # Mark job as completed (with retry)
            def complete_job():
                job.status = 'completed'
                job.completed_at = timezone.now()
                job.progress_percent = 100
                job.metadata['results'] = results
                job.save()
                return None

            retry_on_db_lock(complete_job)

            # Broadcast completion
            self._broadcast_progress(
                job_id=job.id,
                status='completed',
                message=f'Analysis complete: {len(results["successful"])} succeeded, {len(results["failed"])} failed',
                progress=100
            )

            logger.info(
                f"Book analysis job {job.id} completed: "
                f"{len(results['successful'])} succeeded, {len(results['failed'])} failed"
            )

            return {
                'job_id': job.id,
                'total_prompts': len(self.PROMPT_NAMES),
                'successful': len(results['successful']),
                'failed': len(results['failed']),
                'summary_ids': [r['summary_id'] for r in results['successful']]
            }

        except Exception as e:
            # Fatal error - mark job as failed
            logger.error(f"Fatal error in book analysis job {job.id}: {str(e)}", exc_info=True)

            def fail_job():
                job.status = 'failed'
                job.error_message = str(e)
                job.completed_at = timezone.now()
                job.save()
                return None

            retry_on_db_lock(fail_job)

            self._broadcast_progress(
                job_id=job.id,
                status='failed',
                message=f'Analysis failed: {str(e)}',
                progress=0
            )

            raise

    def generate_book_analysis_with_job(
        self,
        book: Book,
        model: str,
        job_id: int,
        prompt_names: List[str] = None
    ) -> Dict[str, Any]:
        """
        Generate book analysis using an existing ProcessingJob.
        This version is for background thread execution where job is already created.

        Args:
            book: Book instance
            model: AI model to use
            job_id: Existing ProcessingJob ID to update
            prompt_names: Optional list of prompt names to run. If None, runs all default prompts.

        Returns:
            Dictionary with results
        """
        # Get the existing job
        def get_job():
            return ProcessingJob.objects.get(id=job_id)

        job = retry_on_db_lock(get_job)

        # Concatenate content
        content = self.concatenate_book_content(book)

        if not content:
            def fail_job():
                job.status = 'failed'
                job.error_message = "Book has no content to analyze"
                job.completed_at = timezone.now()
                job.save()
                return None
            retry_on_db_lock(fail_job)
            raise ValueError(f"Book {book.id} has no content to analyze")

        try:
            # Update job to running (with retry)
            def update_job_running():
                job.status = 'running'
                job.started_at = timezone.now()
                job.save()
                return None

            retry_on_db_lock(update_job_running)

            # Use provided prompt_names or default to all prompts
            prompts_to_run = prompt_names if prompt_names is not None else self.PROMPT_NAMES

            # Load prompts (pass the names to load)
            prompts = self._load_prompts(prompt_names=prompts_to_run)

            # Process each prompt
            results = {
                'successful': [],
                'failed': []
            }

            for index, prompt_name in enumerate(prompts_to_run):
                prompt_num = index + 1

                try:
                    # Get prompt
                    prompt = prompts.get(prompt_name)
                    if not prompt:
                        raise ValueError(f"Prompt '{prompt_name}' not found")

                    # Process single prompt
                    result = self._process_single_prompt(
                        book=book,
                        prompt=prompt,
                        content=content,
                        model=model,
                        job=job,
                        prompt_num=prompt_num,
                        total_prompts=len(prompts_to_run)
                    )

                    results['successful'].append({
                        'prompt_name': prompt_name,
                        'summary_id': result['summary_id'],
                        'version': result['version']
                    })

                    # Broadcast success
                    progress = int((prompt_num / len(prompts_to_run)) * 100)
                    self._broadcast_progress(
                        job_id=job.id,
                        status='success',
                        message=f'Completed {prompt_num}/{len(prompts_to_run)}: {prompt_name}',
                        progress=progress,
                        prompt_name=prompt_name
                    )

                except LimitExceededException as e:
                    # Limit exceeded - stop batch
                    error_msg = f"Limit exceeded at prompt {prompt_num}/{len(prompts_to_run)}: {str(e)}"
                    logger.error(error_msg)
                    results['failed'].append({
                        'prompt_name': prompt_name,
                        'error': error_msg
                    })

                    def fail_job_limit():
                        job.status = 'failed'
                        job.error_message = error_msg
                        job.completed_at = timezone.now()
                        job.save()
                        return None

                    retry_on_db_lock(fail_job_limit)

                    self._broadcast_progress(
                        job_id=job.id,
                        status='failed',
                        message=error_msg,
                        progress=0
                    )

                    raise

                except Exception as e:
                    # Individual prompt failure - log and continue
                    error_msg = f"Error processing {prompt_name}: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    results['failed'].append({
                        'prompt_name': prompt_name,
                        'error': str(e)
                    })

                    progress = int((prompt_num / len(self.PROMPT_NAMES)) * 100)
                    self._broadcast_progress(
                        job_id=job.id,
                        status='error',
                        message=error_msg,
                        progress=progress,
                        prompt_name=prompt_name
                    )

            # Mark job as completed (with retry)
            def complete_job():
                job.status = 'completed'
                job.completed_at = timezone.now()
                job.progress_percent = 100
                job.metadata['results'] = results
                job.save()
                return None

            retry_on_db_lock(complete_job)

            # Broadcast completion
            self._broadcast_progress(
                job_id=job.id,
                status='completed',
                message=f'Analysis complete: {len(results["successful"])} succeeded, {len(results["failed"])} failed',
                progress=100
            )

            logger.info(
                f"Book analysis job {job.id} completed: "
                f"{len(results['successful'])} succeeded, {len(results['failed'])} failed"
            )

            return {
                'job_id': job.id,
                'total_prompts': len(self.PROMPT_NAMES),
                'successful': len(results['successful']),
                'failed': len(results['failed']),
                'summary_ids': [r['summary_id'] for r in results['successful']]
            }

        except Exception as e:
            # Fatal error - mark job as failed
            logger.error(f"Fatal error in book analysis job {job.id}: {str(e)}", exc_info=True)

            def fail_job():
                job.status = 'failed'
                job.error_message = str(e)
                job.completed_at = timezone.now()
                job.save()
                return None

            retry_on_db_lock(fail_job)

            self._broadcast_progress(
                job_id=job.id,
                status='failed',
                message=f'Analysis failed: {str(e)}',
                progress=0
            )

            raise

    def _load_prompts(self, prompt_names: List[str] = None) -> Dict[str, Prompt]:
        """
        Load all required prompts from database.

        Args:
            prompt_names: Optional list of prompt names to load. If None, loads default prompts.

        Returns:
            Dictionary mapping prompt name to Prompt instance
        """
        # Use provided prompt names or default to PROMPT_NAMES
        names_to_load = prompt_names if prompt_names is not None else self.PROMPT_NAMES

        prompts = Prompt.objects.filter(name__in=names_to_load)
        prompt_dict = {p.name: p for p in prompts}

        # Check for missing prompts
        missing = set(names_to_load) - set(prompt_dict.keys())
        if missing:
            logger.warning(f"Missing prompts: {missing}")

        return prompt_dict

    @transaction.atomic
    def _process_single_prompt(
        self,
        book: Book,
        prompt: Prompt,
        content: str,
        model: str,
        job: ProcessingJob,
        prompt_num: int,
        total_prompts: int
    ) -> Dict[str, Any]:
        """
        Process a single prompt for book analysis.

        Args:
            book: Book instance
            prompt: Prompt instance
            content: Concatenated book content
            model: AI model to use
            job: ProcessingJob instance
            prompt_num: Current prompt number (1-indexed)
            total_prompts: Total number of prompts

        Returns:
            Dictionary with summary_id and version

        Raises:
            LimitExceededException: If limits are exceeded
            Exception: For other errors
        """
        # Broadcast processing status
        self._broadcast_progress(
            job_id=job.id,
            status='processing',
            message=f'Processing {prompt_num}/{total_prompts}: {prompt.name}',
            progress=int(((prompt_num - 1) / total_prompts) * 100),
            prompt_name=prompt.name
        )

        # Check for previous versions to avoid duplicate results
        previous_results_context = ""
        previous_summaries = Summary.objects.filter(
            book=book,
            prompt=prompt
        ).order_by('-version')[:3]  # Get up to 3 most recent versions

        if previous_summaries.exists():
            previous_content = []

            # For extraction prompts (anecdotes, insights, etc.), send FULL content
            # For summary/rating prompts, send preview only
            is_extraction_prompt = prompt.category == 'extraction'

            for prev_summary in previous_summaries:
                prev_text = prev_summary.content_json.get('content', '')
                if prev_text:
                    if is_extraction_prompt:
                        # Send FULL content so AI can see exactly what was already extracted
                        previous_content.append(
                            f"=== VERSION {prev_summary.version} (ALREADY EXTRACTED - DO NOT REPEAT) ===\n{prev_text}"
                        )
                    else:
                        # For summaries/ratings, preview is enough
                        preview = prev_text[:500] + '...' if len(prev_text) > 500 else prev_text
                        previous_content.append(f"Version {prev_summary.version}: {preview}")

            if previous_content:
                if is_extraction_prompt:
                    previous_results_context = (
                        "\n\n" + "="*80 + "\n"
                        "CRITICAL INSTRUCTION: The following content has ALREADY been extracted in previous versions.\n"
                        "You MUST find COMPLETELY DIFFERENT material that is NOT listed below.\n"
                        "Do NOT repeat, paraphrase, or slightly modify any of the content below.\n"
                        "If you cannot find new material, state that explicitly.\n"
                        + "="*80 + "\n\n"
                        + "\n\n".join(previous_content) +
                        "\n\n" + "="*80 + "\n"
                        "REMINDER: Find DIFFERENT material not shown above.\n"
                        + "="*80 + "\n\n"
                    )
                else:
                    previous_results_context = (
                        "\n\n---\n\n"
                        "IMPORTANT: Previous versions of this analysis already exist. "
                        "Please provide DIFFERENT insights, examples, or anecdotes than those below. "
                        "Find new material not covered in previous versions:\n\n"
                        + "\n\n".join(previous_content) +
                        "\n\n---\n\n"
                    )

        # Render prompt with content and previous results context
        prompt_text = prompt.render_template({'content': content})

        # Add previous results warning if this is a re-run
        if previous_results_context:
            # Insert before the content (after the prompt template, before the appended content)
            prompt_text = prompt_text.replace(content, previous_results_context + content, 1)

        # Initialize cost control
        cost_service = CostControlService(model=model)

        # Call OpenAI with cost control
        start_time = timezone.now()
        response = self.openai_service.complete_with_cost_control(
            prompt=prompt_text,
            model=model,
            cost_service=cost_service,
            max_tokens=2000  # Limit output tokens per prompt
        )
        end_time = timezone.now()
        processing_time_ms = int((end_time - start_time).total_seconds() * 1000)

        # Get next version number
        max_version = Summary.objects.filter(
            book=book,
            prompt=prompt
        ).aggregate(models.Max('version'))['version__max'] or 0
        next_version = max_version + 1

        # Get previous version for linking
        previous_version = None
        if max_version > 0:
            previous_version = Summary.objects.filter(
                book=book,
                prompt=prompt,
                version=max_version
            ).first()

        # Create Summary (with retry on database lock)
        def create_summary():
            return Summary.objects.create(
                book=book,
                chapter=None,
                prompt=prompt,
                summary_type='analysis',
                content_json={'content': response['content']},
                tokens_used=response['tokens_used'],
                model_used=model,
                processing_time_ms=processing_time_ms,
                version=next_version,
                previous_version=previous_version,
                estimated_cost_usd=Decimal(response['actual_cost_usd'])
            )

        summary = retry_on_db_lock(create_summary)

        logger.info(
            f"Created summary {summary.id} for book {book.id}, prompt {prompt.name}, "
            f"version {next_version}, tokens: {response['tokens_used']}"
        )

        return {
            'summary_id': summary.id,
            'version': next_version
        }

    def _broadcast_progress(
        self,
        job_id: int,
        status: str,
        message: str,
        progress: int = None,
        prompt_name: str = None
    ) -> None:
        """
        Broadcast progress update via WebSocket channel layer.

        Args:
            job_id: ProcessingJob ID
            status: Status ('processing', 'success', 'error', 'completed', 'failed')
            message: Status message
            progress: Progress percentage (0-100)
            prompt_name: Current prompt name
        """
        group_name = f'book_analysis_{job_id}'

        message_data = {
            'type': 'progress',
            'status': status,
            'message': message,
            'timestamp': timezone.now().isoformat()
        }

        if progress is not None:
            message_data['progress'] = progress

        if prompt_name:
            message_data['prompt_name'] = prompt_name

        try:
            async_to_sync(self.channel_layer.group_send)(
                group_name,
                {
                    'type': 'book_analysis_progress',
                    'message': message_data
                }
            )
            logger.debug(f"Broadcast progress for job {job_id}: {message_data}")
        except Exception as e:
            # Log error but don't fail the batch
            logger.error(f"Failed to broadcast progress for job {job_id}: {str(e)}")

    def get_latest_summaries(self, book: Book) -> Dict[str, Summary]:
        """
        Get latest version of all summaries for a book.

        Args:
            book: Book instance

        Returns:
            Dictionary mapping prompt name to latest Summary instance
        """
        from django.db import models

        # Get ALL summaries for the book, not just the default prompts
        summaries = Summary.objects.filter(
            book=book
        ).select_related('prompt').order_by('prompt__name', '-version')

        # Get latest version for each prompt
        latest_summaries = {}
        for summary in summaries:
            prompt_name = summary.prompt.name
            if prompt_name not in latest_summaries:
                latest_summaries[prompt_name] = summary

        return latest_summaries
