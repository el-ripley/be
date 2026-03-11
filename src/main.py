from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
import uvicorn
import socketio
import jwt
import asyncpg
import warnings

from src.agent.general_agent import AgentRunner
from src.agent.general_agent.context.manager import AgentContextManager
from src.settings import settings, AppEnvironment
from src.utils.logger import get_logger
from src.api.facebook import FbHandler, FbWebhookHandler, comments_router
from src.api.facebook.router import router as fb_router
from src.api.users.handler import UserHandler, UserFilesHandler
from src.api.auth.router import router as auth_router
from src.api.users.router import router as users_router
from src.api.openai_conversations.router import router as openai_conversations_router
from src.api.suggest_response.router import router as suggest_response_router
from src.api.billing.router import router as billing_router
from src.api.escalations.router import router as escalations_router
from src.api.notifications.router import router as notifications_router
from src.api.suggest_response.handler import SuggestResponseHandler
from src.services.suggest_response.suggest_response_agent_service import (
    SuggestResponseAgentService,
)
from src.services.suggest_response.suggest_response_prompts_service import (
    SuggestResponsePromptsService,
)
from src.services.auth_service import AuthService
from src.services.users.user_service import UserService
from src.services.facebook.auth import FacebookAuthService, FacebookPageService
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.services.facebook.auth import FacebookPermissionService
from src.services.facebook.comments._internal.comment_service import CommentService
from src.services.facebook.comments.sync.comment_write_service import (
    CommentWriteService,
)
from src.services.facebook.comments.comment_conversation_service import (
    CommentConversationService,
)
from src.services.facebook.comments.webhook_handler import CommentWebhookHandler
from src.services.facebook.comments.api_handler import CommentAPIHandler
from src.services.facebook.messages.webhook_handler import MessageWebhookHandler
from src.services.facebook.messages.api_handler import MessageAPIHandler
from src.services.facebook.messages.sync.inbox_sync_service import InboxSyncService
from src.services.facebook.posts.post_sync_service import PostSyncService
from src.services.facebook.comments.sync.comment_sync_service import CommentSyncService
from src.services.facebook.full_sync_service import FullSyncService
from src.socket_service import SocketService
from src.redis_client.redis_client import RedisClient
from src.redis_client.redis_agent_manager import RedisAgentManager
from src.redis_client.redis_user_sessions import RedisUserSessions
from src.redis_client.redis_suggest_response_cache import RedisSuggestResponseCache
from src.middleware.exception_handler import (
    http_exception_handler,
    validation_exception_handler,
    jwt_exception_handler,
    database_exception_handler,
    general_exception_handler,
    business_logic_exception_handler,
    external_service_exception_handler,
    BusinessLogicError,
    ExternalServiceError,
)

logger = get_logger()

# Suppress Pydantic serialization warnings for OpenAI SDK union types
# These warnings occur when serializing ParsedResponse objects with complex union types
# Filter by both module and message pattern to catch all variations
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    message=".*PydanticSerializationUnexpectedValue.*",
)
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module="pydantic.main",
)

