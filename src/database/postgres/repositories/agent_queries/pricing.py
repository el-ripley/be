"""
Model pricing constants and cost calculation utilities.
"""

from decimal import Decimal
from typing import Dict

from src.agent.common.constants import OPENAI_MODEL_PRICING

# ================================================================
# MODEL PRICING CONSTANTS
# ================================================================

_PER_TOKEN_DIVISOR = Decimal("1000000")  # constants store cost per 1M tokens
_DEFAULT_MODEL = "gpt-5-mini"

MODEL_PRICING = {
    name: {
        "input": Decimal(str(pricing["input"])) / _PER_TOKEN_DIVISOR,
        "output": Decimal(str(pricing["output"])) / _PER_TOKEN_DIVISOR,
    }
    for name, pricing in OPENAI_MODEL_PRICING.items()
}
if MODEL_PRICING:
    DEFAULT_PRICING = MODEL_PRICING.get(
        _DEFAULT_MODEL, next(iter(MODEL_PRICING.values()))
    )
else:
    DEFAULT_PRICING = {"input": Decimal("0"), "output": Decimal("0")}


# ================================================================
# COST CALCULATION
# ================================================================


def calculate_cost(
    model: str, input_tokens: int, output_tokens: int
) -> Dict[str, Decimal]:
    pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)

    input_cost = Decimal(str(input_tokens)) * pricing["input"]
    output_cost = Decimal(str(output_tokens)) * pricing["output"]
    total_cost = input_cost + output_cost

    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": total_cost,
    }


def calculate_embedding_cost(
    model: str, input_tokens: int
) -> Dict[str, Decimal]:
    """Calculate cost for embedding API calls (input tokens only, no output)."""
    return calculate_cost(model, input_tokens, 0)
