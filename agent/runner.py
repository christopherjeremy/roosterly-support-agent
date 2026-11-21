"""Process every open ticket once: decide, send one decision, write one log line.

The helpdesk is the source of truth for what has been handled:
- Only tickets the helpdesk lists as open are processed, so after a crash or interruption a new run
  continues with exactly the tickets that are still open.
- Right before sending, the ticket is checked again; if it was handled in the meantime (another run, or a
  send whose response was lost), nothing is sent.
- If a send times out, its outcome is unknown. The ticket is checked before any retry, so a decision the
  helpdesk already saved is never sent twice.
- A 409 already_handled is recorded and never retried.
If the model is unavailable, the ticket is left open and reported, so the next run picks it up.
"""

import json
import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent.helpdesk import HelpdeskClient, HelpdeskError, HelpdeskOutcomeUnknown
from agent.llm import InvalidModelOutput, ModelUnavailable
from agent.pipeline import Outcome, TicketDecider
from agent.schema import Decision

logger = logging.getLogger(__name__)

LOG_PATH = Path(os.getenv("AGENT_LOG_PATH") or Path(__file__).resolve().parent.parent / "logs" / "agent.log")
MAX_SEND_ATTEMPTS = 3


@dataclass
class RunSummary:
    statuses: Counter = field(default_factory=Counter)
    actions: Counter = field(default_factory=Counter)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def incomplete(self) -> int:
        done = ("sent", "already_handled", "dry_run")
        return sum(count for status, count in self.statuses.items() if status not in done)


def payload_for(decision: Decision) -> dict:
    """The request body for the helpdesk, with only the fields the API defines for this action."""
    payload = {
        "language": decision.language,
        "category": decision.category,
        "priority": decision.priority,
        "reason": decision.reason,
        "confidence": round(decision.confidence, 2),
    }
    if decision.action == "reply":
        payload["draft_reply"] = decision.draft_reply
    elif decision.action == "handoff":
        payload["handoff_note"] = decision.handoff_note
    return payload


def send_once(helpdesk: HelpdeskClient, ticket_id: str, action: str, payload: dict) -> str:
    """Send a decision so that the ticket ends up handled exactly once. Returns the resulting status."""
    outcome_unknown = False
    for _ in range(MAX_SEND_ATTEMPTS):
        if helpdesk.is_handled(ticket_id):
            return "sent" if outcome_unknown else "already_handled"
        try:
            helpdesk.send_decision(ticket_id, action, payload)
            return "sent"
        except HelpdeskOutcomeUnknown as exc:
            outcome_unknown = True
            logger.warning("%s: %s; checking the ticket before trying again", ticket_id, exc)
        except HelpdeskError as exc:
            if exc.status_code == 409:
                return "already_handled"
            raise
    return "unconfirmed"


def write_log_line(entry: dict) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_entry(run_id: str, ticket_id: str, status: str, outcome: Outcome | None, model: str, error: str = "") -> dict:
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": run_id,
        "ticket": ticket_id,
        "status": status,
        "model": model,
    }
    if outcome:
        d = outcome.decision
        entry |= {
            "action": d.action,
            "language": d.language,
            "category": d.category,
            "priority": d.priority,
            "confidence": d.confidence,
            "faq_ids": d.faq_ids,
            "handoff_rule": d.handoff_rule,
            "safety_overrides": outcome.overrides,
            "payment_details_masked": outcome.had_payment_details,
            "reason": d.reason,
            "model_seconds": outcome.usage.seconds,
            "input_tokens": outcome.usage.input_tokens,
            "output_tokens": outcome.usage.output_tokens,
        }
    if error:
        entry["error"] = error
    return entry


def run(helpdesk: HelpdeskClient, decider: TicketDecider, model: str, dry_run: bool = False,
        only: set[str] | None = None, on_decision=None) -> RunSummary:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary = RunSummary()
    tickets = [t for t in helpdesk.iter_tickets(status="open") if not only or t["id"] in only]
    logger.info("Run %s: %d open tickets%s", run_id, len(tickets), " (dry run)" if dry_run else "")

    for ticket in tickets:
        ticket_id = ticket["id"]
        outcome = None
        error = ""
        try:
            outcome = decider.decide(ticket)
            summary.actions[outcome.decision.action] += 1
            summary.input_tokens += outcome.usage.input_tokens
            summary.output_tokens += outcome.usage.output_tokens
            if on_decision:
                on_decision(ticket, outcome)
            if dry_run:
                status = "dry_run"
            else:
                status = send_once(helpdesk, ticket_id, outcome.decision.action, payload_for(outcome.decision))
        except (ModelUnavailable, InvalidModelOutput) as exc:
            status, error = "model_error_left_open", str(exc)
        except HelpdeskError as exc:
            status, error = "send_failed", str(exc)

        summary.statuses[status] += 1
        if not dry_run:
            write_log_line(log_entry(run_id, ticket_id, status, outcome, model, error))
        logger.info("%s: %s%s", ticket_id, status, f" ({outcome.decision.action})" if outcome else "")

    return summary
