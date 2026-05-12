# Multiple AI

Ask one question, get answers from **Groq** and **Ollama** in parallel — then have **Gemini** judge the answers and pick the best one.

```
            ┌─────────────────────────────┐
your        │   FastAPI backend (Python)  │
question ─► │   ├─ /api/ask  (fan-out)    │ ─► single best answer + rationale
            │   ├─ providers.py           │
            │   └─ judge.py               │
            └──────────┬──────────────────┘
                       │ parallel async calls
              ┌────────┴────────┐
              ▼                 ▼
            Groq             Ollama
           (Llama)          (local LLM)
                     ▲
                  Google Gemini
                  (judge only)
```

## Project layout

```
Multiple AI/
├── backend/
│   ├── __init__.py
│   ├── main.py        # FastAPI app, routes, schemas
│   ├── providers.py   # Async clients for Groq / Ollama / Google
│   ├── judge.py       # Gemini scores and picks the best answer
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
# Edit .env and fill in:
#   GROQ_API_KEY=...
#   GOOGLE_API_KEY=...

# 3. (Optional) Run Ollama locally
#    Install from https://ollama.com, then:
ollama serve
ollama pull llama3.2

# 4. Run
python run.py
```

Then open **http://127.0.0.1:8000**.

Ollama is optional — if it's not running it's silently skipped. Groq and Gemini only need their API keys.

### Where to get the keys

- **Groq** — https://console.groq.com (free tier, generous rate limits)
- **Google Gemini** — https://aistudio.google.com/app/apikey (free tier)
- **Ollama** — https://ollama.com (free, runs locally, no key needed)

## How "best answer" is picked

After both providers respond, Gemini reads all answers and:

1. Scores each answer 1–10 across 5 dimensions (accuracy, completeness, clarity, conciseness, helpfulness)
2. Picks the strongest answer (`winner`)
3. Explains the choice in 2–3 sentences (`rationale`)
4. Produces an improved, synthesized version combining the best parts (`synthesized`)

If JSON parsing fails or the judge errors out, the app falls back to the longest non-error answer.

## Admin page

Visit **http://127.0.0.1:8000/admin** to:

- See which providers are active and toggle them on/off
- Switch the judge provider live without editing `.env`
- View token-usage totals and charts per provider
- See a log of recent requests with latency and token counts
- Change the admin password from the UI
- Clear usage history

Admin is gated by `ADMIN_PASSWORD` in `.env`. Leave it blank to disable admin entirely.

## API

| Method | Route                          | Description                                  |
| ------ | ------------------------------ | -------------------------------------------- |
| GET    | `/`                            | Single-page UI                               |
| GET    | `/admin`                       | Admin dashboard                              |
| GET    | `/api/health`                  | Lists configured providers                   |
| POST   | `/api/ask`                     | `{ "question": "..." }` → all answers + best |
| POST   | `/api/admin/login`             | Body: `{ "password": "..." }`                |
| POST   | `/api/admin/logout`            | Clears admin cookie                          |
| GET    | `/api/admin/state`             | Providers, judge, usage log                  |
| POST   | `/api/admin/judge`             | Body: `{ "provider": "google" }`             |
| POST   | `/api/admin/provider-toggle`   | Body: `{ "name": "groq", "enabled": false }` |
| POST   | `/api/admin/clear-usage`       | Clears in-memory usage log                   |
| POST   | `/api/admin/change-password`   | Body: `{ "current_password", "new_password"}`|

Auto-generated API docs at `/docs`.

## Environment variables

```
GROQ_API_KEY=...
GOOGLE_API_KEY=...

OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2

GROQ_MODEL=llama-3.3-70b-versatile
GOOGLE_MODEL=gemini-2.5-flash

JUDGE_PROVIDER=google

ADMIN_PASSWORD=your_password_here
```
