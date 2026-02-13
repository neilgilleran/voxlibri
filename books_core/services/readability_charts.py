"""
Server-side SVG generation for readability visualizations.

Generates inline SVG strings for:
- Difficulty curve (grade level per chapter as a line chart)
- Distribution bars (chapters per difficulty tier as horizontal bars)

No external dependencies - pure Python string generation.
"""

from typing import Dict, List


# Tier colors matching the CSS tier-* classes
TIER_COLORS = {
    'accessible': '#28a745',
    'moderate': '#ffc107',
    'technical': '#e67e22',
    'dense': '#dc3545',
}

# Grade level boundaries for tier background bands
TIER_BANDS = [
    ('accessible', 0, 8, '#d4edda'),
    ('moderate', 8, 12, '#fff3cd'),
    ('technical', 12, 16, '#ffe0cc'),
    ('dense', 16, 24, '#f8d7da'),
]


class ReadabilityChartService:
    """Generate SVG charts for readability data."""

    def generate_difficulty_curve_svg(self, chapter_data: List[Dict]) -> str:
        """
        Generate an SVG line chart of grade level across chapters.

        Args:
            chapter_data: List of dicts with chapter_number, flesch_kincaid_grade,
                          difficulty_tier, title

        Returns:
            SVG string ready for inline embedding
        """
        if not chapter_data or len(chapter_data) < 2:
            return ''

        n = len(chapter_data)
        grades = [d['flesch_kincaid_grade'] for d in chapter_data]

        # Chart dimensions
        width = 700
        height = 280
        pad_left = 50
        pad_right = 20
        pad_top = 20
        pad_bottom = 40

        plot_w = width - pad_left - pad_right
        plot_h = height - pad_top - pad_bottom

        # Y-axis range: snap to nice boundaries
        y_min = max(0, (min(grades) // 2) * 2 - 2)
        y_max = max(max(grades) + 2, y_min + 6)
        y_max = ((y_max + 1) // 2) * 2  # round up to even
        y_range = y_max - y_min if y_max > y_min else 1

        def x_pos(i):
            return pad_left + (i / max(n - 1, 1)) * plot_w

        def y_pos(grade):
            return pad_top + (1 - (grade - y_min) / y_range) * plot_h

        # Build SVG
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'class="difficulty-curve-svg" role="img" aria-label="Difficulty curve chart">'
        ]

        # Background tier bands (clipped to plot area)
        parts.append(f'<defs><clipPath id="plot-clip"><rect x="{pad_left}" y="{pad_top}" '
                      f'width="{plot_w}" height="{plot_h}"/></clipPath></defs>')
        parts.append('<g clip-path="url(#plot-clip)">')
        for tier_name, band_min, band_max, color in TIER_BANDS:
            band_y_top = y_pos(min(band_max, y_max))
            band_y_bottom = y_pos(max(band_min, y_min))
            band_height = band_y_bottom - band_y_top
            if band_height > 0:
                parts.append(
                    f'<rect x="{pad_left}" y="{band_y_top}" width="{plot_w}" '
                    f'height="{band_height}" fill="{color}" opacity="0.3"/>'
                )
        parts.append('</g>')

        # Y-axis gridlines and labels
        y_step = 2 if y_range <= 16 else 4
        grade_val = int(y_min)
        while grade_val <= y_max:
            yp = y_pos(grade_val)
            parts.append(
                f'<line x1="{pad_left}" y1="{yp}" x2="{pad_left + plot_w}" y2="{yp}" '
                f'stroke="#ccc" stroke-width="0.5"/>'
            )
            parts.append(
                f'<text x="{pad_left - 8}" y="{yp + 4}" text-anchor="end" '
                f'font-size="11" fill="#666">{grade_val}</text>'
            )
            grade_val += y_step

        # Y-axis title
        parts.append(
            f'<text x="14" y="{pad_top + plot_h / 2}" text-anchor="middle" '
            f'font-size="11" fill="#666" transform="rotate(-90 14 {pad_top + plot_h / 2})">Grade Level</text>'
        )

        # X-axis labels (skip if too many chapters)
        skip = 1
        if n > 30:
            skip = 5
        elif n > 15:
            skip = 2
        for i, d in enumerate(chapter_data):
            if i % skip == 0 or i == n - 1:
                xp = x_pos(i)
                parts.append(
                    f'<text x="{xp}" y="{height - 8}" text-anchor="middle" '
                    f'font-size="10" fill="#666">{d["chapter_number"]}</text>'
                )

        # X-axis title
        parts.append(
            f'<text x="{pad_left + plot_w / 2}" y="{height - 0}" text-anchor="middle" '
            f'font-size="11" fill="#666">Chapter</text>'
        )

        # Data polyline
        points = ' '.join(f'{x_pos(i)},{y_pos(g)}' for i, g in enumerate(grades))
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="#4a6fa5" '
            f'stroke-width="2.5" stroke-linejoin="round"/>'
        )

        # Data points with tooltips
        for i, d in enumerate(chapter_data):
            cx = x_pos(i)
            cy = y_pos(d['flesch_kincaid_grade'])
            color = TIER_COLORS.get(d.get('difficulty_tier', ''), '#4a6fa5')
            title = f'Ch. {d["chapter_number"]}: {d.get("title", "")} (Grade {d["flesch_kincaid_grade"]})'
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="4.5" fill="{color}" stroke="white" stroke-width="1.5">'
                f'<title>{_escape(title)}</title></circle>'
            )

        parts.append('</svg>')
        return '\n'.join(parts)

    def generate_distribution_bars_svg(self, difficulty_profile: Dict) -> str:
        """
        Generate horizontal bar chart of chapters per difficulty tier.

        Args:
            difficulty_profile: Dict like {'accessible': 3, 'moderate': 5, ...}

        Returns:
            SVG string ready for inline embedding
        """
        if not difficulty_profile:
            return ''

        # Ordered tiers
        tiers = ['accessible', 'moderate', 'technical', 'dense']
        data = [(t, difficulty_profile.get(t, 0)) for t in tiers]
        max_count = max((count for _, count in data), default=1) or 1

        width = 400
        bar_height = 32
        gap = 8
        pad_left = 90
        pad_right = 50
        pad_top = 10
        bar_area_w = width - pad_left - pad_right
        total_height = pad_top + len(data) * (bar_height + gap)

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {total_height}" '
            f'class="distribution-bars-svg" role="img" aria-label="Difficulty distribution chart">'
        ]

        for i, (tier, count) in enumerate(data):
            y = pad_top + i * (bar_height + gap)
            bar_w = (count / max_count) * bar_area_w if max_count > 0 else 0
            color = TIER_COLORS.get(tier, '#999')

            # Tier label
            parts.append(
                f'<text x="{pad_left - 8}" y="{y + bar_height / 2 + 5}" '
                f'text-anchor="end" font-size="13" fill="#444" font-weight="500">'
                f'{tier.title()}</text>'
            )

            # Bar background
            parts.append(
                f'<rect x="{pad_left}" y="{y}" width="{bar_area_w}" height="{bar_height}" '
                f'rx="4" fill="#f0f0f0"/>'
            )

            # Filled bar
            if bar_w > 0:
                parts.append(
                    f'<rect x="{pad_left}" y="{y}" width="{bar_w}" height="{bar_height}" '
                    f'rx="4" fill="{color}" opacity="0.8"/>'
                )

            # Count label
            label_x = pad_left + bar_w + 8
            parts.append(
                f'<text x="{label_x}" y="{y + bar_height / 2 + 5}" '
                f'font-size="13" fill="#444" font-weight="600">'
                f'{count} ch.</text>'
            )

        parts.append('</svg>')
        return '\n'.join(parts)


def _escape(text: str) -> str:
    """Escape XML special characters."""
    return (
        str(text)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )
