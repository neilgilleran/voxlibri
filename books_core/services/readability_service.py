"""
ReadabilityService - Local textstat-based readability metrics computation.

Computes readability scores per chapter and aggregates to book level.
Zero API cost - all computation is local via the textstat library.
"""

import logging
import re
from typing import Dict, Any, List, Optional

import textstat

from books_core.models import Book, Chapter

logger = logging.getLogger(__name__)

# Difficulty tier definitions
DIFFICULTY_TIERS = {
    'accessible': {'min_fre': 60, 'label': 'Accessible', 'wpm': 275},
    'moderate': {'min_fre': 40, 'label': 'Moderate', 'wpm': 200},
    'technical': {'min_fre': 20, 'label': 'Technical', 'wpm': 100},
    'dense': {'min_fre': 0, 'label': 'Dense', 'wpm': 60},
}

TIER_BENCHMARKS = {
    'accessible': {
        'comparable_to': 'Popular fiction, USA Today, most blog posts',
        'audience': 'General audience, no specialized knowledge needed',
    },
    'moderate': {
        'comparable_to': 'The Economist, Harvard Business Review, popular nonfiction',
        'audience': 'Educated general readers',
    },
    'technical': {
        'comparable_to': 'Academic papers, legal documents, scientific journals',
        'audience': 'Subject-matter professionals, graduate-level readers',
    },
    'dense': {
        'comparable_to': 'Philosophy texts, advanced mathematics, medical literature',
        'audience': 'Domain experts, requires significant background knowledge',
    },
}


