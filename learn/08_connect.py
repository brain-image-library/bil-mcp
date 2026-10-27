"""Lesson 8: connect bil-mcp to Claude Desktop on this Mac.

Run from the repo root:
    uv run python learn/08_connect.py            # dry run: shows what would change
    uv run python learn/08_connect.py --write    # backs up the config, then adds "bil"

Claude Desktop reads its list of local MCP servers from
~/Library/Application Support/Claude/claude_desktop_config.json at start-up.
Each entry is the command the HOST runs to start a server as a subprocess,
exactly what learn/01_raw_handshake.py did by hand. Existing servers in the
file are kept; only the "bil" entry is added or replaced.
"""

import argparse
import json
import shutil
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"


def find_uv() -> str:
    # Desktop apps don't inherit your shell's PATH, so the config needs uv's absolute path.
    found = shutil.which("uv")
    for candidate in (found, Path.home() / ".local/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv"):
        if candidate and Path(candidate).exists():
            return str(Path(candidate).resolve())
    raise SystemExit("uv not found. Install it, or put its absolute path in the config by hand.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="actually modify the config (a backup is made first)")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = ap.parse_args()

    entry = {"command": find_uv(), "args": ["--directory", str(REPO), "run", "bil-mcp"]}
    config = json.loads(args.config.read_text()) if args.config.exists() else {}
    servers = config.setdefault("mcpServers", {})
    print("config file      :", args.config, "(exists)" if args.config.exists() else "(will be created)")
    print("servers already  :", sorted(servers) or "none")
    print("entry for 'bil'  :", json.dumps(entry, indent=2))
    if servers.get("bil") == entry:
        print("\nAlready configured; nothing to do.")
        return
    servers["bil"] = entry
    if not args.write:
        print("\nDry run. Re-run with --write to apply.")
        return
    if args.config.exists():
        backup = args.config.with_name(args.config.name + time.strftime(".bak-%Y%m%d-%H%M%S"))
        shutil.copy2(args.config, backup)
        print("\nbackup written   :", backup)
    args.config.parent.mkdir(parents=True, exist_ok=True)
    args.config.write_text(json.dumps(config, indent=2) + "\n")
    print("config updated. Now quit Claude Desktop completely (Cmd+Q) and reopen it.")


main()
