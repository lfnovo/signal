# ϟ Signal

[![CI](https://github.com/lfnovo/signal/actions/workflows/ci.yml/badge.svg)](https://github.com/lfnovo/signal/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Container](https://img.shields.io/badge/container-ghcr.io%2Flfnovo%2Fsignal-blue.svg)](https://github.com/lfnovo/signal/pkgs/container/signal)

A home for your links, files and rabbit holes. Signal extracts the full story, writes the short version and lets you chat with every find. It is a single-user Python app with a dark, keyboard-friendly interface and an MCP server for your AI agents.

## Get started

The quickest hosted installation uses Docker Compose. You need Docker, a public HTTPS URL and an AI provider key:

```bash
git clone https://github.com/lfnovo/signal.git
cd signal
cp .env.example .env
# Edit .env: set your provider key, SIGNAL_PASSWORD, SIGNAL_API_URL and
# SIGNAL_ALLOWED_HOSTS, then start the released image and SurrealDB.
docker compose up -d
```

Open the URL configured in `SIGNAL_API_URL`. Compose keeps application uploads and the database in named volumes. See the [deployment guide](docs/deployment.md) for reverse proxy, upgrades, backups and source installs.

For Portainer, deploy [`compose.portainer.yaml`](compose.portainer.yaml) as a Stack and configure its
environment variables in the Portainer UI. It pulls the released GHCR image directly and contains no
local build step. See the [Portainer instructions](docs/deployment.md#portainer) for the required values.

For local development, install [uv](https://docs.astral.sh/uv/) and SurrealDB **3.x**. The defaults expect SurrealDB at `127.0.0.1:8019` with namespace/database `signal` and `root` / `root` credentials.

```bash
uv sync --frozen --group dev
cp .env.example .env
# Add your provider key, then:
uv run signal serve
```

Open **http://127.0.0.1:8020**. The server also processes your inbox in the background.

For a hosted single-user instance, set `SIGNAL_API_URL` to its public HTTPS origin, choose a long `SIGNAL_PASSWORD`, set `SIGNAL_HOST=0.0.0.0`, and place Signal behind a TLS reverse proxy. The same password signs you into the web app and approves agent connections; agents never receive it. Keep SurrealDB private to the host or Compose network.

In another terminal, from the project directory:

```bash
uv run signal https://example.com
uv run signal ~/Documents/article.pdf
uv run signal './my notes.md'
```

The CLI saves your find and waits for processing. It also works without the web server: the CLI consumes the queue itself. To save without waiting:

```bash
uv run signal https://example.com --enqueue
# Start the web app or a worker to process the queue:
uv run signal worker
```

uv manages Python and dependencies. No manual environment activation needed. From another directory, use `uv run --project /path/to/signal signal …`.

## Pick your AI setup

Open **Preferences** in the inbox:

- **Summary & chat language** controls generated content. Your current setting is Portuguese; the interface always uses English.
- **Summaries & chat** selects the language provider and model.
- **Embeddings** selects the provider and model for new source vectors.
- **Web pages** and **Documents & PDF links** select extraction engines, including Docling for layout, tables and OCR.
- **Audio & video transcription** selects its own provider and model. FFmpeg is required for media files; YouTube may use existing captions.
- **Fetch model suggestions** asks the selected provider for its model list. You can also enter a model or deployment ID manually.

Save your preferences and they apply to the next capture or message, with no restart. Settings are stored in SurrealDB and override the corresponding `.env` defaults. In-progress jobs keep their starting configuration. Existing summaries stay intact; retrieval for an existing source keeps using its original embedding model.

On a source page, click **Reprocess** beside **The Full Story** to reprocess with a different engine or transcription model. Signal rebuilds the summary and embeddings, keeps the previous content until success, and archives earlier chats separately. Docling is available through the optional `docling` dependency group; its first run may download models. See [engine setup](docs/configuration.md#arquivos-e-extração) for external services.

Processing failures show the failed stage and error message on the source page; server logs include the traceback and configured provider/model, with known secrets masked.

API keys stay in `.env` and are never sent to the browser. The provider selector shows whether a key is present; that does not verify credits or access to a particular model. Restart the app after changing keys or connection settings in `.env`.

The initial defaults are `google` / `gemini-2.5-flash` for language and `google` / `gemini-embedding-001` for embeddings, using `GEMINI_API_KEY` or `GOOGLE_API_KEY`.

## Review before you keep

Every capture starts in **Inbox**, your triage queue. On a source page, choose **Accept into Library** to keep it, **Move back to Inbox** to reconsider, or **Delete** for confirmed permanent removal. Deletion removes the stored file copy, extracted content, summary, vectors and chat; global topics and the original file stay intact. Existing sources start in Inbox.

Both lists display topic links. A topic page includes Inbox and Library finds with their collection identified. Processing status remains separate from your triage decision.

## Find your way back

Press **/** or use the global search field. Typing suggests keyword matches; Enter opens full **Hybrid** search across words and meaning. Choose **Keywords** for exact phrases in quotes or **Semantic** for ideas expressed in different words. Sources appear once with a relevant excerpt, and topics can match by name or related meaning.

Filter by Inbox/Library, Focus, type or topic. Chats are excluded. Existing embedding models remain respected; a provider outage yields partial results with a notice.

## Keep your hands on the keyboard

Use **1–4** for Inbox, Library, Focus and Topics; **↑/↓** to select; **Space** for a quick summary preview; **Enter** to open. **I / L / F** move to Inbox, move to Library or toggle Focus. **Cmd/Ctrl + Delete/Backspace** asks to delete. In source chat, **Option/Alt + Enter** sends the question. Press **?** for help and to disable single-key shortcuts. Single-key shortcuts pause while typing.

## Give a find your attention

Tap **☆ Focus** on a source to mark it for a closer look. **Focus** in the menu brings together marked finds from Inbox and Library. Moving between collections keeps the mark; tapping again removes it without deleting the find.

## Connect AI agents

Signal exposes the same MCP server over Streamable HTTP and stdio. A remote client connects to `https://your-signal-host/mcp`, discovers OAuth automatically and opens Signal in the browser for approval. Each client gets its own rotating tokens; revoke one at any time from **Connections** without changing the Signal password.

For a local agent, configure its stdio command as:

```bash
uv run --project /path/to/signal signal mcp
```

Agents can capture URLs and files, browse or search Inbox/Library/Focus, read and chat with sources, triage and rename them, and work with topic context. Permanent deletion is intentionally absent from the first MCP tool set. Resources expose individual sources, previews, topics and the three collection views.

## Save from your iPhone

Open **Connections** and create a capture token named “iPhone”. It is shown once and can only
submit URLs; revoke it there at any time. In Apple Shortcuts, create a Share Sheet shortcut that
posts the shared URL to `/api/sources` with the token in the Authorization header. The response
confirms whether the link was newly saved or already existed. Follow the
[iPhone shortcut setup](docs/iphone-shortcut.md); the same steps are available in Connections.

## Follow the topics

**Topics** in the menu groups your finds into official topics (green) and AI suggestions (violet). Each generated summary also proposes 3–5 relevant topics, reusing your official vocabulary when it fits. Approve, rename, merge or delete topics across the library, or edit associations directly on a source. Topics you create are official; manual choices survive regeneration.

Click **Regenerate** beside **The Short Version** to refresh a summary and its topics. Add an optional nudge—“focus on practical applications,” for example. The previous summary stays available until the new one is ready. Existing finds get topics when you regenerate or reprocess them.

## What’s inside

- URL and file capture from the CLI or web app; a Chrome extension for the current tab.
- A persistent queue, processing states, crash recovery and retryable failures.
- Deduplication by normalized URL or file hash, preserving the existing entry.
- Extraction through [content-core](https://github.com/lfnovo/content-core).
- Summaries and source chat through [Esperanto](https://github.com/lfnovo/esperanto), with versioned prompts rendered by [AI Prompter](https://github.com/lfnovo/ai-prompter).
- Separate content and summary embeddings stored in SurrealDB.
- A chronological inbox and source pages with full content, summaries and cited chat answers.
- Persistent chat history for each source, with a clear-history action.
- An MCP server for local stdio clients and OAuth-protected remote HTTP clients.
- English UI with rotating hints, while source content and AI output retain their own language.
- A bundled JetBrains Mono font. No frontend CDN or Node build required.

## Chrome extension

1. Open `chrome://extensions` and enable **Developer mode**.
2. Click **Load unpacked** and select this project’s `extension/` directory.
3. Keep `uv run signal serve` running, open an HTTP/HTTPS page and click the extension.
4. Click **Save it**.

The default address is `http://127.0.0.1:8020`. Use the extension’s **Settings** page to change the local port. It sends only the URL, not browser cookies or the authenticated page contents. After updating extension files, click **Reload** in Chrome’s extensions page.

## Development

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
# Real SurrealDB 3 integration, without LLM charges:
SIGNAL_INTEGRATION=1 uv run pytest
node --test tests/extension.test.cjs tests/frontend.test.cjs
```

Integration tests create and remove only temporary `signal_test_*` databases in the `signal` namespace. Your library is preserved. `uv.lock` pins dependencies.

[Detailed documentation](docs/README.md): installation and deployment, configuration, architecture, API and verification. The detailed guides are currently in Portuguese.

## Releases and container images

Every published GitHub release builds signed metadata, provenance and an SBOM for a multi-platform image at `ghcr.io/lfnovo/signal`. Stable releases publish `latest`, the full semantic version and the matching major/minor tag. Pull requests and changes to `main` run Python/Node tests, package builds and a Docker build before release.

Signal is available under the [MIT License](LICENSE). Contributions are described in [CONTRIBUTING.md](CONTRIBUTING.md); security reports should follow [SECURITY.md](SECURITY.md).

### Inbox triage

The Inbox keeps capture within reach and places a compact list beside a reading panel. Select a find to review its summary and edit topics inline. Library and confirmed deletion advance to the next item; Focus remains an independent bookmark. Open full page retains the complete reading and chat experience. Selection is remembered within the browser tab. Arrow keys browse, Left/Right move between the list and actions, and existing triage shortcuts remain available. On small screens the panels stack.

### Topics that know what you’re looking for

Topics opens with **Overview**, a map of clusters formed by shared finds. Select a cluster to inspect its topics and strongest connections, or select a topic to open its context and finds. Search highlights topics, zoom reveals smaller labels, and topics without connections remain available below the map. Green and violet still mean official and suggested. The map starts with Library, with an option to include Inbox.

Switch to **Topics** for instant search, Official/Suggested filters, inline context, and the graph centered on an individual topic. Click the topic name or either context field to edit it in place. **Merge** and **Delete** remain beside the name and require confirmation. Use **Sort by → Most links & files** or **Fewest links & files** to order by the count in the current Library/Include Inbox scope. Select a connection to inspect its shared finds; source previews keep reading and topic review on the same page.

Use **What belongs here** to define a topic and **Why this matters to me** for your current research questions. Official context informs future tagging and chat. **Why this might matter to you** shows a separate AI interpretation, keeping the factual summary intact. Existing finds can generate or refresh that interpretation without re-extraction; changes to topic context do not trigger bulk processing.


### Edit without leaving the page

Click a topic name, its definition or personal context, or a source title to edit in place. Source titles are editable in Inbox and topic previews as well as the full page. **Enter** saves a name/title; **Cmd/Ctrl + Enter** saves a multiline field; **Esc** or **Cancel** discards that field’s draft. Saving one context field leaves the other alone. Errors keep your draft available to retry. Custom source titles survive extraction, retries and reprocessing.


### A breather between YouTube videos

YouTube extractions use a random **3–5 minute cooldown after each attempt**, including failures. The first eligible video starts immediately. While videos wait, the worker processes other links and files in queue order. The cooldown is saved in SurrealDB and shared by the web app, extension and CLI worker; restarting Signal or retrying a video does not reset it.

Already-extracted transcripts can continue through summaries and embeddings without waiting. Summary regeneration also proceeds normally; a full YouTube re-extraction waits. Waiting videos display **YouTube cooldown** and remain in the queue. This reduces bursts of requests but does not guarantee that YouTube will avoid or lift a block.
