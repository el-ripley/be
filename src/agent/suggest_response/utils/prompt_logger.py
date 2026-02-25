"""Utility to log prompts for debugging and monitoring."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger()

# Directory to store logs (logs subdirectory under suggest_response)
LOGS_DIR = Path(__file__).parent.parent / "logs"


def log_suggest_response_prompts(
    input_messages: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    prefix: str = "suggest_response",
) -> None:
    """
    Log input_messages fully to .json and only system prompt to .md.
    Files are overwritten on each call (no timestamp in filename).

    Args:
        input_messages: List of message dicts (system + user/assistant messages)
        metadata: Optional metadata dict (stored in .json only)
        prefix: Prefix for log filenames (default: "suggest_response")

    Files created (overwritten on each call):
        - {prefix}.json - Full input_messages + metadata (complete payload)
        - {prefix}.md - Only system prompt content (raw/origin text, no code fences)
    """
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        # 1) .json: full input_messages + metadata
        json_filename = LOGS_DIR / f"{prefix}.json"
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "input_messages": input_messages,
            "metadata": metadata or {},
        }
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ Logged full input_messages to: {json_filename}")

        # 2) .md: only system prompt(s), raw/origin text (no extra ``` or formatting)
        md_filename = LOGS_DIR / f"{prefix}.md"
        md_parts: List[str] = ["# System Prompt", ""]
        system_messages = [m for m in input_messages or [] if m.get("role") == "system"]
        if not system_messages:
            md_parts.append("_No system message found._")
        else:
            for i, msg in enumerate(system_messages, start=1):
                content = msg.get("content", "")
                if len(system_messages) > 1 and i > 1:
                    md_parts.append("")
                    md_parts.append("---")
                    md_parts.append("")
                    md_parts.append(f"## System Message {i}")
                    md_parts.append("")
                raw_text = ""
                if isinstance(content, str):
                    raw_text = content
                elif isinstance(content, list):
                    text_parts = [
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("text")
                    ]
                    raw_text = "\n\n".join(text_parts)
                else:
                    raw_text = str(content)
                md_parts.append(raw_text)
                if i < len(system_messages):
                    md_parts.append("")

        with open(md_filename, "w", encoding="utf-8") as f:
            f.write("\n".join(md_parts).rstrip() + "\n")
        logger.info(f"✅ Logged system prompt only to: {md_filename}")

    except Exception as e:
        logger.error(f"❌ Error logging prompts: {str(e)}", exc_info=True)


def _extract_system_prompt_text(messages: List[Dict[str, Any]]) -> str:
    """Extract raw text from the first system message in a message list."""
    for m in messages or []:
        if m.get("role") != "system":
            continue
        content = m.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("text")
            ]
            return "\n\n".join(parts)
        return str(content)
    return ""


def log_playbook_retriever_input(
    user_id: str,
    fan_page_id: str,
    conversation_type: str,
    agent_response_id: str,
    input_messages: List[Dict[str, Any]],
    settings: Dict[str, Any],
    llm_messages: Optional[List[Dict[str, str]]] = None,
) -> None:
    """
    Log full input of PlaybookRetriever.retrieve() for debugging.

    Writes to suggest_response/logs/:
    - playbook_retriever_input.json (full payload including system_prompt)
    - playbook_retriever_system_prompt.md (system prompt only, human-readable)
    Does not log conn or api_key.

    Args:
        user_id: Owner user ID.
        fan_page_id: Facebook page ID.
        conversation_type: 'messages' or 'comments'.
        agent_response_id: Agent response ID for billing.
        input_messages: Prepared context messages passed to retriever.
        settings: LLM settings (model, reasoning, verbosity).
        llm_messages: Optional; messages actually sent to situation-analysis LLM (if already built).
    """
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        json_filename = LOGS_DIR / "playbook_retriever_input.json"
        log_data: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "fan_page_id": fan_page_id,
            "conversation_type": conversation_type,
            "agent_response_id": agent_response_id,
            "input_messages": input_messages,
            "settings": settings,
        }
        if llm_messages is not None:
            log_data["llm_messages"] = llm_messages
            system_prompt_text = _extract_system_prompt_text(llm_messages)
            if system_prompt_text:
                log_data["system_prompt"] = system_prompt_text

        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2, default=str)
        logger.info("✅ Logged playbook retriever full input to: %s", json_filename)

        # Also write system prompt to .md for easy reading
        system_prompt_text = log_data.get("system_prompt", "")
        md_filename = LOGS_DIR / "playbook_retriever_system_prompt.md"
        with open(md_filename, "w", encoding="utf-8") as f:
            f.write("# Playbook Retriever System Prompt\n\n")
            f.write(system_prompt_text if system_prompt_text else "_No system prompt._")
            f.write("\n")
        logger.info("✅ Logged playbook retriever system prompt to: %s", md_filename)
    except Exception as e:
        logger.error(
            f"❌ Error logging playbook retriever input: {e!s}",
            exc_info=True,
        )
