"""Tool description for Manage Playbook Tool."""

from pathlib import Path

_TOOL_DESCRIPTION_FILE = Path(__file__).parent / "tool_description.md"

with open(_TOOL_DESCRIPTION_FILE, "r", encoding="utf-8") as f:
    TOOL_DESCRIPTION = f.read().strip()
