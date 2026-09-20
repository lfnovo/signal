import base64
import hashlib
import time
from dataclasses import replace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mcp import Client
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from signal_inbox.auth import AuthManager, SignalOAuthProvider
from signal_inbox.database import Database
from signal_inbox.mcp_server import build_mcp_server
from signal_inbox.web import create_app


class FakeAI:
    async def close(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("api_url", ["https://signal.example", "https://signal.example/"])
@pytest.mark.parametrize("approved", [True, False])
async def test_oauth_callback_issuer_matches_discovery_exactly(settings, api_url, approved):
    protected = replace(settings, password="test password", api_url=api_url)
    app = create_app(protected, database=Database(protected), ai=FakeAI(), start_worker=False)
    provider = app.state.oauth_provider
    provider._put = AsyncMock()
    provider._delete = AsyncMock(
        return_value={
            "client_id": "test-client",
            "expires_at": time.time() + 600,
            "params": {
                "state": "test-state",
                "scopes": ["signal"],
                "code_challenge": "challenge",
                "redirect_uri": "http://127.0.0.1:8765/callback",
                "redirect_uri_provided_explicitly": True,
            },
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=api_url
    ) as client:
        metadata = await client.get("/.well-known/oauth-authorization-server")
        resource = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert metadata.status_code == resource.status_code == 200
        issuer = metadata.json()["issuer"]
        assert resource.json()["authorization_servers"] == [issuer]
        await client.post("/login", data={"password": protected.password})
        response = await client.post(
            "/oauth/approve",
            data={"request_id": "test-request", "decision": "approve" if approved else "deny"},
        )
        assert response.status_code == 303
        callback = parse_qs(urlsplit(response.headers["location"]).query)
        assert callback["iss"] == [issuer]
        assert callback["state"] == ["test-state"]
        assert ("code" in callback) is approved
        if not approved:
            assert callback["error"] == ["access_denied"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback", ["http://127.0.0.1:8765/callback", "https://agent.example/callback"]
)
async def test_consent_form_allows_only_its_registered_callback(settings, callback):
    protected = replace(settings, password="test password")
    app = create_app(protected, database=Database(protected), ai=FakeAI(), start_worker=False)
    app.state.oauth_provider.pending = AsyncMock(
        return_value=(
            {"params": {"redirect_uri": callback, "scopes": ["signal"]}},
            OAuthClientInformationFull(client_id="test-client", client_name="Test agent"),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8020"
    ) as client:
        await client.post("/login", data={"password": protected.password})
        page = await client.get("/oauth/approve?request=test-request")
        assert page.status_code == 200
        parts = urlsplit(callback)
        assert page.headers["content-security-policy"].endswith(
            f"form-action 'self' {parts.scheme}://{parts.netloc}"
        )
        ordinary = await client.get("/login")
        assert ordinary.headers["content-security-policy"].endswith("form-action 'self'")


def test_password_sessions_are_signed_expiring_and_bound_to_password(tmp_path):
    auth = AuthManager("a strong password", tmp_path, secure_cookie=True)
    token = auth.create_session()

    assert auth.check_password("a strong password")
    assert not auth.check_password("wrong")
    assert auth.valid_session(token)
    assert not auth.valid_session(token + "changed")
    assert not AuthManager("a new password", tmp_path, True).valid_session(token)
    assert (tmp_path / "auth.key").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_web_login_protects_private_routes_and_mcp_publishes_oauth_metadata(settings):
    protected = replace(settings, password="correct horse battery staple")
    app = create_app(
        protected,
        database=Database(protected),
        ai=FakeAI(),
        start_worker=False,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8020", follow_redirects=False
    ) as client:
        page = await client.get("/", headers={"Accept": "text/html"})
        assert page.status_code == 303
        assert page.headers["location"].startswith("/login?next=")
        assert (await client.get("/api/preferences")).status_code == 401

        failed = await client.post("/login", data={"password": "wrong", "next": "/focus"})
        assert failed.status_code == 401
        signed_in = await client.post(
            "/login", data={"password": protected.password, "next": "/focus"}
        )
        assert signed_in.status_code == 303
        assert signed_in.headers["location"] == "/focus"
        assert "signal_session=" in signed_in.headers["set-cookie"]
        assert "HttpOnly" in signed_in.headers["set-cookie"]

        metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert metadata.status_code == 200
        assert metadata.json()["resource"] == "http://127.0.0.1:8020/mcp"


@pytest.mark.asyncio
async def test_hosted_login_accepts_the_public_origin_and_rejects_others(settings):
    hosted = replace(
        settings,
        password="correct horse battery staple",
        api_url="https://signal.example.com",
        allowed_hosts=("signal.example.com",),
    )
    app = create_app(hosted, database=Database(hosted), ai=FakeAI(), start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://signal.example.com",
        follow_redirects=False,
    ) as client:
        form = {"password": hosted.password, "next": "/focus"}
        page = await client.get("/login")
        assert page.status_code == 200
        assert page.headers["referrer-policy"] == "same-origin"
        signed_in = await client.post(
            "/login", data=form, headers={"Origin": "https://signal.example.com"}
        )
        assert signed_in.status_code == 303
        assert "signal_session=" in signed_in.headers["set-cookie"]
        assert signed_in.headers["access-control-allow-origin"] == "https://signal.example.com"
        for origin in ("https://evil.example", "http://signal.example.com", "null"):
            rejected = await client.post("/login", data=form, headers={"Origin": origin})
            assert rejected.status_code == 403


@pytest.mark.asyncio
async def test_mcp_catalog_is_shared_by_in_process_and_transport_clients(settings):
    db = Database(settings)
    server = build_mcp_server(db, FakeAI())
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        templates = {
            item.uri_template
            for item in (await client.list_resource_templates()).resource_templates
        }

    assert {
        "get_status",
        "capture_url",
        "capture_file",
        "list_sources",
        "search_sources",
        "get_source",
        "ask_source",
        "move_source_to_library",
        "move_source_to_inbox",
        "set_source_focus",
        "rename_source",
        "list_topics",
        "get_topic",
        "update_topic_context",
    } <= tools.keys()
    assert tools["get_source"].annotations.read_only_hint is True
    assert tools["capture_url"].annotations.read_only_hint is False
    assert "signal://sources/{identifier}" in templates
    assert "signal://sources/{identifier}/preview" in templates
    assert "signal://topics/{identifier}" in templates
    assert "signal://views/{view}" in templates


@pytest.mark.asyncio
async def test_oauth_approval_issues_refreshes_and_revokes_client_tokens(db):
    issuer = "https://signal.example"
    provider = SignalOAuthProvider(db, issuer, issuer + "/mcp")
    client = OAuthClientInformationFull(
        client_id="test-client",
        client_name="Test agent",
        redirect_uris=[AnyUrl("http://127.0.0.1:8765/callback")],
        token_endpoint_auth_method="none",
    )
    await provider.register_client(client)
    approval_url = await provider.authorize(
        client,
        AuthorizationParams(
            state="state-value",
            scopes=["signal"],
            code_challenge="challenge",
            redirect_uri=AnyUrl("http://127.0.0.1:8765/callback"),
            redirect_uri_provided_explicitly=True,
            resource=issuer + "/mcp",
        ),
    )
    request_id = parse_qs(urlsplit(approval_url).query)["request"][0]
    assert urlsplit(approval_url).path == "/oauth/approve"
    assert (await provider.pending(request_id))[1].client_name == "Test agent"

    callback = await provider.finish_authorization(request_id, approved=True)
    params = parse_qs(urlsplit(callback).query)
    assert params["state"] == ["state-value"]
    assert params["iss"] == [issuer + "/"]
    code = await provider.load_authorization_code(client, params["code"][0])
    issued = await provider.exchange_authorization_code(client, code)
    access = await provider.load_access_token(issued.access_token)
    refresh = await provider.load_refresh_token(client, issued.refresh_token)
    assert access.subject == "owner" and access.resource == issuer + "/mcp"
    assert refresh.subject == "owner"

    rotated = await provider.exchange_refresh_token(client, refresh, ["signal"])
    assert await provider.load_access_token(issued.access_token) is None
    current = await provider.load_access_token(rotated.access_token)
    await provider.revoke_token(current)
    assert await provider.load_access_token(rotated.access_token) is None


@pytest.mark.asyncio
async def test_remote_mcp_oauth_flow_reaches_protected_tool_catalog(db):
    protected = replace(db.settings, password="one private signal password")
    app = create_app(protected, database=db, ai=FakeAI(), start_worker=False)
    verifier = "a" * 64
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    redirect_uri = "http://127.0.0.1:8765/callback"

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8020",
            follow_redirects=False,
        ) as client:
            metadata = await client.get("/.well-known/oauth-authorization-server")
            assert metadata.status_code == 200
            registration = await client.post(
                "/register",
                json={
                    "client_name": "Integration agent",
                    "redirect_uris": [redirect_uri],
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "scope": "signal",
                },
            )
            assert registration.status_code == 201
            client_id = registration.json()["client_id"]

            authorization = await client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "state": "test-state",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "scope": "signal",
                    "resource": "http://127.0.0.1:8020/mcp",
                },
            )
            assert authorization.status_code in {302, 303, 307}
            approval_url = authorization.headers["location"]
            request_id = parse_qs(urlsplit(approval_url).query)["request"][0]

            signed_in = await client.post(
                "/login",
                data={"password": protected.password, "next": approval_url},
            )
            assert signed_in.status_code == 303
            assert (await client.get(approval_url)).status_code == 200
            approved = await client.post(
                "/oauth/approve",
                data={"request_id": request_id, "decision": "approve"},
            )
            callback = parse_qs(urlsplit(approved.headers["location"]).query)
            assert callback["iss"] == [metadata.json()["issuer"]]

            token = await client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": callback["code"][0],
                    "code_verifier": verifier,
                    "redirect_uri": redirect_uri,
                    "resource": "http://127.0.0.1:8020/mcp",
                },
            )
            assert token.status_code == 200, token.text
            access = token.json()["access_token"]

            unauthenticated = await client.post("/mcp", json={})
            assert unauthenticated.status_code == 401
            catalog = await client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer " + access,
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/list",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {
                        "_meta": {
                            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                            "io.modelcontextprotocol/clientCapabilities": {},
                            "io.modelcontextprotocol/clientInfo": {
                                "name": "integration-test",
                                "version": "1.0",
                            },
                        }
                    },
                },
            )
            assert catalog.status_code == 200, catalog.text
            names = {tool["name"] for tool in catalog.json()["result"]["tools"]}
            assert {"search_sources", "ask_source", "update_topic_context"} <= names


