import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def ensure_dict(data: Any) -> Optional[Dict[str, Any]]:
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def ensure_list(data: Any) -> List[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def looks_like_content_list(content: Any) -> bool:
    if not isinstance(content, list) or not content:
        return False

    for item in content:
        if not isinstance(item, dict):
            return False
        item_type = item.get("type")
        if not item_type:
            return False
        if item_type in {"input_text", "output_text", "summary_text", "refusal"}:
            if "text" not in item:
                return False
        elif item_type == "input_image":
            if "image_url" not in item:
                return False
    return True


def ensure_content_items(content: Any, role: str) -> List[Dict[str, Any]]:
    if looks_like_content_list(content):
        return content  # type: ignore[return-value]

    if isinstance(content, str):
        text = content
    elif content is None:
        text = ""
    else:
        text = stringify_content(content)

    item_type = "output_text" if role == "assistant" else "input_text"
    return [{"type": item_type, "text": text}]


def stringify_json_payload(payload: Any) -> str:
    if payload is None:
        return "{}"
    if isinstance(payload, str):
        stripped = payload.strip()
        return stripped or "{}"
    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return stringify_content(payload)


def stringify_content(content: Any) -> str:
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def extract_url(value: Any) -> Optional[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, dict):
        for key in ("url", "src", "href", "link", "image_url"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def safe_timestamp(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def format_timestamp(value: Any) -> str:
    """
    Convert supported timestamp inputs into a human-readable UTC string.
    Returns an empty string when input cannot be parsed.
    """
    ts = safe_timestamp(value)
    if ts <= 0:
        return ""

    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return ""

    return dt.strftime("%Y-%m-%d %H:%M:%S %Z").strip()


async def replace_expired_images_with_map(
    content: List[Dict[str, Any]], expiration_map: Dict[str, Optional[int]]
) -> List[Dict[str, Any]]:
    """
    Replace expired images in content array using pre-fetched expiration map.
    This is the optimized version that uses a shared expiration map for all messages.

    Args:
        content: Content array with potential image items
        expiration_map: Pre-fetched map of URL -> expires_at from media_assets

    Returns:
        Content array with expired images replaced by placeholder text
    """
    if not isinstance(content, list):
        return content

    # Get current timestamp in milliseconds
    current_timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Process content and replace expired images
    result: List[Dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            result.append(item)
            continue

        item_type = item.get("type")
        if item_type == "input_image":
            image_url = item.get("image_url")
            if image_url:
                # Check if URL is in expiration_map (means it's in media_assets table)
                if image_url in expiration_map:
                    expires_at = expiration_map[image_url]
                    # expires_at can be None (permanent) or int (expiration timestamp)
                    if expires_at is not None and expires_at <= current_timestamp_ms:
                        # Expired - replace with placeholder
                        result.append(
                            {
                                "type": "input_text",
                                "text": "image expired - no longer available",
                            }
                        )
                    else:
                        # Not expired (or permanent with expires_at=None) - keep image
                        result.append(item)
                else:
                    # Not found in expiration_map - assume not expired (external/legacy URLs)
                    result.append(item)
            else:
                result.append(item)
        else:
            # Not an image, keep as is
            result.append(item)

    return result


__all__ = [
    "ensure_dict",
    "ensure_list",
    "looks_like_content_list",
    "ensure_content_items",
    "stringify_json_payload",
    "stringify_content",
    "extract_url",
    "safe_timestamp",
    "format_timestamp",
    "replace_expired_images_with_map",
]