# Initialize Socket.IO server for real-time communication
# Use Redis client_manager for cross-worker event broadcasting (required for multi-worker production)
_sio_client_manager = socketio.AsyncRedisManager(settings.redis_connection_url)

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    client_manager=_sio_client_manager,
    logger=False,
    engineio_logger=False,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting server (env={settings.app_env})")

    # Create singleton service instances in dependency order

    redis_client = RedisClient()
    await redis_client.connect()
    app.state.redis_client = redis_client

    redis_user_sessions = RedisUserSessions(redis_client)
    redis_agent_manager = RedisAgentManager(redis_client)
    app.state.redis_agent_manager = redis_agent_manager
    app.state.redis_user_sessions = redis_user_sessions

    # Initialize job queue for async operations
    from src.redis_client.redis_job_queue import RedisJobQueue

    job_queue = RedisJobQueue(redis_client)
    app.state.job_queue = job_queue

    suggest_response_cache = RedisSuggestResponseCache(redis_client)
    app.state.suggest_response_cache = suggest_response_cache

    auth_service = AuthService()
    app.state.auth_service = auth_service

    context_manager = AgentContextManager(redis_agent_manager)

    user_service = UserService(auth_service)
    app.state.user_service = user_service

    # Initialize Facebook services
    facebook_auth_service = FacebookAuthService()
    facebook_page_service = FacebookPageService()
    page_scope_user_service = PageScopeUserService()
    facebook_permission_service = FacebookPermissionService(facebook_page_service)
    app.state.facebook_page_service = facebook_page_service
    app.state.page_scope_user_service = page_scope_user_service
    app.state.facebook_permission_service = facebook_permission_service
    inbox_sync_service = InboxSyncService(
        page_service=facebook_page_service,
        page_scope_user_service=page_scope_user_service,
    )
    app.state.inbox_sync_service = inbox_sync_service

    # Initialize comment services and handlers
    comment_service = CommentService()
    comment_write_service = CommentWriteService(
        page_scope_user_service=page_scope_user_service,
    )
    comment_conversation_service = CommentConversationService()

    # Initialize post sync service
    post_sync_service = PostSyncService(
        page_service=facebook_page_service,
        page_scope_user_service=page_scope_user_service,
    )
    app.state.post_sync_service = post_sync_service

    # Initialize comment sync service
    comment_sync_service = CommentSyncService(
        page_service=facebook_page_service,
        page_scope_user_service=page_scope_user_service,
        comment_conversation_service=comment_conversation_service,
        comment_write_service=comment_write_service,
    )
    app.state.comment_sync_service = comment_sync_service

    # Initialize sync locks for preventing concurrent syncs
    from src.redis_client.redis_facebook_sync_locks import RedisFacebookSyncLocks

    sync_locks = RedisFacebookSyncLocks(redis_client=redis_client)

    # Initialize full sync service
    full_sync_service = FullSyncService(
        post_sync_service=post_sync_service,
        comment_sync_service=comment_sync_service,
    )
    app.state.full_sync_service = full_sync_service

    # Initialize unified sync job manager for API and Agent tools
    from src.services.facebook.facebook_sync_job_manager import FacebookSyncJobManager

    sync_job_manager = FacebookSyncJobManager(
        job_queue=job_queue,
        sync_locks=sync_locks,
        default_lock_ttl=3600,  # 1 hour
    )
    app.state.sync_job_manager = sync_job_manager

    # Initialize socket service and agent runner (after sync_job_manager)
    socket_service = SocketService(
        sio, auth_service, redis_agent_manager, redis_user_sessions
    )
    app.state.socket_service = socket_service

    # Notification service (generic in-app notifications; used by escalation trigger and connect)
    from src.services.notifications import NotificationService
    from src.services.notifications.escalation_trigger import (
        EscalationNotificationTrigger,
    )

    notification_service = NotificationService(socket_service)
    app.state.notification_service = notification_service
    escalation_trigger = EscalationNotificationTrigger(notification_service)

    # Billing handler (uses notification_service for payment.credits_added notifications)
    from src.api.billing.handler import BillingHandler
    app.state.billing_handler = BillingHandler(notification_service=notification_service)

    # Initialize Suggest Response services (before agent_runner - needed for trigger_suggest_response tool)
    from src.agent.suggest_response import SuggestResponseRunner
    from src.agent.suggest_response import SuggestResponseOrchestrator

    suggest_response_runner = SuggestResponseRunner(
        socket_service,
        redis_agent_manager=redis_agent_manager,
        suggest_response_cache=suggest_response_cache,
        escalation_trigger=escalation_trigger,
    )
    suggest_response_orchestrator = SuggestResponseOrchestrator(
        runner=suggest_response_runner,
        suggest_response_cache=suggest_response_cache,
        session_manager=redis_user_sessions,
        comment_conversation_service=comment_conversation_service,
        socket_service=socket_service,
        page_service=facebook_page_service,
    )
    app.state.suggest_response_orchestrator = suggest_response_orchestrator

    agent_runner = AgentRunner(
        socket_service,
        context_manager,
        sync_job_manager,
        suggest_response_orchestrator=suggest_response_orchestrator,
    )
    app.state.agent_runner = agent_runner
    socket_service.set_dependencies(agent_runner)

    comment_webhook_handler = CommentWebhookHandler(
        comment_service=comment_service,
        comment_write_service=comment_write_service,
        comment_conversation_service=comment_conversation_service,
        page_service=facebook_page_service,
        page_scope_user_service=page_scope_user_service,
        socket_service=socket_service,
        suggest_response_orchestrator=suggest_response_orchestrator,
    )
    app.state.comment_webhook_handler = comment_webhook_handler

    comment_api_handler = CommentAPIHandler(
        page_service=facebook_page_service,
        permission_service=facebook_permission_service,
        comment_conversation_service=comment_conversation_service,
        socket_service=socket_service,
    )
    app.state.comment_api_handler = comment_api_handler

    # Initialize message handlers
    message_webhook_handler = MessageWebhookHandler(
        page_service=facebook_page_service,
        page_scope_user_service=page_scope_user_service,
        socket_service=socket_service,
        suggest_response_orchestrator=suggest_response_orchestrator,
    )
    app.state.message_webhook_handler = message_webhook_handler

    message_api_handler = MessageAPIHandler(
        page_service=facebook_page_service,
        permission_service=facebook_permission_service,
    )
    app.state.message_api_handler = message_api_handler

    app.state.user_handler = UserHandler(user_service, auth_service)

    app.state.user_files_handler = UserFilesHandler()

    app.state.fb_handler = FbHandler(user_service, facebook_auth_service, auth_service)

    app.state.fb_webhook_handler = FbWebhookHandler(
        comment_webhook_handler, message_webhook_handler
    )

    # Initialize Suggest Response services and handler
    suggest_response_agent_service = SuggestResponseAgentService()
    suggest_response_prompts_service = SuggestResponsePromptsService()
    from src.services.suggest_response.suggest_response_history_service import (
        SuggestResponseHistoryService,
    )

    suggest_response_history_service = SuggestResponseHistoryService()
    suggest_response_handler = SuggestResponseHandler(
        agent_service=suggest_response_agent_service,
        prompts_service=suggest_response_prompts_service,
        history_service=suggest_response_history_service,
        orchestrator=suggest_response_orchestrator,
    )
    app.state.suggest_response_handler = suggest_response_handler

    # Agent comm: blocks and escalations
    from src.services.agent_comm import AgentBlockService, EscalationService
    from src.services.users.user_memory_service import UserMemoryService

    agent_block_service = AgentBlockService(
        permission_service=facebook_permission_service
    )
    app.state.agent_block_service = agent_block_service
    app.state.escalation_service = EscalationService()
    app.state.user_memory_service = UserMemoryService()

    # Ensure Qdrant playbooks collection exists (idempotent)
    try:
        from src.database.qdrant import ensure_playbooks_collection

        await ensure_playbooks_collection()
    except Exception as e:
        logger.warning("Qdrant playbooks collection init skipped: %s", e)

    logger.info("Startup complete")

    yield  # App runs here

    logger.info("Shutting down")

    await redis_client.disconnect()
    logger.info("Redis connection closed")


