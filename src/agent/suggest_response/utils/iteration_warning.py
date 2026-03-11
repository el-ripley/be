"""Inject iteration limit warnings into tool results for suggest_response agent."""

from typing import List, Optional

from src.agent.general_agent.context.function_output_normalizer import (
    normalize_function_output_to_api_format,
)
from src.api.openai_conversations.schemas import MessageResponse


class SuggestResponseIterationWarningInjector:
    """Inject iteration warnings into tool results at threshold levels for suggest_response."""

    THRESHOLDS = [
        (0.90, "critical"),  # 90% - last chance
        (0.80, "urgent"),  # 80% - running low
    ]

    WARNING_MESSAGES = {
        "urgent": (
            "<system-reminder>\n"
            "ITERATION WARNING: {used}/{max} iterations used (80% limit).\n"
            "You should call generate_suggestions soon to complete this run.\n"
            "IMPORTANT: When max iterations reached, the system will FORCE TERMINATE "
            "and return 0 suggestions to the user. Plan to finish within the remaining iterations.\n"
            "NOTE: This reminder was injected by the system and is not part of the tool output above.\n"
            "</system-reminder>"
        ),
        "critical": (
            "<system-reminder>\n"
            "ITERATION CRITICAL: {used}/{max} iterations used (90% limit).\n"
            "Only {remaining} iteration(s) remaining before FORCE TERMINATION.\n"
            "Action required: Call generate_suggestions NOW to provide suggestions to the user.\n"
            "If you don't call generate_suggestions before the limit, the run will fail with 0 suggestions.\n"
            "NOTE: This reminder was injected by the system and is not part of the tool output above.\n"
            "</system-reminder>"
        ),
    }

    @classmethod
    def get_warning_level(cls, current: int, max_iter: int) -> Optional[str]:
        """Return warning level if at threshold, None otherwise."""
        if max_iter <= 0:
            return None
        # current is 0-based, so actual iteration number is current + 1
        ratio = (current + 1) / max_iter
        for threshold, level in cls.THRESHOLDS:
            if ratio >= threshold:
                return level
        return None

    @classmethod
    def get_warning_message(cls, current: int, max_iter: int) -> Optional[str]:
        """Return formatted warning message or None."""
        level = cls.get_warning_level(current, max_iter)
        if not level:
            return None
        used = current + 1
        remaining = max(0, max_iter - used)
        return cls.WARNING_MESSAGES[level].format(
            used=used, max=max_iter, remaining=remaining
        )

    @classmethod
    def inject_warning(
        cls,
        temp_messages: List[MessageResponse],
        current_iteration: int,
        max_iteration: int,
    ) -> None:
        """
        Inject warning into the LAST function_call_output message.
        Modifies temp_messages in place.
        """
        warning = cls.get_warning_message(current_iteration, max_iteration)
        if not warning:
            return

        # Find last function_call_output message (iterate backwards)
        for msg in reversed(temp_messages):
            if msg.type == "function_call_output":
                # Normalize to array format (API: input_text + text) via shared normalizer
                output_array = normalize_function_output_to_api_format(
                    msg.function_output
                )
                # Append warning as input_text block (API expects input_text for array content)
                output_array.append({"type": "input_text", "text": warning})
                # Update message (Pydantic model allows mutation by default)
                msg.function_output = output_array
                break  # Only inject into last one
