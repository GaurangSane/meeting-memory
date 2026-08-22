"""
app/core/redis_client.py

Shared async Redis client instance. Imported by:
  - security.py  (WS ticket store/consume)
  - ws_manager.py (durable transcript buffer)
  - Any future module that needs Redis (rate limiting, caching, etc.)

redis.asyncio is the asyncio-native interface bundled with redis-py >= 4.2.
"""

import redis.asyncio as aioredis
from app.core.config import settings

redis_client: aioredis.Redis = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=False,  # keep bytes for binary safety; callers decode where needed
)