@pytest.mark.asyncio
async def test_capture_token_scope_and_credentials(settings, monkeypatch):
    protected = replace(settings, password="owner password")
    app = create_app(protected, database=Database(protected), ai=FakeAI(), start_worker=False)
    app.state.oauth_provider.valid_capture_token = AsyncMock(side_effect=lambda t: t == "valid")
    capture = AsyncMock(
        return_value=(
            {
                "id": "a" * 64,
                "title": "Test",
                "status": "pending",
                "stage": "queued",
                "kind": "url",
                "original": "https://example.com",
            },
            True,
        )
    )
    monkeypatch.setattr("signal_inbox.web.add_url", capture)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=protected.api_url
    ) as client:
        assert (
            await client.post("/api/sources", json={"url": "https://example.com"})
        ).status_code == 401
        await client.post("/login", data={"password": protected.password})
        # An existing web session must not elevate a supplied capture credential.
        for credential in ("Bearer wrong", "Bearer owner password", "Basic valid", "Bearer"):
            response = await client.post(
                "/api/sources",
                headers={"Authorization": credential},
                json={"url": "https://example.com"},
            )
            assert response.status_code == 401
        headers = {"Authorization": "Bearer valid"}
        for method, path in [
            ("GET", "/api/sources"),
            ("GET", "/api/sources/" + "a" * 64),
            ("POST", "/api/sources/upload"),
            ("DELETE", "/api/sources/" + "a" * 64),
            ("PUT", "/api/preferences"),
            ("GET", "/connections"),
            ("POST", "/connections/capture"),
            ("POST", "/connections/capture/test/revoke"),
        ]:
            assert (await client.request(method, path, headers=headers)).status_code == 401
        client.cookies.clear()
        assert (await client.post("/api/sources", headers=headers, json={})).status_code == 422
        response = await client.post(
            "/api/sources", headers=headers, json={"url": "https://example.com"}
        )
        assert response.status_code == 202
        assert response.json()["created"] is True
        assert capture.await_count == 1
        # Token-management pages are private, too.
        assert (
            await client.post("/connections/capture", data={"name": "iPhone"})
        ).status_code == 401


