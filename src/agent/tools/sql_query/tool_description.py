"""Tool description for SQL Query Tool.

Loads description from tool_description.md file and caches it at import time.
This provides better readability while maintaining the same performance as a Python constant.
"""

from pathlib import Path

# Load and cache the description at module import time
# This ensures same performance as a Python constant (read once, cached in memory)
_TOOL_DESCRIPTION_FILE = Path(__file__).parent / "tool_description.md"

with open(_TOOL_DESCRIPTION_FILE, "r", encoding="utf-8") as f:
    TOOL_DESCRIPTION = f.read().strip()
