# Roosterly support agent

For each open helpdesk ticket: decide language, category, priority and one action (reply, handoff, close),
send it once, log one line.

## How to run

```bash
git clone https://github.com/christopherjeremy/roosterly-support-agent.git && cd roosterly-support-agent
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # Windows: .venv\Scripts\...
cp .env.example .env                       # helpdesk key, email, name; free Gemini key from aistudio.google.com
.venv/bin/python run_agent.py --dry-run    # decide and print, send nothing
.venv/bin/python run_agent.py              # handle every open ticket once (--reset: re-open practice tickets)
.venv/bin/python eval.py --runs 3          # score against the labelled examples
.venv/bin/python -m pytest -q tests        # 33 unit tests;  python -m scripts.crash_test: kill, restart, verify
```

## Decisions

1. **Dutch directly, checked in English.** One Gemini call per ticket returns a schema-validated decision, the reply
   in the customer's language, and English versions of ticket and reply. No translation step, so no meaning lost
   twice. The English comes from the same model, so the decisions are checked against Nordavo's labels and
   hand-written probe tickets; an independent translation check is not done yet (see below). `gemini-3.5-flash-lite` (free) was chosen by measurement: it matched or beat
   `gemini-3.6-flash` on every eval field at ~2 s instead of ~35 s per ticket, within quota.
2. **Safety in code, one-directional.** Card numbers, IBANs, CVCs and expiry dates are masked before the model sees
   the ticket. Afterwards, code can turn a reply into a handoff, never the reverse: payment details, P1, a client
   rule (flagged by the model or matched by nl/en/de keyword), no help-centre citation, confidence < 0.7, or a link
   to a host not in `faq.md`. Injected instructions in tickets are ignored and noted for the human.
3. **The helpdesk is the source of truth.** Only open tickets are processed, each re-checked just before sending. A
   local state file would go stale after a practice reset.

**Assumptions.** Unsure = no help-centre entry, confidence < 0.7 or invalid output. Multi-question tickets get a reply
only if every part is covered; otherwise the handoff note gives the open issue, then the covered answer. Follow-ups,
unexplained bugs (also cosmetic), unexplained charges, payment details and every P1 go to a human.

**AI tools.** Built with Claude Code.

## Reliability

503: backoff retry (nothing was saved). 429: wait `Retry-After`; requests are throttled to 4/s. A write that times
out is never retried blindly: the ticket is checked first. 409: recorded, never retried. After a crash, a restart
handles only the tickets still open; `scripts/crash_test.py` killed a real run after 8 and after 17 tickets and both
restarts ended 30/30 with no duplicates. If Gemini stays down, the ticket stays open for the next run. Security: keys
only in a git-ignored `.env`; payment data masked before the model and in all output; logs hold no ticket text.

## Eval and fix

`eval.py` scores the full agent on the 10 examples from `GET /examples` (labels fetched at run time, never stored).
Three runs each:

| | language | category | priority | action | safety | handoffs (labels 30%) |
|---|---|---|---|---|---|---|
| Before | 97% | 97% | 97% | 87% | 100% | 40% |
| After | 100% | 100% | 100% | 100% | 100% | 30% |

**Fix.** E01 (crash on iOS 15) was always handed off, though F12 explains it (iOS 16 required): the prompt said to
hand off unexplained bugs but never to reply to explained ones. The first fix turned T006, a P1 outage, into a reply,
which the eval could not show and a practice-ticket check caught. Bounding the rule (other bugs and every P1 go to a
human) fixed it; 8 new probe tickets then passed 8/8.

**Cost per 100 tickets.** Measured: 2,401 input + 232 output tokens per ticket. Free tier: €0. Paid rates
($0.30 / $2.50 per 1M): 100 × (2,401 × 0.30 + 232 × 2.50) / 1,000,000 = **$0.13**.

## What I would improve

**Next:** add the probes and risky cases (P1 outage, security, injection, card number) as a labelled regression set;
a 10-example eval scored 100% on a prompt that was unsafe. Check the English versions against an independent
translator, not the same model. In production: retrieval (RAG) once the help centre and rules grow beyond what fits
in a prompt; with 12 entries the whole help centre goes into every call (about 2,400 tokens per prompt, measured)
and no entry can be missed.
Also webhooks instead of polling, and a paid or local model, since free-tier data may be used for training. **Client rules:** add "outages affecting many users
always go to a human"; the rules omit the most urgent case, and the status-page entry tempted the agent to answer.
