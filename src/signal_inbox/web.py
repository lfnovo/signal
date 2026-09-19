"""Local FastAPI application and HTTP intake for the Chrome extension."""

import asyncio
import re
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .ai import AI
from .auth import SESSION_COOKIE, AuthManager, SignalOAuthProvider
from .config import EXTRACTION_KEYS, Settings
from .database import Database, ensure_record_id, public
from .extraction import ENGINE_CHOICES, validate_engines
from .intake import add_file, add_url
from .mcp_server import build_mcp_server
from .providers import discover_models, provider_choices, validate_provider
from .search import Search
from .topic_context import context_signature
from .topics import Topics
from .worker import safe_error, supervise_worker

BASE = Path(__file__).parent


def youtube_video_id(url: str) -> str | None:
    """Recognize video URLs without allowing arbitrary iframe origins."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        host = parsed.hostname
        parts = parsed.path.strip("/").split("/")
        video = None
        if host in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
            video = parts[0]
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if parsed.path == "/watch":
                video = parse_qs(parsed.query).get("v", [None])[0]
            elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
                video = parts[1]
        return video if video and re.fullmatch(r"[A-Za-z0-9_-]{11}", video) else None
    except ValueError:
        return None


class URLInput(BaseModel):
    url: str = Field(min_length=1, max_length=8192)


class SourceTitleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=500)


class ChatInput(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class TopicContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: str = Field(default="", max_length=12000)
    personal_context: str = Field(default="", max_length=24000)


class PreferencesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str | None = Field(default=None, min_length=1, max_length=80)
    llm_provider: str | None = Field(default=None, min_length=1, max_length=80)
    llm_model: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_provider: str | None = Field(default=None, min_length=1, max_length=80)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=200)
    url_engine: str | None = Field(default=None, min_length=1, max_length=30)
    document_engine: str | None = Field(default=None, min_length=1, max_length=30)
    stt_provider: str | None = Field(default=None, min_length=1, max_length=80)
    stt_model: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator(
        "language",
        "llm_provider",
        "llm_model",
        "embedding_provider",
        "embedding_model",
        "url_engine",
        "document_engine",
        "stt_provider",
        "stt_model",
        mode="before",
    )
    @classmethod
    def nonblank(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("This field cannot be empty.")
        return value.strip()


class ReprocessInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url_engine: str | None = Field(default=None, min_length=1, max_length=30)
    document_engine: str | None = Field(default=None, min_length=1, max_length=30)
    stt_provider: str | None = Field(default=None, min_length=1, max_length=80)
    stt_model: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("*", mode="before")
    @classmethod
    def nonblank(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("This field cannot be empty.")
        return value.strip()


class FocusInput(BaseModel):
    focused: bool = Field(strict=True)


class CollectionInput(BaseModel):
    collection: Literal["inbox", "library"]


class DeleteSourceInput(BaseModel):
    confirm: Literal[True]


class TopicInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)


class MergeInput(BaseModel):
    target: str = Field(min_length=1, max_length=100)


class RegenerateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steering: str = Field(default="", max_length=2000)


def create_app(settings=None, *, start_worker=True, database=None, ai=None):
    settings = settings or Settings.load()
    db = database or Database(settings)
    intelligence = ai or AI(settings)
    topics = Topics(db)
    search_service = Search(db, intelligence)
    chat_locks = {}
    public_url = settings.api_url.rstrip("/")
    public_parts = urlsplit(public_url)
    auth_manager = (
        AuthManager(settings.password, settings.data_dir, public_parts.scheme == "https")
        if settings.password
        else None
    )
    oauth_provider = (
        SignalOAuthProvider(db, public_url, public_url + "/mcp") if auth_manager else None
    )
    mcp_server = build_mcp_server(
        db,
        intelligence,
        issuer=public_url if oauth_provider else None,
        oauth_provider=oauth_provider,
    )
    public_host = public_parts.netloc or "127.0.0.1:8020"
    mcp_http = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        host=public_parts.hostname or "127.0.0.1",
        transport_security=TransportSecuritySettings(
            allowed_hosts=list(
                dict.fromkeys(
                    [public_host, public_parts.hostname or "127.0.0.1", *settings.allowed_hosts]
                )
            ),
            allowed_origins=[public_url],
        ),
    )

    @asynccontextmanager
    async def lifespan(app):
        await db.initialize()
        task = asyncio.create_task(supervise_worker(db, intelligence)) if start_worker else None
        async with mcp_server.session_manager.run():
            yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await intelligence.close()

    app = FastAPI(title="Signal", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.db = db
    app.state.mcp_server = mcp_server
    app.state.oauth_provider = oauth_provider
    allowed_hosts = ["localhost", "127.0.0.1", "[::1]", "testserver", *settings.allowed_hosts]
    if public_parts.hostname:
        allowed_hosts.append(public_parts.hostname)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(dict.fromkeys(allowed_hosts)))
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"chrome-extension://[a-p]{32}|http://(localhost|127\.0\.0\.1)(:[0-9]+)?",
        allow_methods=["GET", "POST", "DELETE", "PUT", "PATCH", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id", "WWW-Authenticate"],
    )

    @app.middleware("http")
    async def local_requests(request, call_next):
        path = request.url.path
        mcp_public = (
            path == "/mcp"
            or path.startswith("/.well-known/")
            or path in {"/authorize", "/token", "/register", "/revoke"}
            or path.startswith("/oauth/")
        )
        origin = request.headers.get("origin")
        if (
            origin
            and not mcp_public
            and not re.fullmatch(
                r"chrome-extension://[a-p]{32}|http://(localhost|127\.0\.0\.1)(:[0-9]+)?", origin
            )
        ):
            return JSONResponse({"detail": "This origin is not allowed."}, status_code=403)
        length = request.headers.get("content-length", "0")
        if length.isdigit() and int(length) > settings.max_upload_bytes + 1024 * 1024:
            return JSONResponse({"detail": "This file is over the 100 MB limit."}, status_code=413)
        public_web = path == "/login" or path == "/api/health" or path.startswith("/static/")
        if auth_manager and not (mcp_public or public_web):
            if not auth_manager.valid_session(request.cookies.get(SESSION_COOKIE)):
                if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                    target = path + ("?" + request.url.query if request.url.query else "")
                    return RedirectResponse("/login?next=" + quote(target), status_code=303)
                return JSONResponse({"detail": "Authentication required."}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "frame-src https://www.youtube-nocookie.com; "
            "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    templates = Jinja2Templates(directory=BASE / "templates")
    markdown = MarkdownIt("commonmark", {"html": False})
    templates.env.filters["markdown"] = lambda text: Markup(markdown.render(text or ""))
    templates.env.filters["date"] = lambda value: str(value)[:16].replace("T", " ")
    templates.env.globals["auth_enabled"] = bool(auth_manager)
    # Change asset URLs whenever content changes, avoiding stale native-looking selects.
    templates.env.globals["asset_url"] = lambda name: (
        f"/static/{name}?v={(BASE / 'static' / name).stat().st_mtime_ns}"
    )
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

    def safe_next(value: str | None):
        return value if value and value.startswith("/") and not value.startswith("//") else "/"

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/"):
        if not auth_manager:
            return RedirectResponse(safe_next(next), status_code=303)
        if auth_manager.valid_session(request.cookies.get(SESSION_COOKIE)):
            return RedirectResponse(safe_next(next), status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"next": safe_next(next), "error": ""},
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request, password: str = Form(), next: str = Form(default="/")):
        address = request.client.host if request.client else "unknown"
        if auth_manager and not auth_manager.can_attempt(address):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "next": safe_next(next),
                    "error": "Too many attempts. Try again in a minute.",
                },
                status_code=429,
            )
        if not auth_manager or not auth_manager.check_password(password):
            if auth_manager:
                auth_manager.record_failure(address)
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"next": safe_next(next), "error": "That password did not match."},
                status_code=401,
            )
        auth_manager.record_success(address)
        response = RedirectResponse(safe_next(next), status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            auth_manager.create_session(),
            max_age=30 * 24 * 60 * 60,
            httponly=True,
            secure=auth_manager.secure_cookie,
            samesite="lax",
        )
        return response

    @app.post("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/oauth/approve", response_class=HTMLResponse)
    async def oauth_approve_page(request: Request, request_id: str = Query(alias="request")):
        if not oauth_provider or not auth_manager:
            raise HTTPException(404, "MCP authorization is not enabled.")
        if not auth_manager.valid_session(request.cookies.get(SESSION_COOKIE)):
            return RedirectResponse(
                "/login?next=" + quote("/oauth/approve?request=" + request_id), status_code=303
            )
        pending = await oauth_provider.pending(request_id)
        if not pending:
            raise HTTPException(400, "This authorization request expired.")
        row, client = pending
        return templates.TemplateResponse(
            request=request,
            name="oauth_approve.html",
            context={
                "request_id": request_id,
                "client_name": client.client_name or "An AI agent",
                "client_uri": str(client.client_uri) if client.client_uri else "",
                "scopes": row["params"].get("scopes") or ["signal"],
            },
        )

    @app.post("/oauth/approve")
    async def oauth_approve(
        request: Request,
        request_id: str = Form(),
        decision: Literal["approve", "deny"] = Form(),
    ):
        if not oauth_provider or not auth_manager:
            raise HTTPException(404, "MCP authorization is not enabled.")
        if not auth_manager.valid_session(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Sign in before approving a connection.")
        target = await oauth_provider.finish_authorization(request_id, decision == "approve")
        if not target:
            raise HTTPException(400, "This authorization request expired.")
        return RedirectResponse(target, status_code=303)

    @app.get("/connections", response_class=HTMLResponse)
    async def connections(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="connections.html",
            context={"connections": await oauth_provider.connections() if oauth_provider else []},
        )

    @app.post("/connections/{pair_id}/revoke")
    async def revoke_connection(pair_id: str):
        if oauth_provider:
            await oauth_provider.revoke_connection(pair_id)
        return RedirectResponse("/connections", status_code=303)

    async def source_or_404(identifier):
        if not re.fullmatch("[a-f0-9]{64}", identifier):
            raise HTTPException(404, "Source not found.")
        source = await db.get(identifier)
        if not source:
            raise HTTPException(404, "Source not found.")
        return source

    def lock(identifier):
        return chat_locks.setdefault(identifier, asyncio.Lock())

    def capture_response(source, created):
        # Duplicate captures should not send a whole document and its vectors to the popup.
        return {
            "source": public(
                {key: source[key] for key in ("id", "title", "status", "stage", "kind", "original")}
            ),
            "created": created,
        }

    @app.exception_handler(ValueError)
    async def invalid_input(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/search/suggest")
    async def search_suggest(q: str = Query(default="", max_length=500)):
        result = await search_service.run(q, mode="keywords", limit=5, partial=True)
        return {"sources": result["sources"], "topics": result["topics"][:5]}

    @app.get("/api/search")
    async def search_api(
        q: str = Query(default="", max_length=500),
        mode: Literal["hybrid", "keywords", "semantic"] = "hybrid",
        collection: Literal["", "inbox", "library"] = "",
        focused: bool = False,
        kind: Literal["", "url", "file"] = "",
        topic: str = Query(default="", max_length=100),
        page: int = Query(default=1, ge=1),
    ):
        return await search_service.run(q, mode, collection, focused, kind, topic, page)

    @app.get("/search", response_class=HTMLResponse)
    async def search_page(
        request: Request,
        q: str = Query(default="", max_length=500),
        mode: Literal["hybrid", "keywords", "semantic"] = "hybrid",
        collection: Literal["", "inbox", "library"] = "",
        focused: bool = False,
        kind: Literal["", "url", "file"] = "",
        topic: str = Query(default="", max_length=100),
        page: int = Query(default=1, ge=1),
    ):
        result = await search_service.run(q, mode, collection, focused, kind, topic, page)
        from urllib.parse import urlencode

        filters = {
            "q": q,
            "mode": mode,
            "collection": collection,
            "focused": str(focused).lower(),
            "kind": kind,
            "topic": topic,
        }
        return templates.TemplateResponse(
            request=request,
            name="search.html",
            context={
                **result,
                **filters,
                "only_focus": focused,
                "page": page,
                "all_topics": public(await topics.list()),
                "previous_url": "/search?" + urlencode(filters | {"page": page - 1}),
                "next_url": "/search?" + urlencode(filters | {"page": page + 1}),
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def inbox(request: Request, page: int = 1):
        page = max(1, page)
        sources = await db.sources(offset=(page - 1) * 50, limit=51, collection="inbox")
        return templates.TemplateResponse(
            request=request,
            name="inbox.html",
            context={
                "sources": public(sources[:50]),
                "page": page,
                "has_next": len(sources) > 50,
                "preferences": await db.preferences(),
                "providers": provider_choices(),
                "engines": ENGINE_CHOICES,
            },
        )

    @app.get("/library", response_class=HTMLResponse)
    async def library(request: Request, page: int = 1):
        page = max(1, page)
        sources = await db.sources(offset=(page - 1) * 50, limit=51, collection="library")
        return templates.TemplateResponse(
            request=request,
            name="library.html",
            context={
                "sources": public(sources[:50]),
                "page": page,
                "has_next": len(sources) > 50,
            },
        )

    @app.get("/focus", response_class=HTMLResponse)
    async def focus(request: Request, page: int = 1):
        page = max(1, page)
        sources = await db.sources(offset=(page - 1) * 50, limit=51, focused=True)
        return templates.TemplateResponse(
            request=request,
            name="focus.html",
            context={
                "sources": public(sources[:50]),
                "page": page,
                "has_next": len(sources) > 50,
            },
        )

    @app.get("/sources/{identifier}", response_class=HTMLResponse)
    async def detail(request: Request, identifier: str):
        source = await source_or_404(identifier)
        source["relevance_stale"] = source.get("relevance_signature") != context_signature(
            await topics.contexts()
        )
        history = public(await db.history(identifier))
        revision = source.get("revision", 0)
        preferences = await db.preferences()
        return templates.TemplateResponse(
            request=request,
            name="source.html",
            context={
                "source": public(source),
                "youtube_video_id": youtube_video_id(source.get("original", ""))
                if source.get("kind") == "url"
                else None,
                "history": [turn for turn in history if turn.get("revision", 0) == revision],
                "archived_history": [
                    turn for turn in history if turn.get("revision", 0) != revision
                ],
                "engines": ENGINE_CHOICES,
                "source_topics": public(await topics.for_source(identifier)),
                "all_topics": public(await topics.list()),
                "providers": provider_choices(),
                "extraction": {key: preferences[key] for key in EXTRACTION_KEYS}
                | source.get("reprocess_options", {}),
            },
        )

    @app.get("/api/health")
    async def health():
        await db.preferences()
        return {"status": "ok", "database": settings.database}

    @app.get("/api/navigation-counts")
    async def navigation_counts():
        result = {}
        for name, table, condition in (
            ("inbox", "source", "collection != 'library'"),
            ("library", "source", "collection = 'library'"),
            ("focus", "source", "focused = true"),
            ("topics", "topic", "deleted != true"),
        ):
            rows = await db.query(
                f"SELECT count() AS total FROM {table} WHERE {condition} GROUP ALL"
            )
            result[name] = rows[0]["total"] if rows else 0
        return result

    @app.get("/api/sources")
    async def list_sources(
        offset: int = 0,
        limit: int = 100,
        collection: Literal["inbox", "library"] | None = None,
        focused: bool | None = None,
    ):
        return public(
            await db.sources(max(0, offset), min(100, max(1, limit)), collection, focused)
        )

    @app.post("/api/sources", status_code=202)
    async def capture(body: URLInput):
        source, created = await add_url(db, body.url)
        return capture_response(source, created)

    @app.post("/api/sources/upload", status_code=202)
    async def upload(file: Annotated[UploadFile, File()]):
        try:
            source, created = await add_file(db, file.file, file.filename or "file")
            return capture_response(source, created)
        finally:
            await file.close()

    @app.get("/api/sources/{identifier}/preview")
    async def preview_source(identifier: str):
        source = await source_or_404(identifier)
        summary = source.get("summary", "").strip()
        text = summary or source.get("content", "")
        return {
            "title": source["title"],
            "kind": "summary" if summary else "content",
            "html": markdown.render(text[:16000]),
            "truncated": len(text) > 16000,
            "youtube_video_id": youtube_video_id(source.get("original", ""))
            if source.get("kind") == "url"
            else None,
        }

    @app.get("/sources/{identifier}/triage", response_class=HTMLResponse)
    async def triage_preview(request: Request, identifier: str):
        source = public(await source_or_404(identifier))
        source["relevance_stale"] = source.get("relevance_signature") != context_signature(
            await topics.contexts()
        )
        return templates.TemplateResponse(
            request=request,
            name="triage_preview.html",
            context={
                "source": source,
                "source_topics": public(await topics.for_source(identifier)),
                "all_topics": public(await topics.list()),
                "youtube_video_id": youtube_video_id(source.get("original", ""))
                if source.get("kind") == "url"
                else None,
            },
        )

    @app.get("/api/sources/{identifier}")
    async def get_source(identifier: str):
        source = public(await source_or_404(identifier))
        source.pop("chunks", None)
        source.pop("summary_embedding", None)
        source.pop("file_path", None)
        source.pop("processing_draft", None)
        return source

    @app.put("/api/sources/{identifier}/title")
    async def rename_source(identifier: str, body: SourceTitleInput):
        async with lock(identifier):
            source = await source_or_404(identifier)
            saved = await db.update(
                identifier,
                {"title": body.title, "title_override": body.title},
                expected_created_at=source["created_at"],
            )
            if not saved:
                raise HTTPException(409, "This find changed. Reload and try again.")
        return {"title": saved["title"]}

    @app.put("/api/sources/{identifier}/focus")
    async def focus_source(identifier: str, body: FocusInput):
        async with lock(identifier):
            await source_or_404(identifier)
            await db.update(identifier, {"focused": body.focused})
        return {"focused": body.focused}

    @app.put("/api/sources/{identifier}/collection")
    async def move_source(identifier: str, body: CollectionInput):
        async with lock(identifier):
            await source_or_404(identifier)
            await db.update(identifier, {"collection": body.collection})
        return {"collection": body.collection}

    @app.delete("/api/sources/{identifier}")
    async def delete_source(identifier: str, body: DeleteSourceInput):
        async with lock(identifier):
            source = await source_or_404(identifier)
            # Only remove the app-owned copy; never follow an arbitrary stored path.
            stored = Path(source["file_path"]) if source.get("file_path") else None
            if stored and stored.resolve().parent != (settings.data_dir / "uploads").resolve():
                raise HTTPException(400, "The stored file is outside Signal’s uploads folder.")
            # Quarantine under a unique name before deleting the record. A recapture
            # can then use the original filename without racing the final unlink.
            quarantined = None
            if stored and stored.exists():
                quarantined = stored.with_name(".delete-" + uuid.uuid4().hex)
                stored.rename(quarantined)
            try:
                await topics.transaction(
                    "DELETE conversation WHERE source = $source; DELETE source_topic WHERE source = $source; DELETE $source",
                    {"source": ensure_record_id(identifier)},
                )
            except BaseException:
                if quarantined:
                    quarantined.rename(stored)
                raise
            if quarantined:
                quarantined.unlink(missing_ok=True)
        return {"deleted": True}

    @app.post("/api/sources/{identifier}/reprocess", status_code=202)
    async def reprocess(identifier: str, body: ReprocessInput):
        values = body.model_dump(exclude_unset=True)
        validate_engines(values)
        preferences = await db.preferences()
        if "stt_provider" in values:
            validate_provider(values["stt_provider"], "speech_to_text")
            if values["stt_provider"] != preferences["stt_provider"] and "stt_model" not in values:
                raise HTTPException(400, "Choose a transcription model when changing its provider.")
        options = {key: preferences[key] for key in EXTRACTION_KEYS} | values
        async with lock(identifier):
            await source_or_404(identifier)
            rows = await db.query(
                "UPDATE $source SET status = 'pending', stage = 'waiting', error = '', "
                "regenerate_requested = false, summary_steering = '', reprocess_requested = true, reprocess_options = $options, processing_draft = {}, "
                "updated_at = time::now() WHERE status IN ['ready', 'error'] RETURN AFTER",
                {"source": ensure_record_id(identifier), "options": options},
            )
            if not rows:
                raise HTTPException(
                    409, "This source already has a job in the queue. Wait for it to finish."
                )
        return {"status": "pending"}

    @app.post("/api/sources/{identifier}/regenerate", status_code=202)
    async def regenerate(identifier: str, body: RegenerateInput):
        async with lock(identifier):
            source = await source_or_404(identifier)
            if not source.get("content") or not source.get("chunks"):
                raise HTTPException(
                    409, "Finish processing this source before regenerating its summary."
                )
            rows = await db.query(
                "UPDATE $source SET status = 'pending', stage = 'waiting', error = '', "
                "regenerate_requested = true, reprocess_requested = false, reprocess_options = {}, "
                "summary_steering = $steering, processing_draft = {}, updated_at = time::now() "
                "WHERE status IN ['ready', 'error'] RETURN AFTER",
                {"source": ensure_record_id(identifier), "steering": body.steering.strip()},
            )
            if not rows:
                raise HTTPException(409, "This source already has a job in the queue.")
        return {"status": "pending"}

    async def topic_or_404(identifier):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", identifier):
            raise HTTPException(404, "Topic not found.")
        topic = await topics.get(identifier)
        if not topic:
            raise HTTPException(404, "Topic not found.")
        return topic

    @app.get("/topics", response_class=HTMLResponse)
    async def topic_index(request: Request):
        return templates.TemplateResponse(request=request, name="topics.html", context={})

    @app.get("/api/topics/workspace")
    async def topic_workspace(include_inbox: bool = False):
        return await topics.workspace(include_inbox)

    @app.get("/api/topics/{identifier}")
    async def topic_inspect(identifier: str, include_inbox: bool = False):
        await topic_or_404(identifier)
        graph = await topics.workspace(include_inbox)
        topic = next(t for t in graph["topics"] if t["id"] == identifier)
        edges = [edge for edge in graph["edges"] if identifier in (edge["source"], edge["target"])]
        ids = set(topic["source_ids"])
        return {
            "topic": topic,
            "related": edges,
            "sources": [s for s in graph["sources"] if s["id"] in ids],
            "scope": graph["scope"],
        }

    @app.put("/api/topics/{identifier}/context")
    async def topic_context_update(identifier: str, body: TopicContextInput):
        async with lock("topic-context:" + identifier):
            await topic_or_404(identifier)
            await topics.save_context(identifier, body.definition, body.personal_context)
            return public(await topics.get(identifier))

    @app.patch("/api/topics/{identifier}/context")
    async def edit_topic_context(identifier: str, body: TopicContextInput):
        async with lock("topic-context:" + identifier):
            await topic_or_404(identifier)
            await topics.patch_context(identifier, body.model_dump(exclude_unset=True))
            return public(await topics.get(identifier))

    @app.post("/api/sources/{identifier}/relevance")
    async def source_relevance(identifier: str):
        async with lock(identifier):
            source = await source_or_404(identifier)
            if source.get("status") != "ready" or not source.get("content"):
                raise HTTPException(409, "Finish processing this find first.")
            contexts = await topics.contexts()
            selected = await db.preferences()
            try:
                with intelligence.use_preferences(selected):
                    text = await intelligence.relevance(source, contexts, selected["language"])
            except Exception as exc:
                raise HTTPException(502, safe_error(exc)) from None
            from .database import now

            values = {
                "personal_relevance": text,
                "relevance_signature": context_signature(contexts),
                "relevance_context": contexts,
                "relevance_generated_at": now(),
            }
            saved = await db.query(
                "UPDATE $source MERGE $values WHERE created_at = $created AND status = 'ready' "
                "AND (revision ?? 0) = $revision AND (summary ?? '') = $summary RETURN AFTER",
                {
                    "source": ensure_record_id(identifier),
                    "values": values,
                    "created": source["created_at"],
                    "revision": source.get("revision", 0),
                    "summary": source.get("summary", ""),
                },
            )
            if not saved:
                raise HTTPException(
                    409, "This find changed while generating. Refresh and try again."
                )
            return {
                "html": markdown.render(text),
                "has_context": any(t["personal_context"] for t in contexts),
                "stale": context_signature(contexts) != context_signature(await topics.contexts()),
            }

    @app.get("/topics/{identifier}", response_class=HTMLResponse)
    async def topic_detail(request: Request, identifier: str, page: int = 1):
        await topic_or_404(identifier)
        return templates.TemplateResponse(
            request=request,
            name="topics.html",
            context={"initial_topic": identifier},
        )

    @app.get("/api/topics")
    async def topic_list():
        return public(await topics.list())

    @app.post("/api/topics", status_code=201)
    async def topic_create(body: TopicInput):
        return public(await topics.create(body.name))

    @app.put("/api/topics/{identifier}")
    async def topic_rename(identifier: str, body: TopicInput):
        await topic_or_404(identifier)
        await topics.rename(identifier, body.name)
        return public(await topics.get(identifier))

    @app.post("/api/topics/{identifier}/approve")
    async def topic_approve(identifier: str):
        await topic_or_404(identifier)
        await topics.approve(identifier)
        return {"official": True}

    @app.post("/api/topics/{identifier}/merge")
    async def topic_merge(identifier: str, body: MergeInput):
        await topic_or_404(identifier)
        await topic_or_404(body.target)
        await topics.merge(identifier, body.target)
        return {"target": body.target}

    @app.delete("/api/topics/{identifier}")
    async def topic_delete(identifier: str):
        await topic_or_404(identifier)
        await topics.delete(identifier)
        return {"deleted": True}

    @app.post("/api/sources/{identifier}/topics", status_code=201)
    async def source_topic_create(identifier: str, body: TopicInput):
        await source_or_404(identifier)
        return public(await topics.create(body.name, source=identifier))

    @app.put("/api/sources/{identifier}/topics/{topic_id}")
    async def source_topic_attach(identifier: str, topic_id: str):
        await source_or_404(identifier)
        await topic_or_404(topic_id)
        await topics.attach(identifier, topic_id)
        return {"attached": True}

    @app.delete("/api/sources/{identifier}/topics/{topic_id}")
    async def source_topic_remove(identifier: str, topic_id: str):
        await source_or_404(identifier)
        await topic_or_404(topic_id)
        await topics.attach(identifier, topic_id, remove=True)
        return {"removed": True}

    @app.post("/api/sources/{identifier}/retry")
    async def retry(identifier: str):
        await source_or_404(identifier)
        rows = await db.query(
            "UPDATE $source SET status = 'pending', error = '' WHERE status = 'error' RETURN AFTER",
            {"source": (await db.get(identifier))["id"]},
        )
        if not rows:
            raise HTTPException(409, "Only failed sources can be retried.")
        return {"status": "pending"}

    @app.get("/api/preferences")
    async def get_preferences():
        return await db.preferences()

    @app.get("/api/models")
    async def models(provider: str, kind: str = "language"):
        return {"models": await discover_models(provider, kind)}

    @app.put("/api/preferences")
    async def preferences(body: PreferencesInput):
        values = body.model_dump(exclude_unset=True)
        if not values:
            raise HTTPException(400, "Choose at least one setting to save.")
        current = await db.preferences()
        validate_engines(values)
        for prefix, kind in (
            ("llm", "language"),
            ("embedding", "embedding"),
            ("stt", "speech_to_text"),
        ):
            provider_key, model_key = f"{prefix}_provider", f"{prefix}_model"
            if provider_key in values:
                validate_provider(values[provider_key], kind)
                if values[provider_key] != current[provider_key] and model_key not in values:
                    raise HTTPException(400, "Choose a model when changing its provider.")
        return await db.set_preferences(values)

    @app.get("/api/sources/{identifier}/chat")
    async def history(identifier: str):
        await source_or_404(identifier)
        return public(await db.history(identifier))

    @app.post("/api/sources/{identifier}/chat")
    async def chat(identifier: str, body: ChatInput):
        question = body.question.strip()
        if not question:
            raise HTTPException(400, "Enter a question first.")
        async with lock(identifier):
            source = await source_or_404(identifier)
            if source["status"] != "ready":
                raise HTTPException(
                    409, "This source is still processing. Chat will be ready soon."
                )
            source["topic_context"] = await topics.contexts(identifier)
            try:
                selected = await db.preferences()
                with intelligence.use_preferences(selected):
                    answer, evidence = await intelligence.chat(
                        source,
                        await db.history(identifier, revision=source.get("revision", 0)),
                        question,
                        selected["language"],
                    )
            except Exception as exc:
                raise HTTPException(502, safe_error(exc)) from None
            turn = await db.save_turn(
                identifier,
                question,
                answer,
                evidence,
                selected["llm_model"],
                selected["llm_provider"],
                revision=source.get("revision", 0),
            )
            return public(turn)

    @app.delete("/api/sources/{identifier}/chat")
    async def clear_chat(identifier: str):
        async with lock(identifier):
            await source_or_404(identifier)
            await db.clear_history(identifier)
        return {"cleared": True}

    app.mount("/", mcp_http)
    return app
