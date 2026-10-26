# OrderLane

An AI agent that turns WhatsApp text and voice orders into checked, staff-approved sales orders for a small wholesaler.

> **Status: Day 5 of 7.** The full order loop works end to end, and staff now have a dashboard: secure sign-in, an order inbox, a review screen (conversation, editable lines, approve/reject, agent trace), catalog with aliases, customers with balances, and failed jobs. Next: full evaluation, web chat simulator and security tests (Day 6). See [docs/PLAN.md](docs/PLAN.md).

## Staff dashboard

React 19 + TypeScript + Vite, TanStack Query, React Router, Tailwind CSS.

| Screen | What staff do there |
|---|---|
| **Orders** | Inbox by status (needs review, waiting for customer, confirmed, cancelled), search by customer or order number |
| **Order review** | Read the WhatsApp conversation (voice notes with transcripts), fix lines with a product search or one-click suggestions, teach the matcher a new alias, approve or reject, and see the timeline and each agent step with timing, tokens and cost |
| **Catalog** | Search products by name, SKU, brand or alias; prices per tier and unit; stock; add or remove aliases |
| **Customers** | Balances against credit limits, ledger, order history |
| **Failed jobs** | Messages the worker gave up on, with one-click retry |

**Sign-in security:** Argon2id passwords, server-side sessions (only a hash of the token is stored), `HttpOnly` + `SameSite=Lax` cookies, double-submit CSRF tokens on every change, lockout after 5 failed attempts per email and per IP, the same error for wrong passwords and unknown emails, and an audit log of sign-ins and review actions.

Run it in development (with the Docker stack running):

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to the API on :8000
```

Sign in with the account the seed script prints (`SEED_ADMIN_EMAIL`, password from `SEED_ADMIN_PASSWORD` or the generated one).
>
> This is a portfolio project. It is not used by a real business, and all results come from a volunteer-written evaluation dataset.

## How it works (so far)

```
WhatsApp ──webhook──▶ FastAPI ──▶ PostgreSQL (message + job saved once)
                         │
                         └──enqueue──▶ Redis ──▶ arq worker (links customer, marks processed)
```

- The webhook **checks Meta's signature**, saves the message, queues a job and replies `200` immediately. All slow work happens in the worker.
- **Duplicate deliveries are harmless:** messages are unique by `(channel, external_id)`, and queued jobs have a fixed ID.
- Failed jobs **retry with backoff** (3 tries), then are marked failed with the error, so nothing is lost silently.

## Order understanding (rules baseline)

Before any LLM is involved, a rules pipeline turns messages like `"5 ctn tapal 950, 2 dz surf adha kilo"` into order lines:

1. **Normalize:** units (`ctn/peti` → carton, `dz/darjan` → dozen), number words (`paanch` → 5, `aadha` → 0.5), sizes (`adha kilo` → 500g), filler words removed.
2. **Parse:** split into items, whether the quantity comes first (`5 ctn tapal`) or last (`tapal 5 ctn`), with or without separators.
3. **Match:** exact alias, then fuzzy matching ([rapidfuzz](https://github.com/rapidfuzz/RapidFuzz)) that scores word coverage, closeness and pack size, so `surf 500` never becomes Surf 1kg.
4. **Decide:** confident matches are accepted. Requests like `1 dozen ariel` (two sizes exist) are marked **ambiguous** and held for review instead of guessed.
5. **Apply:** keyword intents (new order, add, modify, remove, cancel, question, complaint) update an in-memory draft.

This is the **baseline** the LLM agent will be measured against, and the agent's fallback when the LLM is unavailable.

## The agent (LangGraph)

```
interpret -> resolve_products -> question: answer_question
                              -> other:    handoff
                              -> order:    update_draft -> check_business_rules -> save_order
save_order -> needs staff: hold_reply -> staff_review (pauses) -> compose_reply
           -> otherwise:   compose_reply
```

- **interpret:** one structured-output call to Azure OpenAI returns the intent and order instructions (product mention, quantity, unit, add/set/remove). Without an LLM, or if it fails, the rules pipeline produces the same shape, and the order is held for review.
- **resolve_products:** the Day 2 matcher maps each mention to the catalog. Only when several products are close does the LLM choose, **and only from the candidates it is given**; any other answer is ignored.
- **check_business_rules:** prices (by customer tier), stock, credit limit, first order and large orders. All numbers come from the database, never the LLM.
- **Routing is fixed code:** uncertain lines, credit or stock problems, first orders, low confidence or an LLM outage go to **staff review**. The run pauses with LangGraph `interrupt()`, its state saved in PostgreSQL, and resumes when staff approve or reject.
- **Replies** are templates filled from the database, so prices in a message are always real.
- **Voice notes:** downloaded from WhatsApp and transcribed by Azure AI Speech (short-audio REST API, free tier). Each configured language is tried and the most confident transcript wins.
- **Tracing:** every run is stored in `agent_runs` (intent, route, latency, tokens, estimated cost, prompt version) with one `agent_steps` row per step.

## The order loop

```
customer message ──▶ agent ──▶ reply queued ──(10 s quiet)──▶ sent on WhatsApp
                                  │
        clear order ──────────────┴──▶ summary with [Confirm] [Edit] [Cancel]
        unsure / risky ──▶ "we're checking" ──▶ staff fix lines, approve or reject
                                                     │ (resumes the paused agent run)
                                                     ▼
