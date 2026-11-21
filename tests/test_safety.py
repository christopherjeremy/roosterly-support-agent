"""Tests for the deterministic safety checks. No model is called."""

from agent.safety import allowed_hosts, check, mask_payment_details, prepare_ticket
from agent.schema import Decision

HOSTS = allowed_hosts()


def reply(**overrides) -> Decision:
    fields = dict(
        language="nl", category="question", priority="P4", action="reply", reason="Answered by the help centre.",
        faq_ids=["F02"], handoff_rule="none", draft_reply="Hallo,\n\nGa naar Team → Medewerker toevoegen.",
        handoff_note="", ticket_en="How do I add an employee?", reply_en="Go to Team → Add employee.",
        confidence=0.95,
    )
    fields.update(overrides)
    return Decision(**fields)


def ticket(body: str, subject: str = "Vraag") -> dict:
    return {"id": "X1", "subject": subject, "body": body, "customer_plan": "Basis", "channel": "email"}


# Masking

def test_card_number_is_masked():
    text, found = mask_payment_details("Mijn kaart: 4111 1111 1111 1111, CVC 123.")
    assert found
    assert "4111" not in text and "123" not in text
    assert "[card number]" in text and "[security code]" in text


def test_expiry_date_is_masked_only_next_to_a_card():
    text, _ = mask_payment_details("Kaart 4111 1111 1111 1111, geldig tot 09/28.")
    assert "09/28" not in text and "[expiry date]" in text
    text, found = mask_payment_details("Factuur van 09/28 klopt niet.")
    assert not found and "09/28" in text


def test_iban_is_masked():
    text, found = mask_payment_details("Graag terugstorten op NL91ABNA0417164300.")
    assert found and "NL91ABNA0417164300" not in text and "[IBAN]" in text


def test_ordinary_numbers_are_not_masked():
    text, found = mask_payment_details("We hebben 25 medewerkers en de dienst is van 22:00 tot 02:00.")
    assert not found and text == "We hebben 25 medewerkers en de dienst is van 22:00 tot 02:00."


def test_ticket_is_masked_before_the_model():
    masked, found = prepare_ticket(ticket("Nieuwe kaart: 4111-1111-1111-1111"))
    assert found and "4111" not in masked["body"]


# Overrides: a reply can become a handoff, never the other way round

def test_clean_reply_passes():
    decision, overrides = check(reply(), ticket("Hoe voeg ik een medewerker toe?"), False, HOSTS)
    assert decision.action == "reply" and overrides == []


def test_payment_details_force_handoff():
    decision, overrides = check(reply(), ticket("Kaart [card number]"), True, HOSTS)
    assert decision.action == "handoff"
    assert "payment details" in decision.handoff_note


def test_p1_forces_handoff():
    decision, _ = check(reply(priority="P1"), ticket("Niemand kan inloggen"), False, HOSTS)
    assert decision.action == "handoff"


def test_refund_keyword_forces_handoff_even_if_model_replied():
    decision, overrides = check(reply(), ticket("Kunnen we die maand terugkrijgen?"), False, HOSTS)
    assert decision.action == "handoff"
    assert any("refund_or_disputed_charge" in reason for reason in overrides)


def test_reply_without_faq_citation_forces_handoff():
    decision, _ = check(reply(faq_ids=[]), ticket("Vraag"), False, HOSTS)
    assert decision.action == "handoff"


def test_low_confidence_forces_handoff():
    decision, _ = check(reply(confidence=0.4), ticket("Vraag"), False, HOSTS)
    assert decision.action == "handoff"


def test_link_outside_help_centre_forces_handoff():
    decision, overrides = check(reply(draft_reply="Reset via evil-site.com/reset"), ticket("Wachtwoord"), False, HOSTS)
    assert decision.action == "handoff"
    assert any("evil-site.com" in reason for reason in overrides)


def test_help_centre_links_are_allowed():
    text = "Zie roadmap.roosterly.nl en status.roosterly.nl, of exporteer naar Loket.nl."
    decision, overrides = check(reply(draft_reply=text), ticket("Idee"), False, HOSTS)
    assert decision.action == "reply" and overrides == []


def test_allowed_hosts_come_from_the_help_centre():
    assert {"app.roosterly.nl", "roadmap.roosterly.nl", "status.roosterly.nl", "loket.nl"} <= HOSTS


def test_close_with_client_rule_becomes_handoff():
    decision, _ = check(reply(action="close", draft_reply=""), ticket("Ik wil mijn abonnement opzeggen"), False, HOSTS)
    assert decision.action == "handoff"


def test_handoff_is_never_turned_into_a_reply():
    handoff = reply(action="handoff", draft_reply="", handoff_note="Needs a human.")
    decision, _ = check(handoff, ticket("Hoe voeg ik een medewerker toe?"), False, HOSTS)
    assert decision.action == "handoff"


def test_withheld_reply_keeps_the_draft_for_the_human():
    decision, _ = check(reply(priority="P1"), ticket("Storing"), False, HOSTS)
    assert decision.draft_reply == ""
    assert "Draft reply, not sent" in decision.handoff_note