app = FastAPI(
    title="El Ripley AI Agent",
    description="AI-powered Facebook Fanpage Management System — multi-LLM, RLS-secured, real-time via Socket.IO",
    version="2.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,  # Required for cookies
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Add comprehensive exception handlers (order matters - most specific first)

# Custom application exceptions
app.add_exception_handler(BusinessLogicError, business_logic_exception_handler)
app.add_exception_handler(ExternalServiceError, external_service_exception_handler)

# Authentication/JWT exceptions
app.add_exception_handler(jwt.PyJWTError, jwt_exception_handler)
app.add_exception_handler(jwt.ExpiredSignatureError, jwt_exception_handler)
app.add_exception_handler(jwt.InvalidTokenError, jwt_exception_handler)
app.add_exception_handler(jwt.DecodeError, jwt_exception_handler)

# Validation exceptions
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# HTTP exceptions
app.add_exception_handler(HTTPException, http_exception_handler)

# Database exceptions
app.add_exception_handler(asyncpg.PostgresError, database_exception_handler)
app.add_exception_handler(
    asyncpg.ConnectionDoesNotExistError, database_exception_handler
)

# Catch-all for any other exceptions
app.add_exception_handler(Exception, general_exception_handler)

# Include routers
app.include_router(auth_router)
app.include_router(fb_router)
app.include_router(users_router)
app.include_router(openai_conversations_router)
app.include_router(comments_router)
app.include_router(suggest_response_router)
app.include_router(billing_router)
app.include_router(escalations_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")


@app.get("/")
async def root():
    return {
        "message": "El Ripley AI Agent",
        "status": "ready",
        "endpoints": {
            "auth": "/facebook/auth/callback",
            "pages": "/facebook/page-admins",
            "send_message": "/facebook/messages/send",
            "conversations": "/facebook/messages/conversations",
            "mark_read_status": "/facebook/messages/conversations/{conversation_id}/mark-as-read",
            "comments": "/facebook/pages/{page_id}/posts/{post_id}/comments",
            "webhook": "/facebook/webhook",
            "user_info": "/users/me",
            "openai": "/openai",
        },
        "websocket": {
            "endpoint": "/socket.io/",
            "authentication": "JWT token required in auth object",
            "events": {
                "incoming": ["ai_message", "update_active_tab"],
                "outgoing": [
                    "connected",
                    "webhook_event",
                    "ai_response",
                    "ai_message_received",
                    "system_message",
                    "active_tab_updated",
                    "error",
                ],
            },
        },
    }


@app.get("/health")
async def health_check(request: Request):
    checks: dict[str, str] = {"api": "healthy"}
    try:
        redis_client = getattr(request.app.state, "redis_client", None)
        if redis_client is not None and await redis_client.is_connected():
            checks["redis"] = "healthy"
        else:
            checks["redis"] = "degraded"
    except Exception:
        checks["redis"] = "degraded"
    overall = "healthy" if all(v == "healthy" for v in checks.values()) else "degraded"
    return {"status": overall, "checks": checks, "message": "El Ripley AI Agent"}


# Create ASGI app with Socket.IO support
main_app = socketio.ASGIApp(socketio_server=sio, other_asgi_app=app)

if __name__ == "__main__":
    is_production = settings.app_env == AppEnvironment.PRODUCTION

    if is_production:
        # Production: multi-worker, no reload, optimized settings
        # workers = (2 * CPU_cores) + 1 is a common guideline for I/O-bound apps
        import multiprocessing

        cpu_count = multiprocessing.cpu_count()
        workers = (2 * cpu_count) + 1

        uvicorn.run(
            "src.main:main_app",
            host="0.0.0.0",
            port=8000,
            workers=workers,
            log_level=settings.log_level.lower(),
            access_log=False,  # Nginx handles access logs
            timeout_keep_alive=65,  # Slightly above nginx keepalive_timeout (65s)
        )
    else:
        # Development: single worker with hot-reload
        uvicorn.run(
            "src.main:main_app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            log_level="info",
        )
