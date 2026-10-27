"""Add, update or remove the bil-mcp entry in Claude Desktop's config (macOS).

    uv run python scripts/configure_claude_desktop.py                      # dry run
    uv run python scripts/configure_claude_desktop.py --write              # install / update
    uv run python scripts/configure_claude_desktop.py --log-level DEBUG --write
    uv run python scripts/configure_claude_desktop.py --log-level default --write
    uv run python scripts/configure_claude_desktop.py --remove --write

Claude Desktop reads ~/Library/Application Support/Claude/claude_desktop_config.json
at start-up. Each "mcpServers" entry is the command it runs to start a local
server. This script edits only the "bil" entry; every other server and setting
is kept, and a timestamped backup is written before any change.
After --write: quit Claude Desktop completely (Cmd+Q) and reopen it.

--log-level controls how much the server writes to stderr, which Claude Desktop
appends to ~/Library/Logs/Claude/mcp-server-bil.log. DEBUG logs every request
sent to BIL; "default" removes the flag (warnings only).
"""

import argparse
import difflib
import json
import shutil
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
NAME = "bil"
LEVELS = ["default", "DEBUG", "INFO", "WARNING", "ERROR"]


def find_uv() -> str:
    # Desktop apps don't inherit your shell's PATH, so the config needs uv's absolute path.
    found = shutil.which("uv")
    for candidate in (found, Path.home() / ".local/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv"):
        if candidate and Path(candidate).exists():
            return str(Path(candidate).resolve())
    raise SystemExit("uv not found. Install it, or put its absolute path in the config by hand.")


def current_level(entry: dict | None) -> str:
    args = (entry or {}).get("args", [])
    return args[args.index("--log-level") + 1] if "--log-level" in args[:-1] else "default"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="apply the change (default: dry run)")
    ap.add_argument("--log-level", choices=LEVELS, help="server log level (default: keep the current one)")
    ap.add_argument("--remove", action="store_true", help="remove the bil entry")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="config file (for testing)")
    a = ap.parse_args()

    before_text = a.config.read_text() if a.config.exists() else "{}\n"
    config = json.loads(before_text)
    servers = config.setdefault("mcpServers", {})
    old = servers.get(NAME)

    if a.remove:
        servers.pop(NAME, None)
    else:
        level = a.log_level or current_level(old)
        args = ["--directory", str(REPO), "run", "bil-mcp"]
        if level != "default":
            args += ["--log-level", level]
        servers[NAME] = {**(old or {}), "command": find_uv(), "args": args}  # keeps e.g. an "env" block

    after_text = json.dumps(config, indent=2) + "\n"
    print(f"config file : {a.config}" + ("" if a.config.exists() else " (will be created)"))
    print(f"servers     : {sorted(k for k in servers if k != NAME) or 'none'} + "
          f"{'bil' if NAME in servers else '(bil removed)'}")
    if old == servers.get(NAME):
        print("\nNo change needed.")
        return
    print("\nChange to the 'bil' entry:")
    for line in difflib.unified_diff(json.dumps(old, indent=2).splitlines() if old else [],
                                     json.dumps(servers.get(NAME), indent=2).splitlines() if NAME in servers else [],
                                     "before", "after", lineterm="", n=10):
        print("  " + line)
    if not a.write:
        print("\nDry run. Add --write to apply.")
        return
    if a.config.exists():
        stamp, n = time.strftime("%Y%m%d-%H%M%S"), 0
        backup = a.config.with_name(f"{a.config.name}.bak-{stamp}")
        while backup.exists():  # two runs in the same second: never overwrite a backup
            n += 1
            backup = a.config.with_name(f"{a.config.name}.bak-{stamp}-{n}")
        shutil.copy2(a.config, backup)
        print(f"\nbackup      : {backup}")
    a.config.parent.mkdir(parents=True, exist_ok=True)
    a.config.write_text(after_text)
    print("written. Quit Claude Desktop completely (Cmd+Q) and reopen it.")


main()
