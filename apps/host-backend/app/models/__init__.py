"""SQLAlchemy models. Import every model here so Alembic autogenerate sees them."""

from app.db.base import Base
from app.models.chat import (
    Conversation,
    Message,
    MessageRole,
    ToolCall,
    ToolCallStatus,
)
from app.models.connector import (
    AuthKind,
    ConnectionStatus,
    MCPConnection,
    MCPConnector,
    MCPCredential,
    OAuthTransaction,
)
from app.models.user import (
    AuthorizationCode,
    OAuthClient,
    RefreshToken,
    User,
)

__all__ = [
    "AuthKind",
    "AuthorizationCode",
    "Base",
    "ConnectionStatus",
    "Conversation",
    "MCPConnection",
    "MCPConnector",
    "MCPCredential",
    "Message",
    "MessageRole",
    "OAuthClient",
    "OAuthTransaction",
    "RefreshToken",
    "ToolCall",
    "ToolCallStatus",
    "User",
]
