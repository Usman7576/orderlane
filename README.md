# OrderLane

An AI agent that turns WhatsApp text and voice orders into checked, staff-approved sales orders for a small wholesaler.

> **Status: Day 2 of 7.** WhatsApp webhook, background worker, database schema, seed data, CI, the rules-only matching pipeline and the evaluation harness are in place. The LLM agent and dashboard are next. See [docs/PLAN.md](docs/PLAN.md).
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

## Evaluation

```bash
cd backend
uv run python -m evals.run --show-failures
```

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
