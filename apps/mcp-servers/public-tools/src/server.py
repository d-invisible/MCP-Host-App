"""MCP-3 — Public Tools.

The no-authentication demo server: anyone who can reach the URL may call its
tools. That makes it the right first server to build, because a failure here is
always a transport or protocol problem, never an auth problem.

Contrast with the other five demo servers, which each construct `MCPServer`
with `token_verifier=` and `auth=AuthSettings(...)`. Omitting both is what
makes a server public — there is no "auth: off" switch.

Run:
    uv run python src/server.py
    uv run python src/server.py --port 9103    # override
"""

from __future__ import annotations

import argparse
import hashlib
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server import MCPServer

DEFAULT_PORT = 8103

mcp = MCPServer(
    "Public Tools",
    instructions=(
        "Open utility tools: current time in any timezone, unit conversion, "
        "and text hashing. No authentication required."
    ),
    version="0.1.0",
)


@mcp.tool()
def current_time(timezone: str = "UTC") -> str:
    """Return the current time in an IANA timezone.

    Args:
        timezone: IANA name such as "UTC", "Asia/Kolkata", "America/New_York".
    """
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        # Return the error as content rather than raising: the model can read
        # this and correct itself, whereas an exception just fails the call.
        return (
            f"Unknown timezone {timezone!r}. Use an IANA name "
            'like "UTC", "Asia/Kolkata", or "Europe/London".'
        )

    now = datetime.now(zone)
    return f"{now:%Y-%m-%d %H:%M:%S} {now:%Z} (UTC{now:%z})"


# Everything is defined relative to a base unit per family, so conversion is
# always "to base, then from base" rather than an N×N table.
_LENGTH = {
    "m": 1.0,
    "km": 1000.0,
    "cm": 0.01,
    "mm": 0.001,
    "mi": 1609.344,
    "ft": 0.3048,
    "in": 0.0254,
    "yd": 0.9144,
}
_MASS = {
    "kg": 1.0,
    "g": 0.001,
    "mg": 1e-6,
    "lb": 0.45359237,
    "oz": 0.028349523125,
    "t": 1000.0,
}
_FAMILIES = {"length": _LENGTH, "mass": _MASS}


@mcp.tool()
def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    """Convert a value between units of length, mass, or temperature.

    Args:
        value: The quantity to convert.
        from_unit: Source unit, e.g. "km", "lb", "c".
        to_unit: Target unit, e.g. "mi", "kg", "f".
    """
    source = from_unit.strip().lower()
    target = to_unit.strip().lower()

    temperature = _convert_temperature(value, source, target)
    if temperature is not None:
        # 6 significant digits, so 273.15 K survives instead of rounding to 273.1.
        return f"{value} {from_unit} = {temperature:.6g} {to_unit}"

    for units in _FAMILIES.values():
        if source in units and target in units:
            converted = value * units[source] / units[target]
            return f"{value} {from_unit} = {converted:.6g} {to_unit}"

    known = ", ".join(sorted({*_LENGTH, *_MASS, "c", "f", "k"}))
    return f"Cannot convert {from_unit!r} to {to_unit!r}. Known units: {known}."


def _convert_temperature(value: float, source: str, target: str) -> float | None:
    """Temperature needs offsets, so it cannot use the scale-factor table."""
    scales = {"c", "celsius", "f", "fahrenheit", "k", "kelvin"}
    if source not in scales or target not in scales:
        return None

    initial = source[0]
    final = target[0]

    celsius = {
        "c": value,
        "f": (value - 32.0) * 5.0 / 9.0,
        "k": value - 273.15,
    }[initial]

    return {
        "c": celsius,
        "f": celsius * 9.0 / 5.0 + 32.0,
        "k": celsius + 273.15,
    }[final]


@mcp.tool()
def hash_text(text: str, algorithm: str = "sha256") -> str:
    """Hash text with a named algorithm.

    Args:
        text: The text to hash.
        algorithm: One of sha256, sha1, sha512, md5.
    """
    name = algorithm.strip().lower()
    allowed = {"sha256", "sha1", "sha512", "md5"}
    if name not in allowed:
        return f"Unsupported algorithm {algorithm!r}. Choose one of: {', '.join(sorted(allowed))}."

    digest = hashlib.new(name, text.encode("utf-8")).hexdigest()
    return f"{name}({text!r}) = {digest}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Public Tools MCP server.")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", DEFAULT_PORT)),
        help=f"port to listen on (default {DEFAULT_PORT})",
    )
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    args = parser.parse_args()

    print(f"Public Tools MCP server → http://{args.host}:{args.port}/mcp  (no auth)")
    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
