"""
WebSocket URL routing for books_core application.

Defines URL patterns for WebSocket connections used in batch processing
and real-time progress updates.
"""

from django.urls import path
from books_core import consumers

websocket_urlpatterns = [
    path('ws/batch/<str:job_id>/', consumers.BatchProgressConsumer.as_asgi()),
]
