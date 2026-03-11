"""Test proxy LLM AI VN (sv2) – OpenAI client hoặc Anthropic SDK, in ra JSON đầy đủ.

Run:
  poetry run python tests/test_anthropic_proxy.py           # mặc định: OpenAI client
  poetry run python tests/test_anthropic_proxy.py --anthropic   # thử Anthropic SDK
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# Config: LLM AI VN proxy (sv2)
PROXY_BASE_URL = "https://api.sv2.llm.ai.vn/v1"
# Anthropic SDK gửi path /v1/messages → base_url nên để không có /v1
ANTHROPIC_BASE_URL = "https://api.sv2.llm.ai.vn"
API_KEY = os.environ.get("ANTHROPIC_PROXY_API_KEY", "")
MODEL = "anthropic:sonnet-4-5-20250929"

# Đổi sang True hoặc chạy với --anthropic để test bằng Anthropic SDK
USE_ANTHROPIC_SDK = "--anthropic" in sys.argv

# PROMPT = """
# I want to setup ssh connection by ssh key, here is my ssh public key
# "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIN2eze5/7J2sYwJqcWQkqTZlGwMePdCzpBVSA5WAOQD6 your_email@example.com"
# please write it into ~/.ssh/authorized_keys file
# then run hostname -I to check my ip address then write me a command so I can connect to my computer by my ssh key
# """
PROMPT = "Hãy chạy lệnh ip addr rồi báo cáo lại cho tôi public IP address nhé"


