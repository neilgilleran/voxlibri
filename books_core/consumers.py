"""
WebSocket consumers for real-time batch processing updates.

Handles WebSocket connections for batch summary generation jobs, broadcasting
progress updates to connected clients.
"""

import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class BatchProgressConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for batch processing progress updates.

    Clients connect to ws://host/ws/batch/{job_id}/ to receive real-time
    progress updates for a specific batch processing job.

    Message format:
    {
        "type": "progress",
        "chapter_id": 123,
        "status": "success|error|processing",
        "message": "Processing chapter...",
        "progress": 50,
        "summary_id": 456 (optional, only on success)
    }
    """

    async def connect(self):
        """
        Accept WebSocket connection and join batch job group.
        """
        self.job_id = self.scope['url_route']['kwargs']['job_id']
        self.group_name = f'batch_{self.job_id}'

        # Join batch group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        # Accept connection
        await self.accept()

        logger.info(f"WebSocket connection accepted for job {self.job_id}")

    async def disconnect(self, close_code):
        """
        Handle WebSocket disconnection and leave batch job group.
        """
        # Leave batch group
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

        logger.info(f"WebSocket connection closed for job {self.job_id} with code {close_code}")

    async def batch_progress(self, event):
        """
        Receive progress message from channel layer and send to WebSocket client.

        Args:
            event: Dictionary with 'message' key containing progress data
        """
        message = event['message']

        # Send message to WebSocket
        await self.send(text_data=json.dumps(message))

        logger.debug(f"Sent progress update for job {self.job_id}: {message}")
