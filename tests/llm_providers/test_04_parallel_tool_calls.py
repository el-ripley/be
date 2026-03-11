"""Test 04: Parallel tool calls (multiple tools in one response).

Run: poetry run python tests/llm_providers/test_04_parallel_tool_calls.py
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

TEST_NAME = "04_parallel_tool_calls"
PROMPT = "What's the weather in Hanoi and what time is it in Tokyo?"
SYSTEM = "You are a helpful assistant."

WEATHER_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
    },
    "required": ["city"],
}
TIME_SCHEMA = {
    "type": "object",
    "properties": {
        "timezone": {"type": "string", "description": "City or timezone e.g. Tokyo"}
    },
    "required": ["timezone"],
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
            "name": "get_time",
            "description": "Get current time for a timezone or city",
            "input_schema": TIME_SCHEMA,
        },
    ]
    model = get_anthropic_model()
    request_params = {
        "model": model,
        "max_tokens": 1024,
        "system": SYSTEM,
        "tools": tools,
        "messages": [{"role": "user", "content": PROMPT}],
    }
    response = client.messages.create(**request_params)

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

    # Send both tool results back
    if tool_calls:
        tool_results = [
            {
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": "25°C, sunny"
                if tc["name"] == "get_weather"
                else "3:45 PM JST",
            }
            for tc in tool_calls
        ]
        messages = [
            {"role": "user", "content": PROMPT},
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tool_results},
        ]
        response2 = client.messages.create(
            model=model, max_tokens=1024, system=SYSTEM, tools=tools, messages=messages
        )
    else:
        response2 = None

    save_evidence(
        test_name=TEST_NAME,
        provider="anthropic",
        request_data={"method": "messages.create", "params": request_params},
        raw_response={
            "turn1_response": serialize_response(response),
            "tool_calls_count": len(tool_calls),
            "tool_calls": tool_calls,
            "turn2_response": serialize_response(response2) if response2 else None,
        },
        key_observations={
            "multiple_tool_calls_in_one_response": len(tool_calls) >= 2,
            "each_has_id": all(tc.get("id") for tc in tool_calls),
            "sending_multiple_results": "single user message with array of tool_result blocks",
        },
        mapping={
            "equivalent_openai_param": "parallel_tool_calls",
            "differences": [
                "Anthropic supports multiple tool_use blocks; each has unique id."
            ],
            "conversion_needed": "Same as single tool call; collect all tool_use blocks and send all tool_results.",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(response2, "model", None)
        if response2
        else getattr(response, "model", None),
    )
    print("Anthropic: OK", "-", len(tool_calls), "tool calls")


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
                name="get_time",
                description="Get current time for a timezone or city",
                parameters=TIME_SCHEMA,
            ),
        ]
    )
    config = types.GenerateContentConfig(system_instruction=SYSTEM, tools=[tool])
    response = client.models.generate_content(
        model=model, contents=PROMPT, config=config
    )
    raw = serialize_response(response)
    function_calls = []
    if getattr(response, "function_calls", None):
        for fc in response.function_calls:
            name = getattr(fc, "name", None) or (
                fc.get("name") if isinstance(fc, dict) else None
            )
            func_call = (
                getattr(fc, "function_call", fc) if hasattr(fc, "function_call") else fc
            )
            args = (
                getattr(func_call, "args", None)
                if hasattr(func_call, "args")
                else (func_call.get("args") if isinstance(func_call, dict) else None)
            )
            if name:
                function_calls.append({"name": name, "args": args})

    response2 = None
    if function_calls and response.candidates:
        try:
            user_content = types.Content(
                role="user", parts=[types.Part.from_text(text=PROMPT)]
            )
            function_call_content = response.candidates[0].content
            tool_parts = [
                types.Part.from_function_response(
                    name=fc["name"],
                    response={
                        "result": "25°C, sunny"
                        if fc["name"] == "get_weather"
                        else "3:45 PM JST"
                    },
                )
                for fc in function_calls
            ]
            function_response_content = types.Content(role="tool", parts=tool_parts)
            response2 = client.models.generate_content(
                model=model,
                contents=[
                    user_content,
                    function_call_content,
                    function_response_content,
                ],
                config=config,
            )
        except Exception:
            response2 = None

    save_evidence(
        test_name=TEST_NAME,
        provider="gemini",
        request_data={
            "method": "models.generate_content",
            "params": {"model": model, "tools": [tool], "prompt": PROMPT},
        },
        raw_response={
            "turn1_response": raw,
            "function_calls_count": len(function_calls),
            "function_calls": function_calls,
            "turn2_response": serialize_response(response2) if response2 else None,
        },
        key_observations={
            "multiple_tool_calls_in_one_response": len(function_calls) >= 2,
            "no_call_id": "Gemini matches by function name only",
            "sending_multiple_results": "Content(role=tool) with multiple Part.from_function_response",
        },
        mapping={
            "equivalent_openai_param": "parallel_tool_calls",
            "differences": [
                "Gemini can return multiple function_call parts; order/match by name."
            ],
            "conversion_needed": "Assign synthetic call_ids when converting to unified format for tool_executor.",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=getattr(response2, "model_version", None)
        if response2
        else getattr(response, "model_version", None) or raw.get("model_version"),
    )
    print("Gemini: OK", "-", len(function_calls), "function calls")


def main() -> None:
    print("Test 04: Parallel tool calls")
    print("Anthropic (direct)...")
    run_anthropic()
    # print("Gemini...")
    # run_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
