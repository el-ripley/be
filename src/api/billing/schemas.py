"""
Billing API schemas.
"""

from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field


class CreditBalanceResponse(BaseModel):
    """Credit balance response."""

    balance_usd: Decimal = Field(..., description="Current credit balance in USD")
    lifetime_earned_usd: Optional[Decimal] = Field(
        None, description="Lifetime earned credits"
    )
    lifetime_spent_usd: Optional[Decimal] = Field(
        None, description="Lifetime spent credits"
    )


class CreditTransactionResponse(BaseModel):
    """Credit transaction response."""

    id: str
    transaction_type: str
    amount_usd: Decimal
    balance_before_usd: Decimal
    balance_after_usd: Decimal
    source_type: Optional[str]
    source_id: Optional[str]
    description: Optional[str]
    created_at: int


class CreditTransactionsResponse(BaseModel):
    """Credit transactions list response."""

    transactions: List[CreditTransactionResponse]
    total: int


class CheckoutRequest(BaseModel):
    """Checkout session request."""

    success_url: str = Field(
        ..., description="URL to redirect after successful payment"
    )
    cancel_url: str = Field(..., description="URL to redirect after canceled payment")


class CheckoutResponse(BaseModel):
    """Checkout session response."""

    checkout_url: str = Field(..., description="Stripe Checkout URL")


class TopupCheckoutRequest(CheckoutRequest):
    """Top-up checkout request."""

    amount_usd: Decimal = Field(
        ..., description="Top-up amount in USD (e.g., 5.00, 10.00, 20.00)", gt=0
    )


class PolarCheckoutRequest(CheckoutRequest):
    """Polar top-up checkout request."""

    amount_usd: Decimal = Field(
        ..., description="Top-up amount in USD (min $10, max $10000)", gt=0
    )


class ModelPricingInfo(BaseModel):
    """Model pricing information."""

    name: str = Field(..., description="Model identifier (e.g., gpt-5-mini)")
    input_cost: float = Field(..., description="Input cost per 1M tokens (USD)")
    output_cost: float = Field(..., description="Output cost per 1M tokens (USD)")
    context_window: int = Field(..., description="Maximum context window (tokens)")


class ModelListResponse(BaseModel):
    """List of available models with pricing."""

    models: List[ModelPricingInfo] = Field(
        ..., description="Available models with pricing"
    )


class SePayWebhookRequest(BaseModel):
    """SePay webhook request payload."""

    id: int = Field(..., description="Transaction ID on SePay")
    gateway: str = Field(..., description="Bank brand name")
    transactionDate: str = Field(..., description="Transaction date from bank")
    accountNumber: str = Field(..., description="Bank account number")
    code: Optional[str] = Field(
        None, description="Payment code (auto-detected by SePay)"
    )
    content: str = Field(..., description="Transfer content")
    transferType: str = Field(
        ..., description="Transaction type: 'in' for incoming, 'out' for outgoing"
    )
    transferAmount: int = Field(..., description="Transaction amount")
    accumulated: int = Field(..., description="Account balance (accumulated)")
    subAccount: Optional[str] = Field(None, description="Sub account (virtual account)")
    referenceCode: Optional[str] = Field(None, description="SMS reference code")
    description: str = Field(..., description="Full SMS content")


class SePayTopupInfoResponse(BaseModel):
    """SePay top-up info response."""

    topup_code: str = Field(..., description="Unique topup code for user")
    bank_code: str = Field(..., description="Bank code for QR URL")
    account_number: str = Field(..., description="Bank account number")
    account_name: str = Field(..., description="Account holder name")
    transfer_content: str = Field(
        ..., description="Transfer content to include in bank transfer"
    )
    exchange_rate_vnd_per_usd: int = Field(
        ..., description="Exchange rate: VND per 1 USD"
    )
    min_amount_vnd: int = Field(..., description="Minimum top-up amount in VND")
    max_amount_vnd: int = Field(..., description="Maximum top-up amount in VND")
