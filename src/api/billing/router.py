"""
Billing API Router.
"""

from fastapi import APIRouter, Request, Depends, HTTPException, status
from typing import Dict, Any

from src.api.billing.handler import BillingHandler
from src.api.billing.schemas import (
    CreditBalanceResponse,
    CreditTransactionsResponse,
    TopupCheckoutRequest,
    PolarCheckoutRequest,
    CheckoutResponse,
    ModelListResponse,
    SePayWebhookRequest,
    SePayTopupInfoResponse,
)
from src.middleware.auth_middleware import get_current_user_id
from src.settings import settings
from src.utils.logger import get_logger
import stripe
import os

logger = get_logger()

router = APIRouter(prefix="/billing", tags=["Billing"])


def get_billing_handler(request: Request) -> BillingHandler:
    """Get billing handler from app state."""
    if not hasattr(request.app.state, "billing_handler"):
        request.app.state.billing_handler = BillingHandler()
    return request.app.state.billing_handler


@router.get("/balance", response_model=CreditBalanceResponse)
async def get_balance(
    current_user_id: str = Depends(get_current_user_id),
    handler: BillingHandler = Depends(get_billing_handler),
) -> Dict[str, Any]:
    """Get current credit balance for authenticated user."""
    return await handler.get_balance(current_user_id)


@router.get("/transactions", response_model=CreditTransactionsResponse)
async def get_transactions(
    current_user_id: str = Depends(get_current_user_id),
    handler: BillingHandler = Depends(get_billing_handler),
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """Get credit transaction history for authenticated user."""
    return await handler.get_transactions(current_user_id, limit, offset)


@router.post("/checkout/topup", response_model=CheckoutResponse, status_code=201)
async def create_topup_checkout(
    request_data: TopupCheckoutRequest,
    current_user_id: str = Depends(get_current_user_id),
    handler: BillingHandler = Depends(get_billing_handler),
) -> Dict[str, Any]:
    """Create Stripe Checkout session for top-up."""
    return await handler.create_topup_checkout(
        current_user_id,
        request_data.amount_usd,
        request_data.success_url,
        request_data.cancel_url,
    )


@router.post("/polar/checkout", response_model=CheckoutResponse, status_code=201)
async def create_polar_checkout(
    request_data: PolarCheckoutRequest,
    current_user_id: str = Depends(get_current_user_id),
    handler: BillingHandler = Depends(get_billing_handler),
) -> Dict[str, Any]:
    """Create Polar Checkout session for top-up."""
    return await handler.create_polar_checkout(
        current_user_id,
        request_data.amount_usd,
        request_data.success_url,
        request_data.cancel_url,
    )


@router.post("/polar/webhook")
async def polar_webhook(
    request: Request,
    handler: BillingHandler = Depends(get_billing_handler),
) -> Dict[str, str]:
    """
    Handle Polar webhook events (order.created, order.paid).

    Verifies webhook signature using POLAR_WEBHOOK_SECRET.
    """
    webhook_secret = settings.polar_webhook_secret
    if not webhook_secret:
        logger.error("POLAR_WEBHOOK_SECRET not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Polar webhook secret not configured",
        )

    body = await request.body()
    headers = {k: v for k, v in request.headers.items()}

    try:
        from polar_sdk.webhooks import validate_event, WebhookVerificationError
    except ImportError:
        logger.error("polar_sdk not installed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook verification not available",
        )

    try:
        event = validate_event(
            body=body,
            headers=headers,
            secret=webhook_secret,
        )
        if isinstance(event, dict):
            event_dict = event
        elif hasattr(event, "model_dump"):
            event_dict = event.model_dump()
        else:
            event_dict = {
                "id": getattr(event, "id", None),
                "type": getattr(event, "type", None),
                "data": getattr(event, "data", event),
            }
        # Normalize: Polar SDK may return event_type/event_id instead of type/id
        if event_dict.get("type") is None:
            event_dict["type"] = event_dict.get("event_type") or getattr(
                event, "event_type", None
            )
        if event_dict.get("id") is None:
            event_dict["id"] = event_dict.get("event_id") or getattr(
                event, "event_id", None
            )
        # Fallback: if data is the order object (has id, status, paid), infer type/id from it
        data = event_dict.get("data")
        if isinstance(data, dict) and event_dict.get("type") is None:
            if data.get("paid") is True or data.get("status") == "paid":
                event_dict["type"] = "order.paid"
            elif data.get("id") is not None:
                event_dict["type"] = "order.created"
            if event_dict.get("id") is None and data.get("id") is not None:
                event_dict["id"] = str(data["id"])
        return await handler.handle_polar_webhook(event_dict)
    except WebhookVerificationError as e:
        logger.warning(f"Invalid Polar webhook signature: {e}")
        raise HTTPException(status_code=401, detail="Invalid signature")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Polar webhook error: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to process webhook"
        )


