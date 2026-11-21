"""Tests for sending a decision exactly once, using a fake helpdesk."""

import pytest

from agent.helpdesk import HelpdeskError, HelpdeskOutcomeUnknown
from agent.runner import payload_for, send_once
from agent.schema import Decision


class FakeHelpdesk:
    """Replays send outcomes in order; a send that 'saves' marks the ticket handled."""

    def __init__(self, outcomes, handled=False):
        self.outcomes = list(outcomes)
        self.handled = handled
        self.posts = 0

    def is_handled(self, ticket_id):
        return self.handled

    def send_decision(self, ticket_id, action, payload):
        self.posts += 1
        outcome = self.outcomes.pop(0)
        if outcome in ("ok", "saved_then_timeout"):
            self.handled = True
        if outcome == "saved_then_timeout":
            raise HelpdeskOutcomeUnknown("ReadTimeout")
        if outcome == "lost_timeout":
            raise HelpdeskOutcomeUnknown("ConnectTimeout")
        if isinstance(outcome, HelpdeskError):
            raise outcome
        return {"ok": True}


def test_sends_once_when_open():
    helpdesk = FakeHelpdesk(["ok"])
    assert send_once(helpdesk, "T1", "reply", {}) == "sent"
    assert helpdesk.posts == 1


def test_does_not_send_when_already_handled():
    helpdesk = FakeHelpdesk([], handled=True)
    assert send_once(helpdesk, "T1", "reply", {}) == "already_handled"
    assert helpdesk.posts == 0


def test_timeout_after_save_is_not_sent_again():
    helpdesk = FakeHelpdesk(["saved_then_timeout", "ok"])
    assert send_once(helpdesk, "T1", "reply", {}) == "sent"
    assert helpdesk.posts == 1


def test_timeout_before_save_is_retried():
    helpdesk = FakeHelpdesk(["lost_timeout", "ok"])
    assert send_once(helpdesk, "T1", "reply", {}) == "sent"
    assert helpdesk.posts == 2


def test_409_is_recorded_not_retried():
    helpdesk = FakeHelpdesk([HelpdeskError(409, "already_handled", "handled")])
    assert send_once(helpdesk, "T1", "reply", {}) == "already_handled"
    assert helpdesk.posts == 1


def test_validation_error_is_raised():
    helpdesk = FakeHelpdesk([HelpdeskError(422, "validation_failed", "draft_reply missing")])
    with pytest.raises(HelpdeskError):
        send_once(helpdesk, "T1", "reply", {})


def test_gives_up_when_outcome_stays_unknown():
    helpdesk = FakeHelpdesk(["lost_timeout"] * 3)
    assert send_once(helpdesk, "T1", "reply", {}) == "unconfirmed"


def decision(action, **extra):
    fields = dict(language="nl", category="question", priority="P4", action=action, reason="r", faq_ids=["F01"],
                  handoff_rule="none", draft_reply="", handoff_note="", ticket_en="t", reply_en="", confidence=0.9)
    fields.update(extra)
    return Decision(**fields)


def test_payload_contains_only_fields_for_the_action():
    assert "draft_reply" in payload_for(decision("reply", draft_reply="Hallo"))
    handoff = payload_for(decision("handoff", handoff_note="Note"))
    assert "handoff_note" in handoff and "draft_reply" not in handoff
    close = payload_for(decision("close"))
    assert "draft_reply" not in close and "handoff_note" not in close
    assert "ticket_en" not in close and "faq_ids" not in close
