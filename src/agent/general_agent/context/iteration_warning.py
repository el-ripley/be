"""Inject iteration limit warnings into tool results at 80%, 90%, 95% thresholds."""

from typing import List, Optional

from src.api.openai_conversations.schemas import MessageResponse

from .function_output_normalizer import normalize_function_output_to_api_format


class IterationWarningInjector:
    """Inject iteration warnings into tool results at threshold levels."""

    THRESHOLDS = [
        (0.95, "critical"),  # 95% - kết thúc ngay
        (0.90, "urgent"),  # 90% - hoàn thành nhanh
        (0.80, "warning"),  # 80% - cảnh báo nhẹ
    ]

    WARNING_MESSAGES = {
        "warning": (
            "<system-reminder>\n"
            "ITERATION WARNING: {used}/{max} iterations used (80% limit).\n"
            "Plan remaining work efficiently. Prioritize essential tasks.\n"
            "IMPORTANT: When max iterations reached, the system will FORCE TERMINATE this conversation "
            "mid-execution. You will not get another chance to respond to the user.\n"
            "NOTE: This reminder was injected by the system and is not part of the tool output above.\n"
            "</system-reminder>"
        ),
        "urgent": (
            "<system-reminder>\n"
            "ITERATION URGENT: {used}/{max} iterations used (90% limit).\n"
            "Only {remaining} iteration(s) remaining before FORCE TERMINATION.\n"
            "Action required: WRAP UP NOW. Complete current task and send final response to user.\n"
            "If you don't respond before the limit, the conversation ends abruptly and user receives nothing.\n"
            "NOTE: This reminder was injected by the system and is not part of the tool output above.\n"
            "</system-reminder>"
        ),
        "critical": (
            "<system-reminder>\n"
            "ITERATION CRITICAL: {used}/{max} iterations used (AT LIMIT).\n"
            "This is your LAST chance to respond. The system will TERMINATE after this iteration.\n"
            "Action required: STOP all tool calls. Send final summary to user NOW.\n"
            "If you call another tool instead of responding, user receives no response.\n"
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
