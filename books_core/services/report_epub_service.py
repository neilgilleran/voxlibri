"""
EPUB generation service for Book Analysis Reports.

Creates downloadable EPUB files containing:
- Cover page with book image
- Rating overview with scores and verdict
- Best/worst chapter highlights
- Book essence (overview, wisdom, references)
- Chapter-by-chapter analysis
"""

import io
import logging
import re
from typing import Dict, List, Optional

import markdown
from ebooklib import epub
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from ..models import Book

logger = logging.getLogger(__name__)


class ReportEpubService:
    """Service for generating EPUB files from Book Analysis Reports."""

    def __init__(self, book: Book):
        self.book = book
        self.epub_book: Optional[epub.EpubBook] = None
        self.chapters: List[epub.EpubHtml] = []
        self.toc_items: List = []

    def generate_report_epub(
        self,
        book_rating: Optional[Dict],
        book_essence: Optional[Dict],
        chapter_analyses: List[Dict],
        book_readability: Optional[Dict] = None,
        readability_charts: Optional[Dict] = None
    ) -> bytes:
        """
        Generate EPUB file from report data.

        Args:
            book_rating: Book-level rating data with scores and verdict
            book_essence: Aggregated essence (thesis, wisdom, references)
            chapter_analyses: List of per-chapter analysis dicts

        Returns:
            EPUB file content as bytes
        """
        self._create_epub_book()

        # Add cover
        cover_chapter = self._add_cover_chapter()
        if cover_chapter:
            self.chapters.append(cover_chapter)

        # Add rating overview
        if book_rating:
            rating_chapter = self._add_rating_overview_chapter(book_rating)
            self.chapters.append(rating_chapter)
            self.toc_items.append(epub.Link(rating_chapter.file_name, 'Rating Overview', 'rating'))

            # Add highlights (best/worst chapters)
            if book_rating.get('best_chapter') or book_rating.get('worst_chapter'):
                highlights_chapter = self._add_highlights_chapter(book_rating)
                self.chapters.append(highlights_chapter)
                self.toc_items.append(epub.Link(highlights_chapter.file_name, 'Chapter Highlights', 'highlights'))

        # Add readability profile
        if book_readability:
            readability_chapter = self._add_readability_chapter(
                book_readability, readability_charts=readability_charts or {}
            )
            self.chapters.append(readability_chapter)
            self.toc_items.append(epub.Link(readability_chapter.file_name, 'Readability Profile', 'readability'))

        # Add book essence sections
        if book_essence:
            essence_chapters = self._add_essence_chapters(book_essence)
            for ch in essence_chapters:
                self.chapters.append(ch)

        # Add chapter-by-chapter analysis
        if chapter_analyses:
            analysis_chapters = self._add_chapter_analysis_chapters(chapter_analyses)
            chapter_toc_items = []
            for ch in analysis_chapters:
                self.chapters.append(ch)
                chapter_toc_items.append(epub.Link(ch.file_name, ch.title, ch.id))

            if chapter_toc_items:
                self.toc_items.append((
                    epub.Section('Chapter-by-Chapter Analysis'),
                    chapter_toc_items
                ))

        # Add all chapters to book
        for chapter in self.chapters:
            self.epub_book.add_item(chapter)

        # Add CSS
        css = self._get_css()
        css_item = epub.EpubItem(
            uid='style',
            file_name='style/main.css',
            media_type='text/css',
            content=css.encode('utf-8')
        )
        self.epub_book.add_item(css_item)

        # Link CSS to all chapters
        for chapter in self.chapters:
            chapter.add_item(css_item)

        # Build spine (reading order)
        self.epub_book.spine = ['nav'] + self.chapters

        # Build TOC
        self.epub_book.toc = self.toc_items

        # Add navigation
        self.epub_book.add_item(epub.EpubNcx())
        self.epub_book.add_item(epub.EpubNav())

        return self._write_to_bytes()

    def _create_epub_book(self) -> None:
        """Initialize EPUB book with metadata from source book."""
        self.epub_book = epub.EpubBook()

        # Set unique identifier
        self.epub_book.set_identifier(f'voxlibri-report-{self.book.id}')

        # Set metadata
        title = f"{self.book.title} - Analysis Report"
        self.epub_book.set_title(title)
        self.epub_book.set_language('en')

        if self.book.author:
            self.epub_book.add_author(self.book.author)

        # Add custom metadata
        self.epub_book.add_metadata('DC', 'description', f'AI-generated analysis report for {self.book.title}')
        self.epub_book.add_metadata('DC', 'publisher', 'VoxLibri')

    def _add_watermark_to_image(self, image_data: bytes) -> bytes:
        """
        Add large transparent 'VoxLibri' watermark diagonally across the cover.

        Args:
            image_data: Original image bytes

        Returns:
            Modified image bytes with watermark
        """
        try:
            # Open the image
            img = Image.open(io.BytesIO(image_data))

            # Convert to RGBA for transparency support
            if img.mode != 'RGBA':
                img = img.convert('RGBA')

            # Calculate font size - make it MASSIVE (roughly 40% of image width)
            font_size = max(80, img.width // 2)

            # Try to use a bold font, fall back to default
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
            except (OSError, IOError):
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
                except (OSError, IOError):
                    font = ImageFont.load_default()

            watermark_text = "VoxLibri"

            # Create a larger canvas for the rotated text
            # We need extra space because rotation expands the bounding box
            txt_layer = Image.new('RGBA', (img.width * 3, img.height * 3), (255, 255, 255, 0))
            txt_draw = ImageDraw.Draw(txt_layer)

            # Get text bounding box
            bbox = txt_draw.textbbox((0, 0), watermark_text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            # Position text in center of the larger canvas
            x = (txt_layer.width - text_width) // 2
            y = (txt_layer.height - text_height) // 2

            # Draw black outline/stroke for visibility (draw text multiple times offset)
            outline_color = (0, 0, 0, 100)
            for offset_x, offset_y in [(-3, -3), (-3, 3), (3, -3), (3, 3), (-3, 0), (3, 0), (0, -3), (0, 3)]:
                txt_draw.text((x + offset_x, y + offset_y), watermark_text, font=font, fill=outline_color)

            # Draw main text - white with ~50% opacity (much more visible)
            txt_draw.text((x, y), watermark_text, font=font, fill=(255, 255, 255, 130))

            # Rotate the text layer diagonally (30 degrees)
            txt_layer = txt_layer.rotate(30, resample=Image.BICUBIC, expand=False)

            # Crop the rotated layer to match original image size (center crop from 3x canvas)
            left = (txt_layer.width - img.width) // 2
            top = (txt_layer.height - img.height) // 2
            txt_layer = txt_layer.crop((left, top, left + img.width, top + img.height))

            # Composite the text overlay onto the original
            watermarked = Image.alpha_composite(img, txt_layer)

            # Convert back to RGB for JPEG compatibility
            if watermarked.mode == 'RGBA':
                # Create white background for JPEG
                background = Image.new('RGB', watermarked.size, (255, 255, 255))
                background.paste(watermarked, mask=watermarked.split()[3])
                watermarked = background

            # Save to bytes
            output = io.BytesIO()
            # Determine format from original
            original_format = Image.open(io.BytesIO(image_data)).format or 'JPEG'
            watermarked.save(output, format=original_format, quality=95)
            output.seek(0)
            return output.read()

        except Exception as e:
            logger.warning(f"Could not add watermark to cover: {e}")
            return image_data  # Return original on failure

    def _add_cover_chapter(self) -> Optional[epub.EpubHtml]:
        """Create cover page with image and title."""
        cover_image_data = None
        cover_extension = 'jpg'

        # Try to get cover image from book
        if self.book.cover_image:
            try:
                cover_path = self.book.cover_image.path
                with open(cover_path, 'rb') as f:
                    cover_image_data = f.read()
                # Detect extension from filename
                if cover_path.lower().endswith('.png'):
                    cover_extension = 'png'

                # Add VoxLibri watermark to cover
                cover_image_data = self._add_watermark_to_image(cover_image_data)
            except Exception as e:
                logger.warning(f"Could not read cover image: {e}")

        # Create cover page HTML
        if cover_image_data:
            # Add cover image to EPUB (don't use set_cover as it creates duplicate items)
            cover_image = epub.EpubImage()
            cover_image.file_name = f'images/cover.{cover_extension}'
            cover_image.media_type = f'image/{cover_extension}'
            cover_image.content = cover_image_data
            cover_image.id = 'cover-image'
            self.epub_book.add_item(cover_image)

            # Add cover metadata
            self.epub_book.add_metadata(None, 'meta', '', {'name': 'cover', 'content': 'cover-image'})

            cover_html = f'''
<div class="cover-page">
    <img src="images/cover.{cover_extension}" alt="Book Cover" class="cover-image"/>
    <h1 class="cover-title">{self._escape_html(self.book.title)}</h1>
    <p class="cover-subtitle">Analysis Report</p>
    <p class="cover-author">by {self._escape_html(self.book.author or 'Unknown')}</p>
</div>
'''
        else:
            # Text-only cover
            cover_html = f'''
<div class="cover-page text-only">
    <h1 class="cover-title">{self._escape_html(self.book.title)}</h1>
    <p class="cover-subtitle">Analysis Report</p>
    <p class="cover-author">by {self._escape_html(self.book.author or 'Unknown')}</p>
</div>
'''

        chapter = epub.EpubHtml(title='Cover', file_name='title.xhtml', lang='en')
        chapter.id = 'cover'
        chapter.set_content(self._wrap_html(cover_html))
        return chapter

    def _add_rating_overview_chapter(self, book_rating: Dict) -> epub.EpubHtml:
        """Create rating overview chapter."""
        overall = book_rating.get('overall_avg', 'N/A')
        verdict = book_rating.get('verdict', '')

        # Build criteria table
        criteria = [
            ('Insight', book_rating.get('insight_avg', 'N/A')),
            ('Clarity', book_rating.get('clarity_avg', 'N/A')),
            ('Evidence', book_rating.get('evidence_avg', 'N/A')),
            ('Engagement', book_rating.get('engagement_avg', 'N/A')),
            ('Actionability', book_rating.get('actionability_avg', 'N/A')),
        ]

        criteria_html = ''
        for name, score in criteria:
            criteria_html += f'''
            <tr>
                <td class="criterion-name">{name}</td>
                <td class="criterion-score">{score}/10</td>
            </tr>
            '''

        chapters_analyzed = book_rating.get('chapters_analyzed', 0)
        model_used = book_rating.get('model_used', 'unknown')

        html = f'''
        <h1>Rating Overview</h1>

        <div class="overall-score-section">
            <div class="score-badge">{overall}</div>
            <span class="score-max">/ 10</span>
        </div>

        <div class="verdict-section">
            <p class="verdict">{self._escape_html(verdict)}</p>
        </div>

        <h2>Criteria Scores</h2>
        <table class="criteria-table">
            {criteria_html}
        </table>

        <p class="meta-info">Based on {chapters_analyzed} chapters | Model: {model_used}</p>
        '''

        chapter = epub.EpubHtml(title='Rating Overview', file_name='rating.xhtml', lang='en')
        chapter.id = 'rating'
        chapter.set_content(self._wrap_html(html))
        return chapter

    def _add_highlights_chapter(self, book_rating: Dict) -> epub.EpubHtml:
        """Create best/worst chapter highlights page."""
        html = '<h1>Chapter Highlights</h1>'

        best = book_rating.get('best_chapter')
        if best:
            html += f'''
            <div class="highlight-box best">
                <h2>Best Chapter</h2>
                <p class="chapter-ref">Chapter {best.get('number', '?')}: {self._escape_html(best.get('title', 'Untitled'))}</p>
                <p class="highlight-score">Score: {best.get('score', 'N/A')}/10</p>
            </div>
            '''

        worst = book_rating.get('worst_chapter')
        if worst:
            html += f'''
            <div class="highlight-box weakest">
                <h2>Weakest Chapter</h2>
                <p class="chapter-ref">Chapter {worst.get('number', '?')}: {self._escape_html(worst.get('title', 'Untitled'))}</p>
                <p class="highlight-score">Score: {worst.get('score', 'N/A')}/10</p>
            </div>
            '''

        chapter = epub.EpubHtml(title='Chapter Highlights', file_name='highlights.xhtml', lang='en')
        chapter.id = 'highlights'
        chapter.set_content(self._wrap_html(html))
        return chapter

    def _add_readability_chapter(
        self, book_readability: Dict, readability_charts: Optional[Dict] = None
    ) -> epub.EpubHtml:
        """Create readability profile chapter with full enhanced data."""
        readability_charts = readability_charts or {}
        tier = book_readability.get('difficulty_tier', 'unknown').title()
        fre = book_readability.get('flesch_reading_ease', 'N/A')
        grade = book_readability.get('flesch_kincaid_grade', 'N/A')
        fog = book_readability.get('gunning_fog', 'N/A')
        hours = book_readability.get('reading_time_hours', 0)
        minutes = book_readability.get('reading_time_minutes', 0)
        total_words = book_readability.get('total_word_count', 0)
        chapters_analyzed = book_readability.get('chapters_analyzed', 0)

        time_display = f"{hours} hours" if hours >= 1 else f"{minutes} minutes"

        # Benchmark
        benchmark_html = ''
        benchmark = book_readability.get('benchmark')
        if benchmark:
            benchmark_html = f'''
            <p style="font-style: italic; color: #666;">
                Comparable to: {self._escape_html(benchmark.get('comparable_to', ''))}
            </p>
            <p style="font-size: 0.9em; color: #888;">
                {self._escape_html(benchmark.get('audience', ''))}
            </p>
            '''

        # Reading time variants
        skim_h = book_readability.get('reading_time_skim_hours', 0)
        skim_m = book_readability.get('reading_time_skim_minutes', 0)
        study_h = book_readability.get('reading_time_study_hours', 0)
        study_m = book_readability.get('reading_time_study_minutes', 0)
        skim_display = f"{skim_h}h" if skim_h >= 1 else f"{skim_m}m"
        study_display = f"{study_h}h" if study_h >= 1 else f"{study_m}m"

        time_variants_html = ''
        if skim_m or study_m:
            time_variants_html = f'''
            <table class="criteria-table" style="margin: 1em 0;">
                <tr>
                    <td class="criterion-name">Skim (350 wpm)</td>
                    <td class="criterion-score">{skim_display}</td>
                </tr>
                <tr>
                    <td class="criterion-name">Read (adjusted)</td>
                    <td class="criterion-score"><strong>{time_display}</strong></td>
                </tr>
                <tr>
                    <td class="criterion-name">Study (100 wpm)</td>
                    <td class="criterion-score">{study_display}</td>
                </tr>
            </table>
            '''

        # Score explanations
        explanations = book_readability.get('score_explanations', {})
        fre_exp = explanations.get('flesch_reading_ease', '')
        grade_exp = explanations.get('flesch_kincaid_grade', '')
        fog_exp = explanations.get('gunning_fog', '')

        fre_exp_html = f'<br/><span style="font-size: 0.85em; color: #888; font-style: italic;">{self._escape_html(fre_exp)}</span>' if fre_exp else ''
        grade_exp_html = f'<br/><span style="font-size: 0.85em; color: #888; font-style: italic;">{self._escape_html(grade_exp)}</span>' if grade_exp else ''
        fog_exp_html = f'<br/><span style="font-size: 0.85em; color: #888; font-style: italic;">{self._escape_html(fog_exp)}</span>' if fog_exp else ''

        # Difficulty curve SVG
        curve_html = ''
        if readability_charts.get('difficulty_curve'):
            narrative = book_readability.get('difficulty_narrative', '')
            narrative_p = f'<p style="font-style: italic; color: #666; margin-top: 0.5em;">{self._escape_html(narrative)}</p>' if narrative else ''
            curve_html = f'''
            <h2>Difficulty Curve</h2>
            {readability_charts['difficulty_curve']}
            {narrative_p}
            '''

        # Distribution SVG
        dist_html = ''
        if readability_charts.get('distribution'):
            dist_html = f'''
            <h2>Difficulty Distribution</h2>
            {readability_charts['distribution']}
            '''
        else:
            # Fallback to table-based distribution
            profile = book_readability.get('difficulty_profile', {})
            if profile:
                dist_html = '<h2>Difficulty Distribution</h2><table class="criteria-table">'
                for t, count in profile.items():
                    dist_html += f'<tr><td class="criterion-name">{t.title()}</td><td class="criterion-score">{count} chapters</td></tr>'
                dist_html += '</table>'

        # Chapter breakdown table
        breakdown_html = ''
        curve_data = book_readability.get('chapter_curve_data', [])
        if curve_data:
            rows = ''
            for ch in curve_data:
                rows += f'''
                <tr>
                    <td style="text-align: center; width: 40px;">{ch.get('chapter_number', '')}</td>
                    <td>{self._escape_html(ch.get('title', ''))}</td>
                    <td style="text-align: center;">{ch.get('difficulty_tier', '').title()}</td>
                    <td style="text-align: right;">{ch.get('flesch_kincaid_grade', '')}</td>
                    <td style="text-align: right;">{ch.get('reading_time_minutes', '')}m</td>
                </tr>
                '''
            breakdown_html = f'''
            <h2>Chapter Breakdown</h2>
            <table class="criteria-table">
                <tr>
                    <th style="text-align: center;">Ch.</th>
                    <th>Title</th>
                    <th style="text-align: center;">Tier</th>
                    <th style="text-align: right;">Grade</th>
                    <th style="text-align: right;">Time</th>
                </tr>
                {rows}
            </table>
            '''

        # Build extremes section
        extremes_html = ''
        hardest = book_readability.get('hardest_chapter')
        easiest = book_readability.get('easiest_chapter')
        if hardest:
            h_grade = hardest.get('flesch_kincaid_grade', '')
            h_time = hardest.get('reading_time_minutes', '')
            h_extra = f' | Grade {h_grade}' if h_grade else ''
            h_extra += f' | {h_time}m' if h_time else ''
            extremes_html += f'''
            <div class="highlight-box weakest">
                <h3>Hardest Chapter</h3>
                <p class="chapter-ref">Chapter {hardest.get('number', '?')}: {self._escape_html(hardest.get('title', 'Untitled'))}</p>
                <p>Difficulty: {hardest.get('difficulty_tier', '').title()} (FRE: {hardest.get('flesch_reading_ease', 'N/A')}){h_extra}</p>
            </div>
            '''
        if easiest:
            e_grade = easiest.get('flesch_kincaid_grade', '')
            e_time = easiest.get('reading_time_minutes', '')
            e_extra = f' | Grade {e_grade}' if e_grade else ''
            e_extra += f' | {e_time}m' if e_time else ''
            extremes_html += f'''
            <div class="highlight-box best">
                <h3>Easiest Chapter</h3>
                <p class="chapter-ref">Chapter {easiest.get('number', '?')}: {self._escape_html(easiest.get('title', 'Untitled'))}</p>
                <p>Difficulty: {easiest.get('difficulty_tier', '').title()} (FRE: {easiest.get('flesch_reading_ease', 'N/A')}){e_extra}</p>
            </div>
            '''

        html = f'''
        <h1>Readability Profile</h1>

        <div class="overall-score-section">
            <p style="font-size: 1.5em; font-weight: bold;">{tier}</p>
            {benchmark_html}
        </div>

        <h2>Reading Time</h2>
        {time_variants_html}

        <h2>Scores</h2>
        <table class="criteria-table">
            <tr><td class="criterion-name">Flesch Reading Ease</td><td class="criterion-score">{fre}{fre_exp_html}</td></tr>
            <tr><td class="criterion-name">Flesch-Kincaid Grade</td><td class="criterion-score">{grade}{grade_exp_html}</td></tr>
            <tr><td class="criterion-name">Gunning Fog Index</td><td class="criterion-score">{fog}{fog_exp_html}</td></tr>
        </table>

        {curve_html}

        {dist_html}

        {breakdown_html}

        {extremes_html}

        <p class="meta-info">{chapters_analyzed} chapters analyzed | {total_words:,} words total</p>
        '''

        chapter = epub.EpubHtml(title='Readability Profile', file_name='readability.xhtml', lang='en')
        chapter.id = 'readability'
        chapter.set_content(self._wrap_html(html))
        return chapter

    def _add_essence_chapters(self, book_essence: Dict) -> List[epub.EpubHtml]:
        """Create book essence chapters (overview, wisdom, references)."""
        chapters = []

        # Book Overview / Thesis
        book_thesis = book_essence.get('book_thesis')
        if book_thesis and book_thesis.get('content'):
            html = f'''
            <h1>Book Overview</h1>
            <div class="essence-content">
                {self._markdown_to_html(book_thesis['content'])}
            </div>
            '''
            ch = epub.EpubHtml(title='Book Overview', file_name='essence_overview.xhtml', lang='en')
            ch.id = 'essence_overview'
            ch.set_content(self._wrap_html(html))
            chapters.append(ch)
            self.toc_items.append(epub.Link(ch.file_name, 'Book Overview', ch.id))

        # Wisdom
        wisdom = book_essence.get('wisdom')
        if wisdom and wisdom.get('content'):
            html = f'''
            <h1>Wisdom</h1>
            <div class="essence-content">
                {self._markdown_to_html(wisdom['content'])}
            </div>
            '''
            ch = epub.EpubHtml(title='Wisdom', file_name='essence_wisdom.xhtml', lang='en')
            ch.id = 'essence_wisdom'
            ch.set_content(self._wrap_html(html))
            chapters.append(ch)
            self.toc_items.append(epub.Link(ch.file_name, 'Wisdom', ch.id))

        # References
        references = book_essence.get('references')
        if references and references.get('content'):
            html = f'''
            <h1>References</h1>
            <div class="essence-content">
                {self._markdown_to_html(references['content'])}
            </div>
            '''
            ch = epub.EpubHtml(title='References', file_name='essence_references.xhtml', lang='en')
            ch.id = 'essence_references'
            ch.set_content(self._wrap_html(html))
            chapters.append(ch)
            self.toc_items.append(epub.Link(ch.file_name, 'References', ch.id))

        return chapters

    def _add_chapter_analysis_chapters(self, chapter_analyses: List[Dict]) -> List[epub.EpubHtml]:
        """Create chapter-by-chapter analysis pages."""
        chapters = []

        for idx, analysis in enumerate(chapter_analyses):
            chapter_num = analysis.get('chapter_number', idx + 1)
            chapter_title = analysis.get('title', f'Chapter {chapter_num}')
            word_count = analysis.get('word_count', 0)
            readability = analysis.get('readability', {})

            # Build readability line
            readability_html = ''
            if readability and readability.get('difficulty_tier') and readability['difficulty_tier'] != 'unknown':
                tier_label = readability['difficulty_tier'].title()
                reading_min = readability.get('reading_time_minutes', '')
                time_str = f' | ~{reading_min} min' if reading_min else ''
                readability_html = f'<p class="word-count">{tier_label}{time_str}</p>'

            # Build summary section
            summary_html = ''
            summary = analysis.get('summary')
            if summary and summary.get('content'):
                summary_html = f'''
                <div class="chapter-summary">
                    {self._markdown_to_html(summary['content'])}
                </div>
                '''

            # Build rating section
            rating_html = ''
            rating = analysis.get('rating')
            if rating:
                overall = rating.get('overall', 'N/A')
                verdict = rating.get('one_line_verdict', '')

                rating_html = f'''
                <div class="chapter-rating">
                    <div class="rating-header">
                        <span class="rating-score">{overall}/10</span>
                    </div>
                    <table class="rating-criteria">
                        <tr><td>Insight</td><td>{rating.get('insight', 'N/A')}/10</td></tr>
                        <tr><td>Clarity</td><td>{rating.get('clarity', 'N/A')}/10</td></tr>
                        <tr><td>Evidence</td><td>{rating.get('evidence', 'N/A')}/10</td></tr>
                        <tr><td>Engagement</td><td>{rating.get('engagement', 'N/A')}/10</td></tr>
                        <tr><td>Actionability</td><td>{rating.get('actionability', 'N/A')}/10</td></tr>
                    </table>
                    <p class="verdict">{self._escape_html(verdict)}</p>
                </div>
                '''

            html = f'''
            <div class="chapter-header">
                <h1>Chapter {chapter_num}: {self._escape_html(chapter_title)}</h1>
                <p class="word-count">{word_count:,} words</p>
                {readability_html}
            </div>

            {summary_html}

            {rating_html if rating_html else '<p class="no-rating">No rating available</p>'}
            '''

            file_name = f'chapter_{chapter_num:03d}.xhtml'
            title = f'Ch. {chapter_num}: {chapter_title}'

            ch = epub.EpubHtml(title=title, file_name=file_name, lang='en')
            ch.id = f'chapter_{chapter_num}'
            ch.set_content(self._wrap_html(html))
            chapters.append(ch)

        return chapters

    def _markdown_to_html(self, content: str) -> str:
        """Convert markdown to EPUB-compatible HTML."""
        if not content:
            return ''

        try:
            # Convert markdown to HTML
            html = markdown.markdown(content, extensions=['extra', 'nl2br'])
            return html
        except Exception as e:
            logger.warning(f"Markdown conversion failed: {e}")
            # Fallback to escaped plain text
            return f'<p>{self._escape_html(content)}</p>'

    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters."""
        if not text:
            return ''
        return (
            str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
        )

    def _wrap_html(self, content: str) -> bytes:
        """Wrap content in XHTML document structure and encode as bytes."""
        html = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <title>{self._escape_html(self.book.title)}</title>
    <link rel="stylesheet" type="text/css" href="style/main.css"/>
</head>
<body>
{content}
</body>
</html>'''
        return html.encode('utf-8')

    def _get_css(self) -> str:
        """Return CSS stylesheet for EPUB content."""
        return '''
/* Base styles for e-reader compatibility */
body {
    font-family: Georgia, serif;
    line-height: 1.6;
    margin: 1em;
    color: #333;
}

h1 {
    font-size: 1.6em;
    margin-bottom: 0.5em;
    border-bottom: 1px solid #ccc;
    padding-bottom: 0.3em;
}

h2 {
    font-size: 1.3em;
    margin-top: 1.5em;
    margin-bottom: 0.5em;
}

h3 {
    font-size: 1.1em;
    margin-top: 1em;
}

p {
    margin: 0.8em 0;
}

/* Cover page */
.cover-page {
    text-align: center;
    padding: 2em 1em;
}

.cover-image {
    max-width: 100%;
    max-height: 60%;
    margin-bottom: 1em;
}

.cover-title {
    font-size: 2em;
    border-bottom: none;
    margin-bottom: 0.3em;
}

.cover-subtitle {
    font-size: 1.2em;
    color: #666;
    font-style: italic;
}

.cover-author {
    font-size: 1.1em;
    margin-top: 1em;
}

/* Score display */
.overall-score-section {
    text-align: center;
    margin: 1.5em 0;
}

.score-badge {
    display: inline-block;
    font-size: 3em;
    font-weight: bold;
}

.score-max {
    font-size: 1.5em;
    color: #666;
}

/* Verdict */
.verdict-section {
    margin: 1.5em 0;
    padding: 1em;
    background-color: #f9f9f9;
    border-left: 4px solid #333;
}

.verdict {
    font-style: italic;
    color: #444;
}

/* Criteria table */
.criteria-table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
}

.criteria-table td {
    padding: 0.5em;
    border-bottom: 1px solid #ddd;
}

.criterion-name {
    font-weight: bold;
}

.criterion-score {
    text-align: right;
}

/* Highlight boxes */
.highlight-box {
    padding: 1em;
    margin: 1em 0;
    border-radius: 0.5em;
}

.highlight-box.best {
    background-color: #e8f5e9;
    border-left: 4px solid #4CAF50;
}

.highlight-box.weakest {
    background-color: #fff8e1;
    border-left: 4px solid #FFC107;
}

.highlight-box h2 {
    margin-top: 0;
    font-size: 1.1em;
}

.chapter-ref {
    font-weight: bold;
}

.highlight-score {
    color: #666;
}

/* Essence content */
.essence-content {
    margin-top: 1em;
}

/* Chapter analysis */
.chapter-header {
    border-bottom: 2px solid #333;
    padding-bottom: 0.5em;
    margin-bottom: 1em;
}

.chapter-header h1 {
    border-bottom: none;
    margin-bottom: 0.2em;
}

.word-count {
    color: #666;
    font-size: 0.9em;
}

.chapter-summary {
    margin: 1em 0;
}

.chapter-rating {
    margin-top: 1.5em;
    padding: 1em;
    background-color: #f5f5f5;
}

.rating-header {
    text-align: center;
    margin-bottom: 0.5em;
}

.rating-score {
    font-size: 1.8em;
    font-weight: bold;
}

.rating-criteria {
    width: 100%;
    border-collapse: collapse;
    margin: 0.5em 0;
}

.rating-criteria td {
    padding: 0.3em 0.5em;
    border-bottom: 1px solid #ddd;
}

.rating-criteria td:last-child {
    text-align: right;
}

.no-rating {
    color: #999;
    font-style: italic;
}

/* Metadata */
.meta-info {
    margin-top: 2em;
    font-size: 0.85em;
    color: #666;
    text-align: center;
}

/* Lists */
ul, ol {
    margin: 1em 0;
    padding-left: 2em;
}

li {
    margin-bottom: 0.5em;
}

/* Blockquotes */
blockquote {
    margin: 1em 0;
    padding-left: 1em;
    border-left: 3px solid #999;
    font-style: italic;
    color: #555;
}

/* Tables in content */
table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
}

th, td {
    padding: 0.5em;
    border: 1px solid #ddd;
    text-align: left;
}

th {
    background-color: #f5f5f5;
    font-weight: bold;
}
'''

    def _write_to_bytes(self) -> bytes:
        """Write EPUB to bytes buffer for streaming."""
        buffer = io.BytesIO()
        epub.write_epub(buffer, self.epub_book)
        buffer.seek(0)
        return buffer.read()
