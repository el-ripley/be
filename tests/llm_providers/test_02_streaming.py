"""Test 02: Streaming events for Anthropic and Gemini.

Run: poetry run python tests/llm_providers/test_02_streaming.py
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

TEST_NAME = "02_streaming"
PROMPT = "Write a short poem about coding."
SYSTEM = "You are a helpful assistant."


def run_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    request_params = {
        "model": get_anthropic_model(),
        "max_tokens": 1024,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": PROMPT}],
    }

    def _safe_event_data(
        ev,
    ):  # avoid event.model_dump() to prevent Pydantic union serialization warnings
        out = {"type": getattr(ev, "type", type(ev).__name__)}
        if hasattr(ev, "content_block") and ev.content_block is not None:
            cb = ev.content_block
            out["content_block"] = {"type": getattr(cb, "type", None)}
            if getattr(cb, "text", None) is not None:
                out["content_block"]["text"] = cb.text
            if getattr(cb, "thinking", None) is not None:
                out["content_block"]["thinking"] = (cb.thinking or "")[:200]
        if hasattr(ev, "delta") and ev.delta is not None:
            d = ev.delta
            out["delta"] = {"type": getattr(d, "type", None)}
            if getattr(d, "text", None) is not None:
                out["delta"]["text"] = d.text
            if getattr(d, "thinking", None) is not None:
                out["delta"]["thinking"] = (d.thinking or "")[:200]
        if hasattr(ev, "index") and ev.index is not None:
            out["index"] = ev.index
        return out

    events = []
    final_message = None
    with client.messages.stream(**request_params) as stream:
        for event in stream:
            data = _safe_event_data(event)
            variant = data.get("type") or type(event).__name__
            events.append({"type": variant, "data": data})
        final_message = stream.get_final_message()

    event_types_seen = list({e["type"] for e in events})
    delta_location = (
        "content_block_delta.delta.text (text_delta)"
        if any(e.get("type") == "content_block_delta" for e in events)
        else "see events"
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        final_message_serialized = (
            serialize_response(final_message) if final_message else None
        )

    save_evidence(
        test_name=TEST_NAME,
        provider="anthropic",
        request_data={"method": "messages.stream", "params": request_params},
        raw_response={
            "events": events,
            "event_count": len(events),
            "event_types": event_types_seen,
            "final_message": final_message_serialized,
        },
        key_observations={
            "event_types_complete_list": event_types_seen,
            "event_ordering": "message_start -> content_block_start -> content_block_delta (repeated) -> content_block_stop -> message_delta -> message_stop",
            "delta_format_location": delta_location,
            "final_response_method": "stream.get_final_message()",
            "openai_event_mapping": {
                "response.created": "message_start",
                "response.output_text.delta": "content_block_delta (text_delta)",
                "response.output_item.done": "content_block_stop + message_stop",
                "response.reasoning_summary_text.delta": "N/A or content_block_delta (thinking)",
                "response.failed": "error event type if any",
            },
        },
        mapping={
            "equivalent_openai_param": "stream() yields events; get_final_response() at end",
            "differences": [
                "Anthropic event names: message_start, content_block_delta, etc.; OpenAI: response.output_text.delta",
                "Anthropic: context manager with get_final_message(); OpenAI: get_final_response() on stream",
            ],
            "conversion_needed": "Map Anthropic event types to normalized internal events for llm_stream_handler.",
        },
        model_used=get_anthropic_model(),
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(final_message, "model", None)
        if final_message
        else None,
    )
    print("Anthropic: OK", "-", len(events), "events", "-", event_types_seen)


def run_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    request_params = {"prompt": PROMPT, "stream": True}
    chunks = []
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=PROMPT,
        config=types.GenerateContentConfig(system_instruction=SYSTEM),
    ):
        chunk_data = {"text": getattr(chunk, "text", None)}
        chunks.append(
            serialize_response(chunk) if hasattr(chunk, "model_dump") else chunk_data
        )
    event_types_seen = ["GenerateContentChunk"] * len(chunks) if chunks else []

    save_evidence(
        test_name=TEST_NAME,
        provider="gemini",
        request_data={
            "method": "generate_content_stream",
            "params": {"model": model, "system_instruction": SYSTEM, **request_params},
        },
        raw_response={
            "chunks": chunks,
            "chunk_count": len(chunks),
            "event_types": event_types_seen,
        },
        key_observations={
            "event_types_complete_list": list(set(event_types_seen))
            if event_types_seen
            else [],
            "event_ordering": "Iteration of GenerateContentChunk; no named event types like Anthropic",
            "delta_format_location": "chunk.text (each chunk is partial text)",
            "final_response_method": "Accumulate chunk.text or use last chunk; no explicit get_final",
            "openai_event_mapping": {
                "response.created": "first chunk",
                "response.output_text.delta": "chunk.text per chunk",
                "response.output_item.done": "last chunk",
                "response.reasoning_summary_text.delta": "N/A or chunk part",
                "response.failed": "exception or chunk with error",
            },
        },
        mapping={
            "equivalent_openai_param": "Stream yields chunks; no separate final response call",
            "differences": [
                "Gemini: no event type names; each yield is a chunk with .text",
                "Gemini: final = accumulate chunks; OpenAI: explicit get_final_response()",
            ],
            "conversion_needed": "Wrap chunk iteration as normalized delta events; build final from chunks.",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=getattr(chunks[0], "model_version", None) if chunks else None,
    )
    print("Gemini: OK", "-", len(chunks), "chunks")


def main() -> None:
    print("Test 02: Streaming")
    print("Anthropic (direct)...")
    run_anthropic()
    # print("Gemini...")
    # run_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
