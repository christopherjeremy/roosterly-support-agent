"""Deterministic safety checks around the model.

The model applies the client rules, but a model can be wrong or manipulated, so these checks run in code:

Before the model:
- Payment details (IBANs, card numbers, security codes) are detected and masked, so the model never sees
  them and cannot repeat them.

After the model:
- Each check can turn a reply into a handoff. No check can turn anything into a reply.
- Every outgoing text field is masked again before it is sent or logged.
"""

import re
from pathlib import Path

from agent.schema import Decision

FAQ_PATH = Path(__file__).resolve().parent.parent / "data" / "faq.md"
CONFIDENCE_THRESHOLD = 0.7

IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
CARD_CANDIDATE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
CARD_EXPIRY = re.compile(r"\b(?:0[1-9]|1[0-2])\s?/\s?(?:\d{2}|20\d{2})\b")
SECURITY_CODE = re.compile(r"\b(?:CVC|CVV|CVC2|CID)\s*:?\s*\d{3,4}\b", re.IGNORECASE)
URL = re.compile(r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s)\]]*)?", re.IGNORECASE)

# Words that signal a client rule. Matching is on lower-cased ticket text, in Dutch, English and German.
# These only ever force a handoff, so a false match costs a human a minute, never a wrong answer.
RULE_KEYWORDS = {
    "refund_or_disputed_charge": [
        "terugbetal", "terugstort", "terugkrijgen", "terug krijgen", "geld terug", "dubbel afgeschreven",
        "twee keer afgeschreven", "refund", "money back", "charged twice", "double charge", "chargeback",
        "rückerstatt", "erstatten", "doppelt abgebucht",
    ],
    "cancellation_or_contract_change": [
        "opzeggen", "opzegging", "abonnement stoppen", "contract beëindig", "cancel our subscription",
        "cancel my subscription", "cancel the subscription", "terminate", "kündig",
    ],
    "personal_data_request": [
        "avg", "gdpr", "dsgvo", "persoonsgegevens", "inzageverzoek", "gegevens verwijder",
        "personal data", "delete my data", "personenbezogen",
    ],
    "security_incident": [
        "gehackt", "gehacked", "hacked", "phishing", "niet wij", "wasn't us", "was not us",
        "onbekende login", "unknown login", "unauthorized", "datalek", "data breach",
    ],
    "legal_threat": [
        "advocaat", "juridisch", "rechtszaak", "schadevergoeding", "aansprakelijk", "lawyer", "attorney",
        "legal action", "lawsuit", "compensation", "anwalt", "rechtliche schritte", "schadenersatz",
    ],
    "threat_to_leave": [
        "overstappen", "andere aanbieder", "we stoppen", "zeggen we op", "switch provider",
        "switching to", "we are leaving", "we will leave", "anbieter wechseln",
    ],
}


def _luhn_valid(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _mask_cards(text: str) -> tuple[str, bool]:
    found = False

    def replace(match: re.Match) -> str:
        nonlocal found
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            found = True
            return "[card number]"
        return match.group()

    return CARD_CANDIDATE.sub(replace, text), found


def mask_payment_details(text: str) -> tuple[str, bool]:
    """Replace IBANs, card numbers and security codes. Returns the masked text and whether any were found."""
    if not text:
        return text, False
    text, found_card = _mask_cards(text)
    if found_card:
        text = CARD_EXPIRY.sub("[expiry date]", text)
    text, iban_count = IBAN.subn("[IBAN]", text)
    text, code_count = SECURITY_CODE.subn("[security code]", text)
    return text, found_card or iban_count > 0 or code_count > 0


def prepare_ticket(ticket: dict) -> tuple[dict, bool]:
    """Mask payment details in the subject and body before the ticket is sent to the model."""
    subject, found_in_subject = mask_payment_details(ticket.get("subject", ""))
    body, found_in_body = mask_payment_details(ticket.get("body", ""))
    return {**ticket, "subject": subject, "body": body}, found_in_subject or found_in_body


def allowed_hosts(faq_text: str | None = None) -> set[str]:
    """Web addresses the help centre contains; replies may not link anywhere else."""
    text = faq_text if faq_text is not None else FAQ_PATH.read_text(encoding="utf-8")
    return {_host(match) for match in URL.findall(text)}


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", url.lower()).split("/")[0].rstrip(".,;:")


RULE_PATTERNS = {
    rule: re.compile(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")", re.IGNORECASE)
    for rule, words in RULE_KEYWORDS.items()
}


def _rule_matches(text: str) -> list[str]:
    """Client rules whose keywords appear at the start of a word in the text."""
    return [rule for rule, pattern in RULE_PATTERNS.items() if pattern.search(text)]


def check(decision: Decision, ticket: dict, had_payment_details: bool, hosts: set[str]) -> tuple[Decision, list[str]]:
    """Apply the deterministic checks. Returns the final decision and the reasons for any override."""
    reasons = []
    ticket_text = f"{ticket.get('subject', '')}\n{ticket.get('body', '')}"

    if decision.action == "reply":
        if had_payment_details:
            reasons.append("ticket contains payment details")
        if decision.priority == "P1":
            reasons.append("priority P1 always goes to a human")
        if decision.handoff_rule != "none":
            reasons.append(f"model flagged client rule {decision.handoff_rule}")
        for rule in _rule_matches(ticket_text):
            reasons.append(f"keyword match for client rule {rule}")
        if not decision.faq_ids:
            reasons.append("reply cites no help-centre entry")
        if not decision.draft_reply.strip():
            reasons.append("reply text is empty")
        if decision.confidence < CONFIDENCE_THRESHOLD:
            reasons.append(f"confidence {decision.confidence} below {CONFIDENCE_THRESHOLD}")
        foreign = sorted({_host(url) for url in URL.findall(decision.draft_reply)} - hosts)
        if foreign:
            reasons.append(f"reply links outside the help centre: {', '.join(foreign)}")
        if mask_payment_details(decision.draft_reply)[1]:
            reasons.append("reply contains payment details")

    if decision.action == "close" and _rule_matches(ticket_text):
        reasons.append("ticket to be closed matches a client rule")

    if reasons:
        decision = _to_handoff(decision, reasons)
    elif decision.action == "handoff" and not decision.handoff_note.strip():
        decision = decision.model_copy(update={"handoff_note": f"Needs review. Customer request: {decision.ticket_en}"})

    if had_payment_details and decision.action == "handoff":
        decision = decision.model_copy(update={
            "handoff_note": decision.handoff_note
            + " The customer included payment details; they are masked here, see the original ticket."
        })

    return _mask_outgoing(decision), reasons


def _to_handoff(decision: Decision, reasons: list[str]) -> Decision:
    note = f"Automatic {decision.action} withheld by safety checks ({'; '.join(reasons)}). "
    if decision.handoff_note.strip():
        note += decision.handoff_note.strip() + " "
    else:
        note += f"Customer request: {decision.ticket_en.strip()} "
    if decision.reply_en.strip():
        note += f"Draft reply, not sent: {decision.reply_en.strip()}"
    return decision.model_copy(update={
        "action": "handoff",
        "handoff_note": note.strip(),
        "draft_reply": "",
        "reason": f"{decision.reason} Safety override: {'; '.join(reasons)}.",
    })


def _mask_outgoing(decision: Decision) -> Decision:
    fields = ("reason", "draft_reply", "handoff_note", "ticket_en", "reply_en")
    return decision.model_copy(update={name: mask_payment_details(getattr(decision, name))[0] for name in fields})
