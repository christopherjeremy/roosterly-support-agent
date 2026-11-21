"""The decision for one ticket: mask payment details, ask the model, apply the safety checks."""

from dataclasses import dataclass

from agent.llm import DecisionModel, Usage
from agent.safety import allowed_hosts, check, prepare_ticket
from agent.schema import Decision


@dataclass
class Outcome:
    decision: Decision
    overrides: list[str]
    had_payment_details: bool
    usage: Usage


class TicketDecider:
    def __init__(self, model: DecisionModel):
        self._model = model
        self._hosts = allowed_hosts()

    def decide(self, ticket: dict) -> Outcome:
        masked, had_payment_details = prepare_ticket(ticket)
        decision, usage = self._model.decide(masked)
        decision, overrides = check(decision, masked, had_payment_details, self._hosts)
        return Outcome(decision, overrides, had_payment_details, usage)
