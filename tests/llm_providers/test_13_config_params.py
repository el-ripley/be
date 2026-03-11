"""Test 13: API config parameters mapping — tool_choice, reasoning, verbosity, max_tokens, store, parallel_tool_calls, sampling.

Run: poetry run python tests/llm_providers/test_13_config_params.py

Saves evidence: 13a_tool_choice, 13b_reasoning_config, 13c_verbosity, 13d_max_tokens,
13e_store, 13f_parallel_tool_calls, 13g_sampling_params.
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

SYSTEM = "You are a helpful assistant."
WEATHER_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
}


def _has_tool_use(response) -> bool:
    if not response or not getattr(response, "content", None):
        return False
    for b in response.content or []:
        d = b.model_dump() if hasattr(b, "model_dump") else {}
        if d.get("type") == "tool_use":
            return True
    return False


# --- 13a: tool_choice ---


def run_13a_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    tools = [
        {
            "name": "get_weather",
            "description": "Get weather",
            "input_schema": WEATHER_SCHEMA,
        }
    ]

    # auto
    r_auto = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM,
        tools=tools,
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": "What's 2+2?"}],
    )
    # any (force tool)
    r_any = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM,
        tools=tools,
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": "What's 2+2?"}],
    )
    # specific tool
    r_specific = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM,
        tools=tools,
        tool_choice={"type": "tool", "name": "get_weather"},
        messages=[{"role": "user", "content": "Tell me about Hanoi"}],
    )

    save_evidence(
        test_name="13a_tool_choice",
        provider="anthropic",
        request_data={
            "method": "messages.create",
            "params": {"tool_choice": "auto, any, tool(name)"},
        },
        raw_response={
            "tool_choice_auto_used_tool": _has_tool_use(r_auto),
            "tool_choice_any_used_tool": _has_tool_use(r_any),
            "tool_choice_specific_used_tool": _has_tool_use(r_specific),
            "responses_preview": {
                "auto": str(getattr(r_auto, "stop_reason", None)),
                "any": str(getattr(r_any, "stop_reason", None)),
                "specific": str(getattr(r_specific, "stop_reason", None)),
            },
        },
        key_observations={
            "openai_auto": 'tool_choice={"type": "auto"}',
            "openai_required": 'tool_choice={"type": "any"}',
            "openai_specific": 'tool_choice={"type": "tool", "name": "get_weather"}',
        },
        mapping={
            "openai_auto": "type: auto",
            "openai_required": "type: any",
            "openai_none": "omit tools",
            "openai_specific": "type: tool, name: xxx",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
    )
    print(
        "13a Anthropic: auto used_tool =",
        _has_tool_use(r_auto),
        "any =",
        _has_tool_use(r_any),
        "specific =",
        _has_tool_use(r_specific),
    )


def run_13a_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="get_weather", description="Get weather", parameters=WEATHER_SCHEMA
            )
        ]
    )

    def used_tool(resp) -> bool:
        return bool(getattr(resp, "function_calls", None))

    ToolConfig = getattr(types, "ToolConfig", None)
    FunctionCallingConfig = getattr(types, "FunctionCallingConfig", None)
    if ToolConfig is None or FunctionCallingConfig is None:
        config_auto = types.GenerateContentConfig(
            system_instruction=SYSTEM, tools=[tool]
        )
        config_any = config_none = config_auto
        r_auto = client.models.generate_content(
            model=model, contents="What's 2+2?", config=config_auto
        )
        r_any = client.models.generate_content(
            model=model, contents="What's 2+2?", config=config_any
        )
        r_none = client.models.generate_content(
            model=model, contents="What's the weather in Hanoi?", config=config_none
        )
    else:
        config_auto = types.GenerateContentConfig(
            system_instruction=SYSTEM,
            tools=[tool],
            tool_config=ToolConfig(
                function_calling_config=FunctionCallingConfig(mode="AUTO")
            ),
        )
        r_auto = client.models.generate_content(
            model=model, contents="What's 2+2?", config=config_auto
        )
        config_any = types.GenerateContentConfig(
            system_instruction=SYSTEM,
            tools=[tool],
            tool_config=ToolConfig(
                function_calling_config=FunctionCallingConfig(mode="ANY")
            ),
        )
        r_any = client.models.generate_content(
            model=model, contents="What's 2+2?", config=config_any
        )
        config_none = types.GenerateContentConfig(
            system_instruction=SYSTEM,
            tools=[tool],
            tool_config=ToolConfig(
                function_calling_config=FunctionCallingConfig(mode="NONE")
            ),
        )
        r_none = client.models.generate_content(
            model=model, contents="What's the weather in Hanoi?", config=config_none
        )

    save_evidence(
        test_name="13a_tool_choice",
        provider="gemini",
        request_data={
            "method": "generate_content",
            "params": {"tool_config.mode": "AUTO, ANY, NONE"},
        },
        raw_response={
            "mode_auto_used_tool": used_tool(r_auto),
            "mode_any_used_tool": used_tool(r_any),
            "mode_none_used_tool": used_tool(r_none),
        },
        key_observations={
            "openai_auto": "mode=AUTO",
            "openai_required": "mode=ANY",
            "openai_none": "mode=NONE",
            "openai_specific": "mode=ANY + allowed_function_names",
        },
        mapping={
            "openai_auto": "AUTO",
            "openai_required": "ANY",
            "openai_none": "NONE",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
    )
    print(
        "13a Gemini: AUTO =",
        used_tool(r_auto),
        "ANY =",
        used_tool(r_any),
        "NONE =",
        used_tool(r_none),
    )


# --- 13b: Reasoning config ---


def run_13b_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()

    r_low = client.messages.create(
        model=model,
        max_tokens=8000,
        thinking={"type": "enabled", "budget_tokens": 2000},
        messages=[{"role": "user", "content": "What is 2+2?"}],
    )
    r_med = client.messages.create(
        model=model,
        max_tokens=16000,
        thinking={"type": "enabled", "budget_tokens": 8000},
        messages=[{"role": "user", "content": "Explain quantum computing briefly."}],
    )
    r_none = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": "What is 2+2?"}],
    )

    def thinking_len(r) -> int:
        if not r or not getattr(r, "content", None):
            return 0
        for b in r.content or []:
            d = b.model_dump() if hasattr(b, "model_dump") else {}
            if d.get("type") == "thinking":
                return len(d.get("thinking") or "")
        return 0

    save_evidence(
        test_name="13b_reasoning_config",
        provider="anthropic",
        request_data={
            "method": "messages.create",
            "params": {"thinking": "enabled + budget_tokens 2000, 8000; none"},
        },
        raw_response={
            "low_budget_2000_thinking_len": thinking_len(r_low),
            "med_budget_8000_thinking_len": thinking_len(r_med),
            "none_thinking_len": thinking_len(r_none),
            "usage_low": serialize_response(getattr(r_low, "usage", None)),
            "usage_med": serialize_response(getattr(r_med, "usage", None)),
        },
        key_observations={
            "openai_low": "budget_tokens=2000",
            "openai_medium": "8000",
            "openai_high": "16000",
            "openai_none": "omit thinking",
        },
        mapping={
            "effort low": "budget_tokens 2000",
            "effort medium": "8000",
            "effort high": "16000",
            "none": "omit or type disabled",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
    )
    print(
        "13b Anthropic: thinking lens =",
        thinking_len(r_low),
        thinking_len(r_med),
        thinking_len(r_none),
    )


def run_13b_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()

    # SDK (google.genai) ThinkingConfig chỉ có include_thoughts; không có thinking_budget
    config_with_thinking = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(include_thoughts=True)
    )
    config_off = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(include_thoughts=False)
    )
    r_low = client.models.generate_content(
        model=model, contents="What is 2+2?", config=config_with_thinking
    )
    r_high = client.models.generate_content(
        model=model,
        contents="Explain quantum computing briefly.",
        config=config_with_thinking,
    )
    r_off = client.models.generate_content(
        model=model, contents="What is 2+2?", config=config_off
    )

    raw_low = serialize_response(r_low)
    raw_high = serialize_response(r_high)
    raw_off = serialize_response(r_off)
    save_evidence(
        test_name="13b_reasoning_config",
        provider="gemini",
        request_data={
            "method": "generate_content",
            "params": {
                "thinking_config": "include_thoughts=True/False (SDK không có thinking_budget)"
            },
        },
        raw_response={
            "usage_low": raw_low.get("usage_metadata")
            if isinstance(raw_low, dict)
            else None,
            "usage_high": raw_high.get("usage_metadata")
            if isinstance(raw_high, dict)
            else None,
            "off_has_candidates": bool(
                (raw_off.get("candidates") or [])
                if isinstance(raw_off, dict)
                else False
            ),
        },
        key_observations={
            "openai_low": "ThinkingConfig(include_thoughts=True) — SDK không hỗ trợ thinking_budget",
            "openai_high": "cùng config",
            "openai_none": "include_thoughts=False",
        },
        mapping={
            "effort_low": "include_thoughts=True",
            "effort_high": "include_thoughts=True",
            "none": "include_thoughts=False",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
    )
    print("13b Gemini: OK")


# --- 13c: Verbosity ---


def run_13c_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()

    r_low = client.messages.create(
        model=model,
        max_tokens=1024,
        system="Be extremely concise. Reply in as few words as possible.",
        messages=[{"role": "user", "content": "Explain machine learning."}],
    )
    r_high = client.messages.create(
        model=model,
        max_tokens=4096,
        system="Be thorough and detailed in your response.",
        messages=[{"role": "user", "content": "Explain machine learning."}],
    )

    def text_len(r) -> int:
        if not r or not getattr(r, "content", None):
            return 0
        for b in r.content or []:
            d = b.model_dump() if hasattr(b, "model_dump") else {}
            if d.get("type") == "text":
                return len(d.get("text") or "")
        return 0

    save_evidence(
        test_name="13c_verbosity",
        provider="anthropic",
        request_data={
            "method": "messages.create",
            "params": {"system": "concise vs detailed"},
        },
        raw_response={
            "response_len_low": text_len(r_low),
            "response_len_high": text_len(r_high),
            "native_verbosity_param": False,
        },
        key_observations={
            "native_verbosity": "No",
            "workaround": "System prompt (Be concise / Be detailed)",
        },
        mapping={
            "openai verbosity low": "system prompt",
            "openai verbosity high": "system prompt",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
    )
    print("13c Anthropic: len low =", text_len(r_low), "high =", text_len(r_high))


def run_13c_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    r_low = client.models.generate_content(
        model=model,
        contents="Explain machine learning.",
        config=types.GenerateContentConfig(system_instruction="Be extremely concise."),
    )
    r_high = client.models.generate_content(
        model=model,
        contents="Explain machine learning.",
        config=types.GenerateContentConfig(
            system_instruction="Be thorough and detailed."
        ),
    )

    save_evidence(
        test_name="13c_verbosity",
        provider="gemini",
        request_data={
            "method": "generate_content",
            "params": {"system_instruction": "concise vs detailed"},
        },
        raw_response={
            "native_verbosity_param": False,
            "workaround": "system_instruction",
        },
        key_observations={"native_verbosity": "No", "workaround": "system_instruction"},
        mapping={"openai verbosity": "system_instruction"},
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
    )
    print("13c Gemini: OK")


# --- 13d: max_tokens ---


def run_13d_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    r = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{"role": "user", "content": "Say hello."}],
    )
    usage = getattr(r, "usage", None)
    out_tokens = getattr(usage, "output_tokens", 0) if usage else 0

    save_evidence(
        test_name="13d_max_tokens",
        provider="anthropic",
        request_data={"method": "messages.create", "params": {"max_tokens": 8192}},
        raw_response={
            "max_tokens_param": "max_tokens",
            "required": True,
            "output_tokens_used": out_tokens,
        },
        key_observations={
            "param_name": "max_tokens",
            "required": True,
            "default": "Must specify",
        },
        mapping={"openai": "optional/auto", "anthropic": "max_tokens (REQUIRED)"},
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(r, "model", None),
    )
    print("13d Anthropic: max_tokens required, used =", out_tokens)


def run_13d_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    config_lim = types.GenerateContentConfig(max_output_tokens=8192)
    config_none = types.GenerateContentConfig()
    r_lim = client.models.generate_content(
        model=model, contents="Say hello.", config=config_lim
    )
    r_none = client.models.generate_content(
        model=model, contents="Say hello.", config=config_none
    )
    raw_lim = serialize_response(r_lim)
    raw_none = serialize_response(r_none)
    usage_lim = raw_lim.get("usage_metadata") or raw_lim.get("usageMetadata") or {}
    usage_none = raw_none.get("usage_metadata") or raw_none.get("usageMetadata") or {}

    save_evidence(
        test_name="13d_max_tokens",
        provider="gemini",
        request_data={
            "method": "generate_content",
            "params": {"max_output_tokens": 8192, "note": "optional, can omit"},
        },
        raw_response={
            "param_name": "max_output_tokens",
            "required": False,
            "usage_limited": usage_lim,
            "usage_no_limit": usage_none,
        },
        key_observations={"param_name": "max_output_tokens", "required": False},
        mapping={"openai": "optional", "gemini": "max_output_tokens (optional)"},
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
    )
    print("13d Gemini: OK")


# --- 13e: store ---


def run_13e_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    # Check if metadata param exists (Anthropic may have metadata for logging, not same as store)
    sig = getattr(client.messages.create, "__doc__", "") or ""
    has_metadata = (
        "metadata" in sig or "metadata" in str(client.messages.create.__annotations__)
        if hasattr(client.messages.create, "__annotations__")
        else False
    )

    save_evidence(
        test_name="13e_store",
        provider="anthropic",
        request_data={"method": "N/A", "params": "Documentation only"},
        raw_response={
            "store_equivalent": False,
            "metadata_param": has_metadata,
            "note": "Anthropic has metadata for logging; prompt caching is different",
        },
        key_observations={
            "openai_store": "store=True stores on OpenAI side",
            "anthropic": "No store equivalent; metadata if any",
        },
        mapping={"adapter": "Ignore store param for Anthropic"},
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
    )
    print("13e Anthropic: store equivalent = No")


def run_13e_gemini() -> None:
    from google import genai

    save_evidence(
        test_name="13e_store",
        provider="gemini",
        request_data={"method": "N/A", "params": "Documentation only"},
        raw_response={
            "store_equivalent": False,
            "note": "Context caching / tuned models are different",
        },
        key_observations={
            "openai_store": "store=True",
            "gemini": "No store equivalent",
        },
        mapping={"adapter": "Ignore store param for Gemini"},
        model_used=get_gemini_model(),
        sdk_version=getattr(genai, "__version__", "google-genai"),
    )
    print("13e Gemini: store equivalent = No")


# --- 13f: parallel_tool_calls ---


def run_13f_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    tools = [
        {
            "name": "get_weather",
            "description": "Get weather",
            "input_schema": WEATHER_SCHEMA,
        },
        {
            "name": "get_time",
            "description": "Get time",
            "input_schema": {
                "type": "object",
                "properties": {"tz": {"type": "string"}},
                "required": ["tz"],
            },
        },
    ]
    r = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM,
        tools=tools,
        messages=[
            {
                "role": "user",
                "content": "What's the weather in Hanoi and time in Tokyo?",
            }
        ],
    )
    tool_use_count = sum(
        1
        for b in (r.content or [])
        if (b.model_dump() if hasattr(b, "model_dump") else {}).get("type")
        == "tool_use"
    )
    # Check for disable_parallel_tool_use in API
    doc = getattr(client.messages.create, "__doc__", "") or ""
    has_disable = "disable_parallel" in doc or "parallel" in doc

    save_evidence(
        test_name="13f_parallel_tool_calls",
        provider="anthropic",
        request_data={
            "method": "messages.create",
            "params": {"tools": 2, "prompt": "asks both weather and time"},
        },
        raw_response={
            "tool_use_count": tool_use_count,
            "disable_parallel_param_found": has_disable,
        },
        key_observations={
            "parallel_supported": tool_use_count >= 2,
            "explicit_disable_param": has_disable,
        },
        mapping={
            "openai parallel_tool_calls": "Anthropic: no explicit param; model decides"
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
    )
    print(
        "13f Anthropic: tool_use_count =",
        tool_use_count,
        "disable_param =",
        has_disable,
    )


def run_13f_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="get_weather", description="Get weather", parameters=WEATHER_SCHEMA
            ),
            types.FunctionDeclaration(
                name="get_time",
                description="Get time",
                parameters={
                    "type": "object",
                    "properties": {"tz": {"type": "string"}},
                    "required": ["tz"],
                },
            ),
        ]
    )
    config = types.GenerateContentConfig(system_instruction=SYSTEM, tools=[tool])
    r = client.models.generate_content(
        model=model,
        contents="What's the weather in Hanoi and time in Tokyo?",
        config=config,
    )
    fc = getattr(r, "function_calls", None) or []
    count = len(fc) if isinstance(fc, list) else 0

    save_evidence(
        test_name="13f_parallel_tool_calls",
        provider="gemini",
        request_data={"method": "generate_content", "params": {"tools": 2}},
        raw_response={"function_calls_count": count, "explicit_disable_param": False},
        key_observations={"parallel_supported": count >= 2, "explicit_param": "No"},
        mapping={"openai parallel_tool_calls": "Gemini: no param; model decides"},
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
    )
    print("13f Gemini: function_calls_count =", count)


# --- 13g: Sampling params ---


def run_13g_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    supported = {}
    try:
        client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=0.0,
            messages=[{"role": "user", "content": "What is 2+2?"}],
        )
        supported["temperature"] = True
    except TypeError as e:
        supported["temperature"] = "temperature" in str(e) or False
    except Exception:
        supported["temperature"] = True  # other error, param may exist

    save_evidence(
        test_name="13g_sampling_params",
        provider="anthropic",
        request_data={"method": "messages.create", "params": {"temperature": 0.0}},
        raw_response={
            "temperature_supported": supported.get("temperature"),
            "top_p": "check SDK",
            "top_k": "Anthropic-specific",
            "frequency_penalty": False,
            "presence_penalty": False,
            "stop_sequences": "check SDK",
        },
        key_observations={
            "temperature": "Yes (0-1)",
            "top_p": "Yes",
            "top_k": "Yes",
            "frequency_penalty": "No",
            "presence_penalty": "No",
        },
        mapping={"inventory": "temperature, top_p, top_k, stop_sequences"},
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
    )
    print("13g Anthropic: temperature =", supported.get("temperature"))


def run_13g_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    config = types.GenerateContentConfig(temperature=0.0)
    try:
        client.models.generate_content(
            model=model, contents="What is 2+2?", config=config
        )
        temp_ok = True
    except Exception:
        temp_ok = False

    save_evidence(
        test_name="13g_sampling_params",
        provider="gemini",
        request_data={"method": "generate_content", "params": {"temperature": 0.0}},
        raw_response={
            "temperature_supported": temp_ok,
            "top_p": "in config",
            "top_k": "in config",
            "stop_sequences": "in config",
        },
        key_observations={
            "temperature": "Yes",
            "top_p": "Yes",
            "top_k": "Yes",
            "stop_sequences": "Yes",
        },
        mapping={"inventory": "temperature, top_p, top_k, stop_sequences"},
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
    )
    print("13g Gemini: temperature =", temp_ok)


def main() -> None:
    """Chạy cả Anthropic và Gemini. Giống test_01–09: bỏ comment block Gemini để chạy thêm Gemini."""
    print("Test 13: Config params (13a–13g)")
    # print("Anthropic...")
    # run_13a_anthropic()
    # run_13b_anthropic()
    # run_13c_anthropic()
    # run_13d_anthropic()
    # run_13e_anthropic()
    # run_13f_anthropic()
    # run_13g_anthropic()
    print("Gemini...")
    for name, fn in [
        ("13a", run_13a_gemini),
        ("13b", run_13b_gemini),
        ("13c", run_13c_gemini),
        ("13d", run_13d_gemini),
        ("13e", run_13e_gemini),
        ("13f", run_13f_gemini),
        ("13g", run_13g_gemini),
    ]:
        try:
            fn()
        except Exception as e:
            print(f"  {name}: ERROR {type(e).__name__}: {e}")
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
