"""Tests for content brief seeding feature."""

import json
import os

import pytest
import pytest_asyncio

from app.llm.mock import MockLLM
from app.models.seed_batch import create_seed_batch
from app.pipeline.transitions.t1_ideation import run_ideation
from app.pipeline.transitions.t2_idea_approval import approve_ideas
from app.pipeline.transitions.t4_drafting import run_drafting
from app.utils.ulid import generate_id
from app.utils.time import utc_now


class FakeConfig:
    DATABASE_PATH = ":memory:"
    MAGIC_LINK_SECRET = "test-secret"
    BASE_URL = "http://test"
    APP_BASE_URL = "http://test"
    RESEND_API_KEY = ""
    ADMIN_EMAILS = ""
    GHOST_URL = "https://test.ghost.io"
    GHOST_ADMIN_API_KEY = ""
    FROM_EMAIL = "test@test.com"
    APP_ENV = "test"


@pytest_asyncio.fixture
async def db_with_user(db):
    """DB with a subscribed user."""
    now = utc_now()
    user_id = generate_id()
    await db.execute(
        """INSERT INTO users (id, email, subscription_status, ghost_key_valid,
           ghost_url, brand_voice, default_word_count, publish_days, created_at, updated_at)
           VALUES (?, 'test@test.com', 'active', 1, 'https://test.ghost.io',
           'Casual and friendly', 1500, '[]', ?, ?)""",
        (user_id, now, now),
    )
    await db.commit()
    return db, user_id


@pytest.mark.asyncio
async def test_content_brief_assembly_at_t2(db_with_user):
    """Test that content_brief JSON is assembled on article at CP1 approval."""
    db, user_id = db_with_user
    now = utc_now()

    # Create batch with content brief style seeds
    batch_id, seed_ids = await create_seed_batch(db, user_id, [
        {"seed_type": "topic", "content": "Best practices for remote work\n\nKeywords: remote work, async communication"},
        {"seed_type": "url", "content": "https://example.com/remote-work"},
    ])

    topic_seed_id = seed_ids[0]

    # Add a seed image with role and description
    img_id = generate_id()
    await db.execute(
        """INSERT INTO seed_images (id, seed_id, filename, storage_path, mime_type, image_role, description, created_at)
           VALUES (?, ?, 'office.jpg', '/tmp/office.jpg', 'image/jpeg', 'cover', 'A modern open-plan office with warm lighting', ?)""",
        (img_id, topic_seed_id, now),
    )

    # Create an idea
    idea_id = generate_id()
    await db.execute(
        """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword,
           search_intent, estimated_volume, status, created_at)
           VALUES (?, ?, ?, 'Remote Work Best Practices', 'Test angle', 'remote work tips',
           'Learn tips', 'low', 'pending', ?)""",
        (idea_id, batch_id, topic_seed_id, now),
    )
    await db.commit()

    # Approve the idea
    result = await approve_ideas(db, user_id, batch_id, [{"id": idea_id}])
    assert result["articles_created"] == 1

    # Verify content_brief was assembled on the article
    cursor = await db.execute("SELECT content_brief FROM articles WHERE idea_id = ?", (idea_id,))
    article = await cursor.fetchone()
    assert article["content_brief"] is not None

    brief = json.loads(article["content_brief"])
    assert brief["user_description"] == "Best practices for remote work"
    assert brief["user_keywords"] == "remote work, async communication"
    assert len(brief["reference_materials"]) == 1
    assert brief["reference_materials"][0]["url"] == "https://example.com/remote-work"
    assert len(brief["user_images"]) == 1
    assert brief["user_images"][0]["role"] == "cover"
    assert brief["user_images"][0]["description"] == "A modern open-plan office with warm lighting"


@pytest.mark.asyncio
async def test_content_brief_assembly_no_keywords(db_with_user):
    """Test content brief assembly without keywords."""
    db, user_id = db_with_user
    now = utc_now()

    batch_id, seed_ids = await create_seed_batch(db, user_id, [
        {"seed_type": "topic", "content": "AI in healthcare diagnostics"},
    ])

    idea_id = generate_id()
    await db.execute(
        """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword,
           search_intent, estimated_volume, status, created_at)
           VALUES (?, ?, ?, 'AI Healthcare', 'Test', 'ai healthcare', 'Learn', 'low', 'pending', ?)""",
        (idea_id, batch_id, seed_ids[0], now),
    )
    await db.commit()

    result = await approve_ideas(db, user_id, batch_id, [{"id": idea_id}])
    assert result["articles_created"] == 1

    cursor = await db.execute("SELECT content_brief FROM articles WHERE idea_id = ?", (idea_id,))
    article = await cursor.fetchone()
    brief = json.loads(article["content_brief"])
    assert brief["user_description"] == "AI in healthcare diagnostics"
    assert "user_keywords" not in brief
    assert "reference_materials" not in brief