def serialize_response(obj):
    """Chuyển object SDK sang dict/list có thể JSON."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {k: serialize_response(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_response(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "__dict__"):
        return serialize_response(obj.__dict__)
    return str(obj)


def _run_openai() -> tuple[dict, dict, str | None]:
    """Gọi proxy bằng OpenAI client. Trả về (request_data, raw_json_dict, error_msg)."""
    import openai

    client = openai.OpenAI(api_key=API_KEY, base_url=PROXY_BASE_URL)
    request_params = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 16000,
    }
    error_msg = None
    response = None
    try:
        response = client.chat.completions.create(**request_params)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print("Error:", error_msg)

    content_block_types = []
    thinking_text = ""
    answer_text = ""
    model_in_response = None
    usage_dict = {}
    if response and not error_msg:
        model_in_response = getattr(response, "model", None)
        if getattr(response, "usage", None):
            u = response.usage
            usage_dict = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0),
                "completion_tokens": getattr(u, "completion_tokens", 0),
                "total_tokens": getattr(u, "total_tokens", 0),
            }
        if getattr(response, "choices", None) and len(response.choices) > 0:
            msg = response.choices[0].message
            if getattr(msg, "content", None):
                answer_text = (msg.content or "")[:2000]
                content_block_types.append("text")
            if getattr(msg, "reasoning_content", None):
                thinking_text = (msg.reasoning_content or "")[:2000]
                content_block_types.insert(0, "reasoning")

    raw_json = {}
    if error_msg:
        raw_json["error"] = error_msg
    else:
        raw_json["response"] = serialize_response(response)
    raw_json["content_block_types"] = content_block_types
    raw_json["thinking_preview"] = thinking_text
    raw_json["answer_preview"] = answer_text
    raw_json["usage"] = usage_dict

    request_data = {"method": "chat.completions.create", "params": request_params}
    key_obs = {
        "thinking_present": "reasoning" in content_block_types or bool(thinking_text),
        "content_block_types_found": content_block_types,
        "model_in_response": model_in_response,
    }
    return request_data, {"raw_json": raw_json, "key_observations": key_obs}, error_msg


def _run_anthropic_sdk() -> tuple[dict, dict, str | None]:
    """Gọi proxy bằng Anthropic SDK (messages.create). Trả về (request_data, response_dict, error_msg)."""
    import anthropic

    client = anthropic.Anthropic(
        api_key=API_KEY,
        base_url=ANTHROPIC_BASE_URL,
        timeout=90.0,
    )
    request_params = {
        "model": MODEL,
        "max_tokens": 16000,
        "thinking": {"type": "enabled", "budget_tokens": 10000},
        "messages": [{"role": "user", "content": PROMPT}],
    }
    error_msg = None
    response = None
    try:
        response = client.messages.create(**request_params)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print("Error:", error_msg)

    content_block_types = []
    thinking_text = ""
    answer_text = ""
    usage_dict = {}
    model_in_response = None
    if response and getattr(response, "content", None):
        for block in response.content or []:
            data = serialize_response(block)
            t = data.get("type")
            content_block_types.append(t)
            if t == "thinking":
                thinking_text = (data.get("thinking") or "")[:2000]
            elif t == "text":
                answer_text = (data.get("text") or "")[:2000]
    if response and getattr(response, "usage", None):
        u = response.usage
        usage_dict = {
            "input_tokens": getattr(u, "input_tokens", 0),
            "output_tokens": getattr(u, "output_tokens", 0),
        }
        for f in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            v = getattr(u, f, None)
            if v is not None:
                usage_dict[f] = v
    model_in_response = getattr(response, "model", None) if response else None

    raw_json = {}
    if error_msg:
        raw_json["error"] = error_msg
    else:
        raw_json["response"] = serialize_response(response)
    raw_json["content_block_types"] = content_block_types
    raw_json["thinking_preview"] = thinking_text
    raw_json["answer_preview"] = answer_text
    raw_json["usage"] = usage_dict

    request_data = {"method": "messages.create", "params": request_params}
    key_obs = {
        "thinking_present": "thinking" in content_block_types,
        "content_block_types_found": content_block_types,
        "model_in_response": model_in_response,
    }
    return request_data, {"raw_json": raw_json, "key_observations": key_obs}, error_msg


def main() -> None:
    if USE_ANTHROPIC_SDK:
        print(f"Base URL (Anthropic SDK): {ANTHROPIC_BASE_URL}")
        print(f"Model: {MODEL}")
        print("Calling API (Anthropic SDK messages.create, thinking enabled)...")
        request_data, response_dict, error_msg = _run_anthropic_sdk()
        provider = "anthropic_sdk"
        sdk_version = getattr(__import__("anthropic"), "__version__", "")
    else:
        print(f"Base URL: {PROXY_BASE_URL}")
        print(f"Model: {MODEL}")
        print("Calling API (OpenAI client chat.completions)...")
        request_data, response_dict, error_msg = _run_openai()
        provider = "anthropic_via_openai"
        sdk_version = getattr(__import__("openai"), "__version__", "")

    raw_json = response_dict["raw_json"]
    key_observations = response_dict["key_observations"]
    content_block_types = raw_json.get("content_block_types", [])
    thinking_text = raw_json.get("thinking_preview", "")
    answer_text = raw_json.get("answer_preview", "")

    payload = {
        "test_name": "proxy_sv2_opus_4_6_thinking",
        "provider": provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sdk_version": sdk_version,
        "model_requested": MODEL,
        "model_used": MODEL,
        "request": request_data,
        "response": response_dict,
        "model_in_response": key_observations.get("model_in_response"),
    }

    out_dir = project_root / "tests" / "llm_providers" / "evidence" / "anthropic"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "proxy_sv2_opus_4_6_thinking.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n→ Đã ghi JSON đầy đủ ra: {out_file}")
    print("\n--- Request ---")
    print(json.dumps(payload["request"], indent=2, ensure_ascii=False))
    print("\n--- Response (raw_json) ---")
    print(
        json.dumps(
            payload["response"]["raw_json"], indent=2, ensure_ascii=False, default=str
        )
    )
    print("\n--- Key observations ---")
    print(
        f"  thinking_present: {payload['response']['key_observations']['thinking_present']}"
    )
    print(f"  content_block_types: {content_block_types}")
    if thinking_text:
        print(f"  thinking_preview: {thinking_text[:200]}...")
    if answer_text:
        print(f"  answer_preview: {answer_text[:200]}...")

    if error_msg:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
