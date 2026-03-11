"""Test 01: Basic completion (non-streaming) for Anthropic and Gemini.

Run: poetry run python tests/llm_providers/test_01_basic_completion.py
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

TEST_NAME = "01_basic_completion"
PROMPT = "What is 2+2? Reply in one sentence."
SYSTEM = "You are a helpful assistant."


def run_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    request_params = {
        "model": model,
        "max_tokens": 1024,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": PROMPT}],
    }
    response = client.messages.create(**request_params)

    content_path = "response.content[0].text" if response.content else "N/A"
    roles = list({getattr(c, "type", None) or "text" for c in (response.content or [])})
    stop = getattr(response, "stop_reason", None)
    usage = getattr(response, "usage", None)
    usage_dict = (
        {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
        if usage
        else {}
    )

    save_evidence(
        test_name=TEST_NAME,
        provider="anthropic",
        request_data={"method": "messages.create", "params": request_params},
        raw_response=response,
        key_observations={
            "response_structure": "id, type, role, content, stop_reason, stop_sequence, model, usage",
            "content_format": "content is list of content blocks (text, tool_use); text in .text",
            "role_values": ["user", "assistant"],
            "stop_reason": str(stop) if stop else "N/A",
            "usage_format": usage_dict,
            "content_path": content_path,
        },
        mapping={
            "equivalent_openai_param": "input= messages (OpenAI uses input= with message items)",
            "differences": [
                "Anthropic: system= separate param; OpenAI: system in input as role system",
                "Anthropic: content[].text; OpenAI: content[].output_text / input_text",
                "Anthropic: stop_reason; OpenAI: status / output items",
            ],
            "conversion_needed": "Map system to/from messages; normalize content block types.",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(response, "model", None),
    )
    print("Anthropic: OK", "-", content_path, "-", stop)


def run_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    request_params = {"prompt": PROMPT, "system_instruction": SYSTEM}
    model = get_gemini_model()
    response = client.models.generate_content(
        model=model,
        contents=PROMPT,
        config=types.GenerateContentConfig(system_instruction=SYSTEM),
    )

    raw = serialize_response(response)
    candidates = raw.get("candidates", [])
    content_path = "N/A"
    if candidates:
        c0 = candidates[0]
        content = c0.get("content", {}) or c0.get("content")
        if content:
            parts = content.get("parts", []) if isinstance(content, dict) else []
            if parts:
                content_path = "candidates[0].content.parts[0].text"
    usage = raw.get("usage_metadata") or raw.get("usageMetadata") or {}
    usage_dict = {
        "input_tokens": usage.get(
            "prompt_token_count", usage.get("promptTokenCount", 0)
        ),
        "output_tokens": usage.get(
            "candidates_token_count", usage.get("candidatesTokenCount", 0)
        ),
    }
    finish_reason = "N/A"
    if candidates:
        finish_reason = candidates[0].get(
            "finish_reason", candidates[0].get("finishReason", "")
        )

    save_evidence(
        test_name=TEST_NAME,
        provider="gemini",
        request_data={
            "method": "generate_content",
            "params": {"model": model, "system_instruction": SYSTEM, **request_params},
        },
        raw_response=response,
        key_observations={
            "response_structure": "candidates, usage_metadata, prompt_feedback",
            "content_format": "candidates[0].content.parts[].text",
            "role_values": ["user", "model"],
            "stop_reason": str(finish_reason),
            "usage_format": usage_dict,
            "content_path": content_path,
        },
        mapping={
            "equivalent_openai_param": "input= messages; system_instruction= maps to system",
            "differences": [
                "Gemini: candidates[].content.parts; OpenAI: output[]",
                "Gemini: finish_reason; OpenAI: status",
                "Gemini: usage_metadata; OpenAI: usage",
            ],
            "conversion_needed": "Normalize candidates[0].content.parts to output items.",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=getattr(response, "model_version", None)
        or raw.get("model_version"),
    )
    print("Gemini: OK", "-", content_path, "-", finish_reason)


def main() -> None:
    print("Test 01: Basic completion")
    print("Anthropic (direct)...")
    run_anthropic()
    # print("Gemini...")
    # run_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