@pytest.mark.asyncio
async def test_capture_token_lifecycle_and_url_capture(db):
    import re

    protected = replace(db.settings, password="owner password")
    app = create_app(protected, database=db, ai=FakeAI(), start_worker=False)
    provider = app.state.oauth_provider
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=protected.api_url
    ) as client:
        await client.post("/login", data={"password": protected.password})
        for name in ("", " " * 3, "x" * 81):
            assert (await client.post("/connections/capture", data={"name": name})).status_code in {
                400,
                422,
            }
        page = await client.post("/connections/capture", data={"name": "iPhone"})
        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store"
        token = re.search(r'value="(signal_capture_[^"]+)"', page.text).group(1)
        rows = await db.query("SELECT * FROM capture_token")
        assert len(rows) == 1
        assert token not in str(rows)
        assert str(rows[0]["id"].id) == hashlib.sha256(token.encode()).hexdigest()
        token_id = rows[0]["token_id"]
        listed = await client.get("/connections")
        assert token not in listed.text
        assert "iPhone" in listed.text
        assert listed.headers["cache-control"] == "no-store"
        other = await provider.create_capture_token("Other device")
        assert await provider.load_access_token(token) is None  # Capture token is not MCP OAuth.
        assert not await provider.valid_capture_token(protected.password)
        client.cookies.clear()
        headers = {"Authorization": "Bearer " + token}
        assert (await client.post("/mcp", headers=headers, json={})).status_code == 401
        for created in (True, False):
            response = await client.post(
                "/api/sources", headers=headers, json={"url": "https://example.com/shortcut-test"}
            )
            assert response.status_code == 202
            assert response.json()["created"] is created
            assert set(response.json()["source"]) == {
                "id",
                "title",
                "status",
                "stage",
                "kind",
                "original",
            }
        await client.post("/login", data={"password": protected.password})
        assert (await client.post(f"/connections/capture/{token_id}/revoke")).status_code == 303
        client.cookies.clear()
        assert (
            await client.post(
                "/api/sources", headers=headers, json={"url": "https://example.com/revoked"}
            )
        ).status_code == 401
        assert await provider.valid_capture_token(other)
        assert not await provider.valid_capture_token(token)
