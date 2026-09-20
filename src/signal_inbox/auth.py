"""Single-user web sessions and OAuth approval for remote MCP clients."""

import hashlib
import hmac
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl
from surrealdb import RecordID

from .database import Database, now, one

SESSION_COOKIE = "signal_session"
SESSION_SECONDS = 30 * 24 * 60 * 60
ACCESS_SECONDS = 60 * 60
REFRESH_SECONDS = 30 * 24 * 60 * 60


def _key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AuthManager:
    """Authenticate the one Signal owner and mint signed browser sessions."""

    def __init__(self, password: str, data_dir: Path, secure_cookie: bool):
        self.password = password
        self.secure_cookie = secure_cookie
        data_dir.mkdir(parents=True, exist_ok=True)
        key_path = data_dir / "auth.key"
        if not key_path.exists():
            key_path.write_bytes(secrets.token_bytes(32))
            key_path.chmod(0o600)
        salt = key_path.read_bytes()
        self.signing_key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000, dklen=32)
        self.password_digest = hmac.new(
            self.signing_key, password.encode(), hashlib.sha256
        ).digest()
        self.failures: dict[str, list[float]] = {}

    def check_password(self, candidate: str) -> bool:
        candidate_digest = hmac.new(self.signing_key, candidate.encode(), hashlib.sha256).digest()
        return hmac.compare_digest(candidate_digest, self.password_digest)

    def can_attempt(self, address: str) -> bool:
        cutoff = time.monotonic() - 60
        recent = [attempt for attempt in self.failures.get(address, []) if attempt > cutoff]
        self.failures[address] = recent
        return len(recent) < 5

    def record_failure(self, address: str) -> None:
        self.failures.setdefault(address, []).append(time.monotonic())

    def record_success(self, address: str) -> None:
        self.failures.pop(address, None)

    def create_session(self) -> str:
        payload = f"{int(time.time()) + SESSION_SECONDS}.{secrets.token_urlsafe(24)}"
        signature = hmac.new(self.signing_key, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def valid_session(self, token: str | None) -> bool:
        if not token:
            return False
        try:
            expires, nonce, signature = token.split(".", 2)
            payload = f"{expires}.{nonce}"
            expected = hmac.new(self.signing_key, payload.encode(), hashlib.sha256).hexdigest()
            return int(expires) > time.time() and hmac.compare_digest(signature, expected)
        except (ValueError, TypeError):
            return False


class SignalRefreshToken(RefreshToken):
    resource: str | None = None
    pair_id: str


class SignalAccessToken(AccessToken):
    pair_id: str


class SignalOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, SignalRefreshToken, SignalAccessToken]
):
    """Persistent OAuth provider whose consent screen uses the Signal password."""

    def __init__(self, db: Database, issuer: str, resource: str):
        self.db = db
        # Match the URL serialization used by MCP discovery; OAuth compares issuers exactly.
        self.issuer = str(AnyHttpUrl(issuer))
        self.resource = resource

    async def create_capture_token(self, name: str) -> str:
        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("Token name must have 1–80 characters.")
        token = "signal_capture_" + secrets.token_urlsafe(32)
        await self._put(
            "capture_token",
            token,
            {"token_id": secrets.token_hex(16), "name": name, "created_at": now()},
        )
        return token

    async def valid_capture_token(self, token: str) -> bool:
        if not token.startswith("signal_capture_") or len(token) > 100:
            return False
        return bool(await self._get("capture_token", token))

    async def capture_tokens(self):
        return await self.db.query(
            "SELECT token_id, name, created_at FROM capture_token ORDER BY created_at DESC"
        )

    async def revoke_capture_token(self, token_id: str):
        await self.db.query(
            "DELETE capture_token WHERE token_id = $token_id", {"token_id": token_id}
        )

    async def _put(self, table: str, key: str, values: dict):
        return one(
            await self.db.query(
                "UPSERT $record CONTENT $values RETURN AFTER",
                {"record": RecordID(table, _key(key)), "values": values},
            )
        )

    async def _get(self, table: str, key: str):
        async with self.db.connection() as connection:
            return one(await connection.select(RecordID(table, _key(key))))

    async def _delete(self, table: str, key: str):
        rows = await self.db.query(
            "DELETE $record RETURN BEFORE", {"record": RecordID(table, _key(key))}
        )
        return one(rows)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = await self._get("auth_client", client_id)
        return OAuthClientInformationFull.model_validate(row["data"]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await self._put(
            "auth_client",
            client_info.client_id,
            {"data": client_info.model_dump(mode="json"), "created_at": now()},
        )

    async def authorize(self, client, params: AuthorizationParams) -> str:
        request_id = secrets.token_urlsafe(32)
        await self._put(
            "auth_pending",
            request_id,
            {
                "client_id": client.client_id,
                "params": params.model_dump(mode="json"),
                "expires_at": int(time.time()) + 600,
                "created_at": now(),
            },
        )
        return f"{self.issuer.rstrip('/')}/oauth/approve?request={quote(request_id)}"

    async def pending(self, request_id: str):
        row = await self._get("auth_pending", request_id)
        if not row or row["expires_at"] <= time.time():
            return None
        client = await self.get_client(row["client_id"])
        return (row, client) if client else None

    async def finish_authorization(self, request_id: str, approved: bool) -> str | None:
        row = await self._delete("auth_pending", request_id)
        if not row or row["expires_at"] <= time.time():
            return None
        params = AuthorizationParams.model_validate(row["params"])
        target = str(params.redirect_uri)
        if not approved:
            return construct_redirect_uri(
                target,
                error="access_denied",
                state=params.state,
                iss=self.issuer,
            )
        code = AuthorizationCode(
            code=secrets.token_urlsafe(32),
            client_id=row["client_id"],
            scopes=params.scopes or ["signal"],
            expires_at=time.time() + 300,
            code_challenge=params.code_challenge,
            redirect_uri=AnyUrl(target),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource or self.resource,
            subject="owner",
        )
        await self._put(
            "auth_code",
            code.code,
            {"data": code.model_dump(mode="json", exclude={"code"}), "created_at": now()},
        )
        return construct_redirect_uri(
            target,
            code=code.code,
            state=params.state,
            iss=self.issuer,
        )

    async def load_authorization_code(self, client, authorization_code: str):
        row = await self._get("auth_code", authorization_code)
        if not row:
            return None
        code = AuthorizationCode(code=authorization_code, **row["data"])
        if code.client_id != client.client_id or code.expires_at <= time.time():
            return None
        return code

    async def _issue(self, client_id: str, scopes: list[str], resource: str | None):
        access_value = secrets.token_urlsafe(32)
        refresh_value = secrets.token_urlsafe(40)
        pair_id = secrets.token_urlsafe(18)
        access = SignalAccessToken(
            token=access_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + ACCESS_SECONDS,
            resource=resource or self.resource,
            subject="owner",
            pair_id=pair_id,
        )
        refresh = SignalRefreshToken(
            token=refresh_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + REFRESH_SECONDS,
            subject="owner",
            resource=resource or self.resource,
            pair_id=pair_id,
        )
        for kind, token in (("access", access), ("refresh", refresh)):
            await self._put(
                "auth_token",
                token.token,
                {
                    "kind": kind,
                    "pair_id": pair_id,
                    "client_id": client_id,
                    "revoked": False,
                    "data": token.model_dump(mode="json", exclude={"token"}),
                    "created_at": now(),
                },
            )
        return access, refresh

    async def exchange_authorization_code(self, client, authorization_code):
        await self._delete("auth_code", authorization_code.code)
        access, refresh = await self._issue(
            client.client_id, authorization_code.scopes, authorization_code.resource
        )
        return OAuthToken(
            access_token=access.token,
            refresh_token=refresh.token,
            expires_in=ACCESS_SECONDS,
            scope=" ".join(access.scopes),
        )

    async def _load_token(self, value: str, kind: str):
        row = await self._get("auth_token", value)
        if not row or row.get("revoked") or row.get("kind") != kind:
            return None
        data = row["data"]
        if data.get("expires_at") and data["expires_at"] <= time.time():
            return None
        model = SignalAccessToken if kind == "access" else SignalRefreshToken
        return model(token=value, **data)

    async def load_refresh_token(self, client, refresh_token: str):
        token = await self._load_token(refresh_token, "refresh")
        return token if token and token.client_id == client.client_id else None

    async def exchange_refresh_token(self, client, refresh_token, scopes: list[str]):
        await self.revoke_token(refresh_token)
        requested = scopes or refresh_token.scopes
        if not set(requested).issubset(refresh_token.scopes):
            requested = refresh_token.scopes
        access, refresh = await self._issue(client.client_id, requested, refresh_token.resource)
        return OAuthToken(
            access_token=access.token,
            refresh_token=refresh.token,
            expires_in=ACCESS_SECONDS,
            scope=" ".join(requested),
        )

    async def load_access_token(self, token: str):
        return await self._load_token(token, "access")

    async def revoke_token(self, token):
        pair_id = getattr(token, "pair_id", None)
        if pair_id:
            await self.db.query(
                "UPDATE auth_token SET revoked = true WHERE pair_id = $pair",
                {"pair": pair_id},
            )

    async def connections(self):
        rows = await self.db.query(
            "SELECT client_id, pair_id, created_at, data.expires_at AS expires_at "
            "FROM auth_token WHERE kind = 'refresh' AND revoked != true "
            "AND data.expires_at > $now ORDER BY created_at DESC",
            {"now": int(time.time())},
        )
        clients = {}
        for row in rows:
            client = await self.get_client(row["client_id"])
            if client:
                clients[row["pair_id"]] = {
                    "pair_id": row["pair_id"],
                    "client_id": row["client_id"],
                    "client_name": client.client_name or row["client_id"],
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                }
        return list(clients.values())

    async def revoke_connection(self, pair_id: str):
        await self.db.query(
            "UPDATE auth_token SET revoked = true WHERE pair_id = $pair", {"pair": pair_id}
        )
