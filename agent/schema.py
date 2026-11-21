"""The structured decision the model returns for one ticket.

Gemini is given this schema, so every response has exactly these fields with allowed values only.
"""

from typing import Literal

from pydantic import BaseModel, Field

Language = Literal["nl", "en", "de"]
Category = Literal["bug", "billing", "feature_request", "question", "other"]
Priority = Literal["P1", "P2", "P3", "P4"]
Action = Literal["reply", "handoff", "close"]
HandoffRule = Literal[
    "none",
    "refund_or_disputed_charge",
    "cancellation_or_contract_change",
    "personal_data_request",
    "security_incident",
    "legal_threat",
    "threat_to_leave",
]


class Decision(BaseModel):
    language: Language = Field(description="Language the customer wrote in.")
    category: Category
    priority: Priority
    action: Action
    reason: str = Field(description="One or two sentences: why this category, priority and action.")
    faq_ids: list[str] = Field(description="Help-centre entries used, for example ['F02']. Empty if none apply.")
    handoff_rule: HandoffRule = Field(description="The client rule that forces a human, or 'none'.")
    draft_reply: str = Field(description="Reply to the customer in their language. Empty unless action is reply.")
    handoff_note: str = Field(description="Note for the support team in English. Empty unless action is handoff.")
    ticket_en: str = Field(description="The customer's ticket translated into English.")
    reply_en: str = Field(description="English translation of draft_reply. Empty unless action is reply.")
    confidence: float = Field(ge=0, le=1, description="Confidence in the action, from 0 to 1.")
