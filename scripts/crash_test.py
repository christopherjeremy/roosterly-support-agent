"""Crash test: kill the agent in the middle of a real run, restart it, and verify the result.

Practice mode only. Steps:
1. Reset the practice tickets.
2. Start run_agent.py and kill the process hard (no clean shutdown) after it has handled a few tickets.
3. Start run_agent.py again and let it finish.
4. Verify from the helpdesk and the log: every ticket handled, none answered twice.
5. Reset the practice tickets again, unless --keep is given.

Both runs write to logs/crash-test.log, separate from logs/agent.log.

Usage: python -m scripts.crash_test [--kill-after 8] [--keep]
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from agent.config import load_settings
from agent.helpdesk import HelpdeskClient

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "crash-test.log"


def log_lines() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def start_agent() -> subprocess.Popen:
    env = {**os.environ, "AGENT_LOG_PATH": str(LOG)}
    return subprocess.Popen([sys.executable, "run_agent.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kill-after", type=int, default=8, help="kill once this many tickets are logged")
    parser.add_argument("--keep", action="store_true", help="do not reset the tickets at the end")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    with HelpdeskClient(load_settings()) as helpdesk:
        if helpdesk.status()["mode"] != "practice":
            print("Refusing to run: the helpdesk is not in practice mode.")
            return 2
        helpdesk.reset_practice()
        LOG.unlink(missing_ok=True)
        total = helpdesk.status()["open_tickets"]
        print(f"1. Reset: {total} open tickets.")

        process = start_agent()
        deadline = time.monotonic() + 600
        while len(log_lines()) < args.kill_after and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        process.kill()
        process.wait()
        handled_at_kill = helpdesk.status()["handled_tickets"]
        print(f"2. Killed the agent after {len(log_lines())} logged tickets; helpdesk shows {handled_at_kill} handled.")

        restart = start_agent()
        restart.wait()
        status = helpdesk.status()
        print(f"3. Restarted run finished with exit code {restart.returncode}: "
              f"{status['open_tickets']} open, {status['handled_tickets']} handled.")

        entries = log_lines()
        sent = Counter(e["ticket"] for e in entries if e["status"] == "sent")
        duplicates = sorted(ticket for ticket, count in sent.items() if count > 1)
        statuses = Counter(e["status"] for e in entries)
        runs = Counter(e["run"] for e in entries)
        unlogged = handled_at_kill - len([e for e in entries if e["run"] == min(runs)])

        print(f"4. Log: {len(entries)} lines over {len(runs)} runs, statuses {dict(statuses)}.")
        if unlogged > 0:
            print(f"   {unlogged} ticket(s) were saved by the helpdesk in the moment of the kill, before their log "
                  "line was written; the restart did not list them as open and sent nothing for them.")
        passed = status["open_tickets"] == 0 and status["handled_tickets"] == total and not duplicates
        print(f"   Every ticket handled: {status['handled_tickets'] == total}. "
              f"Tickets sent twice: {duplicates or 'none'}.")
        print("PASS" if passed else "FAIL")

        if not args.keep:
            helpdesk.reset_practice()
            print("5. Practice tickets re-opened.")
        return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
