# Contributing

Issues and focused pull requests are welcome. Before starting a larger change, open an issue so the
behavior and data model can be agreed on first.

Signal uses Python 3.13, uv and SurrealDB 3. Install the development environment with:

```bash
uv sync --frozen --group dev
```

Before opening a pull request, run:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
node --test tests/*.test.cjs
uv build
```

Integration tests are opt-in because they need a running SurrealDB instance:

```bash
SIGNAL_INTEGRATION=1 uv run pytest
```

Do not include `.env`, provider credentials, personal source data or `.signal/` contents. Follow
the architecture and testing notes in `AGENTS.md`. By contributing, you agree that your work is
licensed under the MIT License.
