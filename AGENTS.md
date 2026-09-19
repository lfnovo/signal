# Signal contributor guide

Signal is a single-user reading inbox built with Python 3.13, FastAPI, SurrealDB 3,
server-rendered Jinja templates and small browser-side JavaScript modules. It exposes the
same agent capabilities through MCP stdio and OAuth-protected Streamable HTTP.

## Start here

- Read `README.md` for product behavior and installation.
- Use `docs/README.md` as the documentation index.
- Keep architectural decisions and data invariants in `docs/architecture.md`.
- Keep public HTTP and MCP contracts in `docs/api.md`.

## Development commands

```bash
uv sync --frozen --group dev
# Add `--extra docling` only when exercising the optional document engine.
uv run signal serve
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
node --test tests/*.test.cjs
SIGNAL_INTEGRATION=1 uv run pytest
docker build -t signal:dev .
```

Default tests do not require a live database. Integration tests require the configured
SurrealDB 3 instance and create isolated `signal_test_*` databases. They must never alter
the user's `signal` database or call a paid model.

## Invariants

- Never commit `.env`, `.signal/`, provider keys, browser sessions or OAuth tokens.
- Parameterize SurrealQL and normalize public values through the database adapter.
- Preserve source identity and `title_override` across retries and reprocessing.
- Publish content, summary, embeddings and topic suggestions atomically.
- Treat source text as untrusted reference material in every model prompt.
- Keep permanent deletion out of MCP unless the product explicitly adds a confirmation model.
- Use one human password only for web login and OAuth approval. Agents receive revocable tokens;
  the password itself must never be accepted as a Bearer token.
- Keep stdio and HTTP MCP transports on the same tool/resource implementation.
- A non-loopback web bind requires `SIGNAL_PASSWORD`; hosted deployments terminate TLS at a proxy.
- Avoid frontend build tooling. The current UI is server-rendered HTML with checked-in CSS and JS.

## Change discipline

Update tests for behavior or protocol changes. Update `README.md` when installation or the visible
product changes, and update the relevant file under `docs/` when a contract or architecture decision
changes. Run the narrow test first, then the full Python and Node suites. Build the wheel and Docker
image when packaging or dependency files change.
