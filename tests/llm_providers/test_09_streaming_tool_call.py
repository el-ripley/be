"""Test 09: Streaming + tool calls combined.

Run: poetry run python tests/llm_providers/test_09_streaming_tool_call.py
"""

import sys
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

TEST_NAME = "09_streaming_tool_call"
PROMPT = "What's the weather in Hanoi?"
SYSTEM = "You are a helpful assistant."
WEATHER_SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}


def run_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    tools = [{"name": "get_weather", "description": "Get current weather for a city", "input_schema": WEATHER_SCHEMA}]
    model = get_anthropic_model()
    request_params = {
        "model": model,
        "max_tokens": 1024,
        "system": SYSTEM,
        "tools": tools,
        "messages": [{"role": "user", "content": PROMPT}],
    }
    events = []
    final_message = None
    with client.messages.stream(**request_params) as stream:
        for event in stream:
            data = event.model_dump() if hasattr(event, "model_dump") else {}
            variant = data.get("type") if isinstance(data, dict) else None
            if not variant and isinstance(data, dict) and len(data) == 1:
                variant = next(iter(data.keys()))
            variant = variant or type(event).__name__
            events.append({"type": variant, "data": data if isinstance(data, dict) else str(event)})
        final_message = stream.get_final_message()

    event_types = [e["type"] for e in events]
    tool_call_complete_signal = "content_block_stop (for tool_use block) or message_stop"

    save_evidence(
        test_name=TEST_NAME,
        provider="anthropic",
        request_data={"method": "messages.stream", "params": request_params},
        raw_response={
            "events": events,
            "event_count": len(events),
            "event_types": event_types,
            "final_message": serialize_response(final_message) if final_message else None,
        },
        key_observations={
            "event_sequence_with_tool_call": event_types,
            "tool_call_complete_signal": tool_call_complete_signal,
            "mixed_content_streaming": "content_block_start (tool_use) then content_block_delta (input) then content_block_stop",
        },
        mapping={
            "equivalent_openai_param": "stream() with tools; events include response.output_item.added (function_call)",
            "differences": ["Anthropic stream events named differently; final message same as non-stream for tool_use"],
            "conversion_needed": "Map stream events to normalized types; on tool_use complete, same as non-stream tool execution.",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(final_message, "model", None) if final_message else None,
    )
    print("Anthropic: OK", "-", len(events), "events", "-", event_types[:15])


def run_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(name="get_weather", description="Get current weather for a city", parameters=WEATHER_SCHEMA),
        ]
    )
    config = types.GenerateContentConfig(system_instruction=SYSTEM, tools=[tool])
    chunks = []
    response = None
    try:
        for chunk in client.models.generate_content_stream(model=model, contents=PROMPT, config=config):
            chunks.append(serialize_response(chunk) if hasattr(chunk, "model_dump") else {"text": getattr(chunk, "text", None)})
    except TypeError:
        response = client.models.generate_content(model=model, contents=PROMPT, config=config)
        chunks = [serialize_response(response) if hasattr(response, "model_dump") else {"text": getattr(response, "text", None)}]

    model_in_response = getattr(response, "model_version", None) if response else None
    if model_in_response is None and chunks and isinstance(chunks[0], dict):
        model_in_response = chunks[0].get("model_version")

    save_evidence(
        test_name=TEST_NAME,
        provider="gemini",
        request_data={"method": "models.generate_content_stream", "params": {"model": model, "tools": [tool], "prompt": PROMPT}},
        raw_response={
            "chunks": chunks,
            "chunk_count": len(chunks),
        },
        key_observations={
            "event_sequence_with_tool_call": "Chunks may contain partial text and/or function_call when complete",
            "tool_call_complete_signal": "Chunk containing function_call part (or last chunk)",
            "mixed_content_streaming": "Same as non-stream; function_call in chunk parts",
        },
        mapping={
            "equivalent_openai_param": "Stream chunks; final chunk or accumulation may have function_call",
            "differences": ["Gemini streams chunks; tool call appears in chunk when generated"],
            "conversion_needed": "Accumulate chunks; when function_call present, execute and send response.",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=model_in_response,
    )
    print("Gemini: OK", "-", len(chunks), "chunks")


def main() -> None:
    print("Test 09: Streaming + tool calls")
    print("Anthropic (direct)...")
    run_anthropic()
    # print("Gemini...")
    # run_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
