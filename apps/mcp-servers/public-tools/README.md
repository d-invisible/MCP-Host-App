# Public Tools — MCP-3 (no authentication)

The open demo server. Anyone who can reach the URL may call its tools.

```bash
uv sync
uv run python src/server.py          # http://127.0.0.1:8103/mcp
uv run pytest -q                     # 13 tests
```

## Tools

| Tool | Purpose |
|---|---|
| `current_time` | current time in any IANA timezone |
| `convert_units` | length, mass, and temperature conversion |
| `hash_text` | sha256 / sha1 / sha512 / md5 |

## What makes it public

There is no "auth: off" switch. A server is public precisely because it does
**not** pass `token_verifier=` and `auth=AuthSettings(...)` to `MCPServer`:

```python
mcp = MCPServer("Public Tools", instructions=..., version="0.1.0")
```

The other demo servers add both, and the SDK then serves
`/.well-known/oauth-protected-resource/mcp` and rejects unauthenticated
requests with a 401.

This is the right server to build first: a failure here is always a transport
or protocol problem, never an auth problem.

## Two things worth copying

**Return errors as content, not exceptions.** An unknown timezone comes back as
readable text, so the model can correct itself. A raised exception just fails
the call and the model learns nothing:

```python
except ZoneInfoNotFoundError:
    return f"Unknown timezone {timezone!r}. Use an IANA name like \"UTC\"."
```

**`tzdata` is a hard dependency.** Windows and slim containers ship no system
timezone database, so `ZoneInfo` fails for *every* zone — including `"UTC"` —
without it. This was caught by a test that asserted a real offset rather than
just "no exception".

## Docstrings become the tool schema

The MCP SDK derives each tool's description and JSON Schema from the function
signature and docstring, and the host passes those straight to the LLM. A vague
docstring is a vague tool definition, so write them for the model.
