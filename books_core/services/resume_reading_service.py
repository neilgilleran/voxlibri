"""
ResumeReadingService - Generate and cache "resume reading" summaries for fiction books.

This service creates summaries that help readers resume a fiction book after putting it down.
It generates a recap of everything that happened up to a specified chapter, including
character refreshers and plot context.

Key features:
- Cached summaries (stored in Summary model with target_chapter in content_json)
- Collects existing chapter analysis data (plot points, characters)
- Generates new summaries via OpenAI if not cached
"""

import json
import logging
import time
from decimal import Decimal
from typing import Dict, List, Optional, Any

from django.db import transaction
from django.utils import timezone

from django.db.models import Max

from books_core.models import Book, Chapter, Summary, Prompt
from books_core.services.openai_service import OpenAIService
from books_core.services.cost_control_service import CostControlService

logger = logging.getLogger(__name__)


class ResumeReadingService:
    """
    Service for generating and caching "resume reading" summaries for fiction books.

    These summaries include:
    - Story recap up to chapter N
    - Character reminder list
    - Where we left off context
    """

    PROMPT_NAME = 'summarize_to_chapter'

    def __init__(self):
        self.cost_control = CostControlService()

    def get_cached_summary(
        self,
        book: Book,
        target_chapter: int
    ) -> Optional[Summary]:
        """
        Check if a resume summary exists for this book at this chapter.

        Args:
            book: Fiction book
            target_chapter: Chapter number user is resuming at

        Returns:
            Summary if cached, None otherwise
        """
        try:
            prompt = Prompt.objects.get(name=self.PROMPT_NAME)
        except Prompt.DoesNotExist:
            logger.warning(f"Prompt '{self.PROMPT_NAME}' not found")
            return None

        # Find summaries for this book with matching target_chapter
        # We store target_chapter in content_json
        summaries = Summary.objects.filter(
            book=book,
            chapter__isnull=True,  # Book-level summary
            prompt=prompt,
        ).order_by('-created_at')

        # Check content_json for matching target_chapter
        for summary in summaries:
            if summary.content_json.get('target_chapter') == target_chapter:
                logger.info(
                    f"Found cached resume summary for book {book.id} "
                    f"at chapter {target_chapter}"
                )
                return summary

        return None

    def estimate_cost(
        self,
        book: Book,
        target_chapter: int
    ) -> Dict[str, Any]:
        """
        Estimate the cost of generating a resume summary.

        Args:
            book: Fiction book
            target_chapter: Chapter number user is resuming at

        Returns:
            Dictionary with cost estimate and usage info
        """
        # Check if already cached
        cached = self.get_cached_summary(book, target_chapter)
        if cached:
            return {
                'is_cached': True,
                'estimated_cost_usd': '0.00',
                'estimated_tokens': 0,
                'cached_summary_id': cached.id,
                'message': 'Summary already cached - no cost to retrieve',
            }

        # Estimate input tokens (chapter summaries + character data)
        chapters = book.chapters.filter(
            chapter_number__lt=target_chapter,
            is_front_matter=False,
            is_back_matter=False
        )

        chapter_count = chapters.count()

        # Rough estimate: ~200 tokens per chapter summary + ~300 tokens per character
        # Plus prompt overhead ~500 tokens
        estimated_input_tokens = (chapter_count * 200) + 500

        # Output estimate: ~800 tokens for resume summary
        estimated_output_tokens = 800

        total_tokens = estimated_input_tokens + estimated_output_tokens

        # Calculate cost (gpt-4o-mini rates)
        input_cost = estimated_input_tokens * 0.00000015
        output_cost = estimated_output_tokens * 0.0000006
        total_cost = input_cost + output_cost

        # Get current usage
        usage_status = self.cost_control.get_current_usage()

        return {
            'is_cached': False,
            'estimated_tokens': total_tokens,
            'estimated_input_tokens': estimated_input_tokens,
            'estimated_output_tokens': estimated_output_tokens,
            'estimated_cost_usd': f'{total_cost:.6f}',
            'chapters_to_summarize': chapter_count,
            'usage': usage_status,
        }

    def _gather_chapter_summaries(
        self,
        book: Book,
        target_chapter: int
    ) -> List[Dict[str, Any]]:
        """
        Collect chapter plot summaries for chapters 1 to target_chapter-1.

        Falls back to chapter content if no plot summary exists.
        """
        chapters = book.chapters.filter(
            chapter_number__lt=target_chapter,
            is_front_matter=False,
            is_back_matter=False
        ).order_by('chapter_number')

        summaries = []

        for chapter in chapters:
            # Try to get plot_points extraction
            summary = Summary.objects.filter(
                chapter=chapter,
                prompt__name='extract_plot_points'
            ).order_by('-version').first()

            if summary and summary.content_json.get('text'):
                summaries.append({
                    'chapter_number': chapter.chapter_number,
                    'title': chapter.title or f'Chapter {chapter.chapter_number}',
                    'summary': summary.content_json['text'],
                })
            else:
                # Fall back to general chapter summary
                summary = Summary.objects.filter(
                    chapter=chapter,
                    prompt__name='summarize_chapter'
                ).order_by('-version').first()

                if summary and summary.content_json.get('text'):
                    summaries.append({
                        'chapter_number': chapter.chapter_number,
                        'title': chapter.title or f'Chapter {chapter.chapter_number}',
                        'summary': summary.content_json['text'],
                    })
                else:
                    # Last resort: use first 500 words of chapter content
                    content = chapter.content[:2000] if chapter.content else ''
                    summaries.append({
                        'chapter_number': chapter.chapter_number,
                        'title': chapter.title or f'Chapter {chapter.chapter_number}',
                        'summary': f"(No summary available) Content preview: {content}...",
                    })

        return summaries

    def _gather_character_data(
        self,
        book: Book,
        target_chapter: int
    ) -> Dict[str, Any]:
        """
        Collect character data from character extractions up to target_chapter-1.
        """
        chapters = book.chapters.filter(
            chapter_number__lt=target_chapter,
            is_front_matter=False,
            is_back_matter=False
        ).order_by('chapter_number')

        characters = {}

        for chapter in chapters:
            # Get character extraction
            summary = Summary.objects.filter(
                chapter=chapter,
                prompt__name='extract_characters'
            ).order_by('-version').first()

            if summary and summary.content_json.get('text'):
                # Track that this chapter had character data
                # The actual parsing will be done by the AI prompt
                if 'character_sources' not in characters:
                    characters['character_sources'] = []
                characters['character_sources'].append({
                    'chapter_number': chapter.chapter_number,
                    'data': summary.content_json['text'],
                })

        return characters

    def _format_summaries_for_prompt(
        self,
        summaries: List[Dict[str, Any]]
    ) -> str:
        """Format chapter summaries for the prompt."""
        lines = []
        for s in summaries:
            lines.append(f"## Chapter {s['chapter_number']}: {s['title']}")
            lines.append(s['summary'])
            lines.append('')
        return '\n'.join(lines)

    @transaction.atomic
    def generate_resume_summary(
        self,
        book: Book,
        target_chapter: int,
        force_regenerate: bool = False,
        model: str = 'gpt-4o-mini'
    ) -> Summary:
        """
        Generate a resume reading summary for the book at the specified chapter.

        Args:
            book: Fiction book
            target_chapter: Chapter number user is resuming at
            force_regenerate: If True, skip cache and regenerate
            model: AI model to use

        Returns:
            Summary containing the resume reading content

        Raises:
            ValueError: If prompt not found or book type is not fiction
            Exception: If AI generation fails
        """
        # Validate book type
        if book.book_type != 'fiction':
            raise ValueError(
                f"Resume reading is only available for fiction books. "
                f"This book is marked as {book.book_type}."
            )

        # Check cache unless force_regenerate
        if not force_regenerate:
            cached = self.get_cached_summary(book, target_chapter)
            if cached:
                return cached

        # Get prompt
        try:
            prompt = Prompt.objects.get(name=self.PROMPT_NAME)
        except Prompt.DoesNotExist:
            raise ValueError(
                f"Prompt '{self.PROMPT_NAME}' not found. "
                "Please run 'python manage.py sync_prompts' to sync prompts."
            )

        # Gather data
        chapter_summaries = self._gather_chapter_summaries(book, target_chapter)
        character_data = self._gather_character_data(book, target_chapter)

        if not chapter_summaries:
            raise ValueError(
                f"No chapters found before chapter {target_chapter}. "
                "Cannot generate resume summary."
            )

        # Format for prompt
        formatted_summaries = self._format_summaries_for_prompt(chapter_summaries)
        characters_json = json.dumps(character_data, indent=2)

        # Render prompt
        rendered_prompt = prompt.render_template({
            'chapter_summaries': formatted_summaries,
            'characters_json': characters_json,
            'target_chapter': target_chapter,
        })

        # Check cost control - estimate cost and check limits
        input_tokens = self.cost_control.count_tokens(rendered_prompt)
        estimated_output_tokens = 1500  # Estimate for resume summary
        cost_estimate = self.cost_control.estimate_cost(
            input_tokens=input_tokens,
            output_tokens=estimated_output_tokens,
            model=model
        )
        # check_limits raises LimitExceededException if limits would be exceeded
        from books_core.exceptions import LimitExceededException, EmergencyStopException
        try:
            self.cost_control.check_limits(cost_estimate['estimated_cost_usd'])
        except (LimitExceededException, EmergencyStopException) as e:
            raise ValueError(f"Cannot generate summary: {str(e)}")

        # Call OpenAI
        start_time = time.time()

        openai_service = OpenAIService(model=model)
        result = openai_service.complete(
            prompt=rendered_prompt,
            model=model,
            max_tokens=2000,
        )

        processing_time_ms = int((time.time() - start_time) * 1000)

        # Calculate cost from actual tokens used
        actual_cost = self.cost_control.estimate_cost(
            input_tokens=result.get('prompt_tokens', 0),
            output_tokens=result.get('completion_tokens', 0),
            model=model
        )
        tokens_used = result.get('tokens_used', 0)
        estimated_cost = actual_cost['estimated_cost_usd']

        # Record usage
        self.cost_control.update_usage(
            tokens=tokens_used,
            cost=estimated_cost,
            model=model
        )

        # Get next version number for book-level summaries
        existing = Summary.objects.filter(
            book=book,
            chapter__isnull=True,
            prompt=prompt,
        ).aggregate(max_version=Max('version'))

        next_version = (existing['max_version'] or 0) + 1

        # Create summary
        summary = Summary.objects.create(
            book=book,
            chapter=None,  # Book-level summary
            prompt=prompt,
            summary_type='custom',
            content_json={
                'text': result['content'],
                'target_chapter': target_chapter,
                'chapters_summarized': len(chapter_summaries),
                'characters_data_available': bool(character_data.get('character_sources')),
                'prompt_name': self.PROMPT_NAME,
                'model': model,
            },
            tokens_used=tokens_used,
            model_used=model,
            processing_time_ms=processing_time_ms,
            version=next_version,
            estimated_cost_usd=Decimal(str(estimated_cost)),
        )

        logger.info(
            f"Generated resume summary for book {book.id} at chapter {target_chapter}. "
            f"Cost: ${estimated_cost:.6f}, Tokens: {tokens_used}"
        )

        return summary

    def get_or_generate(
        self,
        book: Book,
        target_chapter: int,
        force_regenerate: bool = False,
        model: str = 'gpt-4o-mini'
    ) -> Summary:
        """
        Convenience method: get cached summary or generate new one.

        This is the main entry point for resume reading functionality.
        """
        return self.generate_resume_summary(
            book=book,
            target_chapter=target_chapter,
            force_regenerate=force_regenerate,
            model=model
        )
