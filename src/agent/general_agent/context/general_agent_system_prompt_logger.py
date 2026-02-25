"""Log utility to write system prompt to context/logs/general_agent_system_prompt.md (dev only)."""

from pathlib import Path

from src.settings import AppEnvironment, settings


def logs_general_agent_system_prompt(temp_context: list) -> None:
    """Extract system prompt from temp_context and write to logs/ (only when APP_ENV=development)."""
    if settings.app_env != AppEnvironment.DEVELOPMENT:
        return

    if not temp_context or not temp_context[0].get("content"):
        return

    system_content = temp_context[0]["content"]
    if not system_content or not system_content[0].get("text"):
        return

    system_prompt_text = system_content[0]["text"]

    # Write next to this module: context/logs/ (dev only, typically gitignored)
    this_dir = Path(__file__).resolve().parent
    logs_dir = this_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_path = logs_dir / "general_agent_system_prompt.md"
    file_path.write_text(system_prompt_text, encoding="utf-8")
