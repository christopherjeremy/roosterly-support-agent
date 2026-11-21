"""Settings loaded from environment variables (a local .env file in development)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

HELPDESK_VARIABLES = ("HELPDESK_BASE_URL", "HELPDESK_API_KEY", "CANDIDATE_EMAIL")
LLM_VARIABLES = ("GEMINI_API_KEY", "MODEL")


@dataclass(frozen=True)
class Settings:
    helpdesk_base_url: str
    helpdesk_api_key: str
    candidate_email: str
    candidate_name: str
    gemini_api_key: str
    model: str


def load_settings(require_llm: bool = False) -> Settings:
    """Read settings from the environment and fail early with a clear message if any are missing."""
    load_dotenv(".env")

    required = HELPDESK_VARIABLES + (LLM_VARIABLES if require_llm else ())
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError(
            f"Missing environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the values."
        )

    return Settings(
        helpdesk_base_url=os.environ["HELPDESK_BASE_URL"].rstrip("/"),
        helpdesk_api_key=os.environ["HELPDESK_API_KEY"],
        candidate_email=os.environ["CANDIDATE_EMAIL"],
        candidate_name=os.getenv("CANDIDATE_NAME", ""),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        model=os.getenv("MODEL", ""),
    )
