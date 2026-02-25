"""Test 03: Single tool call round-trip for Anthropic and Gemini.

Run: poetry run python tests/llm_providers/test_03_single_tool_call.py
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

TEST_NAME = "03_single_tool_call"
PROMPT = "What's the weather in Hanoi?"
SYSTEM = "You are a helpful assistant."

# Shared tool definition (conceptual); each provider uses its own format
WEATHER_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "City name"},
        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
    },
    "required": ["city"],
}


def run_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    tools = [
        {
            "name": "get_weather",
            "description": "Get current weather for a city",
            "input_schema": WEATHER_SCHEMA,
        }
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

    tool_use_id = None
    tool_name = None
    tool_input = None
    for block in response.content or []:
        data = block.model_dump() if hasattr(block, "model_dump") else {}
        if data.get("type") == "tool_use":
            tool_use_id = data.get("id")
            tool_name = data.get("name")
            tool_input = data.get("input")
            break

    # Turn 2: send tool result and get final text
    messages = [
        {"role": "user", "content": PROMPT},
        {"role": "assistant", "content": response.content},
    ]
    if tool_use_id and tool_name:
        tool_result_content = "25°C, sunny"
        messages.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": tool_result_content}
            ],
        })
        response2 = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM,
            tools=tools,
            messages=messages,
        )
        final_text = ""
        for block in response2.content or []:
            d = block.model_dump() if hasattr(block, "model_dump") else {}
            if d.get("type") == "text":
                final_text = d.get("text", "")
                break
    else:
        response2 = None
        final_text = ""

    save_evidence(
        test_name=TEST_NAME,
        provider="anthropic",
        request_data={"method": "messages.create", "params": request_params},
        raw_response={
            "turn1_response": serialize_response(response),
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "turn2_response": serialize_response(response2) if response2 else None,
            "final_text_preview": final_text[:200] if final_text else "",
        },
        key_observations={
            "tool_call_location": "content[].type == 'tool_use'",
            "tool_call_id_key": "id (e.g. toolu_xxx)",
            "arguments_key": "input (dict, not JSON string)",
            "stop_reason_tool_use": getattr(response, "stop_reason", None),
            "tool_result_format": "role: user, content: [{type: tool_result, tool_use_id, content}]",
        },
        mapping={
            "equivalent_openai_param": "tools + tool_choice; output has function_call items",
            "differences": [
                "Anthropic: input_schema (not parameters); id (not call_id); input is dict",
                "Tool result in user message as tool_result block; match by tool_use_id",
            ],
            "conversion_needed": "Convert tool def parameters->input_schema; arguments JSON string->dict; call_id<->id.",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(response2, "model", None) if response2 else getattr(response, "model", None),
    )
    print("Anthropic: OK", "-", "tool_use_id:", tool_use_id, "-", "final:", final_text[:50] if final_text else "N/A")


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
            )
        ]
    )
    config = types.GenerateContentConfig(system_instruction=SYSTEM, tools=[tool])
    response = client.models.generate_content(model=model, contents=PROMPT, config=config)
    raw = serialize_response(response)
    func_name = None
    func_args = None
    if getattr(response, "function_calls", None):
        fc = response.function_calls[0]
        func_name = getattr(fc, "name", None) or (fc.get("name") if isinstance(fc, dict) else None)
        func_call = getattr(fc, "function_call", fc) if hasattr(fc, "function_call") else fc
        func_args = getattr(func_call, "args", None) if hasattr(func_call, "args") else (func_call.get("args") if isinstance(func_call, dict) else None)

    response2 = None
    final_text = ""
    if func_name and response.candidates:
        try:
            user_content = types.Content(role="user", parts=[types.Part.from_text(text=PROMPT)])
            function_call_content = response.candidates[0].content
            function_response_part = types.Part.from_function_response(
                name=func_name,
                response={"temperature": "25°C", "condition": "sunny"},
            )
            function_response_content = types.Content(role="tool", parts=[function_response_part])
            contents = [user_content, function_call_content, function_response_content]
            response2 = client.models.generate_content(model=model, contents=contents, config=config)
            final_text = getattr(response2, "text", "") or ""
        except Exception as e:
            response2 = type("Resp", (), {"text": str(e), "candidates": []})()

    save_evidence(
        test_name=TEST_NAME,
        provider="gemini",
        request_data={"method": "models.generate_content", "params": {"model": model, "tools": [tool], "prompt": PROMPT}},
        raw_response={
            "turn1_response": raw,
            "function_call_name": func_name,
            "function_call_args": func_args,
            "turn2_response": serialize_response(response2) if response2 else None,
            "final_text_preview": final_text[:200] if final_text else "",
        },
        key_observations={
            "tool_call_location": "response.function_calls",
            "tool_call_id_key": "NO ID (match by function name)",
            "arguments_key": "args (dict)",
            "stop_reason_tool_use": "function_calls present",
            "tool_result_format": "Content(role=tool) with Part.from_function_response; match by name",
        },
        mapping={
            "equivalent_openai_param": "tools; output in parts as function_call",
            "differences": [
                "Gemini: no call_id; match tool results by function name only",
                "Gemini: tool result as Content role=tool in contents array",
            ],
            "conversion_needed": "Assign synthetic call_id for multi-call ordering; map function_call <-> function_call_output.",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=getattr(response, "model_version", None) or raw.get("model_version"),
    )
    print("Gemini: OK", "-", "func:", func_name, "-", "final:", final_text[:50] if final_text else "N/A")


def main() -> None:
    print("Test 03: Single tool call")
    print("Anthropic (direct)...")
    run_anthropic()
    # print("Gemini...")
    # run_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
