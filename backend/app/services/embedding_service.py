"""
app/services/embedding_service.py

Gemini embedding wrapper — Phase 5, Step 5.3.

Wraps the Google Generative AI SDK's embed_content() call into an async
function. The underlying SDK call is synchronous (uses requests internally),
so we run it in a thread pool executor to avoid blocking the asyncio event
loop during Gemini's network round-trip.

Model: gemini-embedding-001 (3072-dimensional output)
  - Chosen for: high semantic quality on multilingual Indian English text,
    native support in Google AI Studio, and direct compatibility with
    pgvector's VECTOR(3072) column type defined in the Phase 1 migration.

Usage:
    embedding: list[float] = await embed_text("Q3 Sprint Planning...")
    # len(embedding) == 3072

Error handling:
    Any exception from the Gemini SDK propagates to the caller. In
    rag_service.py this would abort the RAG retrieval; in tasks_embeddings.py
    the Celery task will retry. Do not silently catch here.
"""

import asyncio
import logging
from functools import partial
from dotenv import load_dotenv
load_dotenv()
import google.generativeai as genai

from app.core.config import settings

logger = logging.getLogger(__name__)

genai.configure(api_key=settings.GEMINI_API_KEY)


async def embed_text(text: str) -> list[float]:
    loop = asyncio.get_running_loop()
    
    # Read the model name exactly as defined in settings
    model_name = settings.GEMINI_EMBEDDING_MODEL
    
    # Guarantee the SDK prefix exists without altering the model number
    if not model_name.startswith("models/"):
        model_name = f"models/{model_name}"

    sync_call = partial(
        genai.embed_content,
        model=model_name,
        content=text,
    )
    result = await loop.run_in_executor(None, sync_call)
    embedding: list[float] = result["embedding"]
    
    logger.debug(
        "Embedded text (len=%d) → vector dim=%d", len(text), len(embedding)
    )
    return embedding
