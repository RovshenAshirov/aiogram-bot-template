import os

import structlog

# data.config reads env at import time; set before any project import so the local .env doesn't leak into tests
os.environ.update(
    BOT_TOKEN="42:TEST", LOGGING_LEVEL="10", ADMINS="1,2",
    PG_HOST="localhost", PG_PORT="5432", PG_USER="u", PG_PASSWORD="", PG_DATABASE="d",
    FSM_HOST="localhost", FSM_PORT="6379", FSM_PASSWORD="",
    CACHE_HOST="localhost", CACHE_PORT="6379", CACHE_PASSWORD="",
    USE_WEBHOOK="false", USE_CUSTOM_API_SERVER="false",
)
structlog.configure(logger_factory=structlog.ReturnLoggerFactory())  # silence logs; capture_logs() still sees them
