# CLAUDE.md

Guidance for Claude (and other AI assistants) when working in this repository.

## Project summary

**Multiple AI** is a small, self-contained web app that takes one user question,
fans it out to several frontier LLMs in parallel, then uses an LLM judge to
pick the best response. It is intentionally a single deployable unit — Python
backend serves both the JSON API and the static single-page UI.

- **Backend:** FastAPI (async) on Python 3.10+
- **Frontend:** `frontend/index.html` (main UI) + `frontend/admin.html`
  (admin dashboard) — vanilla JS, no build step
- **Providers:** Anthropic (Claude), OpenAI (GPT-4o-mini), Google (Gemini)
- **Judge:** Anthropic (Claude) by default (configurable via `JUDGE_PROVIDER`)
- **"Best answer" strategy:** LLM-judge picks a winner and may synthesize an
  improved answer; falls back to longest non-error answer if the judge fails.
- **Admin:** `/admin` page (password from `ADMIN_PASSWORD` env var) shows
  provider status + masked API keys, lets you change the judge live, and
  displays an in-memory log of recent provider calls with token counts.

## Repository layout

```
Multiple AI/
├── backend/
│   ├── __init__.py
│   ├── main.py        # FastAPI app, routes, Pydantic schemas, admin routes
│   ├── providers.py   # GroqProvider / TogetherProvider / GoogleProvider
│   ├── judge.py       # judge() coroutine + JSON parsing + fallbacks
│   └── admin.py       # In-memory admin state (judge override, usage log)
├── frontend/
│   ├── index.html     # Single-page UI (HTML + CSS + JS in one file)
│   └── admin.html     # Admin dashboard (login + provider/usage views)
├── run.py             # uvicorn launcher
├── requirements.txt
├── .env.example       # Template; copy to .env
├── README.md          # User-facing setup/usage
└── CLAUDE.md          # (this file)
```

## How requests flow

1. Browser POSTs `{ "question": "..." }` to `/api/ask`.
2. `backend/main.py::ask` calls `providers.ask_all(question)`.
3. `ask_all` builds the list of *available* providers (those with API keys)
   and runs them concurrently via `asyncio.gather`.
4. Each provider returns a `ProviderAnswer` dataclass (text, model, latency,
   optional error string — providers never raise).
5. `judge.judge(question, answers)` sends all candidates to one provider with
   a strict JSON-only system prompt; the response is parsed with `_extract_json`
   (tolerates surrounding prose). On any failure, `_fallback_verdict` picks
   the longest non-error answer.
6. `main.py` shapes everything into `AskResponse` and returns it. The
   frontend renders a "best answer" card + one collapsible card per provider
   (the winner is open by default).

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env        # then fill in at least one API key
python run.py               # http://127.0.0.1:8000
```

Uvicorn runs with `reload=True`, so editing any backend file restarts the
server automatically. Frontend changes need only a browser refresh.

## Environment variables

All read in `providers.py` / `judge.py` via `os.getenv` after `load_dotenv()`
runs in `main.py`. Add new vars by editing both `.env.example` and the
relevant module.

| Variable           | Default                  | Purpose                                            |
| ------------------ | ------------------------ | -------------------------------------------------- |
| `ANTHROPIC_API_KEY`| —                        | Enables Claude provider                            |
| `OPENAI_API_KEY`   | —                        | Enables GPT provider                               |
| `GOOGLE_API_KEY`   | —                        | Enables Gemini provider                            |
| `ANTHROPIC_MODEL`  | `claude-sonnet-4-5`      | Override Claude model                              |
| `OPENAI_MODEL`     | `gpt-4o-mini`            | Override OpenAI model                              |
| `GOOGLE_MODEL`     | `gemini-1.5-flash`       | Override Gemini model                              |
| `JUDGE_PROVIDER`   | `anthropic`              | Which provider acts as judge                       |
| `ADMIN_PASSWORD`   | —                        | Password for `/admin`; blank disables admin        |

Missing keys never crash the app — that provider just isn't available.

## Conventions and patterns

- **Providers must not raise.** Every `ask()` returns a `ProviderAnswer`; on
  exception, set `.error` and leave `.answer` empty. The judge and the UI
  both rely on this contract.
- **`is_available()` gates registration.** `build_providers()` filters by
  this, so adding a provider with no key has zero runtime cost.
- **The judge must be defensive.** Models sometimes return prose around JSON.
  Always go through `_extract_json` and route any failure into
  `_fallback_verdict` rather than raising.
- **No frontend build step.** Keep `frontend/index.html` self-contained
  (inline CSS + inline JS). It's fine to add files, but don't introduce a
  bundler — this repo is meant to run with one `pip install` and one
  `python run.py`.
- **Async everywhere on the hot path.** Use the async SDK clients
  (`anthropic.AsyncAnthropic`, `openai.AsyncOpenAI`,
  `genai.GenerativeModel(...).generate_content_async`). Don't introduce
  blocking calls in request handlers.
- **Order of imports in `main.py` matters.** `load_dotenv()` is called
  *before* the `providers`/`judge` imports because those modules read
  `os.getenv` at construction time.

## Adding a new AI provider

1. Implement a class in `backend/providers.py` with: `name`, `label`, an
   `__init__` that reads the API key and model from env, `is_available()`,
   and an `async ask(question, system) -> ProviderAnswer`.
2. Add it to the candidate list in `build_providers()`.
3. Register it in `judge.provider_map` if you want it to be eligible as the
   judge.
4. Add the corresponding env keys to `.env.example`.
5. No frontend changes needed — the UI renders whatever providers the backend
   returns.

## Adding or changing API routes

- Add the route to `backend/main.py`. Define request/response shapes as
  Pydantic models in the same file (or a `schemas.py` if it grows).
- Auto docs at `/docs` will reflect the change.
- The frontend uses `fetch` against `/api/...`; update `index.html` if a new
  endpoint should be surfaced in the UI.

## Testing approach (when added)

There are no tests yet. If you add them, prefer `pytest` + `httpx.AsyncClient`
against the FastAPI app. Mock the SDK clients at the class level
(`AnthropicProvider._client`, `OpenAIProvider._client`, `GoogleProvider._ready`)
rather than monkey-patching the SDKs globally.

## Things to avoid

- Don't bake API keys into source files or commit a real `.env`.
- Don't introduce a frontend framework / bundler — it would break the
  "one command to run" promise.
- Don't make providers raise; always return a `ProviderAnswer` with `.error`.
- Don't call sync HTTP libraries (`requests`, `httpx.Client`) in handlers —
  it blocks the event loop and serializes provider calls.
- Don't hard-code model names in code paths outside `providers.py` — they
  belong in env vars so they can be swapped without a code change.

## Useful commands

```bash
# Compile-check the Python (fast smoke test, no deps needed)
python -m py_compile backend/main.py backend/providers.py backend/judge.py backend/admin.py run.py

# Run with auto-reload
python run.py

# Confirm which providers the backend sees
curl http://127.0.0.1:8000/api/health

# Ask a question from the CLI
curl -X POST http://127.0.0.1:8000/api/ask \
     -H "Content-Type: application/json" \
     -d '{"question":"Explain the Monty Hall problem."}'
```
