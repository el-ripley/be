"""Test 07: Vision (image input) for Anthropic and Gemini.

Run: poetry run python tests/llm_providers/test_07_vision.py
"""

import base64
import sys
from pathlib import Path

import requests as http_requests

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

TEST_NAME = "07_vision"
# User-hosted image (used for OpenAI llm_call as well)
TEST_IMAGE_URL = "https://elripley.s3.ap-southeast-2.amazonaws.com/ephemeral/one_day/2652603d-19e9-4910-9852-3532fb3a9826.jpg"
PROMPT = "Describe this image in one sentence."


def _fetch_image(url: str) -> tuple[bytes, str]:
    """Fetch image bytes and detect media type from URL."""
    resp = http_requests.get(url, timeout=15)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "image/jpeg")
    media_type = content_type.split(";")[0].strip()
    return resp.content, media_type


def run_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()

    # Fetch image and encode as base64 (universally supported across all SDK versions and proxies)
    img_bytes, media_type = _fetch_image(TEST_IMAGE_URL)
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    image_block = {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": img_b64},
    }
    image_method = "base64"

    request_params = {
        "model": model,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    image_block,
                ],
            }
        ],
    }
    response = client.messages.create(**request_params)

    content_blocks = []
    for block in response.content or []:
        data = block.model_dump() if hasattr(block, "model_dump") else {}
        content_blocks.append({"type": data.get("type"), "has_text": "text" in data})

    # For evidence, don't save the full base64 data (too large), replace with placeholder
    evidence_params = {
        "model": model,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": f"<{len(img_b64)} chars base64>"},
                    },
                ],
            }
        ],
    }

    save_evidence(
        test_name=TEST_NAME,
        provider="anthropic",
        request_data={"method": "messages.create", "params": evidence_params},
        raw_response={
            "response": serialize_response(response),
            "content_block_types": content_blocks,
            "image_method": image_method,
            "image_url": TEST_IMAGE_URL,
        },
        key_observations={
            "image_input_format": "content: [{type: text, text: ...}, {type: image, source: {type: base64, media_type: ..., data: ...}}]",
            "url_support": "Also supports {type: url, url: ...} in source (SDK >=0.49)",
            "base64_support": True,
            "content_block_type": "image (Anthropic); OpenAI uses input_image",
        },
        mapping={
            "equivalent_openai_param": "input message with content block type input_image, image_url",
            "differences": [
                "Anthropic: type image, source.type base64/url; OpenAI: type input_image, image_url",
                "Anthropic base64 needs media_type explicitly; OpenAI infers from URL/data",
            ],
            "conversion_needed": "Map input_image -> image with source; for URL: source.type=url; for base64: source.type=base64 + media_type + data.",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(response, "model", None),
    )
    print("Anthropic: OK", "-", "content blocks:", content_blocks)


def run_gemini() -> None:
    from google import genai
    from google.genai import types

    img_bytes, media_type = _fetch_image(TEST_IMAGE_URL)

    client = get_gemini_client()
    model = get_gemini_model()
    contents = [PROMPT, types.Part.from_bytes(data=img_bytes, mime_type=media_type)]
    response = client.models.generate_content(model=model, contents=contents)

    raw = serialize_response(response)
    text = getattr(response, "text", None) or ""
    if not text and raw.get("candidates"):
        c0 = raw["candidates"][0]
        content = c0.get("content") or c0.get("content")
        parts = (
            (
                content.get("parts", [])
                if isinstance(content, dict)
                else getattr(content, "parts", [])
            )
            if content
            else []
        )
        for p in parts:
            t = p.get("text") if isinstance(p, dict) else getattr(p, "text", None)
            if t:
                text = t
                break

    save_evidence(
        test_name=TEST_NAME,
        provider="gemini",
        request_data={
            "method": "generate_content",
            "params": {
                "model": model,
                "contents": ["prompt", "image as {mime_type, data}"],
                "image_source": "bytes from URL",
            },
        },
        raw_response={
            "response": raw,
            "text_preview": text[:300] if text else "",
        },
        key_observations={
            "image_input_format": "generate_content([text, {mime_type, data: bytes}]) or PIL Image",
            "url_support": "Not direct; pass bytes or PIL (we fetched URL to bytes)",
            "content_block_type": "Inline in contents; no separate type name like input_image",
        },
        mapping={
            "equivalent_openai_param": "input message with input_image block",
            "differences": [
                "Gemini: image as second element in contents; OpenAI: content block in message"
            ],
            "conversion_needed": "Convert image URL to bytes or upload; pass as part of contents.",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=getattr(response, "model_version", None) or (raw.get("model_version") if isinstance(raw, dict) else None),
    )
    print("Gemini: OK", "-", "text len:", len(text))


def main() -> None:
    print("Test 07: Vision")
    print("Anthropic (direct)...")
    run_anthropic()
    # print("Gemini...")
    # run_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
