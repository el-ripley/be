"""Multimodal content builder for post details."""

from typing import Any, Dict, List, Optional


class MultimodalContentBuilder:
    """Builder for multimodal content (text + images)."""

    def __init__(self):
        self.text_items: List[str] = []
        self._media_entries: List[Dict[str, Any]] = []
        self._next_image_index = 0

    def register_image(
        self, image_url: str, image_type: str
    ) -> Optional[Dict[str, Any]]:
        """Register an image and return a reference."""
        if not image_url:
            return None

        trimmed_url = image_url.strip()
        if not trimmed_url:
            return None

        index = self._next_image_index
        self._next_image_index += 1

        self._media_entries.append({"type": "input_image", "image_url": trimmed_url})
        return {"type": image_type, "index": index}

    def get_media_entries(self) -> List[Dict[str, Any]]:
        """Return the media entries."""
        return list(self._media_entries)

    @staticmethod
    def _dump_json(payload: Dict[str, Any]) -> str:
        """Dump payload to JSON string."""
        import json

        return json.dumps(payload, ensure_ascii=False, indent=4)


__all__ = ["MultimodalContentBuilder"]
