"""
Custom template tags for markdown rendering.
"""
from django import template
from django.utils.safestring import mark_safe
import markdown as md

register = template.Library()


@register.filter(name='markdown')
def markdown(value):
    """
    Convert markdown text to HTML.

    Usage:
        {{ chapter.content|markdown }}

    Args:
        value: Markdown text string

    Returns:
        HTML string marked as safe
    """
    if not value:
        return ''

    # Convert markdown to HTML with common extensions
    html = md.markdown(
        value,
        extensions=[
            'extra',  # Enables tables, fenced code blocks, etc.
            'nl2br',  # Converts newlines to <br>
        ]
    )

    return mark_safe(html)
