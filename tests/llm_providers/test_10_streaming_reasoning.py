"""Test 10: Streaming + Reasoning combined.

Run: poetry run python tests/llm_providers/test_10_streaming_reasoning.py

Evidence: event sequence when stream has reasoning/thinking + text.
"""

import sys
import warnings
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from tests.llm_providers.utils import (
    get_anthropic_client_direct,
    get_anthropic_model,
    get_gemini_client,
    get_gemini_model,
    save_evidence,
    serialize_response,
)

TEST_NAME = "10_streaming_reasoning"
PROMPT = "Solve step by step: What is the sum of all prime numbers less than 20?"


def _safe_event_data(
    ev,
):  # avoid event.model_dump() to prevent Pydantic union serialization warnings
    out = {"type": getattr(ev, "type", type(ev).__name__)}
    if hasattr(ev, "content_block") and ev.content_block is not None:
        cb = ev.content_block
        out["content_block"] = {"type": getattr(cb, "type", None)}
        if getattr(cb, "text", None) is not None:
            out["content_block"]["text"] = (cb.text or "")[:200]
        if getattr(cb, "thinking", None) is not None:
            out["content_block"]["thinking"] = (cb.thinking or "")[:200]
    if hasattr(ev, "delta") and ev.delta is not None:
        d = ev.delta
        out["delta"] = {"type": getattr(d, "type", None)}
        if getattr(d, "text", None) is not None:
            out["delta"]["text"] = (d.text or "")[:200]
        if getattr(d, "thinking", None) is not None:
            out["delta"]["thinking"] = (d.thinking or "")[:200]
    if hasattr(ev, "index") and ev.index is not None:
        out["index"] = ev.index
    return out


def run_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    request_params = {
        "model": model,
        "max_tokens": 16000,
        "thinking": {"type": "enabled", "budget_tokens": 10000},
        "messages": [{"role": "user", "content": PROMPT}],
    }
    events = []
    final_message = None
    with client.messages.stream(**request_params) as stream:
        for event in stream:
            data = _safe_event_data(event)
            variant = data.get("type") or type(event).__name__
            events.append({"type": variant, "data": data})
        final_message = stream.get_final_message()

    event_types_seen = list({e["type"] for e in events})
    # Detect thinking vs text block order from events
    thinking_delta_seen = any(
        e.get("data", {}).get("delta", {}).get("thinking") is not None for e in events
    )
    text_delta_seen = any(
        e.get("data", {}).get("delta", {}).get("text") is not None for e in events
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        final_serialized = serialize_response(final_message) if final_message else None

    save_evidence(
        test_name=TEST_NAME,
        provider="anthropic",
        request_data={"method": "messages.stream", "params": request_params},
        raw_response={
            "events": events,
            "event_count": len(events),
            "event_types": event_types_seen,
            "thinking_delta_seen": thinking_delta_seen,
            "text_delta_seen": text_delta_seen,
            "final_message": final_serialized,
        },
        key_observations={
            "event_types_complete_list": event_types_seen,
            "event_ordering": "message_start -> content_block_start (thinking) -> content_block_delta (thinking_delta) -> content_block_stop -> content_block_start (text) -> content_block_delta (text_delta) -> content_block_stop -> message_delta -> message_stop",
            "thinking_delta_format": "content_block_delta with delta.thinking",
            "boundary_thinking_text": "content_block_stop (thinking) then content_block_start (type=text)",
            "final_message_has_thinking_and_text": _final_has_thinking_and_text(
                final_serialized
            ),
            "openai_event_mapping": {
                "response.output_item.added (reasoning)": "content_block_start (type=thinking)",
                "response.reasoning_summary_text.delta": "content_block_delta (thinking_delta)",
                "response.output_item.done (reasoning)": "content_block_stop",
                "response.output_text.delta": "content_block_delta (text_delta)",
            },
        },
        mapping={
            "equivalent_openai_param": "stream() with reasoning; events include reasoning then text",
            "differences": [
                "Anthropic: thinking block then text block in same stream; OpenAI: reasoning then message",
                "Anthropic: delta.thinking and delta.text in content_block_delta",
            ],
            "conversion_needed": "Map content_block_delta (thinking_delta) to response.reasoning_summary_text.delta; (text_delta) to response.output_text.delta.",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=(
            getattr(final_message, "model", None) if final_message else None
        ),
    )
    print("Anthropic: OK", "-", len(events), "events", "-", event_types_seen)


def _final_has_thinking_and_text(final_serialized):  # type: ignore[no-untyped-def]
    if not final_serialized or not isinstance(final_serialized, dict):
        return "N/A"
    content = final_serialized.get("content") or []
    types_found = [c.get("type") for c in content if isinstance(c, dict)]
    return "thinking" in types_found and "text" in types_found


def run_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(include_thoughts=True),
    )
    chunks = []
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=PROMPT,
        config=config,
    ):
        raw = serialize_response(chunk)
        # Extract thought vs text from chunk
        thought_parts = []
        text_parts = []
        for c in (raw.get("candidates") or [])[:1]:
            content = c.get("content") or {}
            parts = content.get("parts") or []
            for p in parts:
                if isinstance(p, dict):
                    if p.get("thought") is True:
                        thought_parts.append(p.get("text", "")[:200])
                    elif p.get("text"):
                        text_parts.append(p.get("text", "")[:200])
        chunks.append(
            {
                "raw_keys": list(raw.keys()),
                "thought_parts_count": len(thought_parts),
                "text_parts_count": len(text_parts),
                "thought_preview": thought_parts[0][:100] if thought_parts else None,
                "text_preview": text_parts[0][:100] if text_parts else None,
            }
        )

    event_types_seen = (
        ["GenerateContentChunk_with_thoughts"] * len(chunks) if chunks else []
    )
    save_evidence(
        test_name=TEST_NAME,
        provider="gemini",
        request_data={
            "method": "generate_content_stream",
            "params": {
                "model": model,
                "thinking_config": {"include_thoughts": True},
                "contents": PROMPT,
            },
        },
        raw_response={
            "chunks": chunks,
            "chunk_count": len(chunks),
            "event_types": event_types_seen,
        },
        key_observations={
            "event_types_complete_list": (
                list(set(event_types_seen)) if event_types_seen else []
            ),
            "event_ordering": "Chunks may contain thought=True parts then text parts; verify ordering",
            "thinking_in_chunks": any(
                c.get("thought_parts_count", 0) > 0 for c in chunks
            ),
            "boundary_thinking_text": "Same chunk can have both thought and text parts; or thought chunks then text chunks",
            "openai_event_mapping": {
                "response.reasoning_summary_text.delta": "chunk parts with thought=True",
                "response.output_text.delta": "chunk parts with text",
            },
        },
        mapping={
            "equivalent_openai_param": "Stream chunks; thought parts then text parts",
            "differences": [
                "Gemini: no named events; chunks have parts with thought=True or text"
            ],
            "conversion_needed": "Map chunk parts with thought=True to reasoning deltas; text parts to output_text deltas.",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=None,
    )
    print("Gemini: OK", "-", len(chunks), "chunks")


def main() -> None:
    print("Test 10: Streaming + Reasoning")
    print("Anthropic (direct)...")
    run_anthropic()
    print("Gemini...")
    run_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
