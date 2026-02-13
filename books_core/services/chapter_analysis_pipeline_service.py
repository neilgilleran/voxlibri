"""
ChapterAnalysisPipelineService - Automated chapter-by-chapter book analysis.

Runs four prompts per chapter:
- rate_chapter: JSON scores for insight, clarity, evidence, engagement, actionability
- summarize_chapter: Short summary for book-level synthesis
- extract_chapter_wisdom: Stories, advice, insights, quotes (full narratives preserved)
- extract_references: Books, people, resources mentioned

Then aggregates results into book-level summaries:
- aggregate_book_rating: Overall verdict from chapter ratings (AI)
- aggregate_summaries: Book thesis synthesized from chapter summaries (AI)
- Wisdom: All chapter wisdom grouped by type - stories, advice, quotes (concatenation, no AI)
- References: All chapter references combined (concatenation, no AI)
"""

import json
import logging
import time
from decimal import Decimal
from typing import Dict, Any, List, Optional

from django.db import transaction
from django.db.models import Max
from django_q.tasks import async_task

from books_core.models import Book, Chapter, Prompt, Summary, ProcessingJob
from books_core.services.openai_service import OpenAIService
from books_core.services.cost_control_service import CostControlService
from books_core.services.summary_service import SummaryService

logger = logging.getLogger(__name__)


