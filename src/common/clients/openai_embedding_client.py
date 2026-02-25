"""OpenAI Embeddings API client — thin wrapper for text embedding."""

from dataclasses import dataclass
from typing import List, Optional

from openai import AsyncOpenAI

from src.settings import settings

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIM = 3072

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    """Return singleton AsyncOpenAI client."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


@dataclass
class EmbeddingResult:
    """Result of an embedding API call."""

    vectors: List[List[float]]
    model: str
    total_tokens: int


async def embed_texts(
    texts: List[str],
    model: str = EMBEDDING_MODEL,
) -> EmbeddingResult:
    """
    Embed a list of texts via OpenAI Embeddings API.

    Args:
        texts: List of text strings to embed.
        model: Embedding model name (default: text-embedding-3-large).

    Returns:
        EmbeddingResult with vectors, model, and total_tokens.

    Raises:
        openai.APIError: On API failure.
    """
    if not texts:
        return EmbeddingResult(vectors=[], model=model, total_tokens=0)

    client = _get_client()
    response = await client.embeddings.create(
        input=texts,
        model=model,
    )

    vectors = [item.embedding for item in response.data]
    total_tokens = response.usage.total_tokens if response.usage else 0

    return EmbeddingResult(
        vectors=vectors,
        model=model,
        total_tokens=total_tokens,
    )
