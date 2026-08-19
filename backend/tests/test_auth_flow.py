"""Task 1.5: Auth flow integration tests."""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.database import get_connection, run_migrations
from app.models.user import create_user
from app.services.email import get_sent_emails, clear_sent_emails


@pytest.fixture
def app(config):
    """Create test app."""
    return create_app(config)


@pytest_asyncio.fixture
async def client(app, config):
    """HTTP test client with migrated DB."""
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        clear_sent_emails()
        # Reset rate limiter
        from app.routes.auth import _rate_limit
        _rate_limit.clear()
        yield ac


@pytest.mark.asyncio
async def test_full_login_flow(client, config):
    """Request → email sent → verify → session created → redirect."""
    resp = await client.post("/auth/request", json={"email": "user@test.com"})
    assert resp.status_code == 200
    assert resp.json()["message"] == "Magic link sent"

    emails = get_sent_emails()
    assert len(emails) == 1
    assert emails[0]["to"] == "user@test.com"
    token = emails[0]["token"]

    resp = await client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert resp.status_code == 307
    assert "session_id" in resp.cookies


@pytest.mark.asyncio
async def test_new_user_created(client, config):
    """Request for new email creates user."""
    resp = await client.post("/auth/request", json={"email": "newuser@test.com"})
    assert resp.status_code == 200

    async with get_connection(config.DATABASE_PATH) as db:
        from app.models.user import get_user_by_email
        user = await get_user_by_email(db, "newuser@test.com")
        assert user is not None


@pytest.mark.asyncio
async def test_existing_user_no_duplicate(client, config):
    """Existing user doesn't create duplicate."""
    async with get_connection(config.DATABASE_PATH) as db:
        await create_user(db, "existing@test.com")

    resp = await client.post("/auth/request", json={"email": "existing@test.com"})
    assert resp.status_code == 200

    async with get_connection(config.DATABASE_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE email = ?", ("existing@test.com",))
        row = await cursor.fetchone()
        assert row[0] == 1


@pytest.mark.asyncio
async def test_bounced_email_rejected(client, config):
    """User with email_bounce=1 gets 400."""
    async with get_connection(config.DATABASE_PATH) as db:
        user = await create_user(db, "bounced@test.com")
        await db.execute("UPDATE users SET email_bounce = 1 WHERE id = ?", (user["id"],))
        await db.commit()

    resp = await client.post("/auth/request", json={"email": "bounced@test.com"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_invalid_token_redirect_error(client):
    """Invalid token redirects with error."""
    resp = await client.get("/auth/verify?token=invalid-token", follow_redirects=False)
    assert resp.status_code == 307
    assert "error" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_cp1_verify_scoped_session(client, config):
    """CP1 token creates scoped session."""
    async with get_connection(config.DATABASE_PATH) as db:
        user = await create_user(db, "cp1user@test.com")
        from app.utils.ulid import generate_id
        from app.utils.time import utc_now
        batch_id = generate_id()
        await db.execute(
            "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, ?, ?)",
            (batch_id, user["id"], "waiting_approval", utc_now()),
        )
        await db.commit()

        from app.models.magic_link import create_magic_link
        token = await create_magic_link(db, user["id"], "checkpoint_1", reference_id=batch_id)

    resp = await client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert resp.status_code == 307
    assert "session_id" in resp.cookies

    session_id = resp.cookies["session_id"]
    async with get_connection(config.DATABASE_PATH) as db:
        cursor = await db.execute("SELECT scope FROM sessions WHERE id = ?", (session_id,))
        row = await cursor.fetchone()
        assert row[0] == "checkpoint_1"


@pytest.mark.asyncio
async def test_logout_clears_session(client, config):
    """Logout deletes session and clears cookie."""
    resp = await client.post("/auth/request", json={"email": "logout@test.com"})
    token = get_sent_emails()[-1]["token"]
    resp = await client.get(f"/auth/verify?token={token}", follow_redirects=False)
    session_cookie = resp.cookies["session_id"]

    client.cookies.set("session_id", session_cookie)
    resp = await client.post("/auth/logout")
    assert resp.status_code == 200

    async with get_connection(config.DATABASE_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (session_cookie,))
        row = await cursor.fetchone()
        assert row[0] == 0


@pytest.mark.asyncio
async def test_rate_limit_per_email(client):
    """11th request for same email → 429 (per-email limit is 10)."""
    for i in range(10):
        resp = await client.post("/auth/request", json={"email": "ratelimit@test.com"})
        assert resp.status_code == 200

    resp = await client.post("/auth/request", json={"email": "ratelimit@test.com"})
    assert resp.status_code == 429

    # Different email should still work (not globally blocked)
    resp = await client.post("/auth/request", json={"email": "other@test.com"})
    assert resp.status_code == 200