[Confirm] ──▶ stock reserved + ledger entry + invoice PDF, in one locked transaction
          ──▶ "Order #1001 confirmed" + invoice document on WhatsApp
```

- **One reply per burst:** replies wait about 10 seconds. If the customer sends another message meanwhile, only the newest order summary is sent; answers to questions are always sent.
- **Safe confirmation:** the order row is locked, so a double tap confirms and invoices once. If stock ran out since the order was checked, it goes back to staff instead.
- **Stale buttons:** tapping Confirm on an order that changed or was already confirmed gets an explanation, never a second invoice.
- **Staff edits teach the matcher:** when staff correct a line, they can save what the customer wrote as a new alias for that product.
- **Delivery failures:** temporary WhatsApp errors (rate limits, outages) are retried with backoff; permanent ones are recorded on the message for staff to see.

## Evaluation

```bash
cd backend
uv run python -m evals.run --show-failures            # rules pipeline (baseline)
uv run python -m evals.run --config agent-rules       # full agent, LLM off
uv run python -m evals.run --config agent-llm         # full agent with Azure OpenAI
```

LLM answers are cached in `evals/cache/` (not committed), so re-running an evaluation costs nothing.

Each case is a conversation (one or more messages) with its correct final order. Quantities are compared in base units, so "half a carton" equals "6 pieces". Metrics: intent accuracy, line precision and recall, order exact match, auto-confirm rate and precision, and whether ambiguous requests were sent to review. Results are broken down by tag.

| Dataset | Cases | What it is |
|---|---|---|
| `examples.jsonl` | 30 | **Development set**, written by the developer while building the rules. Scores here are optimistic by design and only guard against regressions (checked in CI). |
| `volunteer.jsonl` | coming | **Test set:** messages written by volunteers following [scripted tasks](evals/datasets/tasks.yaml), imported with `python -m evals.import_form`. This is the real measurement. |

## Tech stack

Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Alembic · PostgreSQL 16 (`pg_trgm`) · Redis + arq · httpx · structlog · Docker Compose · GitHub Actions · pytest · ruff · mypy (strict)

Coming next: LangGraph, Azure OpenAI, Azure AI Speech, React + TypeScript dashboard.

## Run it locally

**Requirements:** Docker Desktop (with WSL 2 on Windows), and [uv](https://docs.astral.sh/uv/) for running tools outside Docker.

```bash
cp .env.example .env          # then fill in the values (see below)
docker compose up --build     # postgres, redis, migrations, api, worker
```

In a second terminal, load the demo data (about 80 products, 8 shops, a dashboard user):

```bash
docker compose exec api python -m scripts.seed
```

- API: http://localhost:8000 · interactive docs: http://localhost:8000/docs
- Health: `/healthz` (process up) and `/readyz` (database and Redis reachable)

### Connect WhatsApp (Meta test number)

1. Create a Meta developer app with the **WhatsApp** product. Note the **Phone number ID**, the **App secret** (App settings → Basic) and an **access token**. The temporary token expires after about a day; create a **system user token** for steady development.
2. Add your own phone number as a test recipient.
3. Expose the API: `ngrok http --url=<your-static-domain> 8000`.
4. In the app's WhatsApp → Configuration, set the webhook URL to `https://<your-static-domain>/webhooks/whatsapp`, the verify token to your `WA_VERIFY_TOKEN`, and subscribe to **messages**.
5. Put `WA_PHONE_NUMBER_ID`, `WA_ACCESS_TOKEN`, `WA_APP_SECRET` and `WA_VERIFY_TOKEN` in `.env`, restart, and re-run the seed script so the business is linked to your phone number ID.
6. Send a WhatsApp message to the test number. It appears in the `messages` table once, even if Meta delivers it again.

## Tests and checks

```bash
cd backend
uv sync
uv run pytest                 # unit tests; database tests are skipped without TEST_DATABASE_URL
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

With Docker running, run everything including the database tests (they use a separate `orderlane_test` database, so your demo data is safe):

```bash
docker compose exec api pytest
```

CI runs lint, strict type checks, migrations plus a schema-drift check (`alembic check`), all tests with coverage, and a secrets scan.

## Project layout

```
backend/
  app/            api/ integrations/ models/ services/ workers/ matching/ schemas/
  migrations/     Alembic
  scripts/        seed.py
  seed/           catalog.csv, customers.csv (illustrative prices)
  tests/          unit/ integration/
evals/datasets/   tasks.yaml (volunteer tasks with correct answers)
docs/             PLAN.md, dataset-form.md
compose.yaml
```