class ChapterAnalysisPipelineService:
    """
    Automated pipeline for chapter-by-chapter book analysis.

    Runs per chapter:
    - rate_chapter: JSON scores for insight, clarity, evidence, engagement, actionability
    - extract_chapter_wisdom: Stories, advice, insights, quotes (full narratives preserved)
    - extract_references: Books, people, resources mentioned

    Then aggregates into book-level summaries with AI-generated verdicts.
    """

    CHAPTER_PROMPTS = ['rate_chapter', 'summarize_chapter']
    EXTRACTION_PROMPTS = ['extract_chapter_wisdom', 'extract_references']
    AGGREGATE_PROMPT = 'aggregate_book_rating'

    # AI aggregation: chapter summaries → book thesis
    AI_AGGREGATE_PROMPTS = {
        'summarize_chapter': 'aggregate_summaries',
    }

    # Concatenation only (no AI): wisdom and references just get combined
    CONCAT_ONLY_EXTRACTIONS = ['extract_chapter_wisdom', 'extract_references']

    MIN_CHAPTER_WORDS = 500  # Skip tiny chapter fragments

    def __init__(self, model: str = 'gpt-4o-mini'):
        self.model = model
        self.cost_service = CostControlService(model=model)
        self.summary_service = SummaryService()

    def estimate_pipeline_cost(self, book: Book, model: str = None) -> Dict[str, Any]:
        """
        Estimate total cost for running the full pipeline on a book.

        Returns:
            Dict with cost breakdown and chapter counts
        """
        model = model or self.model

        # Get content chapters (exclude front/back matter and tiny fragments)
        chapters = book.chapters.filter(
            is_front_matter=False,
            is_back_matter=False,
            word_count__gte=self.MIN_CHAPTER_WORDS
        ).order_by('chapter_number')

        chapter_count = chapters.count()
        if chapter_count == 0:
            return {
                'chapter_count': 0,
                'total_prompts': 0,
                'estimated_tokens': 0,
                'estimated_cost_usd': '0.00',
                'error': 'No content chapters found'
            }

        # Calculate total tokens across all chapters
        # Each chapter runs: CHAPTER_PROMPTS (rate + summarize) + EXTRACTION_PROMPTS (wisdom + refs)
        prompts_per_chapter = len(self.CHAPTER_PROMPTS) + len(self.EXTRACTION_PROMPTS)
        total_input_tokens = 0
        for chapter in chapters:
            chapter_tokens = self.cost_service.count_tokens(chapter.content, model)
            total_input_tokens += chapter_tokens * prompts_per_chapter

        # Estimate output tokens per chapter
        # rate: 200, summarize: 150, wisdom: 800 (stories+advice+insights), references: 300
        estimated_output_tokens = chapter_count * (200 + 150 + 800 + 300)

        # AI Aggregation input: chapter ratings + chapter summaries
        aggregate_input_estimate = chapter_count * 100  # rating verdicts
        aggregate_input_estimate += chapter_count * 150  # chapter summaries for thesis

        # AI Aggregation output: 1 rating verdict + 1 book thesis
        # (wisdom and references are concatenation only - no AI cost)
        aggregate_output_estimate = 500 + 600  # rating verdict, book thesis

        total_input = total_input_tokens + aggregate_input_estimate
        total_output = estimated_output_tokens + aggregate_output_estimate

        # Get cost estimate
        cost_estimate = self.cost_service.estimate_cost(
            input_tokens=total_input,
            output_tokens=total_output,
            model=model
        )

        # Get current usage
        current_usage = self.cost_service.get_current_usage()

        # Total API calls: prompts_per_chapter * chapters + 1 rating aggregate + 1 summary aggregate
        # (wisdom and references are concatenation - no API calls)
        total_prompts = chapter_count * prompts_per_chapter + 1 + len(self.AI_AGGREGATE_PROMPTS)

        return {
            'chapter_count': chapter_count,
            'total_prompts': total_prompts,
            'estimated_input_tokens': total_input,
            'estimated_output_tokens': total_output,
            'estimated_total_tokens': cost_estimate['total_tokens'],
            'estimated_cost_usd': str(cost_estimate['estimated_cost_usd']),
            'model': model,
            'daily_usage': current_usage['daily'],
            'monthly_usage': current_usage['monthly'],
        }

    def run_pipeline(self, book: Book, model: str = None) -> ProcessingJob:
        """
        Start the analysis pipeline for a book.

        Creates a ProcessingJob and spawns a background thread to process chapters.

        Returns:
            ProcessingJob instance for tracking progress
        """
        model = model or self.model

        # Check for existing running job
        existing_job = ProcessingJob.objects.filter(
            book=book,
            job_type='chapter_analysis_pipeline',
            status__in=['pending', 'running']
        ).first()

        if existing_job:
            raise ValueError(f"Pipeline already running for this book (job_id={existing_job.id})")

        # Check limits BEFORE starting - fail fast
        estimate = self.estimate_pipeline_cost(book, model)
        total_prompts = estimate['total_prompts']

        from books_core.models import Settings, UsageTracking
        from datetime import date
        settings = Settings.get_settings()
        usage, _ = UsageTracking.objects.get_or_create(date=date.today())

        remaining = settings.daily_summary_limit - usage.daily_summaries_count
        if total_prompts > remaining:
            raise ValueError(
                f"Not enough daily budget. Need {total_prompts} API calls, "
                f"but only {remaining} remaining (limit: {settings.daily_summary_limit}). "
                f"Increase limit in Settings or wait until tomorrow."
            )

        # Get content chapters
        chapters = list(book.chapters.filter(
            is_front_matter=False,
            is_back_matter=False,
            word_count__gte=self.MIN_CHAPTER_WORDS
        ).order_by('chapter_number'))

        chapter_count = len(chapters)
        if chapter_count == 0:
            raise ValueError("No content chapters found to analyze")

        # Calculate progress increment per chapter (85% for chapters, 15% for aggregation)
        progress_per_chapter = int(85 / chapter_count) if chapter_count > 0 else 0

        # Create processing job
        job = ProcessingJob.objects.create(
            book=book,
            job_type='chapter_analysis_pipeline',
            status='running',
            progress_percent=0,
            metadata={
                'model': model,
                'chapter_count': chapter_count,
                'chapters_processed': 0,
                'progress_per_chapter': progress_per_chapter,
                'chapter_task_ids': [],
                'chapter_results': [],
                'current_phase': 'chapter_analysis',
            }
        )

        # Compute readability metrics (local, free) before queuing AI tasks
        from books_core.services.readability_service import ReadabilityService
        ReadabilityService().compute_all_for_book(book)

        # Queue chapter tasks in parallel using Django-Q2
        task_ids = []
        for chapter in chapters:
            task_id = async_task(
                'books_core.tasks.process_chapter_analysis',
                chapter.id,
                job.id,
                model,
                hook='books_core.tasks.chapter_analysis_complete',
                task_name=f'analyze_chapter_{chapter.id}'
            )
            task_ids.append(task_id)

        # Update job with task IDs
        job.metadata['chapter_task_ids'] = task_ids
        job.save(update_fields=['metadata'])

        logger.info(f"Pipeline started for book {book.id}: queued {chapter_count} chapter tasks")

        return job

    def _run_pipeline_thread(self, job_id: int, book_id: int, model: str):
        """
        Background thread that processes all chapters.
        """
        try:
            # Re-fetch objects in this thread
            job = ProcessingJob.objects.get(id=job_id)
            book = Book.objects.get(id=book_id)

            job.status = 'running'
            job.save()

            # Get prompts (both chapter prompts and extraction prompts)
            prompts = {}
            all_prompt_names = self.CHAPTER_PROMPTS + self.EXTRACTION_PROMPTS
            for prompt_name in all_prompt_names:
                prompt = Prompt.objects.filter(name=prompt_name).first()
                if not prompt:
                    raise ValueError(f"Prompt '{prompt_name}' not found in database")
                prompts[prompt_name] = prompt

            # Get content chapters (skip tiny fragments)
            chapters = list(book.chapters.filter(
                is_front_matter=False,
                is_back_matter=False,
                word_count__gte=self.MIN_CHAPTER_WORDS
            ).order_by('chapter_number'))

            total_chapters = len(chapters)
            job.metadata['chapter_count'] = total_chapters
            job.save()

            if total_chapters == 0:
                job.status = 'completed'
                job.progress_percent = 100
                job.error_message = 'No content chapters found'
                job.save()
                return

            # Compute readability metrics (local, free) before AI processing
            from books_core.services.readability_service import ReadabilityService
            ReadabilityService().compute_all_for_book(book)

            # Process each chapter
            chapter_ratings = []
            chapter_summaries = []  # For AI aggregation into book thesis
            extractions = {prompt: [] for prompt in self.EXTRACTION_PROMPTS}
            openai_service = OpenAIService(model=model)

            for i, chapter in enumerate(chapters):
                job.metadata['current_chapter'] = chapter.title or f"Chapter {chapter.chapter_number}"
                job.metadata['chapters_processed'] = i
                job.save()

                # Run chapter prompts (summarize + rate)
                for prompt_name in self.CHAPTER_PROMPTS:
                    prompt = prompts[prompt_name]
                    job.metadata['current_prompt'] = prompt_name
                    job.save()

                    try:
                        summary = self._process_chapter(
                            chapter, prompt, model, openai_service
                        )

                        # Collect rating data for aggregation
                        if prompt_name == 'rate_chapter':
                            rating_data = self._parse_rating(summary)
                            if rating_data:
                                rating_data['chapter_number'] = chapter.chapter_number
                                rating_data['chapter_title'] = chapter.title
                                chapter_ratings.append(rating_data)

                        # Collect summary data for book thesis aggregation
                        elif prompt_name == 'summarize_chapter':
                            chapter_summaries.append({
                                'chapter_number': chapter.chapter_number,
                                'chapter_title': chapter.title or f"Chapter {chapter.chapter_number}",
                                'content': summary.content_json.get('text', '')
                            })

                    except Exception as e:
                        logger.error(f"Error processing chapter {chapter.id} with {prompt_name}: {e}")
                        # Continue with other chapters

                # Run extraction prompts (key_ideas, anecdotes, references)
                for prompt_name in self.EXTRACTION_PROMPTS:
                    prompt = prompts[prompt_name]
                    job.metadata['current_prompt'] = prompt_name
                    job.save()

                    try:
                        summary = self._process_chapter(
                            chapter, prompt, model, openai_service
                        )

                        # Collect extraction data for aggregation
                        extractions[prompt_name].append({
                            'chapter_number': chapter.chapter_number,
                            'chapter_title': chapter.title or f"Chapter {chapter.chapter_number}",
                            'content': summary.content_json.get('text', '')
                        })

                    except Exception as e:
                        logger.error(f"Error extracting {prompt_name} from chapter {chapter.id}: {e}")
                        # Continue with other chapters

                # Update progress
                progress = int(((i + 1) / total_chapters) * 85)  # Leave 15% for aggregation
                job.progress_percent = progress
                job.save()

            # Aggregate ratings and generate book-level summary
            job.metadata['current_prompt'] = 'aggregating_ratings'
            job.save()

            if chapter_ratings:
                self._aggregate_book_rating(book, chapter_ratings, model, openai_service)

            job.progress_percent = 88
            job.save()

            # Aggregate chapter summaries into book thesis (AI)
            job.metadata['current_prompt'] = 'aggregating_summaries'
            job.save()

            if chapter_summaries:
                self._aggregate_summaries(book, chapter_summaries, model, openai_service)

            job.progress_percent = 92
            job.save()

            # Aggregate extractions into book essence (concatenation, no AI)
            job.metadata['current_prompt'] = 'aggregating_extractions'
            job.save()

            self._aggregate_extractions_concat(book, extractions)

            job.progress_percent = 95
            job.save()

            # Mark complete
            job.status = 'completed'
            job.progress_percent = 100
            job.metadata['chapters_processed'] = total_chapters
            job.metadata['current_chapter'] = None
            job.metadata['current_prompt'] = None
            job.save()

            logger.info(f"Pipeline completed for book {book_id}: {total_chapters} chapters processed")

        except Exception as e:
            logger.error(f"Pipeline failed for job {job_id}: {e}")
            try:
                job = ProcessingJob.objects.get(id=job_id)
                job.status = 'failed'
                job.error_message = str(e)
                job.save()
            except Exception:
                pass

    def _process_chapter(
        self,
        chapter: Chapter,
        prompt: Prompt,
        model: str,
        openai_service: OpenAIService
    ) -> Summary:
        """
        Process a single chapter with a single prompt.
        """
        start_time = time.time()

        # Render prompt template
        rendered_prompt = prompt.render_template({'content': chapter.content})

        # Make API call with cost control
        result = openai_service.complete_with_cost_control(
            prompt=rendered_prompt,
            model=model,
            max_tokens=1500,  # Enough for summaries and ratings
        )

        processing_time_ms = int((time.time() - start_time) * 1000)

        # Create summary record
        summary = self.summary_service.create_summary(
            chapter=chapter,
            prompt=prompt,
            content=result['content'],
            metadata={
                'tokens_used': result['tokens_used'],
                'model_used': result['model'],
                'processing_time_ms': processing_time_ms,
                'estimated_cost_usd': result['actual_cost_usd'],
                'summary_type': 'analysis' if prompt.category == 'rating' else 'tldr',
            }
        )

        return summary

    def _parse_rating(self, summary: Summary) -> Optional[Dict[str, Any]]:
        """
        Parse rating JSON from a rate_chapter summary.
        """
        try:
            content = summary.content_json.get('text', '')

            # Try to extract JSON from the response
            # Look for JSON block in markdown code fence
            if '```json' in content:
                json_start = content.find('```json') + 7
                json_end = content.find('```', json_start)
                json_str = content[json_start:json_end].strip()
            elif '```' in content:
                json_start = content.find('```') + 3
                json_end = content.find('```', json_start)
                json_str = content[json_start:json_end].strip()
            elif '{' in content:
                # Try to find JSON object directly
                json_start = content.find('{')
                json_end = content.rfind('}') + 1
                json_str = content[json_start:json_end]
            else:
                logger.warning(f"No JSON found in rating summary {summary.id}")
                return None

            rating = json.loads(json_str)
            return rating

        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse rating from summary {summary.id}: {e}")
            return None

    def _aggregate_book_rating(
        self,
        book: Book,
        chapter_ratings: List[Dict[str, Any]],
        model: str,
        openai_service: OpenAIService
    ) -> Summary:
        """
        Calculate averages and generate AI book-level verdict.
        """
        # Calculate averages
        criteria = ['insight', 'clarity', 'evidence', 'engagement', 'actionability', 'overall']
        averages = {}

        for criterion in criteria:
            values = [r.get(criterion, 0) for r in chapter_ratings if criterion in r]
            if values:
                averages[f'{criterion}_avg'] = round(sum(values) / len(values), 1)
            else:
                averages[f'{criterion}_avg'] = 0

        # Find best and worst chapters
        sorted_by_overall = sorted(
            chapter_ratings,
            key=lambda r: r.get('overall', 0),
            reverse=True
        )

        best_chapter = sorted_by_overall[0] if sorted_by_overall else None
        worst_chapter = sorted_by_overall[-1] if len(sorted_by_overall) > 1 else None

        # Collect chapter verdicts for AI prompt
        verdicts = [
            f"Chapter {r.get('chapter_number', '?')}: {r.get('one_line_verdict', 'No verdict')}"
            for r in chapter_ratings
        ]

        # Generate AI verdict
        aggregate_prompt = self._build_aggregate_prompt(averages, verdicts, book.title)

        start_time = time.time()
        result = openai_service.complete_with_cost_control(
            prompt=aggregate_prompt,
            model=model,
            max_tokens=500,
        )
        processing_time_ms = int((time.time() - start_time) * 1000)

        # Parse AI verdict
        verdict = result['content'].strip()
        if verdict.startswith('"') and verdict.endswith('"'):
            verdict = verdict[1:-1]

        # Build aggregate data
        aggregate_data = {
            **averages,
            'chapters_analyzed': len(chapter_ratings),
            'verdict': verdict,
            'best_chapter': {
                'number': best_chapter.get('chapter_number'),
                'title': best_chapter.get('chapter_title', ''),
                'score': best_chapter.get('overall', 0),
            } if best_chapter else None,
            'worst_chapter': {
                'number': worst_chapter.get('chapter_number'),
                'title': worst_chapter.get('chapter_title', ''),
                'score': worst_chapter.get('overall', 0),
            } if worst_chapter else None,
        }

        # Get or create aggregate prompt record
        aggregate_prompt_obj, _ = Prompt.objects.get_or_create(
            name=self.AGGREGATE_PROMPT,
            defaults={
                'category': 'rating',
                'template_text': 'Book rating aggregation prompt',
                'default_model': 'gpt-4o-mini',
            }
        )

        # Get next version number
        existing_max = Summary.objects.filter(
            book=book,
            prompt=aggregate_prompt_obj
        ).aggregate(Max('version'))['version__max'] or 0
        next_version = existing_max + 1

        # Create book-level summary
        summary = Summary.objects.create(
            book=book,
            chapter=None,  # Book-level, not chapter-level
            prompt=aggregate_prompt_obj,
            summary_type='analysis',
            content_json={
                'text': json.dumps(aggregate_data, indent=2),
                'prompt_name': self.AGGREGATE_PROMPT,
                'model': model,
                'aggregate_data': aggregate_data,
            },
            tokens_used=result['tokens_used'],
            model_used=result['model'],
            processing_time_ms=processing_time_ms,
            version=next_version,
            estimated_cost_usd=Decimal(result['actual_cost_usd']),
        )

        logger.info(
            f"Created book-level aggregate summary for book {book.id}: "
            f"overall_avg={averages.get('overall_avg')}"
        )

        return summary

    def _build_aggregate_prompt(
        self,
        averages: Dict[str, float],
        verdicts: List[str],
        book_title: str
    ) -> str:
        """
        Build the prompt for generating the book-level verdict.
        """
        return f"""You are rating the book "{book_title}" based on chapter-by-chapter analysis.

AVERAGE SCORES (1-10 scale):
- Insight: {averages.get('insight_avg', 0)}
- Clarity: {averages.get('clarity_avg', 0)}
- Evidence: {averages.get('evidence_avg', 0)}
- Engagement: {averages.get('engagement_avg', 0)}
- Actionability: {averages.get('actionability_avg', 0)}
- Overall: {averages.get('overall_avg', 0)}

CHAPTER VERDICTS:
{chr(10).join(verdicts)}

Based on these scores and chapter verdicts, write a 2-3 sentence overall verdict for the book.
Focus on what makes this book valuable (or not) and who would benefit from reading it.

Output ONLY the verdict text, no additional formatting."""

    def _aggregate_summaries(
        self,
        book: Book,
        chapter_summaries: List[Dict],
        model: str,
        openai_service: OpenAIService
    ) -> Summary:
        """
        Aggregate chapter summaries into a book thesis using AI.
        """
        # Get aggregate prompt from database
        aggregate_prompt = Prompt.objects.filter(name='aggregate_summaries').first()
        if not aggregate_prompt:
            logger.warning("Aggregate prompt 'aggregate_summaries' not found in database")
            return None

        # Build aggregation input from chapter summaries
        chapters_text = self._format_extraction_for_aggregation(chapter_summaries)

        # Render prompt with chapter summaries
        rendered = aggregate_prompt.render_template({
            'chapter_summaries': chapters_text,
            'book_title': book.title
        })

        start_time = time.time()
        try:
            result = openai_service.complete_with_cost_control(
                prompt=rendered,
                model=model,
                max_tokens=1000,
            )
        except Exception as e:
            logger.error(f"Failed to aggregate summaries: {e}")
            return None

        processing_time_ms = int((time.time() - start_time) * 1000)

        # Get next version number
        existing_max = Summary.objects.filter(
            book=book,
            prompt=aggregate_prompt
        ).aggregate(Max('version'))['version__max'] or 0
        next_version = existing_max + 1

        # Create book-level summary
        summary = Summary.objects.create(
            book=book,
            chapter=None,
            prompt=aggregate_prompt,
            summary_type='analysis',
            content_json={
                'text': result['content'],
                'prompt_name': 'aggregate_summaries',
                'model': model,
                'chapters_included': len(chapter_summaries),
            },
            tokens_used=result['tokens_used'],
            model_used=result['model'],
            processing_time_ms=processing_time_ms,
            version=next_version,
            estimated_cost_usd=Decimal(result['actual_cost_usd']),
        )

        logger.info(f"Created book thesis summary for book {book.id}")
        return summary

    def _aggregate_extractions_concat(
        self,
        book: Book,
        extractions: Dict[str, List[Dict]]
    ) -> Dict[str, Summary]:
        """
        Aggregate extractions via concatenation (no AI).
        Wisdom is grouped by type (stories, advice, etc).
        References are simply concatenated.
        """
        results = {}

        for extraction_type, chapter_data in extractions.items():
            if not chapter_data:
                logger.warning(f"No extraction data for {extraction_type}")
                continue

            # Determine prompt name for storage
            if extraction_type == 'extract_chapter_wisdom':
                prompt_name = 'grouped_wisdom'
                # Group wisdom by section type
                content = self._group_wisdom_by_type(chapter_data)
            else:  # extract_references
                prompt_name = 'concatenated_references'
                # Simple concatenation with chapter headers
                content = self._format_extraction_for_aggregation(chapter_data)

            # Get or create prompt record for storage
            prompt, _ = Prompt.objects.get_or_create(
                name=prompt_name,
                defaults={
                    'category': 'aggregation',
                    'template_text': f'{prompt_name} - concatenated content',
                    'default_model': 'none',
                }
            )

            # Get next version
            existing_max = Summary.objects.filter(
                book=book,
                prompt=prompt
            ).aggregate(Max('version'))['version__max'] or 0
            next_version = existing_max + 1

            # Create summary with zero cost (no AI)
            summary = Summary.objects.create(
                book=book,
                chapter=None,
                prompt=prompt,
                summary_type='analysis',
                content_json={
                    'text': content,
                    'prompt_name': prompt_name,
                    'model': 'concatenation',
                    'extraction_type': extraction_type,
                    'chapters_included': len(chapter_data),
                    'is_concatenated': True,
                },
                tokens_used=0,
                model_used='concatenation',
                processing_time_ms=0,
                version=next_version,
                estimated_cost_usd=Decimal('0'),
            )

            results[extraction_type] = summary
            logger.info(f"Created {prompt_name} for book {book.id}")

        return results

    def _group_wisdom_by_type(self, chapter_data: List[Dict]) -> str:
        """
        Parse chapter wisdom extractions and group by type.

        Input: List of chapter extractions with markdown sections:
        - ## THE STORY
        - ## THE ADVICE
        - ## PEOPLE WORTH KNOWING
        - ## THE SURPRISE
        - ## QUOTABLE

        Output: Grouped markdown with all stories together, all advice together, etc.
        """
        import re

        # Section patterns and their output headers
        sections = {
            'stories': {'pattern': r'## THE STORY\s*\n(.*?)(?=\n---|\n## |$)', 'header': '## ALL STORIES', 'items': []},
            'advice': {'pattern': r'## THE ADVICE\s*\n(.*?)(?=\n---|\n## |$)', 'header': '## ALL ADVICE', 'items': []},
            'people': {'pattern': r'## PEOPLE WORTH KNOWING\s*\n(.*?)(?=\n---|\n## |$)', 'header': '## NOTABLE PEOPLE', 'items': []},
            'insights': {'pattern': r'## THE SURPRISE\s*\n(.*?)(?=\n---|\n## |$)', 'header': '## KEY INSIGHTS', 'items': []},
            'quotes': {'pattern': r'## QUOTABLE\s*\n(.*?)(?=\n---|\n## |$)', 'header': '## MEMORABLE QUOTES', 'items': []},
        }

        # Parse each chapter's content
        for item in chapter_data:
            chapter_num = item['chapter_number']
            chapter_title = item.get('chapter_title', f"Chapter {chapter_num}")
            content = item.get('content', '')

            for section_key, section_info in sections.items():
                match = re.search(section_info['pattern'], content, re.DOTALL | re.IGNORECASE)
                if match:
                    extracted = match.group(1).strip()
                    # Skip "No standout story" type responses
                    if extracted and 'no standout' not in extracted.lower() and 'skip' not in extracted.lower():
                        attribution = f"*From Chapter {chapter_num}: {chapter_title}*\n\n"
                        section_info['items'].append(attribution + extracted)

        # Build output grouped by type
        output_parts = []
        for section_key, section_info in sections.items():
            if section_info['items']:
                output_parts.append(section_info['header'])
                output_parts.append('')
                for i, item in enumerate(section_info['items']):
                    output_parts.append(item)
                    if i < len(section_info['items']) - 1:
                        output_parts.append('')
                        output_parts.append('---')
                        output_parts.append('')
                output_parts.append('')

        return '\n'.join(output_parts)

    def get_book_rating(self, book: Book) -> Optional[Dict[str, Any]]:
        """
        Get the most recent book-level rating if it exists.

        Returns:
            Dict with aggregate data or None if not analyzed yet
        """
        summary = Summary.objects.filter(
            book=book,
            chapter__isnull=True,
            prompt__name=self.AGGREGATE_PROMPT
        ).order_by('-created_at').first()

        if not summary:
            return None

        aggregate_data = summary.content_json.get('aggregate_data', {})

        return {
            'overall_avg': aggregate_data.get('overall_avg', 0),
            'insight_avg': aggregate_data.get('insight_avg', 0),
            'clarity_avg': aggregate_data.get('clarity_avg', 0),
            'evidence_avg': aggregate_data.get('evidence_avg', 0),
            'engagement_avg': aggregate_data.get('engagement_avg', 0),
            'actionability_avg': aggregate_data.get('actionability_avg', 0),
            'chapters_analyzed': aggregate_data.get('chapters_analyzed', 0),
            'verdict': aggregate_data.get('verdict', ''),
            'best_chapter': aggregate_data.get('best_chapter'),
            'worst_chapter': aggregate_data.get('worst_chapter'),
            'created_at': summary.created_at,
            'model_used': summary.model_used,
            'summary_id': summary.id,
        }

    def get_chapter_analyses(self, book: Book) -> List[Dict[str, Any]]:
        """
        Get all chapter analyses for a book.

        Returns:
            List of dicts with chapter info, summary, and rating
        """
        chapters = book.chapters.filter(
            is_front_matter=False,
            is_back_matter=False
        ).order_by('chapter_number')

        results = []

        for chapter in chapters:
            chapter_data = {
                'chapter_id': chapter.id,
                'chapter_number': chapter.chapter_number,
                'title': chapter.title or f"Chapter {chapter.chapter_number}",
                'word_count': chapter.word_count,
                'summary': None,
                'rating': None,
                'readability': chapter.readability_metrics or {},
            }

            # Get latest wisdom extraction (or legacy summarize_chapter)
            summary = Summary.objects.filter(
                chapter=chapter,
                prompt__name='extract_chapter_wisdom'
            ).order_by('-version').first()

            # Fallback to old summarize_chapter for books analyzed before refactor
            if not summary:
                summary = Summary.objects.filter(
                    chapter=chapter,
                    prompt__name='summarize_chapter'
                ).order_by('-version').first()

            if summary:
                chapter_data['summary'] = {
                    'content': summary.content_json.get('text', ''),
                    'tokens_used': summary.tokens_used,
                    'cost_usd': str(summary.estimated_cost_usd),
                    'created_at': summary.created_at,
                }

            # Get latest rating
            rating_summary = Summary.objects.filter(
                chapter=chapter,
                prompt__name='rate_chapter'
            ).order_by('-version').first()

            if rating_summary:
                rating_data = self._parse_rating(rating_summary)
                chapter_data['rating'] = rating_data

            results.append(chapter_data)

        return results

    def _format_extraction_for_aggregation(self, chapter_data: List[Dict]) -> str:
        """
        Format chapter extractions for aggregation prompt input.

        Args:
            chapter_data: List of dicts with chapter_number, chapter_title, content

        Returns:
            Formatted string with all chapter extractions
        """
        sections = []
        for item in chapter_data:
            section = f"## Chapter {item['chapter_number']}"
            if item.get('chapter_title'):
                section += f": {item['chapter_title']}"
            section += f"\n\n{item['content']}"
            sections.append(section)
        return "\n\n---\n\n".join(sections)

    def get_book_essence(self, book: Book) -> Optional[Dict[str, Any]]:
        """
        Get aggregated content for Book Essence section.

        Returns:
            Dict with book_thesis, wisdom, references content, or None if not available
        """
        essence = {}

        # New prompt names and their keys
        prompt_mappings = {
            'aggregate_summaries': 'book_thesis',
            'grouped_wisdom': 'wisdom',
            'concatenated_references': 'references',
        }

        for prompt_name, essence_key in prompt_mappings.items():
            summary = Summary.objects.filter(
                book=book,
                chapter__isnull=True,
                prompt__name=prompt_name
            ).order_by('-created_at').first()

            if summary:
                essence[essence_key] = {
                    'content': summary.content_json.get('text', ''),
                    'chapters_included': summary.content_json.get('chapters_included', 0),
                    'tokens_used': summary.tokens_used,
                    'cost_usd': str(summary.estimated_cost_usd),
                    'is_concatenated': summary.content_json.get('is_concatenated', False),
                    'created_at': summary.created_at,
                }

        # Fallback: check for old-style prompts for backwards compatibility
        if 'wisdom' not in essence:
            old_wisdom = Summary.objects.filter(
                book=book,
                chapter__isnull=True,
                prompt__name='aggregate_wisdom'
            ).order_by('-created_at').first()
            if old_wisdom:
                essence['wisdom'] = {
                    'content': old_wisdom.content_json.get('text', ''),
                    'chapters_included': old_wisdom.content_json.get('chapters_included', 0),
                    'tokens_used': old_wisdom.tokens_used,
                    'cost_usd': str(old_wisdom.estimated_cost_usd),
                    'is_concatenated': False,
                    'created_at': old_wisdom.created_at,
                }

        if 'references' not in essence:
            old_refs = Summary.objects.filter(
                book=book,
                chapter__isnull=True,
                prompt__name='aggregate_references'
            ).order_by('-created_at').first()
            if old_refs:
                essence['references'] = {
                    'content': old_refs.content_json.get('text', ''),
                    'chapters_included': old_refs.content_json.get('chapters_included', 0),
                    'tokens_used': old_refs.tokens_used,
                    'cost_usd': str(old_refs.estimated_cost_usd),
                    'is_concatenated': False,
                    'created_at': old_refs.created_at,
                }

        return essence if essence else None

    def get_book_readability(self, book: Book) -> Optional[Dict[str, Any]]:
        """
        Get book-level readability metrics.

        Returns:
            Dict with readability metrics or None if not computed yet
        """
        metrics = book.readability_metrics
        if metrics and not metrics.get('error'):
            return metrics
        return None
