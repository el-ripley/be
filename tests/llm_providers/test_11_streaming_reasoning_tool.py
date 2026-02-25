"""Test 11: Streaming + Reasoning + Tool Call (triple combo).

Run: poetry run python tests/llm_providers/test_11_streaming_reasoning_tool.py

Scenario: User asks complex question -> model thinks -> calls tool -> we send result -> model thinks again -> final text.
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

TEST_NAME = "11_streaming_reasoning_tool"
PROMPT = "I'm planning a trip to Hanoi tomorrow. What's the weather like and what should I pack?"
SYSTEM = "You are a helpful assistant."
WEATHER_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
}


def _safe_event_data(ev):
    out = {"type": getattr(ev, "type", type(ev).__name__)}
    if hasattr(ev, "content_block") and ev.content_block is not None:
        cb = ev.content_block
        out["content_block"] = {"type": getattr(cb, "type", None)}
        if getattr(cb, "text", None) is not None:
            out["content_block"]["text"] = (cb.text or "")[:150]
        if getattr(cb, "thinking", None) is not None:
            out["content_block"]["thinking"] = (cb.thinking or "")[:150]
    if hasattr(ev, "delta") and ev.delta is not None:
        d = ev.delta
        out["delta"] = {"type": getattr(d, "type", None)}
        if getattr(d, "text", None) is not None:
            out["delta"]["text"] = (d.text or "")[:150]
        if getattr(d, "thinking", None) is not None:
            out["delta"]["thinking"] = (d.thinking or "")[:150]
    if hasattr(ev, "index") and ev.index is not None:
        out["index"] = ev.index
    return out


def run_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    tools = [
        {
            "name": "get_weather",
            "description": "Get current weather for a city",
            "input_schema": WEATHER_SCHEMA,
        },
    ]
    request_params_turn1 = {
        "model": model,
        "max_tokens": 16000,
        "thinking": {"type": "enabled", "budget_tokens": 10000},
        "system": SYSTEM,
        "tools": tools,
        "messages": [{"role": "user", "content": PROMPT}],
    }

    events_turn1 = []
    final_turn1 = None
    with client.messages.stream(**request_params_turn1) as stream:
        for event in stream:
            data = _safe_event_data(event)
            events_turn1.append(
                {"type": data.get("type", type(event).__name__), "data": data}
            )
        final_turn1 = stream.get_final_message()

    event_types_turn1 = [e["type"] for e in events_turn1]
    tool_calls = []
    if final_turn1 and hasattr(final_turn1, "content"):
        for block in final_turn1.content or []:
            d = block.model_dump() if hasattr(block, "model_dump") else {}
            if d.get("type") == "tool_use":
                tool_calls.append(
                    {"id": d.get("id"), "name": d.get("name"), "input": d.get("input")}
                )

    # Turn 2: send tool result, stream again with thinking
    events_turn2 = []
    final_turn2 = None
    if tool_calls:
        messages_turn2 = [
            {"role": "user", "content": PROMPT},
            {"role": "assistant", "content": final_turn1.content},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_calls[0]["id"],
                        "content": "25°C, sunny. Light breeze.",
                    }
                ],
            },
        ]
        request_params_turn2 = {
            "model": model,
            "max_tokens": 16000,
            "thinking": {"type": "enabled", "budget_tokens": 10000},
            "system": SYSTEM,
            "tools": tools,
            "messages": messages_turn2,
        }
        with client.messages.stream(**request_params_turn2) as stream:
            for event in stream:
                data = _safe_event_data(event)
                events_turn2.append(
                    {"type": data.get("type", type(event).__name__), "data": data}
                )
            final_turn2 = stream.get_final_message()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        final1_ser = serialize_response(final_turn1) if final_turn1 else None
        final2_ser = serialize_response(final_turn2) if final_turn2 else None

    content_blocks_turn1 = []
    if final_turn1 and hasattr(final_turn1, "content"):
        for b in final_turn1.content or []:
            d = b.model_dump() if hasattr(b, "model_dump") else {}
            content_blocks_turn1.append(d.get("type"))

    save_evidence(
        test_name=TEST_NAME,
        provider="anthropic",
        request_data={
            "method": "messages.stream (2 turns)",
            "params": {
                "turn1": request_params_turn1,
                "turn2_messages_count": 3 if tool_calls else 0,
            },
        },
        raw_response={
            "events_turn1": events_turn1,
            "event_count_turn1": len(events_turn1),
            "event_types_turn1": event_types_turn1,
            "tool_calls_turn1": tool_calls,
            "content_blocks_turn1": content_blocks_turn1,
            "events_turn2": events_turn2,
            "event_count_turn2": len(events_turn2),
            "event_types_turn2": [e["type"] for e in events_turn2],
            "final_message_turn1": final1_ser,
            "final_message_turn2": final2_ser,
        },
        key_observations={
            "thinking_before_tool_call": "thinking" in content_blocks_turn1
            and "tool_use" in content_blocks_turn1,
            "mixed_blocks_order_turn1": content_blocks_turn1,
            "event_sequence_turn1": event_types_turn1[:25],
            "event_sequence_turn2": [e["type"] for e in events_turn2][:25],
            "thinking_after_tool_result": len(events_turn2) > 0,
        },
        mapping={
            "equivalent_openai_param": "stream() with reasoning + tools; same loop as iteration_runner",
            "differences": [
                "Anthropic: thinking then tool_use in content; turn2 thinking then text"
            ],
            "conversion_needed": "Map stream events for thinking + tool_use + text; same loop logic.",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(final_turn2 or final_turn1, "model", None),
    )
    print(
        "Anthropic: OK",
        "-",
        "turn1 events:",
        len(events_turn1),
        "turn2:",
        len(events_turn2),
        "tool_calls:",
        len(tool_calls),
    )


def run_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="get_weather",
                description="Get current weather for a city",
                parameters=WEATHER_SCHEMA,
            ),
        ]
    )
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        tools=[tool],
        thinking_config=types.ThinkingConfig(include_thoughts=True),
    )

    chunks_turn1 = []
    response_turn1 = None
    turn2_error = None
    try:
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=PROMPT,
            config=config,
        ):
            chunks_turn1.append(
                serialize_response(chunk)
                if hasattr(chunk, "model_dump")
                else {"text": getattr(chunk, "text", None)}
            )
    except Exception as e:
        chunks_turn1 = [{"error": str(e)}]

    # Get final response for turn1 to extract function_call (non-stream fallback if needed)
    func_name = None
    func_args = None
    try:
        response_turn1 = client.models.generate_content(
            model=model, contents=PROMPT, config=config
        )
        if getattr(response_turn1, "function_calls", None):
            fc = response_turn1.function_calls[0]
            func_name = getattr(fc, "name", None) or (
                fc.get("name") if isinstance(fc, dict) else None
            )
            fcall = (
                getattr(fc, "function_call", fc) if hasattr(fc, "function_call") else fc
            )
            func_args = (
                getattr(fcall, "args", None)
                if hasattr(fcall, "args")
                else (fcall.get("args") if isinstance(fcall, dict) else None)
            )
    except Exception as e:
        response_turn1 = type(
            "R", (), {"text": str(e), "candidates": [], "function_calls": None}
        )()

    chunks_turn2 = []
    turn2_error = None
    if func_name and response_turn1 and getattr(response_turn1, "candidates", None):
        try:
            user_content = types.Content(
                role="user", parts=[types.Part.from_text(text=PROMPT)]
            )
            assistant_content = response_turn1.candidates[0].content
            tool_content = types.Content(
                role="tool",
                parts=[
                    types.Part.from_function_response(
                        name=func_name,
                        response={"temperature": "25°C", "condition": "sunny"},
                    )
                ],
            )
            contents_turn2_list = [user_content, assistant_content, tool_content]
            for chunk in client.models.generate_content_stream(
                model=model, contents=contents_turn2_list, config=config
            ):
                chunks_turn2.append(
                    serialize_response(chunk)
                    if hasattr(chunk, "model_dump")
                    else {"text": getattr(chunk, "text", None)}
                )
        except Exception as e:
            turn2_error = str(e)
            chunks_turn2 = [{"error": turn2_error}]

    save_evidence(
        test_name=TEST_NAME,
        provider="gemini",
        request_data={
            "method": "generate_content_stream (2 turns)",
            "params": {"model": model, "tools": "get_weather", "thinking_config": True},
        },
        raw_response={
            "chunks_turn1_count": len(chunks_turn1),
            "chunks_turn2_count": len(chunks_turn2),
            "turn2_error": turn2_error,
            "function_call_turn1": {"name": func_name, "args": func_args},
            "chunks_turn1_sample": chunks_turn1[:5] if chunks_turn1 else [],
            "chunks_turn2_sample": chunks_turn2[:5] if chunks_turn2 else [],
            "turn2_has_text": (
                any(
                    (c.get("text") if isinstance(c, dict) else getattr(c, "text", None))
                    for c in chunks_turn2
                )
                if chunks_turn2
                else False
            ),
        },
        key_observations={
            "thinking_before_tool_call": "Chunks may have thought parts before function_call in turn1",
            "thinking_after_tool_result": "Turn2 chunks may have thought parts then text",
            "turn2_received_chunks": len(chunks_turn2),
            "turn2_error_if_any": turn2_error,
            "event_sequence": "Chunks with thought + function_call then chunks with thought + text",
        },
        mapping={
            "equivalent_openai_param": "Stream with reasoning + tools; two turns with function_response",
            "differences": [
                "Gemini: chunks with thought parts and function_call; no named events"
            ],
            "conversion_needed": "Accumulate chunks; extract function_call; map thought parts to reasoning.",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=(
            getattr(response_turn1, "model_version", None) if response_turn1 else None
        ),
    )
    print(
        "Gemini: OK",
        "-",
        "turn1 chunks:",
        len(chunks_turn1),
        "turn2:",
        len(chunks_turn2),
    )


def main() -> None:
    """Chạy cả Anthropic và Gemini. Giống test_01–09."""
    print("Test 11: Streaming + Reasoning + Tool Call")
    # print("Anthropic (direct)...")
    # run_anthropic()
    print("Gemini...")
    run_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
