import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from src.settings import settings
from src.utils.logger import get_logger

from ..auth.utils import verify_fb_signature
from .handler import FbWebhookHandler

logger = get_logger()

webhook_router = APIRouter()


def get_fb_webhook_handler(request: Request) -> FbWebhookHandler:
    """Get Facebook webhook handler singleton from app state"""
    return request.app.state.fb_webhook_handler


@webhook_router.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == settings.fb_verify_token:
        return PlainTextResponse(content=params.get("hub.challenge"))
    return {"error": "Invalid verify token"}


@webhook_router.post("/webhook")
async def receive_webhook(
    request: Request,
    fb_webhook_handler: FbWebhookHandler = Depends(get_fb_webhook_handler),
):
    """Receive Facebook webhook events and store them to MongoDB for analysis"""
    try:
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature")

        if not verify_fb_signature(body, signature, settings.fb_app_secret):
            logger.error("❌ Invalid webhook signature")
            return JSONResponse(status_code=403, content={"error": "Invalid signature"})

        data = json.loads(body)

        headers = dict(request.headers)

        await fb_webhook_handler.handle_fb_webhook_event(
            raw_data=data, signature=signature, headers=headers
        )

        return {"message": "Webhook processed successfully"}

    except Exception as e:
        logger.error(f"❌ WEBHOOK ERROR: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
