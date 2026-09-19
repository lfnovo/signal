import httpx
import pytest
from test_integration import FakeAI

from signal_inbox.database import public
from signal_inbox.intake import add_url
from signal_inbox.web import create_app


@pytest.mark.asyncio
async def test_preview_prefers_summary_falls_back_and_escapes_html(db):
    source, _ = await add_url(db, "https://example.com/preview")
    sid = public(source["id"])
    app = create_app(db.settings, database=db, ai=FakeAI(db.settings), start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        path = f"/api/sources/{sid}/preview"
        await db.update(
            sid, {"summary": "**Short** <script>alert(1)</script>", "content": "Long content"}
        )
        response = await client.get(path)
        assert (
            "frame-src https://www.youtube-nocookie.com;"
            in response.headers["content-security-policy"]
        )
        data = response.json()
        assert data["kind"] == "summary" and "<strong>Short</strong>" in data["html"]
        assert "<script>" not in data["html"] and "Long content" not in data["html"]
        await db.update(sid, {"summary": "", "content": "Start " + "x" * 17000 + "END"})
        data = (await client.get(path)).json()
        assert data["kind"] == "content" and data["truncated"]
        assert "Start" in data["html"] and "END" not in data["html"]
        await db.update(sid, {"content": ""})
        assert (await client.get(path)).json()["html"] == ""
        await db.update(sid, {"original": "https://youtu.be/dQw4w9WgXcQ"})
        assert (await client.get(path)).json()["youtube_video_id"] == "dQw4w9WgXcQ"
        detail = await client.get(f"/sources/{sid}")
        assert 'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"' in detail.text
        assert (await client.get("/api/sources/" + "f" * 64 + "/preview")).status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=abc",
        "https://youtu.be/dQw4w9WgXcQ?t=42",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/shorts/dQw4w9WgXcQ",
        "https://youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
    ],
)
def test_youtube_video_urls(url):
    from signal_inbox.web import youtube_video_id

    assert youtube_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
        "https://youtube.com@evil.test/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/playlist?list=abc",
        "https://youtube.com/watch?v=bad",
        "https://youtube.com/watch?v=%22%3E%3Cscript%3E",
        "file:///youtube.com/watch?v=dQw4w9WgXcQ",
        "https://[bad",
        "",
    ],
)
def test_non_video_urls_never_become_embeds(url):
    from signal_inbox.web import youtube_video_id

    assert youtube_video_id(url) is None
