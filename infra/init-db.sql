-- Extensions required by the host application.
-- pgcrypto: symmetric encryption of third-party OAuth tokens at rest.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- Server-side UUID generation for defaults.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
