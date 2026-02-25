"""Shared agent type constants used for cost tracking."""

# Main general agent (the primary agent in agent_runner)
AGENT_TYPE_GENERAL_AGENT = "general_agent"

# Summarization agent for context compression
AGENT_TYPE_SUMMARIZATION_AGENT = "summarization_agent"

# Media description agent for image description (oneshot)
AGENT_TYPE_MEDIA_DESCRIPTION_AGENT = "media_description_agent"

# Suggest response agent for generating reply suggestions
AGENT_TYPE_SUGGEST_RESPONSE_AGENT = "suggest_response_agent"

# Subagent types
AGENT_TYPE_SUBAGENT_EXPLORE = "subagent_explore"

__all__ = [
    "AGENT_TYPE_GENERAL_AGENT",
    "AGENT_TYPE_SUMMARIZATION_AGENT",
    "AGENT_TYPE_MEDIA_DESCRIPTION_AGENT",
    "AGENT_TYPE_SUGGEST_RESPONSE_AGENT",
    "AGENT_TYPE_SUBAGENT_EXPLORE",
]
