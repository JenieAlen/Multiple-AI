# Multiple AI

Ask one question, get answers from **Groq**, **Together AI**, and **Gemini**
in parallel — then have Gemini judge the answers and pick the best one.

```
            ┌─────────────────────────────┐
your        │   FastAPI backend (Python)  │
question ─► │   ├─ /api/ask  (fan-out)    │ ─► single best answer + rationale
            │   ├─ providers.py           │
            │   └─ judge.py               │
            └──────────┬──────────────────┘
                       │ parallel async calls
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
       Groq         Together         Google
     (Llama)        (Llama)         (Gemini)
                                    ↑ also the judge
```

## Project layout

```
Multiple AI/
├── backend/
│   ├── __init__.py
│   ├── main.py        # FastAPI app, routes, schemas
│   ├── providers.py   # Async clients for Groq / Together / Google
│   ├── judge.py       # Gemini picks the best answer
│   └── admin.py       # In-memory admin state + usage log
├── frontend/
│   ├── index.html     # Single-page UI (no build step)
│   └── admin.html     # Admin dashboard at /admin
├── run.py             # python run.py → starts the server
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure API keys
cp .env.example .env
# Edit .env and fill in at least one of:
#   GROQ_API_KEY=...
#   TOGETHER_API_KEY=...
#   GOOGLE_API_KEY=...

# 3. Run
python run.py
```

Then open **http://127.0.0.1:8000**.

You don't need keys for all three providers — any combination works. Providers
without a key are silently skipped, and if only one provider answers it's
automatically chosen as the best.

### Where to get the keys

- **Groq** — https://console.groq.com (free tier, generous rate limits)
- **Together AI** — https://api.together.ai (free credits on signup)
- **Google Gemini** — https://aistudio.google.com/app/apikey (free tier)

## How "best answer" is picked

After all providers respond, the app sends every candidate answer to the
judge (configured by `JUDGE_PROVIDER`, default `google` / Gemini) with a
strict JSON-only prompt asking it to:

1. Pick the strongest answer (`winner`)
2. Explain the choice in 1–2 sentences (`rationale`)
3. Optionally produce an improved, synthesized version (`synthesized`)

If JSON parsing fails or the judge errors out, the app falls back to the
longest non-error answer.

## Admin page

Visit **http://127.0.0.1:8000/admin** to:

- See which providers are configured and which model each is using
- Switch the judge provider live without editing `.env`
- View token-usage totals per provider and a log of the most recent requests

Admin is gated by `ADMIN_PASSWORD` in `.env`. Leave it blank to disable the
admin page entirely. All admin state lives in memory and resets when the
server restarts.

## API

| Method | Route                | Description                                  |
| ------ | -------------------- | -------------------------------------------- |
| GET    | `/`                  | Single-page UI                               |
| GET    | `/admin`             | Admin dashboard                              |
| GET    | `/api/health`        | Lists configured providers                   |
| POST   | `/api/ask`           | `{ "question": "..." }` → all answers + best |
| POST   | `/api/admin/login`   | Body: `{ "password": "..." }`                |
| POST   | `/api/admin/logout`  | Clears admin cookie                          |
| GET    | `/api/admin/state`   | Providers, current judge, usage log          |
| POST   | `/api/admin/judge`   | Body: `{ "provider": "google" }`             |

Auto-generated API docs are available at `/docs`.

## Switching models

Edit `.env`:

```
GROQ_MODEL=llama-3.3-70b-versatile
TOGETHER_MODEL=meta-llama/Llama-3.3-70B-Instruct-Turbo
GOOGLE_MODEL=gemini-1.5-flash
JUDGE_PROVIDER=google
```