@pytest.mark.asyncio
async def test_content_brief_assembly_includes_image_style_override(db_with_user):
    """Content Brief image style override is copied from batch to article content_brief."""
    db, user_id = db_with_user
    now = utc_now()

    batch_id, seed_ids = await create_seed_batch(
        db,
        user_id,
        [{"seed_type": "topic", "content": "Explain onboarding metrics"}],
        image_style="illustration",
        image_substyle="isometric",
    )

    idea_id = generate_id()
    await db.execute(
        """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword,
           search_intent, estimated_volume, status, created_at)
           VALUES (?, ?, ?, 'Onboarding Metrics', 'Test', 'onboarding metrics', 'Learn', 'low', 'pending', ?)""",
        (idea_id, batch_id, seed_ids[0], now),
    )
    await db.commit()

    result = await approve_ideas(db, user_id, batch_id, [{"id": idea_id}])
    assert result["articles_created"] == 1

    cursor = await db.execute("SELECT content_brief FROM articles WHERE idea_id = ?", (idea_id,))
    brief = json.loads((await cursor.fetchone())["content_brief"])
    assert brief["image_style"] == "illustration"
    assert brief["image_substyle"] == "isometric"


@pytest.mark.asyncio
async def test_photo_description_preprocessing(db_with_user):
    """Test that T1 calls describe_image for seed images without descriptions."""
    db, user_id = db_with_user
    now = utc_now()

    batch_id, seed_ids = await create_seed_batch(db, user_id, [
        {"seed_type": "topic", "content": "Test topic"},
    ])

    # Create a test image file
    img_id = generate_id()
    os.makedirs("data/seed_images/test_brief", exist_ok=True)
    test_img_path = f"data/seed_images/test_brief/{img_id}.png"
    with open(test_img_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    await db.execute(
        """INSERT INTO seed_images (id, seed_id, filename, storage_path, mime_type, image_role, description, created_at)
           VALUES (?, ?, 'test.png', ?, 'image/png', 'cover', NULL, ?)""",
        (img_id, seed_ids[0], test_img_path, now),
    )
    await db.commit()

    # Run ideation
    llm = MockLLM()
    result = await run_ideation(db, FakeConfig(), batch_id, llm)
    assert result["success"]

    # Verify description was stored
    cursor = await db.execute("SELECT description FROM seed_images WHERE id = ?", (img_id,))
    img = await cursor.fetchone()
    assert img["description"] is not None
    assert len(img["description"]) > 10

    # Cleanup
    try:
        os.remove(test_img_path)
        os.rmdir("data/seed_images/test_brief")
    except OSError:
        pass


@pytest.mark.asyncio
async def test_backward_compatibility_no_content_brief(db_with_user):
    """Test that articles without content_brief don't break the pipeline."""
    db, user_id = db_with_user
    now = utc_now()

    batch_id, seed_ids = await create_seed_batch(db, user_id, [
        {"seed_type": "topic", "content": "Test topic"},
    ])

    idea_id = generate_id()
    await db.execute(
        """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword,
           search_intent, estimated_volume, status, created_at)
           VALUES (?, ?, ?, 'Test Article', 'Test angle', 'test keyword',
           'Test intent', 'low', 'approved', ?)""",
        (idea_id, batch_id, seed_ids[0], now),
    )

    outline = {
        "thesis": "Test thesis",
        "target_word_count": 1500,
        "sections": [
            {"section_number": 1, "subheading": "Test Section", "purpose": "test",
             "key_points": ["point 1"], "research_notes": [], "word_count_target": 1500,
             "image_needed": True}
        ],
        "seo_block": {
            "meta_title": "Test", "meta_description": "Test description",
            "focus_keyword": "test keyword", "visible_tags": ["test"]
        }
    }

    article_id = generate_id()
    await db.execute(
        """INSERT INTO articles (id, user_id, idea_id, state, outline_json, seo_meta,
           content_brief, lifetime_draft_iterations, created_at, updated_at)
           VALUES (?, ?, ?, 'DRAFTING', ?, ?, NULL, 0, ?, ?)""",
        (article_id, user_id, idea_id, json.dumps(outline), json.dumps(outline["seo_block"]), now, now),
    )
    await db.commit()

    # Run drafting — should work without content_brief
    llm = MockLLM()
    result = await run_drafting(db, FakeConfig(), article_id, llm)
    assert result["success"]

    cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
    article = await cursor.fetchone()
    assert article["state"] == "HUMANIZING"


@pytest.mark.asyncio
async def test_seed_image_role_columns(db):
    """Test that seed_images table has image_role and description columns."""
    now = utc_now()
    user_id = generate_id()
    await db.execute(
        """INSERT INTO users (id, email, subscription_status, ghost_key_valid, created_at, updated_at)
           VALUES (?, 'test@test.com', 'active', 1, ?, ?)""",
        (user_id, now, now),
    )

    batch_id, seed_ids = await create_seed_batch(db, user_id, [
        {"seed_type": "topic", "content": "Test"},
    ])

    img_id = generate_id()
    await db.execute(
        """INSERT INTO seed_images (id, seed_id, filename, storage_path, mime_type, image_role, description, created_at)
           VALUES (?, ?, 'test.jpg', '/tmp/test.jpg', 'image/jpeg', 'cover', 'A test image description', ?)""",
        (img_id, seed_ids[0], now),
    )
    await db.commit()

    cursor = await db.execute("SELECT image_role, description FROM seed_images WHERE id = ?", (img_id,))
    row = await cursor.fetchone()
    assert row["image_role"] == "cover"
    assert row["description"] == "A test image description"


@pytest.mark.asyncio
async def test_articles_content_brief_column(db):
    """Test that articles table has content_brief column."""
    cursor = await db.execute("PRAGMA table_info(articles)")
    columns = {row["name"] for row in await cursor.fetchall()}
    assert "content_brief" in columns


@pytest.mark.asyncio
async def test_describe_image_mock():
    """Test MockLLM.describe_image returns a description."""
    llm = MockLLM()
    desc = await llm.describe_image(b"\x89PNG\x00\x00")
    assert isinstance(desc, str)
    assert len(desc) > 20


@pytest.mark.asyncio
async def test_critique_user_message_with_brief_context():
    """Test that critique_user_message includes user_description and user_keywords."""
    from app.llm.prompts import critique_user_message

    msg = critique_user_message(
        article_title="Test Article",
        focus_keyword="test keyword",
        target_word_count=1500,
        meta_description="Test meta",
        humanized_draft_text="Some article text",
        user_description="Original user description",
        user_keywords="keyword1, keyword2",
    )
    assert "Original user description" in msg
    assert "keyword1, keyword2" in msg

    # Without brief context — should still work
    msg_no_brief = critique_user_message(
        article_title="Test Article",
        focus_keyword="test keyword",
        target_word_count=1500,
        meta_description="Test meta",
        humanized_draft_text="Some article text",
    )
    assert "Original user description" not in msg_no_brief


@pytest.mark.asyncio
async def test_image_prompter_user_message_with_photos():
    """Test that image_prompter_user_message includes user photo descriptions."""
    from app.llm.prompts import image_prompter_user_message

    msg = image_prompter_user_message(
        article_title="Test Article",
        focus_keyword="test keyword",
        article_text="Some article text",
        user_photo_descriptions=[
            {"role": "cover", "description": "A sunset over mountains"},
            {"role": "body", "description": "A cozy office workspace"},
        ],
    )
    assert "The user has provided their own photos for this article" in msg
    assert "A sunset over mountains" in msg
    assert "A cozy office workspace" in msg
    # Photos should come AFTER article text
    assert msg.index("Some article text") < msg.index("A sunset over mountains")

    msg_no_photos = image_prompter_user_message(
        article_title="Test Article",
        focus_keyword="test keyword",
        article_text="Some article text",
    )
    assert "The user has provided their own photos" not in msg_no_photos


@pytest.mark.asyncio
async def test_ideation_prompt_has_content_brief_section():
    """Test that the ideation prompt references the content brief format."""
    from app.llm.prompts import ideation_system_prompt

    prompt = ideation_system_prompt(ghost_url="test.ghost.io")
    assert "UNDERSTANDING THE BRIEF" in prompt
    assert "Description (always present)" in prompt


@pytest.mark.asyncio
async def test_critique_prompt_has_intent_alignment():
    """Test that the critique prompt includes the intent alignment dimension."""
    from app.llm.prompts import critique_system_prompt

    prompt = critique_system_prompt(
        iteration_number=1,
        max_iterations=5,
        article_title="Test",
        article_angle="Test angle",
        search_intent="Test intent",
        focus_keyword="test keyword",
    )
    assert "Intent alignment" in prompt
    assert "seven dimensions" in prompt


@pytest.mark.asyncio
async def test_outline_prompt_has_content_brief_section():
    """Test that the outline prompt includes the content brief section."""
    from app.llm.prompts import outline_system_prompt

    prompt = outline_system_prompt(target_word_count=1500, brand_voice="Casual")
    assert "CONTENT BRIEF (if provided)" in prompt
    assert "user_description" in prompt


@pytest.mark.asyncio
async def test_drafting_prompt_has_content_brief_section():
    """Test that the drafting prompt includes the content brief section."""
    from app.llm.prompts import drafting_system_prompt

    prompt = drafting_system_prompt(target_word_count=1500, focus_keyword="test")
    assert "## CONTENT BRIEF" in prompt
    assert "description" in prompt
