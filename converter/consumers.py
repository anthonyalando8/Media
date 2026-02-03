"""
WebSocket consumers for real-time progress updates.
"""
import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
import redis.asyncio as aioredis
from django.conf import settings


class ConversionProgressConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer that sends real-time conversion progress updates.
    """
    
    async def connect(self):
        """Handle WebSocket connection"""
        self.task_id = self.scope['url_route']['kwargs']['task_id']
        self.redis = await aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        
        await self.accept()
        
        # Start sending progress updates
        asyncio.create_task(self.send_progress_updates())
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection"""
        if hasattr(self, 'redis'):
            await self.redis.close()
    
    async def send_progress_updates(self):
        """Continuously send progress updates to the client"""
        try:
            while True:
                # Get progress from Redis
                progress_key = f'conversion_progress:{self.task_id}'
                progress_data = await self.redis.get(progress_key)
                
                if progress_data:
                    data = json.loads(progress_data)
                    
                    # Send to WebSocket client
                    await self.send(text_data=json.dumps(data))
                    
                    # If conversion finished (success or failure), close connection
                    if data.get('state') in ['SUCCESS', 'FAILURE']:
                        await asyncio.sleep(1)  # Give client time to receive final update
                        await self.close()
                        break
                
                # Poll every 500ms
                await asyncio.sleep(0.5)
                
        except Exception as e:
            await self.send(text_data=json.dumps({
                'state': 'ERROR',
                'message': f'Error: {str(e)}'
            }))
            await self.close()
    
    async def receive(self, text_data):
        """Handle messages from WebSocket client (optional)"""
        pass