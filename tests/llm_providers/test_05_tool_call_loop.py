"""Test 05: Multi-turn tool call loop (simulates iteration_runner).

Scenario: User asks for weather in Hanoi then suggest outfit -> get_weather -> suggest_outfit -> final text.

Run: poetry run python tests/llm_providers/test_05_tool_call_loop.py
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

TEST_NAME = "05_tool_call_loop"
PROMPT = "Find the weather in Hanoi, then based on that suggest an outfit."
SYSTEM = "You are a helpful assistant."

WEATHER_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
}
OUTFIT_SCHEMA = {
    "type": "object",
    "properties": {"weather_summary": {"type": "string"}},
    "required": ["weather_summary"],
}


def run_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    tools = [
        {
            "name": "get_weather",
            "description": "Get current weather for a city",
            "input_schema": WEATHER_SCHEMA,
        },
        {
            "name": "suggest_outfit",
            "description": "Suggest outfit based on weather",
            "input_schema": OUTFIT_SCHEMA,
        },
    ]
    messages = [{"role": "user", "content": PROMPT}]
    turn_log = []
    max_turns = 5
    final_text = ""

    model = get_anthropic_model()
    for turn in range(max_turns):
        response = client.messages.create(
            model=model, max_tokens=1024, system=SYSTEM, tools=tools, messages=messages
        )
        turn_log.append(
            {
                "turn": turn + 1,
                "messages_length": len(messages),
                "stop_reason": getattr(response, "stop_reason", None),
                "content_block_types": [
                    getattr(
                        b,
                        "type",
                        getattr(getattr(b, "model_dump", lambda: {})(), "type", None),
                    )
                    for b in (response.content or [])
                ],
            }
        )

        tool_calls = []
        for block in response.content or []:
            data = block.model_dump() if hasattr(block, "model_dump") else {}
            if data.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": data.get("id"),
                        "name": data.get("name"),
                        "input": data.get("input"),
                    }
                )

        messages.append({"role": "assistant", "content": response.content})

        if not tool_calls:
            for block in response.content or []:
                data = block.model_dump() if hasattr(block, "model_dump") else {}
                if data.get("type") == "text":
                    final_text = data.get("text", "")
                    break
            break

        results = []
        for tc in tool_calls:
            if tc["name"] == "get_weather":
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": "25°C, sunny",
                    }
                )
            else:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": "Light shirt and jeans.",
                    }
                )
        messages.append({"role": "user", "content": results})

    save_evidence(
        test_name=TEST_NAME,
        provider="anthropic",
        request_data={
            "method": "messages.create (loop)",
            "params": {
                "model": model,
                "tools": tools,
                "initial_messages": [{"role": "user", "content": PROMPT}],
            },
        },
        raw_response={
            "turn_log": turn_log,
            "final_messages_length": len(messages),
            "final_text_preview": final_text[:300],
        },
        key_observations={
            "context_accumulation": "Append assistant content then user tool_result; same as OpenAI iteration loop.",
            "stop_condition": "stop_reason != 'tool_use' and no tool_use blocks",
            "mixed_content": "Assistant can have text + tool_use in same content array",
        },
        mapping={
            "equivalent_openai_param": "Same loop: get response -> if function_calls execute and append -> repeat",
            "differences": [
                "Anthropic: content array with tool_use; tool_result in user message."
            ],
            "conversion_needed": "IterationRunner can use same loop; convert output items to/from Anthropic format.",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(response, "model", None) if response else None,
    )
    print(
        "Anthropic: OK",
        "-",
        len(turn_log),
        "turns",
        "-",
        "final len(messages):",
        len(messages),
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
            types.FunctionDeclaration(
                name="suggest_outfit",
                description="Suggest outfit based on weather",
                parameters=OUTFIT_SCHEMA,
            ),
        ]
    )
    config = types.GenerateContentConfig(system_instruction=SYSTEM, tools=[tool])
    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part.from_text(text=PROMPT)])
    ]
    turn_log = []
    max_turns = 5
    final_text = ""
    response = None

    for turn in range(1, max_turns + 1):
        response = client.models.generate_content(
            model=model, contents=contents, config=config
        )
        raw = serialize_response(response)
        function_calls = []
        if getattr(response, "function_calls", None):
            for fc in response.function_calls:
                name = getattr(fc, "name", None) or (
                    fc.get("name") if isinstance(fc, dict) else None
                )
                if name:
                    function_calls.append({"name": name})
        turn_log.append({"turn": turn, "function_calls_count": len(function_calls)})
        if not function_calls:
            final_text = getattr(response, "text", "") or ""
            break
        if not response.candidates:
            break
        contents.append(response.candidates[0].content)
        tool_parts = [
            types.Part.from_function_response(
                name=fc["name"],
                response={
                    "result": "25°C, sunny"
                    if fc["name"] == "get_weather"
                    else "Light shirt and jeans."
                },
            )
            for fc in function_calls
        ]
        contents.append(types.Content(role="tool", parts=tool_parts))

    save_evidence(
        test_name=TEST_NAME,
        provider="gemini",
        request_data={
            "method": "models.generate_content (loop)",
            "params": {"model": model, "tools": [tool], "initial_prompt": PROMPT},
        },
        raw_response={
            "turn_log": turn_log,
            "final_text_preview": final_text[:300],
        },
        key_observations={
            "context_accumulation": "Chat session maintains history; send_message adds to history automatically",
            "stop_condition": "No function_call in response parts",
            "mixed_content": "Response can have text and function_call in same parts",
        },
        mapping={
            "equivalent_openai_param": "Same loop; chat.send_message accumulates context",
            "differences": [
                "Gemini chat keeps history; no explicit messages array to maintain."
            ],
            "conversion_needed": "IterationRunner: for Gemini use chat session; for OpenAI/Anthropic maintain messages list.",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=getattr(response, "model_version", None)
        if response
        else raw.get("model_version")
        if isinstance(raw, dict)
        else None,
    )
    print("Gemini: OK", "-", len(turn_log), "turns")


def main() -> None:
    print("Test 05: Multi-turn tool call loop")
    print("Anthropic (direct)...")
    run_anthropic()
    # print("Gemini...")
    # run_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
