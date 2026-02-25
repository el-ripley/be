from typing import Any, Dict, List, Optional


class MultimodalContentBuilder:
    def __init__(self):
        self.text_items: List[str] = []
        self._media_entries: List[Dict[str, Any]] = []
        self._next_image_index = 0

    def add_text(self, text: Optional[str]) -> None:
        if text is None:
            return
        normalized = text.strip()
        if normalized:
            self.text_items.append(normalized)

    def add_json_payload(self, payload: Optional[Dict[str, Any]]) -> None:
        if not payload:
            return
        try:
            json_text = self._dump_json(payload)
            self.text_items.append(json_text)
        except (TypeError, ValueError):
            self.text_items.append(str(payload))

    def register_image(
        self, image_url: Optional[str], image_type: str
    ) -> Optional[Dict[str, Any]]:
        if not image_url:
            return None
        trimmed_url = image_url.strip()
        if not trimmed_url:
            return None

        index = self._next_image_index
        self._next_image_index += 1

        self._media_entries.append({"type": "input_image", "image_url": trimmed_url})
        return {"type": image_type, "index": index}

    def add_image_notice(
        self, text: Optional[str], image_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        normalized = text.strip()
        if not normalized:
            return None

        index = self._next_image_index
        self._next_image_index += 1

        self._media_entries.append({"type": "input_text", "text": normalized})
        if image_type:
            return {"type": image_type, "index": index}
        return None

    def get_text_items(self) -> List[str]:
        """
        Return the accumulated text items (system reminders or payloads).
        """
        return list(self.text_items)

    def get_media_entries(self) -> List[Dict[str, Any]]:
        """
        Return the media entries (images or text notices).
        """
        return list(self._media_entries)

    @staticmethod
    def _dump_json(payload: Dict[str, Any]) -> str:
        import json

        return json.dumps(payload, ensure_ascii=False, indent=4)


__all__ = ["MultimodalContentBuilder"]
