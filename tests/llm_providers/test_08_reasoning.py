"""Test 08: Reasoning / extended thinking for Anthropic and Gemini.

Run: poetry run python tests/llm_providers/test_08_reasoning.py

This test verifies:
- Anthropic: `thinking: {type: "adaptive"}` (no budget_tokens on direct API; use effort for guidance)
  → content blocks include `type: "thinking"` then `type: "text"` (answer)
- Gemini: `thinking_config` with `include_thoughts=True` (and optionally thinking_budget)
  → response parts include `thought=True` parts (thought summaries) then regular text parts (answer)
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

TEST_NAME = "08_reasoning"
# Hard prompt to trigger extended thinking: base-number divisibility (AIME-style)
PROMPT = "Hãy chạy lệnh ip addr rồi báo cáo lại cho tôi public IP address nhé"


def run_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()

    # Adaptive thinking: type "adaptive" only — no budget_tokens (direct API rejects it; proxy may accept extras)
    # See https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking
    request_params = {
        "model": model,
        "max_tokens": 16000,
        # "thinking": {"type": "adaptive"},
        "thinking": {"type": "enabled", "budget_tokens": 10000},
        "messages": [
            {"role": "user", "content": PROMPT},
        ],
    }
    error_msg = None
    try:
        response = client.messages.create(**request_params)
    except Exception as e:
        error_msg = str(e)
        response = None

    content_types = []
    thinking_text = ""
    answer_text = ""
    if response and hasattr(response, "content"):
        for block in response.content or []:
            data = block.model_dump() if hasattr(block, "model_dump") else {}
            t = data.get("type")
            content_types.append(t)
            if t == "thinking":
                thinking_text = (data.get("thinking") or "")[:500]
            elif t == "text":
                answer_text = (data.get("text") or "")[:500]

    usage = getattr(response, "usage", None) if response else None
    usage_dict = {}
    if usage:
        usage_dict = {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
        }
        # Check for thinking-specific token fields (cache_creation_input_tokens, etc.)
        for field in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            val = getattr(usage, field, None)
            if val is not None:
                usage_dict[field] = val

    model_in_response = (
        getattr(response, "model", None) if response and not error_msg else None
    )
    raw_response_data = {}
    if error_msg:
        raw_response_data["error"] = error_msg
    else:
        raw_response_data["response"] = serialize_response(response)
    raw_response_data["content_block_types"] = content_types
    raw_response_data["thinking_preview"] = thinking_text
    raw_response_data["answer_preview"] = answer_text
    raw_response_data["usage"] = usage_dict

    save_evidence(
        test_name=TEST_NAME,
        provider="anthropic",
        request_data={
            "method": "messages.create",
            "params": {
                "model": request_params["model"],
                "max_tokens": request_params["max_tokens"],
                "thinking": request_params["thinking"],
                "messages": request_params["messages"],
            },
        },
        raw_response=raw_response_data,
        key_observations={
            "enable_param": "thinking={type: adaptive} (no budget_tokens; use output_config.effort for guidance)",
            "output_location": "content[].type == 'thinking' with .thinking text; then content[].type == 'text' with .text answer",
            "content_block_types_found": content_types,
            "thinking_present": "thinking" in content_types,
            "model_in_response_source": "from API response message.model (not echoed from request)",
            "streaming_thinking": "content_block_delta with type=thinking_delta (thinking text streamed in deltas)",
            "usage_format": usage_dict,
        },
        mapping={
            "equivalent_openai_param": "reasoning={effort, summary}",
            "differences": [
                "Anthropic: thinking block with full text vs OpenAI: reasoning with optional summary",
                "Anthropic: budget_tokens (absolute limit) vs OpenAI: effort (low/medium/high)",
                "Anthropic: thinking text is raw internal monologue; OpenAI: summary is condensed",
            ],
            "conversion_needed": "Map thinking block to reasoning output; map effort levels to budget_tokens ranges.",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=model_in_response,
    )
    status = "ERROR" if error_msg else "OK"
    print(f"Anthropic: {status}", "-", "content types:", content_types)
    if error_msg:
        print(f"  Error: {error_msg}")
    if thinking_text:
        print(f"  Thinking preview: {thinking_text[:100]}...")


def run_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()

    # Enable thinking + thought summaries in response (include_thoughts=True)
    # See https://ai.google.dev/gemini-api/docs/thinking
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(include_thoughts=True),
    )

    error_msg = None
    try:
        response = client.models.generate_content(
            model=model,
            contents=PROMPT,
            config=config,
        )
    except Exception as e:
        error_msg = str(e)
        response = None

    raw = serialize_response(response) if response else {"error": error_msg}
    text = ""
    thinking_parts = []
    text_parts = []
    candidates = raw.get("candidates", []) if isinstance(raw, dict) else []

    if candidates:
        content = candidates[0].get("content") or {}
        parts = content.get("parts", []) if isinstance(content, dict) else []
        for i, p in enumerate(parts):
            is_dict = isinstance(p, dict)
            is_thought = (
                p.get("thought") if is_dict else getattr(p, "thought", None)
            ) is True
            part_text = (p.get("text") if is_dict else getattr(p, "text", None)) or ""
            if is_thought:
                thinking_parts.append(
                    {"index": i, "thought": True, "text_preview": part_text[:300]}
                )
            elif part_text:
                text_parts.append(
                    {"index": i, "thought": False, "text_preview": part_text[:300]}
                )
                if not text:
                    text = part_text

    usage_meta = raw.get("usage_metadata", {}) if isinstance(raw, dict) else {}
    model_in_response = raw.get("model_version") if isinstance(raw, dict) else None
    if model_in_response is None and response is not None:
        model_in_response = getattr(response, "model_version", None)

    save_evidence(
        test_name=TEST_NAME,
        provider="gemini",
        request_data={
            "method": "generate_content",
            "params": {
                "model": model,
                "prompt": PROMPT,
                "thinking_config": {"include_thoughts": True},
            },
        },
        raw_response={
            "response": raw,
            "text_preview": text[:500],
            "thinking_parts": thinking_parts,
            "text_parts": text_parts,
            "thinking_parts_count": len(thinking_parts),
            "text_parts_count": len(text_parts),
            "usage_metadata": usage_meta,
        },
        key_observations={
            "enable_param": "GenerateContentConfig(thinking_config=ThinkingConfig(include_thoughts=True))",
            "output_location": "candidates[0].content.parts[]; parts with thought=True are thinking, others are answer",
            "thinking_parts_found": len(thinking_parts),
            "text_parts_found": len(text_parts),
            "streaming_thinking": "In streaming, chunks may contain parts with thought=True before text parts",
            "usage_metadata": usage_meta,
        },
        mapping={
            "equivalent_openai_param": "reasoning={effort, summary}",
            "differences": [
                "Gemini: thinking_budget in ThinkingConfig vs OpenAI: effort (low/medium/high)",
                "Gemini: thought parts mixed with text parts in candidates[0].content.parts[]",
                "Gemini: thought=True flag on parts vs Anthropic: separate 'thinking' block type",
            ],
            "conversion_needed": "Map thought=True parts to reasoning output; regular parts to text output.",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=model_in_response,
    )
    status = "ERROR" if error_msg else "OK"
    print(
        f"Gemini: {status}",
        "-",
        f"thinking parts: {len(thinking_parts)}, text parts: {len(text_parts)}",
    )
    if error_msg:
        print(f"  Error: {error_msg}")
    if thinking_parts:
        print(f"  Thinking preview: {thinking_parts[0]['text_preview'][:100]}...")


def main() -> None:
    print("Test 08: Reasoning / extended thinking")
    print("Anthropic (direct)...")
    run_anthropic()
    # print("Gemini...")
    # run_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
