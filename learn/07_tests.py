"""Lesson 7: why the test suite has a fake API AND live contract tests.

Run from the repo root:   uv run python learn/07_tests.py

Re-enacts the real bug from the build, in a throwaway copy of the repo (your
files are not touched):
  * the OLD client logic treated every HTTP 404 as "endpoint missing";
  * real BIL also answers "no matches" with 404 (+ a JSON body).
It runs the test suite against combinations of {old/fixed client} x
{fake that wrongly answers 200 / fake that answers 404 like real BIL}, then the
live contract test. Needs network for the last part.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXED = '''                if "json" in r.headers.get("content-type", ""):
                    return r.json()
                if r.status_code == 404:
                    raise BILAPIError(f"BIL API has no endpoint {path} (HTTP 404)")'''
OLD = '''                if r.status_code == 404:   # OLD (buggy) logic: every 404 = missing endpoint
                    raise BILAPIError(f"BIL API has no endpoint {path} (HTTP 404)")
                if "json" in r.headers.get("content-type", ""):
                    return r.json()'''
FAKE_LINE = "def _fail(endpoint: str, message: str, status: int = {}) -> httpx.Response:"


def run(client: str, fake_status: int, extra: list[str]) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(REPO, copy, ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__"))
        cl = copy / "src/bil_mcp/client.py"
        if client == "old":
            cl.write_text(cl.read_text().replace(FIXED, OLD))
        cf = copy / "tests/conftest.py"
        cf.write_text(cf.read_text().replace(FAKE_LINE.format(404), FAKE_LINE.format(fake_status)))
        env = {**os.environ, "PYTHONPATH": str(copy / "src")}   # make the copy's code win
        out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra],
                             cwd=copy, env=env, capture_output=True, text=True).stdout
        return re.findall(r"^=*\s*(.*(?:passed|failed).*?)\s*=*$", out, re.M)[-1]


print("offline suite | fixed client | fake answers 404 (like BIL)   :", run("fixed", 404, []))
print("offline suite | old client   | fake answers 200 (wrong guess):", run("old", 200, []))
print("offline suite | old client   | fake answers 404 (like BIL)   :", run("old", 404, []))
print("LIVE contract | old client   | real BIL                      :",
      run("old", 404, ["-m", "live", "-k", "no_hits"]))
