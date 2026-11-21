"""Print the model's decision for selected tickets without sending anything to the helpdesk.

A development aid for checking decisions and the English versions of replies.

Usage:
    python -m scripts.decide T005 T010         # practice tickets by id
    python -m scripts.decide --thinking minimal T005
"""

import argparse
import logging
import sys
from dataclasses import replace

from agent.config import load_settings
from agent.helpdesk import HelpdeskClient
from agent.llm import DecisionModel
from agent.pipeline import TicketDecider


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticket_ids", nargs="+")
    parser.add_argument("--thinking", default="low", choices=["minimal", "low", "medium", "high"])
    parser.add_argument("--model", help="override the MODEL setting for this run")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="  %(message)s")
    settings = load_settings(require_llm=True)
    if args.model:
        settings = replace(settings, model=args.model)
    decider = TicketDecider(DecisionModel(settings, thinking_level=args.thinking))

    with HelpdeskClient(settings) as helpdesk:
        for ticket_id in args.ticket_ids:
            ticket = helpdesk.get_ticket(ticket_id)
            outcome = decider.decide(ticket)
            decision, usage = outcome.decision, outcome.usage
            print(f"[{ticket['id']}] {ticket['subject']}")
            print(f"  {decision.language} / {decision.category} / {decision.priority} / {decision.action}"
                  f"  (confidence {decision.confidence}, rule {decision.handoff_rule}, faq {decision.faq_ids})")
            print(f"  Reason:  {decision.reason}")
            if outcome.overrides:
                print(f"  Safety:  {'; '.join(outcome.overrides)}")
            print(f"  Ticket:  {decision.ticket_en}")
            if decision.action == "reply":
                print(f"  Reply:   {decision.draft_reply}")
                print(f"  English: {decision.reply_en}")
            if decision.action == "handoff":
                print(f"  Note:    {decision.handoff_note}")
            print(f"  Usage:   {usage.input_tokens} in, {usage.output_tokens} out, "
                  f"{usage.thinking_tokens} thinking, {usage.seconds}s")
            print()


if __name__ == "__main__":
    main()
