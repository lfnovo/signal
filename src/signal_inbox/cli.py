"""The signal executable accepts a URL or a local file directly."""

import argparse
import asyncio
import sys
from pathlib import Path

from .ai import AI
from .config import Settings
from .database import Database, public
from .intake import add_file, add_url
from .worker import run_worker


async def intake(args, settings):
    db = Database(settings)
    await db.initialize()
    if args.target.lower().startswith(("http://", "https://")):
        source, created = await add_url(db, args.target)
    else:
        path = Path(args.target).expanduser()
        if not path.is_file():
            raise ValueError(f"File not found: {path}")
        with path.open("rb") as file:
            source, created = await add_file(db, file, path.name)
    identifier = public(source["id"])
    print(f"{'Saved' if created else 'Already saved'}: {source['title']}", flush=True)
    print(f"{settings.api_url}/sources/{identifier}", flush=True)
    if args.enqueue or source["status"] in ("ready", "error"):
        if source["status"] == "error":
            print("This source previously failed. Open it in the app to retry.", file=sys.stderr)
            return 1
        return 0
    ai = AI(settings)
    try:
        while True:
            # If the server is running it owns the queue. Otherwise CLI processes it.
            await run_worker(db, ai, once=True)
            source = await db.get(identifier)
            if source is None:
                print("This find was deleted while processing.")
                return 1
            if source["status"] in ("ready", "error"):
                print("Ready." if source["status"] == "ready" else source["error"])
                return 0 if source["status"] == "ready" else 1
            await asyncio.sleep(1)
    finally:
        await ai.close()


async def worker(settings):
    db = Database(settings)
    await db.initialize()
    ai = AI(settings)
    try:
        await run_worker(db, ai)
    finally:
        await ai.close()


def serve_mcp_stdio(settings):
    """Serve the same Signal capabilities over a process-local MCP connection."""
    from .mcp_server import build_mcp_server

    db = Database(settings)
    asyncio.run(db.initialize())
    ai = AI(settings)
    try:
        build_mcp_server(db, ai).run(transport="stdio")
    finally:
        asyncio.run(ai.close())


def main():
    parser = argparse.ArgumentParser(
        prog="signal", description="A home for your links, files and rabbit holes."
    )
    parser.add_argument("target", nargs="?", help="URL, file path, serve, worker or mcp")
    parser.add_argument(
        "--enqueue",
        action="store_true",
        help="Queue only; do not wait for processing",
    )
    parser.add_argument("--port", type=int, default=8020, help="Local web app port (serve)")
    parser.add_argument("--host", help="Web bind address (serve; defaults to SIGNAL_HOST)")
    args = parser.parse_args()
    settings = Settings.load()
    try:
        if args.target == "serve":
            import uvicorn

            from .web import create_app

            host = args.host or settings.host
            if host not in {"127.0.0.1", "localhost", "::1"} and not settings.password:
                raise ValueError("Set SIGNAL_PASSWORD before exposing Signal beyond localhost")
            uvicorn.run(create_app(settings), host=host, port=args.port)
        elif args.target == "worker":
            asyncio.run(worker(settings))
        elif args.target == "mcp":
            serve_mcp_stdio(settings)
        elif args.target:
            sys.exit(asyncio.run(intake(args, settings)))
        else:
            parser.print_help()
    except KeyboardInterrupt:
        print("\nInterrupted. Your find is still saved in the queue.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Never dump provider exceptions, which may carry secrets.
        detail = (
            str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__
        )
        print(
            f"Error: {detail}. Check your settings and the SurrealDB connection.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
