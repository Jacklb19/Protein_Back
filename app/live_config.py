"""Protocol names, close codes and bounds of the live channel, independent of the route."""

from __future__ import annotations

from dataclasses import dataclass

LIVE_PATH = "/live"
# Negotiated in the handshake; a client that does not offer it is not speaking this protocol.
LIVE_SUBPROTOCOL = "proteinforge.live.v1"
LIVE_PROJECTS_PATH = "projects"

# Messages the client may send, and the ones the server sends.
CLIENT_AUTH = "auth"
CLIENT_PING = "ping"
SERVER_READY = "ready"
SERVER_PONG = "pong"
SERVER_PROJECTS_CHANGED = "projects.changed"

# RFC 6455 codes where one fits; 4000-4999 is the range reserved for applications.
CLOSE_UNSUPPORTED_DATA = 1003
CLOSE_POLICY_VIOLATION = 1008
CLOSE_MESSAGE_TOO_BIG = 1009
CLOSE_TRY_AGAIN_LATER = 1013
CLOSE_ROTATE = 4000
CLOSE_MALFORMED = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_TIMEOUT = 4408
CLOSE_TOO_MANY = 4429

DEFAULT_LIVE_POLL_SECONDS = 5.0
# Below the 300 s a hosted function may run, so the server ends the connection before the platform cuts it.
DEFAULT_LIVE_MAX_CONNECTION_SECONDS = 240.0


@dataclass(frozen=True)
class LiveLimits:
    """Bounds of one connection. Counters live in the process, like the request budgets."""

    # How long a new connection may stay silent before its first `auth` message.
    auth_timeout_seconds: float = 10.0
    # The client pings at this interval; a connection silent for `idle_factor` intervals is closed.
    heartbeat_seconds: float = 25.0
    idle_factor: float = 2.5
    # A message carries a token or a ping; nothing legitimate is larger.
    max_message_bytes: int = 8_192
    messages_per_minute: int = 30
    connections_per_caller: int = 4
    # Most recent projects compared between two looks; the total count covers the rest.
    watched_projects: int = 200
    data_timeout_seconds: float = 10.0
    # Consecutive looks that may fail before the connection is handed back to the client to retry.
    max_failed_looks: int = 3
