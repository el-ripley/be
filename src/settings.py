"""Application settings configuration module."""

import os
from enum import Enum
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


class AppEnvironment(str, Enum):
    """Application environment enumeration."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class Settings:
    """Application settings with reliable environment variable loading."""

    def __init__(self):
        """Initialize settings by loading from .env file and environment variables."""
        self._load_env_file()
        self._load_settings()

    def _load_env_file(self):
        """Load environment variables from .env file."""
        if load_dotenv is None:
            print(
                "Warning: python-dotenv not installed. Install with: pip install python-dotenv"
            )
            return

        # Try to find .env file in current directory or project root
        env_paths = [
            Path(".env"),
            Path(__file__).parent.parent.parent / ".env",  # Project root
            Path(os.getcwd()) / ".env",
        ]

        for env_path in env_paths:
            if env_path.exists():
                print(f"Loading .env file from: {env_path}")
                load_dotenv(env_path)
                break
        else:
            print("No .env file found. Using environment variables only.")

    def _get_env(self, key: str, default: str = "", required: bool = False) -> str:
        """Get environment variable with fallback to default."""
        value = os.getenv(key, default)
        if required and not value:
            raise ValueError(f"Required environment variable {key} is not set")
        return value

    def _get_env_int(self, key: str, default: int = 0) -> int:
        """Get integer environment variable."""
        try:
            return int(self._get_env(key, str(default)))
        except ValueError:
            return default

    def _get_env_bool(self, key: str, default: bool = False) -> bool:
        """Get boolean environment variable."""
        value = self._get_env(key, str(default)).lower()
        return value in ("true", "1", "yes", "on")

    def _load_settings(self):
        """Load all settings from environment variables."""
        # App settings
        app_env_str = self._get_env("APP_ENV", AppEnvironment.DEVELOPMENT.value)
        try:
            self.app_env = AppEnvironment(app_env_str)
        except ValueError:
            print(f"Warning: Invalid APP_ENV '{app_env_str}', using DEVELOPMENT")
            self.app_env = AppEnvironment.DEVELOPMENT

        self.debug = self._get_env_bool("DEBUG", True)
        self.log_level = self._get_env("LOG_LEVEL", "INFO")
        self.backend_url = self._get_env("BACKEND_URL", "")

        # Allowed frontend URLs (comma-separated list)
        allowed_urls_str = self._get_env("ALLOWED_FRONTEND_URLS", "")
        if allowed_urls_str:
            self.allowed_frontend_urls = [
                url.strip() for url in allowed_urls_str.split(",") if url.strip()
            ]
        else:
            self.allowed_frontend_urls = []

        # Database settings
        self.postgres_host = self._get_env("POSTGRES_HOST", "localhost")
        self.postgres_port = self._get_env("POSTGRES_PORT", "5434")
        self.postgres_user = self._get_env("POSTGRES_USER", "el-ripley-user")
        self.postgres_password = self._get_env(
            "POSTGRES_PASSWORD", "crypto-gambling-pass"
        )
        self.postgres_db_name = self._get_env("POSTGRES_DB_NAME", "el_ripley")

        # Agent Reader (RLS-restricted role for AI agent SELECT queries)
        self.postgres_agent_reader_user = self._get_env(
            "POSTGRES_AGENT_READER_USER", "agent_reader"
        )
        self.postgres_agent_reader_password = self._get_env(
            "POSTGRES_AGENT_READER_PASSWORD", "agent-reader-dev-password"
        )

        # Agent Writer (RLS-restricted role for AI agent INSERT/UPDATE/DELETE queries)
        self.postgres_agent_writer_user = self._get_env(
            "POSTGRES_AGENT_WRITER_USER", "agent_writer"
        )
        self.postgres_agent_writer_password = self._get_env(
            "POSTGRES_AGENT_WRITER_PASSWORD", "agent-writer-dev-password"
        )

        # Suggest Response Reader (conversation-scoped, minimal SELECT access)
        self.postgres_suggest_response_reader_user = self._get_env(
            "POSTGRES_SUGGEST_RESPONSE_READER_USER", "suggest_response_reader"
        )
        self.postgres_suggest_response_reader_password = self._get_env(
            "POSTGRES_SUGGEST_RESPONSE_READER_PASSWORD",
            "suggest-response-reader-dev-password",
        )

        # Suggest Response Writer (conversation-scoped, minimal INSERT/UPDATE access)
        self.postgres_suggest_response_writer_user = self._get_env(
            "POSTGRES_SUGGEST_RESPONSE_WRITER_USER", "suggest_response_writer"
        )
        self.postgres_suggest_response_writer_password = self._get_env(
            "POSTGRES_SUGGEST_RESPONSE_WRITER_PASSWORD",
            "suggest-response-writer-dev-password",
        )

        # Qdrant (vector DB for playbooks semantic search)
        self.qdrant_host = self._get_env("QDRANT_HOST", "localhost")
        self.qdrant_port_rest = self._get_env_int("QDRANT_PORT_REST", 6333)
        self.qdrant_port_grpc = self._get_env_int("QDRANT_PORT_GRPC", 6334)

        # MongoDB Configuration
        self.mongodb_host = self._get_env("MONGODB_HOST", "localhost")
        self.mongodb_port = self._get_env_int("MONGODB_PORT", 27017)
        self.mongodb_username = self._get_env("MONGODB_USERNAME", "admin")
        self.mongodb_password = self._get_env("MONGODB_PASSWORD", "password")
        self.mongodb_db_name = self._get_env("MONGODB_DB_NAME", "ai_agent_db")

        # Redis Configuration
        self.redis_host = self._get_env("REDIS_HOST", "localhost")
        self.redis_port = self._get_env_int("REDIS_PORT", 6379)
        self.redis_password = self._get_env("REDIS_PASSWORD", "redis-password")
        self.redis_db = self._get_env_int("REDIS_DB", 0)

        # Facebook settings
        self.fb_graph_version = self._get_env("FB_GRAPH_VERSION", "v23.0")
        self.fb_verify_token = self._get_env("FB_WEBHOOK_VERIFY_TOKEN", "")
        self.fb_app_id = self._get_env("FB_APP_ID", "")
        self.fb_app_secret = self._get_env("FB_APP_SECRET", "")

        # AWS S3 settings
        self.aws_access_key_id = self._get_env("AWS_ACCESS_KEY_ID", "")
        self.aws_secret_access_key = self._get_env("AWS_SECRET_ACCESS_KEY", "")
        self.aws_region = self._get_env("AWS_REGION", "ap-southeast-1")
        self.aws_s3_bucket_name = self._get_env("AWS_BUCKET_NAME", "")

        # JWT Authentication settings
        self.jwt_secret_key = self._get_env("JWT_SECRET_KEY", "", required=True)
        self.jwt_algorithm = self._get_env("JWT_ALGORITHM", "HS256")

        # Encryption settings (for BYOK API keys)
        self.encryption_key = self._get_env("ENCRYPTION_KEY", "", required=True)

        # OpenAI API Key (system-wide)
        self.openai_api_key = self._get_env("OPENAI_API_KEY", "", required=True)

        # Anthropic via proxy
        self.anthropic_api_key_proxy = self._get_env("ANTHROPIC_API_KEY_PROXY", "")
        self.supper_api_base_url = self._get_env("SUPPER_API_BASE_URL", "http://supperapi.store")

        # Google (Vertex: path to .gcp/sa.json or AI Studio: GOOGLE_API_KEY)
        self.google_application_credentials = self._get_env("GOOGLE_APPLICATION_CREDENTIALS", "")
        self.google_service_account_json = self._get_env("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        self.google_cloud_project = self._get_env("GOOGLE_CLOUD_PROJECT", "")
        self.google_cloud_location = self._get_env("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.google_vertex_model = self._get_env("GOOGLE_VERTEX_MODEL", "gemini-2.5-pro")
        self.google_api_key = self._get_env("GOOGLE_API_KEY", "")

        # Polar (payment gateway)
        self.polar_access_token = self._get_env("POLAR_ACCESS_TOKEN", "")
        self.polar_webhook_secret = self._get_env("POLAR_WEBHOOK_SECRET", "")
        self.polar_product_id = self._get_env("POLAR_PRODUCT_ID", "")
        self.polar_server = self._get_env("POLAR_SERVER", "")  # "sandbox" to use Polar sandbox API

        # Access token settings (short-lived)
        self.access_token_expire_minutes = self._get_env_int(
            "ACCESS_TOKEN_EXPIRE_MINUTES", 15
        )

        # Refresh token settings (long-lived)
        self.refresh_token_expire_days = self._get_env_int(
            "REFRESH_TOKEN_EXPIRE_DAYS", 30
        )

    @property
    def database_auth_url(self) -> str:
        """Get the database auth URL."""
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db_name}"

    @property
    def database_langchain_short_memory_url(self) -> str:
        """Get the database langchain short memory URL."""
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_langchain_short_memory_db}"

    @property
    def mongodb_connection_string(self) -> str:
        """Get the MongoDB connection string with replica set for transaction support."""
        return f"mongodb://{self.mongodb_username}:{self.mongodb_password}@{self.mongodb_host}:{self.mongodb_port}/admin?replicaSet=rs0"

    @property
    def redis_connection_url(self) -> str:
        """Get the Redis connection URL."""
        return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def qdrant_rest_url(self) -> str:
        """Qdrant REST API base URL (for qdrant-client)."""
        return f"http://{self.qdrant_host}:{self.qdrant_port_rest}"

    @property
    def fb_graph_oauth_url(self) -> str:
        return f"https://graph.facebook.com/{self.fb_graph_version}/oauth/access_token"

    @property
    def fb_redirect_uri(self) -> str:
        return f"{self.backend_url}/facebook/auth/callback"

    @property
    def fb_graph_get_user_infor_url(self) -> str:
        return f"https://graph.facebook.com/{self.fb_graph_version}/me"

    @property
    def fb_graph_get_pages_url(self) -> str:
        return f"https://graph.facebook.com/{self.fb_graph_version}/me/accounts"

    @property
    def cors_allowed_origins(self) -> list[str]:
        """Get list of allowed origins for CORS middleware."""
        return self.allowed_frontend_urls


settings = Settings()
