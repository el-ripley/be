# OpenAI Model Pricing (per 1M tokens)
# Format: model_name: {"input": price, "output": price, "context": price}

OPENAI_MODEL_PRICING = {
    # OpenAI GPT-5 family (allowed models)
    # Prices are per 1M tokens from https://openai.com/api/pricing
    "gpt-5.2": {"input": 1.75, "output": 14.00, "context_window": 400000},
    "gpt-5": {"input": 1.25, "output": 10.00, "context_window": 400000},
    "gpt-5-mini": {"input": 0.25, "output": 2.00, "context_window": 400000},
    "gpt-5-nano": {"input": 0.05, "output": 0.40, "context_window": 400000},
}

# Embedding model pricing (per 1M tokens, input only - no output)
OPENAI_EMBEDDING_PRICING = {
    "text-embedding-3-large": {"input": 0.13},  # $0.13 per 1M tokens, no output tokens
}

# Include embedding models in OPENAI_MODEL_PRICING so calculate_cost works
# (output=0 for embedding models)
OPENAI_MODEL_PRICING["text-embedding-3-large"] = {
    "input": 0.13,
    "output": 0,
    "context_window": 8191,
}

# Model names list for easy access
OPENAI_MODELS = list(OPENAI_MODEL_PRICING.keys())
