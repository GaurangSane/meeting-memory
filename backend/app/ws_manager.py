"""
app/ws_manager.py

One ConnectionContext per active WebSocket. Holds the per-connection state
for a single meeting's audio stream: chunk counter, in-flight STT futures.

CRITICAL PRODUCTION DETAIL: transcript chunks are written to Redis
(`meeting:{id}:transcript_chunks`, a Redis LIST) on every successful STT
result — NOT kept only in an in-process Python list. Two reasons:

  1. The Celery worker that eventually generates the MOM runs in a
     *different process* than the FastAPI WS handler; it cannot read
     the WS handler's in-memory state.

  2. If the WS connection drops mid-meeting and the client reconnects
     (same meeting_id), the durable Redis buffer survives; an in-memory
     list would silently lose all chunks captured before the disconnect.

Redis key schema
----------------
  meeting:{meeting_id}:transcript_chunks  — RPUSH, stored as "<idx>|<text>"
  TTL: set to 24h on first push to prevent orphaned keys from aborted meetings.

Chunk ordering
--------------
  Chunks are stored with their numeric index prepended (`idx|text`) so
  `get_ordered_transcript` can reconstruct the correct order even if
  concurrent STT callbacks resolve out-of-order (which happens when chunk 3
  finishes STT before chunk 2 due to variable network latency).
"""

import asyncio
import logging
from dataclasses import dataclass, field

from app.core.redis_client import redis_client

logger = logging.getLogger(__name__)

_TRANSCRIPT_TTL_SECONDS = 24 * 60 * 60  # 24 hours


@dataclass
class ConnectionContext:
    """
    Holds mutable per-connection state for one active WS audio stream.

    chunk_index:       monotonically increasing counter, incremented before
                       each asyncio.Task is created so every chunk gets a
                       unique index without a lock (single asyncio thread).
    in_flight_futures: list of asyncio.Tasks for pending STT calls;
                       used in the finally block to await all of them before
                       handing off to Celery.
    """
    meeting_id: str
    org_id: str
    user_id: str
    chunk_index: int = 0
    in_flight_futures: list = field(default_factory=list)


class WSConnectionManager:
    """
    In-process registry of active WebSocket connections.

    Only one process (the uvicorn worker that accepted the connection) holds
    a ConnectionContext for any given meeting_id. The durable state that
    survives process restarts lives in Redis, not here.
    """

    def __init__(self) -> None:
        self._connections: dict[str, ConnectionContext] = {}

    def register(self, ctx: ConnectionContext) -> None:
        """Register a new connection. Replaces any stale context for the same meeting."""
        if ctx.meeting_id in self._connections:
            logger.warning(
                "Replacing stale ConnectionContext for meeting=%s", ctx.meeting_id
            )
        self._connections[ctx.meeting_id] = ctx
        logger.info(
            "WS registered meeting=%s org=%s user=%s",
            ctx.meeting_id, ctx.org_id, ctx.user_id,
        )

    def get(self, meeting_id: str) -> ConnectionContext | None:
        return self._connections.get(meeting_id)

    def unregister(self, meeting_id: str) -> None:
        ctx = self._connections.pop(meeting_id, None)
        if ctx is not None:
            logger.info(
                "WS unregistered meeting=%s (total chunks=%d)",
                meeting_id, ctx.chunk_index,
            )

    # ── Redis-backed transcript buffer ────────────────────────────────────────

    @staticmethod
    async def append_transcript_chunk(
        meeting_id: str, chunk_index: int, text: str
    ) -> None:
        """
        Durably append a transcribed chunk to the Redis LIST.

        Uses RPUSH (right-push) so chunks arrive in append order within
        each connection, then are re-sorted by numeric index on retrieval
        to handle out-of-order concurrent STT completions.

        A 24-hour TTL is refreshed on every push to prevent orphaned keys
        from meetings that never received a 'stop' signal.
        """
        key = f"meeting:{meeting_id}:transcript_chunks"
        await redis_client.rpush(key, f"{chunk_index}|{text}")
        await redis_client.expire(key, _TRANSCRIPT_TTL_SECONDS)

    @staticmethod
    async def get_ordered_transcript(meeting_id: str) -> str:
        """
        Read all chunks from Redis, sort by chunk_index, and join into a
        single space-separated transcript string.

        Handles out-of-order chunks: concurrent STT calls may resolve in a
        different order than they were dispatched, so we never trust RPUSH
        order alone — always sort by the numeric prefix.

        Returns an empty string if no chunks were stored (silent meeting,
        all chunks dropped by VAD).
        """
        key = f"meeting:{meeting_id}:transcript_chunks"
        raw_chunks = await redis_client.lrange(key, 0, -1)
        if not raw_chunks:
            return ""

        parsed: list[tuple[int, str]] = []
        for raw in raw_chunks:
            decoded = raw.decode()
            idx_str, _, text = decoded.partition("|")
            try:
                parsed.append((int(idx_str), text))
            except ValueError:
                logger.warning(
                    "Malformed transcript chunk in Redis for meeting=%s: %r",
                    meeting_id, decoded,
                )

        parsed.sort(key=lambda x: x[0])
        return " ".join(text for _, text in parsed if text.strip())

    @staticmethod
    async def cleanup_transcript_buffer(meeting_id: str) -> None:
        """
        Delete the Redis buffer after the MOM task has consumed the transcript.
        Called after generate_mom_task is enqueued so stale keys don't persist.
        """
        key = f"meeting:{meeting_id}:transcript_chunks"
        await redis_client.delete(key)
        logger.debug("Cleaned up transcript buffer for meeting=%s", meeting_id)


ws_manager = WSConnectionManager()
