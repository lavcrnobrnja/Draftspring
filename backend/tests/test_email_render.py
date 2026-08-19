"""Test email rendering for CP2 article review email.

Renders the email with mock data, saves to /tmp for visual inspection,
and verifies structural requirements (image constraints, overflow, max-width).
"""

import asyncio
import re
import sys
import os

# Ensure backend is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class MockConfig:
    APP_ENV = "test"
    APP_BASE_URL = "https://app.draftspring.io"
    RESEND_API_KEY = "test"
    EMAIL_FROM_NAME = "DraftSpring"
    EMAIL_FROM_ADDRESS = "noreply@draftspring.io"


SAMPLE_ARTICLE_HTML = """\
<h2>Why Remote Work Is Here to Stay</h2>
<p>The landscape of work has fundamentally shifted. What began as a necessity during global lockdowns has evolved into a <strong>preferred mode of operation</strong> for millions of knowledge workers worldwide.</p>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:16px 0;overflow:hidden;">
<tr><td align="center" style="overflow:hidden;">
<img src="https://example.com/images/remote-work-stats.jpg" alt="Remote work statistics" style="display:block;max-width:100%;width:100%;height:auto;border-radius:8px;" />
</td></tr></table>

<h3>The Productivity Paradox</h3>
<p>Studies consistently show that remote workers are <em>more productive</em> than their in-office counterparts. A Stanford study found a 13% performance increase among remote workers.</p>

<p>Here are the key benefits:</p>
<ul>
<li>Flexible scheduling leads to better work-life balance</li>
<li>Reduced commute time saves an average of 40 minutes per day</li>
<li>Lower overhead costs for employers</li>
<li>Access to a global talent pool</li>
</ul>

<img src="https://example.com/images/chart-no-style.png" alt="A chart with no inline styles" />

<h3>Challenges to Address</h3>
<p>Of course, remote work isn't without its challenges. Team cohesion, communication overhead, and the blurring of work-life boundaries remain real concerns that organizations must actively manage.</p>

<blockquote>
<p>"The future of work is not about where you work, but how you work." — Satya Nadella</p>
</blockquote>

<p>Companies that invest in asynchronous communication tools, clear documentation practices, and intentional team-building will be the ones that thrive in this new paradigm.</p>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:16px 0;overflow:hidden;">
<tr><td align="center" style="overflow:hidden;">
<img src="https://example.com/images/future-office.jpg" alt="The future office" style="display:block;max-width:100%;width:100%;height:auto;border-radius:8px;" />
</td></tr></table>

<h3>Conclusion</h3>
<p>Remote work is not a trend — it's a structural shift. The companies that recognize this and adapt their cultures accordingly will attract the best talent and build more resilient organizations.</p>
"""


async def render_and_save():
    from app.services.email import send_article_review_email, get_sent_emails, clear_sent_emails

    clear_sent_emails()

    config = MockConfig()

    # Test WITH cover image
    await send_article_review_email(
        config=config,
        to="test@example.com",
        article_title="Why Remote Work Is Here to Stay: A Comprehensive Analysis of the Modern Workplace",
        article_html=SAMPLE_ARTICLE_HTML,
        cover_image_url="https://images.unsplash.com/photo-1522202176988-66273c2fd55f?w=800",
        magic_link_token="test-token-abc123-def456",
        next_publish_date_formatted="Tuesday, March 25 at 9:00 AM EST",
    )

    emails = get_sent_emails()
    assert len(emails) == 1, f"Expected 1 email, got {len(emails)}"

    html_with_cover = emails[0]["html"]

    # Save desktop preview
    with open("/tmp/email_preview_desktop.html", "w") as f:
        f.write(html_with_cover)
    print("✅ Saved /tmp/email_preview_desktop.html")

    # Test WITHOUT cover image
    clear_sent_emails()
    await send_article_review_email(
        config=config,
        to="test@example.com",
        article_title="Short Title Test",
        article_html="<p>A brief article body.</p>",
        cover_image_url=None,
        magic_link_token="test-token-no-cover",
        next_publish_date_formatted="Wednesday, March 26 at 10:00 AM EST",
    )
    emails = get_sent_emails()
    html_no_cover = emails[0]["html"]

    with open("/tmp/email_preview_no_cover.html", "w") as f:
        f.write(html_no_cover)
    print("✅ Saved /tmp/email_preview_no_cover.html")

    # ── Structural assertions ──

    errors = []

    # 1. All <img> tags must have max-width style
    img_tags = re.findall(r'<img\s[^>]+>', html_with_cover, re.IGNORECASE)
    for i, tag in enumerate(img_tags):
        if "max-width" not in tag:
            errors.append(f"Image #{i+1} missing max-width: {tag[:100]}")

    # 2. Card max-width is 600px
    if 'max-width:600px' not in html_with_cover:
        errors.append("Card missing max-width:600px")

    # 3. Article body td has overflow:hidden
    if 'overflow:hidden;word-break:break-word' not in html_with_cover:
        errors.append("Article body td missing overflow:hidden;word-break:break-word")

    # 4. table-layout:fixed on the card
    if 'table-layout:fixed' not in html_with_cover:
        errors.append("Card table missing table-layout:fixed")

    # 5. Cover image should be 120px thumbnail, not full-width
    if 'width:120px;height:120px' not in html_with_cover:
        errors.append("Cover image not rendered as 120px thumbnail")

    # 6. No-cover version should NOT have thumbnail
    if 'width:120px' in html_no_cover:
        errors.append("No-cover version incorrectly shows thumbnail")

    # 7. Both versions have action buttons
    for label, h in [("with-cover", html_with_cover), ("no-cover", html_no_cover)]:
        if "Request Revision" not in h:
            errors.append(f"{label}: missing Request Revision button")
        if "Approve &amp; Publish" not in h:
            errors.append(f"{label}: missing Approve & Publish button")

    # 8. Accent bar present
    if 'linear-gradient' not in html_with_cover:
        errors.append("Missing accent bar gradient")

    if errors:
        print("\n❌ FAILURES:")
        for e in errors:
            print(f"   - {e}")
        sys.exit(1)
    else:
        print("\n✅ All structural checks passed!")
        print(f"   - {len(img_tags)} <img> tags all have max-width")
        print(f"   - Card max-width: 600px ✓")
        print(f"   - Article body overflow: hidden ✓")
        print(f"   - table-layout: fixed ✓")
        print(f"   - Cover image: 120px thumbnail ✓")
        print(f"   - Action buttons: present ✓")


if __name__ == "__main__":
    asyncio.run(render_and_save())