class ReadabilityService:
    """Compute readability metrics using textstat (local, zero API cost)."""

    def compute_all_for_book(self, book: Book) -> Dict[str, Any]:
        """
        Entry point: compute readability for all chapters then aggregate to book.

        Returns:
            Book-level readability metrics dict
        """
        chapters = book.chapters.filter(
            is_front_matter=False,
            is_back_matter=False,
            word_count__gte=500
        ).order_by('chapter_number')

        for chapter in chapters:
            self.compute_chapter_metrics(chapter)

        return self.compute_book_metrics(book)

    def compute_chapter_metrics(self, chapter: Chapter) -> Dict[str, Any]:
        """
        Compute readability metrics for a single chapter.

        Stores results in chapter.readability_metrics and saves.
        Returns the metrics dict.
        """
        text = self._clean_markdown(chapter.content or '')

        if len(text.split()) < 100:
            metrics = {
                'flesch_reading_ease': None,
                'flesch_kincaid_grade': None,
                'gunning_fog': None,
                'smog_index': None,
                'coleman_liau_index': None,
                'word_count': len(text.split()),
                'sentence_count': textstat.sentence_count(text) if text else 0,
                'syllable_count': textstat.syllable_count(text) if text else 0,
                'difficulty_tier': 'unknown',
                'reading_time_minutes': 0,
                'reading_time_skim': 0,
                'reading_time_study': 0,
                'too_short': True,
            }
        else:
            fre = textstat.flesch_reading_ease(text)
            tier = self._classify_difficulty(fre)
            wpm = DIFFICULTY_TIERS.get(tier, DIFFICULTY_TIERS['moderate'])['wpm']
            word_count = len(text.split())

            metrics = {
                'flesch_reading_ease': round(fre, 1),
                'flesch_kincaid_grade': round(textstat.flesch_kincaid_grade(text), 1),
                'gunning_fog': round(textstat.gunning_fog(text), 1),
                'smog_index': round(textstat.smog_index(text), 1),
                'coleman_liau_index': round(textstat.coleman_liau_index(text), 1),
                'word_count': word_count,
                'sentence_count': textstat.sentence_count(text),
                'syllable_count': textstat.syllable_count(text),
                'difficulty_tier': tier,
                'reading_time_minutes': round(word_count / wpm, 1),
                'reading_time_skim': round(word_count / 350, 1),
                'reading_time_study': round(word_count / 100, 1),
                'too_short': False,
            }

        chapter.readability_metrics = metrics
        chapter.save(update_fields=['readability_metrics'])

        return metrics

    def compute_book_metrics(self, book: Book) -> Dict[str, Any]:
        """
        Aggregate chapter metrics into book-level readability profile.

        Stores results in book.readability_metrics and saves.
        Returns the metrics dict.
        """
        chapters = book.chapters.filter(
            is_front_matter=False,
            is_back_matter=False,
            word_count__gte=500
        ).order_by('chapter_number')

        chapter_metrics = []
        for ch in chapters:
            m = ch.readability_metrics
            if m and not m.get('too_short'):
                chapter_metrics.append({
                    'chapter_number': ch.chapter_number,
                    'title': ch.title or f'Chapter {ch.chapter_number}',
                    'metrics': m,
                })

        if not chapter_metrics:
            metrics = {'error': 'No chapters with sufficient content for readability analysis'}
            book.readability_metrics = metrics
            book.save(update_fields=['readability_metrics'])
            return metrics

        # Weighted averages by word count
        total_words = sum(cm['metrics']['word_count'] for cm in chapter_metrics)
        score_fields = [
            'flesch_reading_ease', 'flesch_kincaid_grade', 'gunning_fog',
            'smog_index', 'coleman_liau_index'
        ]

        averages = {}
        for field in score_fields:
            weighted_sum = sum(
                cm['metrics'][field] * cm['metrics']['word_count']
                for cm in chapter_metrics
                if cm['metrics'].get(field) is not None
            )
            averages[field] = round(weighted_sum / total_words, 1) if total_words > 0 else 0

        # Overall difficulty tier from weighted average FRE
        overall_tier = self._classify_difficulty(averages['flesch_reading_ease'])
        overall_wpm = DIFFICULTY_TIERS.get(overall_tier, DIFFICULTY_TIERS['moderate'])['wpm']

        # Total reading time (tier-adjusted, skim, study)
        total_reading_minutes = round(total_words / overall_wpm, 1)
        total_reading_hours = round(total_reading_minutes / 60, 1)

        # Difficulty profile: count chapters per tier
        tier_counts = {}
        for cm in chapter_metrics:
            tier = cm['metrics'].get('difficulty_tier', 'unknown')
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

        # Hardest and easiest chapters
        sorted_by_fre = sorted(
            chapter_metrics,
            key=lambda cm: cm['metrics'].get('flesch_reading_ease', 100)
        )
        hardest = sorted_by_fre[0]
        easiest = sorted_by_fre[-1]

        # Chapter curve data for visualization
        max_time = max(
            (cm['metrics']['reading_time_minutes'] for cm in chapter_metrics),
            default=1
        )
        chapter_curve_data = [
            {
                'chapter_number': cm['chapter_number'],
                'title': cm['title'],
                'flesch_kincaid_grade': cm['metrics']['flesch_kincaid_grade'],
                'difficulty_tier': cm['metrics']['difficulty_tier'],
                'reading_time_minutes': cm['metrics']['reading_time_minutes'],
                'reading_time_pct': round((cm['metrics']['reading_time_minutes'] / max_time) * 100) if max_time > 0 else 0,
                'word_count': cm['metrics']['word_count'],
            }
            for cm in chapter_metrics
        ]

        # Score explanations
        score_explanations = {
            'flesch_reading_ease': self.get_score_explanation('flesch_reading_ease', averages['flesch_reading_ease']),
            'flesch_kincaid_grade': self.get_score_explanation('flesch_kincaid_grade', averages['flesch_kincaid_grade']),
            'gunning_fog': self.get_score_explanation('gunning_fog', averages['gunning_fog']),
        }

        metrics = {
            **averages,
            'total_word_count': total_words,
            'total_sentence_count': sum(cm['metrics']['sentence_count'] for cm in chapter_metrics),
            'chapters_analyzed': len(chapter_metrics),
            'difficulty_tier': overall_tier,
            'reading_time_minutes': total_reading_minutes,
            'reading_time_hours': total_reading_hours,
            'reading_time_skim_minutes': round(total_words / 350, 1),
            'reading_time_skim_hours': round(total_words / 350 / 60, 1),
            'reading_time_study_minutes': round(total_words / 100, 1),
            'reading_time_study_hours': round(total_words / 100 / 60, 1),
            'difficulty_profile': tier_counts,
            'benchmark': TIER_BENCHMARKS.get(overall_tier, TIER_BENCHMARKS['moderate']),
            'score_explanations': score_explanations,
            'chapter_curve_data': chapter_curve_data,
            'difficulty_narrative': self._compute_difficulty_narrative(chapter_metrics),
            'hardest_chapter': {
                'number': hardest['chapter_number'],
                'title': hardest['title'],
                'flesch_reading_ease': hardest['metrics']['flesch_reading_ease'],
                'flesch_kincaid_grade': hardest['metrics']['flesch_kincaid_grade'],
                'difficulty_tier': hardest['metrics']['difficulty_tier'],
                'reading_time_minutes': hardest['metrics']['reading_time_minutes'],
            },
            'easiest_chapter': {
                'number': easiest['chapter_number'],
                'title': easiest['title'],
                'flesch_reading_ease': easiest['metrics']['flesch_reading_ease'],
                'flesch_kincaid_grade': easiest['metrics']['flesch_kincaid_grade'],
                'difficulty_tier': easiest['metrics']['difficulty_tier'],
                'reading_time_minutes': easiest['metrics']['reading_time_minutes'],
            },
        }

        book.readability_metrics = metrics
        book.save(update_fields=['readability_metrics'])

        return metrics

    @staticmethod
    def get_score_explanation(metric_name: str, value: float) -> str:
        """Return a plain-English explanation for a readability score."""
        if value is None:
            return ''

        if metric_name == 'flesch_reading_ease':
            if value >= 90:
                return 'Very easy to read'
            elif value >= 80:
                return 'Easy to read'
            elif value >= 70:
                return 'Fairly easy to read'
            elif value >= 60:
                return 'Standard / plain English'
            elif value >= 50:
                return 'Fairly difficult to read'
            elif value >= 30:
                return 'Difficult to read'
            else:
                return 'Very difficult to read'

        elif metric_name == 'flesch_kincaid_grade':
            grade = round(value)
            if grade <= 5:
                return f'About {grade}th grade reading level'
            elif grade <= 8:
                return f'About {grade}th grade reading level'
            elif grade <= 12:
                return f'About {grade}th grade / high school level'
            elif grade <= 16:
                return 'College level'
            else:
                return 'Graduate / professional level'

        elif metric_name == 'gunning_fog':
            years = round(value)
            if years <= 12:
                return f'~{years} years of formal education needed'
            elif years <= 16:
                return f'~{years} years of education (college level)'
            else:
                return f'~{years} years of education (post-graduate)'

        return ''

    @staticmethod
    def _compute_difficulty_narrative(chapter_metrics: List[Dict]) -> str:
        """Analyze grade level sequence to produce a trend description."""
        if len(chapter_metrics) < 2:
            return ''

        grades = [cm['metrics']['flesch_kincaid_grade'] for cm in chapter_metrics]
        n = len(grades)

        # Find peak and valley
        peak_idx = max(range(n), key=lambda i: grades[i])
        valley_idx = min(range(n), key=lambda i: grades[i])
        peak_ch = chapter_metrics[peak_idx]['chapter_number']
        valley_ch = chapter_metrics[valley_idx]['chapter_number']

        # Overall trend: compare first third avg to last third avg
        third = max(1, n // 3)
        first_third_avg = sum(grades[:third]) / third
        last_third_avg = sum(grades[-third:]) / third
        diff = last_third_avg - first_third_avg

        # Grade range
        grade_range = max(grades) - min(grades)

        parts = []

        if grade_range < 1.5:
            parts.append('Difficulty stays remarkably consistent throughout')
        else:
            if diff > 2:
                parts.append('Gets progressively harder toward the end')
            elif diff < -2:
                parts.append('Eases up toward the end')
            else:
                parts.append('Difficulty varies but stays in a similar range overall')

            parts.append(f'Peaks at Chapter {peak_ch} (grade {grades[peak_idx]})')

            if peak_idx != valley_idx:
                parts.append(f'easiest at Chapter {valley_ch} (grade {grades[valley_idx]})')

        return '. '.join(parts) + '.'

    @staticmethod
    def _clean_markdown(text: str) -> str:
        """Strip markdown formatting before analysis."""
        # Remove code blocks
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'`[^`]+`', '', text)
        # Remove headings markers
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # Remove bold/italic markers
        text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
        text = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', text)
        # Remove links but keep text
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        # Remove images
        text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', '', text)
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Remove horizontal rules
        text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
        # Remove blockquote markers
        text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
        # Remove list markers
        text = re.sub(r'^[\s]*[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^[\s]*\d+\.\s+', '', text, flags=re.MULTILINE)
        # Collapse whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    @staticmethod
    def _classify_difficulty(flesch_reading_ease: float) -> str:
        """Map Flesch Reading Ease score to difficulty tier."""
        if flesch_reading_ease >= 60:
            return 'accessible'
        elif flesch_reading_ease >= 40:
            return 'moderate'
        elif flesch_reading_ease >= 20:
            return 'technical'
        else:
            return 'dense'