@router.get("/models", response_model=ModelListResponse)
async def get_models(
    handler: BillingHandler = Depends(get_billing_handler),
) -> Dict[str, Any]:
    """
    Get list of available OpenAI models with pricing.

    This endpoint returns all supported models and their pricing information.
    Useful for displaying model options and costs to users.
    """
    return await handler.get_models()


@router.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    handler: BillingHandler = Depends(get_billing_handler),
) -> Dict[str, str]:
    """
    Handle Stripe webhook events.

    This endpoint verifies the webhook signature and processes the event.
    """
    # Get webhook secret from environment
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        raise ValueError("Webhook secret not configured")

    # Get request body and signature
    body = await request.body()
    signature = request.headers.get("stripe-signature")

    if not signature:
        logger.error("Missing Stripe signature header")
        raise ValueError("Missing signature")

    try:
        # Verify webhook signature
        event = stripe.Webhook.construct_event(body, signature, webhook_secret)

        # Process event
        return await handler.handle_stripe_webhook(event)
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {e}")
        raise ValueError("Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid webhook signature: {e}")
        raise ValueError("Invalid signature")


@router.get("/sepay/topup-info", response_model=SePayTopupInfoResponse)
async def get_sepay_topup_info(
    current_user_id: str = Depends(get_current_user_id),
    handler: BillingHandler = Depends(get_billing_handler),
) -> Dict[str, Any]:
    """
    Get SePay top-up info for authenticated user.

    Returns topup_code, bank info, exchange rate, and limits.
    Frontend uses this info to generate QR code via qr.sepay.vn API.
    """
    return await handler.get_sepay_topup_info(current_user_id)


@router.post("/sepay/webhook", status_code=201)
async def sepay_webhook(
    request: Request,
    webhook_data: SePayWebhookRequest,
    handler: BillingHandler = Depends(get_billing_handler),
) -> Dict[str, Any]:
    """
    Handle SePay webhook events.

    This endpoint verifies the API Key authentication and processes the webhook.
    SePay sends webhook with header: "Authorization": "Apikey API_KEY_CUA_BAN"
    """
    # Get API key from environment
    sepay_api_key = os.getenv("SEPAY_WEBHOOK_API_KEY")
    if not sepay_api_key:
        logger.error("SEPAY_WEBHOOK_API_KEY not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SePay webhook API key not configured",
        )

    # Verify API Key from Authorization header
    auth_header = request.headers.get("Authorization", "")
    expected_auth = f"Apikey {sepay_api_key}"

    if auth_header != expected_auth:
        logger.warning(
            f"🔐 SePay webhook authentication failed - Path: {request.url.path}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )

    try:
        # Convert Pydantic model to dict for processing
        webhook_dict = webhook_data.model_dump()

        # Process webhook
        result = await handler.handle_sepay_webhook(webhook_dict)

        # SePay expects response with success: true and status code 200 or 201
        return {"success": True, **result}
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(
            f"💥 Error processing SePay webhook: {str(e)} - Path: {request.url.path}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process webhook",
        )
