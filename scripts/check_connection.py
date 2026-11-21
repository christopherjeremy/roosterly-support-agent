"""Check that the helpdesk credentials work and summarise what the account contains.

Usage: python -m scripts.check_connection
"""

from agent.config import load_settings
from agent.helpdesk import HelpdeskClient


def main() -> None:
    settings = load_settings()
    with HelpdeskClient(settings) as helpdesk:
        status = helpdesk.status()
        print(f"Mode:            {status['mode']}")
        print(f"Open tickets:    {status['open_tickets']}")
        print(f"Handled tickets: {status['handled_tickets']}")

        examples = helpdesk.examples()
        print(f"Examples:        {len(examples)}")

        tickets = list(helpdesk.iter_tickets(status="all"))
        print(f"Tickets in set:  {len(tickets)} ({tickets[0]['id']} to {tickets[-1]['id']})")


if __name__ == "__main__":
    main()
