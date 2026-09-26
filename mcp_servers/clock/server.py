"""Time and time zones over MCP, with the standard library only.

    python server.py [--local-timezone Europe/Paris]

Same tools as the reference time server (`get_current_time`, `convert_time`), with the
zone database Python already ships (zoneinfo) — nothing to download.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _mcp_stdio import McpServer  # noqa: E402

server = McpServer("clock", "1.0.0", "Time & time zones")
LOCAL: dict = {"zone": "Europe/Paris"}


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo((name or LOCAL["zone"]).strip())
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown time zone '{name}'. Use an IANA name such as Europe/Paris or America/New_York.") from None


@server.tool("get_current_time", "The current date and time in a time zone (IANA name; default: this machine's).",
             {"type": "object", "properties": {"timezone": {"type": "string"}}}, read_only=True)
def get_current_time(timezone: str = "") -> dict:
    try:
        zone = _zone(timezone)
    except ValueError as exc:
        return {"error": str(exc)}
    now = datetime.now(zone)
    return {"timezone": str(zone), "datetime": now.isoformat(timespec="seconds"),
            "weekday": now.strftime("%A"), "is_dst": bool(now.dst())}


@server.tool("convert_time", "Convert a time (HH:MM, today) from one time zone to another.",
             {"type": "object", "properties": {"source_timezone": {"type": "string"}, "time": {"type": "string"},
                                               "target_timezone": {"type": "string"}},
              "required": ["source_timezone", "time", "target_timezone"]}, read_only=True)
def convert_time(source_timezone: str, time: str, target_timezone: str) -> dict:
    try:
        source, target = _zone(source_timezone), _zone(target_timezone)
        hours, minutes = (int(x) for x in time.strip().split(":")[:2])
    except ValueError as exc:
        return {"error": str(exc) if "time zone" in str(exc) else "time must be HH:MM"}
    moment = datetime.now(source).replace(hour=hours, minute=minutes, second=0, microsecond=0)
    converted = moment.astimezone(target)
    return {"source": {"timezone": str(source), "datetime": moment.isoformat(timespec="minutes")},
            "target": {"timezone": str(target), "datetime": converted.isoformat(timespec="minutes")},
            "difference_hours": (converted.utcoffset() - moment.utcoffset()).total_seconds() / 3600}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-timezone", default="Europe/Paris")
    args = parser.parse_args()
    LOCAL["zone"] = args.local_timezone
    server.run()


if __name__ == "__main__":
    main()
