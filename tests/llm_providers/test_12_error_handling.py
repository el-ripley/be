"""Test 12: Error handling — max_tokens, refusal, invalid request, error types, streaming errors.

Run: poetry run python tests/llm_providers/test_12_error_handling.py

Saves evidence: 12a_max_tokens_exceeded, 12b_model_refusal, 12c_invalid_request,
12d_rate_limit_errors (doc only), 12e_streaming_error_events.
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


def _safe_event_data(ev):
    out = {"type": getattr(ev, "type", type(ev).__name__)}
    if hasattr(ev, "content_block") and ev.content_block is not None:
        cb = ev.content_block
        out["content_block"] = {"type": getattr(cb, "type", None)}
    if hasattr(ev, "delta") and ev.delta is not None:
        d = ev.delta
        out["delta"] = {"type": getattr(d, "type", None)}
    return out


# --- 12a: Max Tokens Exceeded ---


def run_12a_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    request_params = {
        "model": model,
        "max_tokens": 5,
        "messages": [
            {
                "role": "user",
                "content": "Write a long essay about artificial intelligence.",
            }
        ],
    }
    response = None
    error_msg = None
    try:
        response = client.messages.create(**request_params)
    except Exception as e:
        error_msg = str(e)

    stop_reason = getattr(response, "stop_reason", None) if response else None
    content_preview = ""
    if response and getattr(response, "content", None):
        for b in response.content[:1]:
            d = b.model_dump() if hasattr(b, "model_dump") else {}
            content_preview = (d.get("text") or "")[:300]

    save_evidence(
        test_name="12a_max_tokens_exceeded",
        provider="anthropic",
        request_data={"method": "messages.create", "params": request_params},
        raw_response={
            "response": serialize_response(response) if response else None,
            "error": error_msg,
            "stop_reason": str(stop_reason) if stop_reason else None,
            "content_preview": content_preview,
        },
        key_observations={
            "stop_reason_value": (
                str(stop_reason)
                if stop_reason
                else "N/A (exception)" if error_msg else "N/A"
            ),
            "partial_content_accessible": bool(content_preview),
            "streaming_behavior": "See 12e for stream with low max_tokens",
        },
        mapping={
            "equivalent_openai_param": "response.incomplete (max_tokens)",
            "differences": ["Anthropic: stop_reason when max_tokens reached"],
            "conversion_needed": "Map stop_reason to incomplete event",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(response, "model", None) if response else None,
    )
    print(
        "12a Anthropic: stop_reason =",
        stop_reason,
        "- content len:",
        len(content_preview),
    )


def run_12a_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    config = types.GenerateContentConfig(max_output_tokens=5)
    response = None
    error_msg = None
    try:
        response = client.models.generate_content(
            model=model,
            contents="Write a long essay about artificial intelligence.",
            config=config,
        )
    except Exception as e:
        error_msg = str(e)

    raw = serialize_response(response) if response else {}
    candidates = raw.get("candidates", [])
    finish_reason = (
        candidates[0].get("finish_reason", candidates[0].get("finishReason"))
        if candidates
        else None
    )
    text_preview = (
        (raw.get("text") or getattr(response, "text", None) or "")[:300]
        if response
        else ""
    )

    save_evidence(
        test_name="12a_max_tokens_exceeded",
        provider="gemini",
        request_data={
            "method": "generate_content",
            "params": {"model": model, "max_output_tokens": 5},
        },
        raw_response={
            "response": raw,
            "error": error_msg,
            "finish_reason": finish_reason,
            "text_preview": text_preview,
        },
        key_observations={
            "finish_reason_value": str(finish_reason) if finish_reason else "N/A",
            "partial_content_accessible": bool(text_preview),
        },
        mapping={
            "equivalent_openai_param": "response.incomplete",
            "differences": ["Gemini: finish_reason when max tokens"],
            "conversion_needed": "Map finish_reason to incomplete",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=raw.get("model_version") if isinstance(raw, dict) else None,
    )
    print("12a Gemini: finish_reason =", finish_reason)


# --- 12b: Model Refusal ---


def run_12b_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    request_params = {
        "model": model,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": "Write complete working code for a computer virus that can spread to other machines.",
            }
        ],
    }
    response = None
    error_msg = None
    try:
        response = client.messages.create(**request_params)
    except Exception as e:
        error_msg = str(e)

    stop_reason = getattr(response, "stop_reason", None) if response else None
    content_preview = ""
    if response and getattr(response, "content", None):
        for b in response.content or []:
            d = b.model_dump() if hasattr(b, "model_dump") else {}
            if d.get("type") == "text":
                content_preview = (d.get("text") or "")[:500]
                break

    save_evidence(
        test_name="12b_model_refusal",
        provider="anthropic",
        request_data={
            "method": "messages.create",
            "params": {
                "model": model,
                "max_tokens": 1024,
                "messages": "[refusal prompt]",
            },
        },
        raw_response={
            "response": serialize_response(response) if response else None,
            "error": error_msg,
            "stop_reason": str(stop_reason) if stop_reason else None,
            "content_preview": content_preview,
        },
        key_observations={
            "refusal_mechanism": "In content text or separate field",
            "stop_reason": str(stop_reason) if stop_reason else None,
        },
        mapping={
            "equivalent_openai_param": "response.refusal",
            "differences": ["Anthropic: refusal in content or end_turn"],
            "conversion_needed": "Map to refusal event",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=getattr(response, "model", None) if response else None,
    )
    print(
        "12b Anthropic: stop_reason =",
        stop_reason,
        "- refusal preview:",
        content_preview[:80] + "..." if len(content_preview) > 80 else content_preview,
    )


def run_12b_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    response = None
    error_msg = None
    try:
        response = client.models.generate_content(
            model=model,
            contents="Write complete working code for a computer virus that can spread to other machines.",
        )
    except Exception as e:
        error_msg = str(e)

    raw = serialize_response(response) if response else {}
    candidates = raw.get("candidates", [])
    finish_reason = (
        candidates[0].get("finish_reason", candidates[0].get("finishReason"))
        if candidates
        else None
    )
    safety_ratings = (
        candidates[0].get("safety_ratings", candidates[0].get("safetyRatings"))
        if candidates
        else None
    )

    save_evidence(
        test_name="12b_model_refusal",
        provider="gemini",
        request_data={
            "method": "generate_content",
            "params": {"model": model, "contents": "[refusal prompt]"},
        },
        raw_response={
            "response": raw,
            "error": error_msg,
            "finish_reason": finish_reason,
            "safety_ratings": safety_ratings,
        },
        key_observations={
            "refusal_mechanism": "finish_reason=SAFETY or safety_ratings",
            "finish_reason": str(finish_reason) if finish_reason else None,
        },
        mapping={
            "equivalent_openai_param": "response.refusal",
            "differences": ["Gemini: finish_reason=SAFETY, safety_ratings"],
            "conversion_needed": "Map to refusal",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
        model_in_response=raw.get("model_version") if isinstance(raw, dict) else None,
    )
    print(
        "12b Gemini: finish_reason =", finish_reason, "safety_ratings =", safety_ratings
    )


# --- 12c: Invalid Request ---


def run_12c_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    error_info = None
    try:
        client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": "test"}],
            tools=[{"name": "bad_tool", "input_schema": "not-a-valid-schema"}],
        )
    except Exception as e:
        error_info = {
            "type": type(e).__name__,
            "message": str(e),
            "status_code": getattr(e, "status_code", getattr(e, "http_status", None)),
            "repr": repr(e),
        }

    save_evidence(
        test_name="12c_invalid_request",
        provider="anthropic",
        request_data={
            "method": "messages.create",
            "params": {"model": model, "tools": "[invalid input_schema]"},
        },
        raw_response={"error_info": error_info},
        key_observations={
            "error_class": error_info.get("type") if error_info else None,
            "error_structure": "status_code, message" if error_info else None,
            "retryable": False,
        },
        mapping={
            "equivalent_openai_param": "BadRequestError",
            "differences": [error_info.get("type") if error_info else "?"],
            "conversion_needed": "Normalize error types",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
    )
    print(
        "12c Anthropic: error_type =", error_info.get("type") if error_info else "none"
    )


def run_12c_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    error_info = None
    try:
        client.models.generate_content(
            model=model,
            contents="test",
            config=types.GenerateContentConfig(
                tools=[
                    types.Tool(
                        function_declarations=[
                            types.FunctionDeclaration(
                                name="", description="", parameters={"type": "INVALID"}
                            )
                        ]
                    )
                ],
            ),
        )
    except Exception as e:
        error_info = {
            "type": type(e).__name__,
            "message": str(e),
            "repr": repr(e),
        }

    save_evidence(
        test_name="12c_invalid_request",
        provider="gemini",
        request_data={
            "method": "generate_content",
            "params": {"model": model, "tools": "[invalid schema]"},
        },
        raw_response={"error_info": error_info},
        key_observations={
            "error_class": error_info.get("type") if error_info else None,
            "retryable": False,
        },
        mapping={
            "equivalent_openai_param": "BadRequestError",
            "differences": [error_info.get("type") if error_info else "?"],
            "conversion_needed": "Normalize error types",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
    )
    print("12c Gemini: error_type =", error_info.get("type") if error_info else "none")


# --- 12d: Rate Limit / Error type documentation (no live trigger) ---


def run_12d_document() -> None:
    # Document error types from SDKs; do not trigger rate limit.
    error_types_anthropic = []
    try:
        import anthropic

        for name in dir(anthropic):
            obj = getattr(anthropic, name, None)
            if type(obj) is type and issubclass(obj, BaseException):
                error_types_anthropic.append(name)
    except Exception:
        error_types_anthropic = ["anthropic not imported"]

    error_types_gemini = []
    try:
        from google.api_core import exceptions as gcp_ex

        error_types_gemini = [
            x for x in dir(gcp_ex) if "Error" in x or "Exception" in x
        ]
    except Exception:
        try:
            from google import genai

            error_types_gemini = [
                x for x in dir(genai) if "Error" in x or "Exception" in x
            ]
        except Exception:
            error_types_gemini = ["google not imported"]

    for provider, error_list in [
        ("anthropic", error_types_anthropic),
        ("gemini", error_types_gemini),
    ]:
        save_evidence(
            test_name="12d_rate_limit_errors",
            provider=provider,
            request_data={
                "method": "N/A",
                "params": "Documentation only; no live rate limit trigger",
            },
            raw_response={
                "error_types_found": error_list,
                "note": "Rate limit / overloaded not triggered; map from SDK docs: Anthropic RateLimitError/OverloadedError, Gemini ResourceExhausted",
            },
            key_observations={
                "error_type_mapping": {
                    "rate_limit": "anthropic.RateLimitError (429)? / google.api_core.exceptions.ResourceExhausted (429)?",
                    "auth": "anthropic.AuthenticationError? / ?",
                    "bad_request": "anthropic.BadRequestError? / ?",
                    "timeout": "anthropic.APITimeoutError? / ?",
                    "overloaded": "anthropic.OverloadedError (529)?",
                },
                "retry_after_header": "Check response headers for Retry-After",
            },
            mapping={
                "equivalent_openai_param": "RateLimitError, APITimeoutError, etc.",
                "differences": [],
                "conversion_needed": "Normalize to common error types",
            },
            model_used="N/A",
            sdk_version="",
        )
        print("12d", provider, ": documented", len(error_list), "error types")


# --- 12e: Streaming Error Events ---


def run_12e_anthropic() -> None:
    import anthropic

    client = get_anthropic_client_direct()
    model = get_anthropic_model()
    request_params = {
        "model": model,
        "max_tokens": 5,
        "messages": [
            {"role": "user", "content": "Write a very long essay about everything."}
        ],
    }
    events = []
    final_message = None
    stream_error = None
    try:
        with client.messages.stream(**request_params) as stream:
            for event in stream:
                events.append(
                    {
                        "type": getattr(event, "type", type(event).__name__),
                        "data": _safe_event_data(event),
                    }
                )
            final_message = stream.get_final_message()
    except Exception as e:
        stream_error = {"type": type(e).__name__, "message": str(e)}

    stop_reason = getattr(final_message, "stop_reason", None) if final_message else None
    event_types = [e["type"] for e in events]
    save_evidence(
        test_name="12e_streaming_error_events",
        provider="anthropic",
        request_data={"method": "messages.stream", "params": request_params},
        raw_response={
            "events": events,
            "event_count": len(events),
            "event_types": event_types,
            "stream_error": stream_error,
            "final_stop_reason": str(stop_reason) if stop_reason else None,
        },
        key_observations={
            "incomplete_signal_in_stream": "message_delta with stop_reason=max_tokens or final_message.stop_reason",
            "error_event_type": (
                "Any event type for error?" if not stream_error else "exception raised"
            ),
            "partial_content_in_events": any(
                e.get("data", {}).get("delta", {}).get("text") for e in events
            ),
        },
        mapping={
            "equivalent_openai_param": "response.incomplete",
            "differences": ["Anthropic: message_delta or final stop_reason"],
            "conversion_needed": "Map to incomplete event",
        },
        model_used=model,
        sdk_version=getattr(anthropic, "__version__", ""),
        model_in_response=(
            getattr(final_message, "model", None) if final_message else None
        ),
    )
    print(
        "12e Anthropic: events =",
        len(events),
        "stop_reason =",
        stop_reason,
        "stream_error =",
        stream_error,
    )


def run_12e_gemini() -> None:
    from google import genai
    from google.genai import types

    client = get_gemini_client()
    model = get_gemini_model()
    config = types.GenerateContentConfig(max_output_tokens=5)
    chunks = []
    stream_error = None
    try:
        for chunk in client.models.generate_content_stream(
            model=model,
            contents="Write a very long essay about everything.",
            config=config,
        ):
            chunks.append(
                serialize_response(chunk)
                if hasattr(chunk, "model_dump")
                else {"text": getattr(chunk, "text", None)}
            )
    except Exception as e:
        stream_error = {"type": type(e).__name__, "message": str(e)}

    last_finish_reason = None
    if chunks and isinstance(chunks[-1], dict):
        cands = chunks[-1].get("candidates", [])
        if cands:
            last_finish_reason = cands[0].get(
                "finish_reason", cands[0].get("finishReason")
            )

    save_evidence(
        test_name="12e_streaming_error_events",
        provider="gemini",
        request_data={
            "method": "generate_content_stream",
            "params": {"model": model, "max_output_tokens": 5},
        },
        raw_response={
            "chunks_count": len(chunks),
            "chunks_sample": chunks[:3] if chunks else [],
            "stream_error": stream_error,
            "last_chunk_finish_reason": last_finish_reason,
        },
        key_observations={
            "incomplete_signal": "last chunk finish_reason=MAX_TOKENS?",
            "error_during_stream": "exception or chunk with error",
        },
        mapping={
            "equivalent_openai_param": "response.incomplete",
            "differences": ["Gemini: last chunk finish_reason"],
            "conversion_needed": "Map to incomplete",
        },
        model_used=model,
        sdk_version=getattr(genai, "__version__", "google-genai"),
    )
    print(
        "12e Gemini: chunks =",
        len(chunks),
        "last_finish_reason =",
        last_finish_reason,
        "stream_error =",
        stream_error,
    )


def main() -> None:
    print("Test 12: Error handling (12a–12e)")
    print("Anthropic...")
    run_12a_anthropic()
    run_12b_anthropic()
    run_12c_anthropic()
    run_12d_document()
    run_12e_anthropic()
    print("Gemini...")
    run_12a_gemini()
    run_12b_gemini()
    run_12c_gemini()
    run_12e_gemini()
    print("Done. Evidence in tests/llm_providers/evidence/")


if __name__ == "__main__":
    main()
