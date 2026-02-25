"""Shared utilities for multi-LLM provider evidence collection tests."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Project root (parent of tests/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"

# Proxy config for Anthropic (from .env)
PROXY_BASE_URL = "http://supperapi.store"


def load_env() -> None:
    """Load .env from project root."""
    from dotenv import load_dotenv

    env_path = _PROJECT_ROOT / ".env"
    load_dotenv(env_path)


def get_anthropic_model() -> str:
    """Model for Anthropic via proxy. Default claude-sonnet-4-5 (cheaper); override with ANTHROPIC_MODEL in .env."""
    load_env()
    return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")


# Gemini model (Vertex/AI Studio); override with GOOGLE_VERTEX_MODEL in .env
GEMINI_MODEL = "gemini-2.5-pro"


def get_gemini_model() -> str:
    """Model for Gemini. Default gemini-2.5-pro; override with GOOGLE_VERTEX_MODEL in .env."""
    load_env()
    return os.getenv("GOOGLE_VERTEX_MODEL", GEMINI_MODEL)


def get_anthropic_client():  # type: ignore[no-untyped-def]
    """Return sync Anthropic client via proxy (supperapi.store)."""
    load_env()
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY_PROXY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY_PROXY not set in .env")
    return anthropic.Anthropic(
        api_key=api_key,
        base_url=PROXY_BASE_URL,
        timeout=120.0,
    )


def get_anthropic_client_direct():  # type: ignore[no-untyped-def]
    """Return sync Anthropic client using direct API key (no proxy). Uses ANTHROPIC_API_KEY from .env."""
    load_env()
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
    return anthropic.Anthropic(
        api_key=api_key,
        timeout=120.0,
    )


def get_anthropic_async_client():  # type: ignore[no-untyped-def]
    """Return async Anthropic client via proxy."""
    load_env()
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY_PROXY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY_PROXY not set in .env")
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url=PROXY_BASE_URL,
        timeout=120.0,
    )


def get_gemini_client():  # type: ignore[no-untyped-def]
    """Return google.genai Client: Vertex (service account JSON) or AI Studio (api_key).

    - Vertex: set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS in .env to path to JSON
      (e.g. .gcp/sa.json). Optionally GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION (default us-central1).
      Path is resolved relative to project root if not absolute.
    - AI Studio: set GOOGLE_API_KEY (or GEMINI_API_KEY).
    """
    load_env()
    from google import genai

    json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS"
    )
    if json_path:
        if not os.path.isabs(json_path):
            json_path = str(_PROJECT_ROOT / json_path)
    if json_path and os.path.isfile(json_path):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(json_path)
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project:
            with open(json_path, encoding="utf-8") as f:
                project = json.load(f).get("project_id", "")
        if not project:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT not set and project_id not in JSON"
            )
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        return genai.Client(vertexai=True, project=project, location=location)
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set GOOGLE_API_KEY (AI Studio) or GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_APPLICATION_CREDENTIALS (Vertex JSON path)"
        )
    return genai.Client(api_key=api_key)


def serialize_response(obj: Any) -> Any:
    """Recursively convert SDK response objects to JSON-serializable form."""
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


def save_evidence(
    test_name: str,
    provider: str,
    request_data: dict[str, Any],
    raw_response: Any,
    key_observations: dict[str, Any],
    mapping: dict[str, Any],
    model_used: str,
    sdk_version: str = "",
    model_in_response: str | None = None,
) -> Path:
    """Save structured evidence JSON to evidence/{provider}/{test_name}.json.

    model_used: model requested in the API call.
    model_in_response: model ID returned by the API (to detect proxy/model substitution).
    """
    provider_dir = _EVIDENCE_DIR / provider
    provider_dir.mkdir(parents=True, exist_ok=True)
    path = provider_dir / f"{test_name}.json"
    raw_json = serialize_response(raw_response)
    payload = {
        "test_name": test_name,
        "provider": provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sdk_version": sdk_version,
        "model_requested": model_used,
        "model_used": model_used,
        "request": request_data,
        "response": {
            "raw_json": raw_json,
            "key_observations": key_observations,
        },
        "mapping_to_current_system": mapping,
    }
    if model_in_response is not None:
        payload["model_in_response"] = model_in_response
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    return path
