"""Run the support agent over all open helpdesk tickets.

Usage:
    python run_agent.py              # handle every open ticket once
    python run_agent.py --dry-run    # decide and print, send nothing
    python run_agent.py --reset      # practice mode only: re-open all tickets first, then run
    python run_agent.py --ticket T005 --ticket T010   # only these tickets
"""

import argparse
import logging
import sys
import time

from agent.config import load_settings
from agent.helpdesk import HelpdeskClient
from agent.llm import DecisionModel
from agent.pipeline import Outcome, TicketDecider
from agent.runner import run


def print_decision(ticket: dict, outcome: Outcome) -> None:
    d = outcome.decision
    print(f"[{ticket['id']}] {d.language}/{d.category}/{d.priority}/{d.action}  faq {d.faq_ids}")
    if outcome.overrides:
        print(f"    safety: {'; '.join(outcome.overrides)}")
    print(f"    ticket: {d.ticket_en}")
    if d.action == "reply":
        print(f"    reply (English): {d.reply_en}")
    elif d.action == "handoff":
        print(f"    note: {d.handoff_note}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="decide and print, send nothing")
    parser.add_argument("--reset", action="store_true", help="practice mode: re-open all tickets first")
    parser.add_argument("--ticket", action="append", help="process only this ticket id (repeatable)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("agent").setLevel(logging.INFO)
    settings = load_settings(require_llm=True)
    decider = TicketDecider(DecisionModel(settings))
    started = time.monotonic()

    with HelpdeskClient(settings) as helpdesk:
        status = helpdesk.status()
        print(f"Mode {status['mode']}: {status['open_tickets']} open, {status['handled_tickets']} handled. "
              f"Model {settings.model}.")
        if args.reset:
            if status["mode"] != "practice":
                print("Refusing to reset: the helpdesk is not in practice mode.")
                return 2
            helpdesk.reset_practice()
            print("Practice tickets re-opened.")

        summary = run(helpdesk, decider, settings.model, dry_run=args.dry_run,
                      only=set(args.ticket) if args.ticket else None,
                      on_decision=print_decision if args.dry_run else None)

    print(f"\nDone in {time.monotonic() - started:.0f}s. Actions: {dict(summary.actions)}. "
          f"Status: {dict(summary.statuses)}. Tokens: {summary.input_tokens} in, {summary.output_tokens} out.")
    if summary.incomplete:
        print(f"{summary.incomplete} ticket(s) not handled; they are still open. Run again to process them.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
