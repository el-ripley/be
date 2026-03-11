"""Test 06: Structured output (JSON schema) for Anthropic and Gemini.

Run: poetry run python tests/llm_providers/test_06_structured_output.py
"""

import json
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

TEST_NAME = "06_structured_output"
PROMPT = "Analyze in one sentence: AI is transforming healthcare. Return structured analysis."
SYSTEM = "You are a helpful assistant."

TARGET_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
    },
    "required": ["summary", "key_points", "sentiment"],
}


def run_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    tools = [
        {
            "name": "structured_response",
            "description": "Return structured analysis with summary, key_points, sentiment",
            "input_schema": TARGET_SCHEMA,
        }
    ]
    model = get_anthropic_model()
    request_params = {
        "model": model,
        "max_tokens": 1024,
        "system": SYSTEM,
        "tools": tools,
        "tool_choice": {"type": "tool", "name": "structured_response"},
        "messages": [{"role": "user", "content": PROMPT}],
    }
    response = client.messages.create(**request_params)

    output_location = "tool_use input (no native structured output)"
    parsed = None
    for block in response.content or []:
        data = block.model_dump() if hasattr(block, "model_dump") else {}
        if data.get("type") == "tool_use" and data.get("name") == "structured_response":
            inp = data.get("input")
            if isinstance(inp, dict):
                parsed = inp
                output_location = (
                    "content[].type==tool_use, name==structured_response, input (dict)"
                )
            break

    save_evidence(
        test_name=TEST_NAME,
        provider="anthropic",
        request_data={"method": "messages.create", "params": request_params},
        raw_response={
            "response": serialize_response(response),
            "output_location": output_location,
            "parsed_structured": parsed,
        },
        key_observations={
            "native_structured_output": "No; use tool_choice to force tool_use with schema",
            "output_location": output_location,
            "validation": "input_schema constrains tool_use input; no separate validation step",
        },
        mapping={
            "equivalent_openai_param": "responses.parse(text_format= schema)",
            "differences": [
                "Anthropic: tool-as-structured-output; OpenAI: native parse() with schema"
            ],
            "conversion_needed": "For parse()-like use: create synthetic tool, tool_choice that tool, then read tool_use.input.",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(response, "model", None),
    )
    print(
        "Anthropic: OK", "-", "parsed keys:", list(parsed.keys()) if parsed else "N/A"
    )


def run_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        response_mime_type="application/json",
        response_schema=TARGET_SCHEMA,
    )
    response = client.models.generate_content(
        model=model, contents=PROMPT, config=config
    )
    raw = serialize_response(response)
    text = getattr(response, "text", None) or ""
    parsed = None
    output_location = "response.text"
    if text:
        try:
            parsed = json.loads(text)
            output_location = "response.text as JSON (native response_schema)"
        except json.JSONDecodeError:
            parsed = {"raw_text": text[:200]}

    save_evidence(
        test_name=TEST_NAME,
        provider="gemini",
        request_data={
            "method": "generate_content",
            "params": {
                "model": model,
                "config": {
                    "response_mime_type": "application/json",
                    "response_schema": TARGET_SCHEMA,
                },
                "prompt": PROMPT,
            },
        },
        raw_response={
            "response": raw,
            "text": text[:500],
            "output_location": output_location,
            "parsed_structured": parsed,
        },
        key_observations={
            "native_structured_output": "Yes; response_mime_type=application/json + response_schema",
            "output_location": output_location,
            "validation": "Schema guides model; output is JSON string in text",
        },
        mapping={
            "equivalent_openai_param": "responses.parse(text_format= schema)",
            "differences": [
                "Gemini: native JSON mode; OpenAI: parse() returns parsed object"
            ],
            "conversion_needed": "Parse response.text as JSON; similar to OpenAI parse() output.",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=getattr(response, "model_version", None)
        or raw.get("model_version"),
    )
    print(
        "Gemini: OK",
        "-",
        "parsed keys:",
        list(parsed.keys()) if isinstance(parsed, dict) else "N/A",
    )


def main() -> None:
    print("Test 06: Structured output")
    print("Anthropic (direct)...")
    run_anthropic()
    # print("Gemini...")
    # run_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
