"""Stripe Checkout and Billing Portal routes."""

import stripe
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.database import get_connection
from app.middleware.auth_middleware import get_current_session

router = APIRouter(tags=["checkout"])


@router.get("/api/checkout/session")
async def create_checkout_session(request: Request):
    """Create a Stripe Checkout session and return the URL."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Get user info
        cursor = await db.execute(
            "SELECT id, email, stripe_customer_id FROM users WHERE id = ?",
            (session["user_id"],),
        )
        user = await cursor.fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

    # Create Stripe checkout session
    stripe.api_key = config.STRIPE_SECRET_KEY

    checkout_params = {
        "mode": "subscription",
        "line_items": [{"price": config.STRIPE_PRICE_ID, "quantity": 1}],
        "success_url": f"{config.APP_BASE_URL}/subscribe?checkout=success",
        "cancel_url": f"{config.APP_BASE_URL}/subscribe?checkout=cancel",
        "client_reference_id": user["id"],
        "customer_email": user["email"],
        "subscription_data": {"trial_period_days": config.STRIPE_TRIAL_DAYS},
    }

    # If user already has a Stripe customer ID, use it
    if user["stripe_customer_id"]:
        checkout_params["customer"] = user["stripe_customer_id"]
        del checkout_params["customer_email"]

    try:
        checkout_session = stripe.checkout.Session.create(**checkout_params)
        return {"url": checkout_session.url, "session_id": checkout_session.id}
    except stripe.AuthenticationError:
        raise HTTPException(status_code=503, detail="Payment system is not configured. Please contact support.")
    except stripe.StripeError as e:
        raise HTTPException(status_code=400, detail="Payment processing error. Please try again later.")


@router.post("/api/billing/portal")
async def create_billing_portal(request: Request):
    """Create a Stripe Customer Portal session."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        cursor = await db.execute(
            "SELECT stripe_customer_id FROM users WHERE id = ?",
            (session["user_id"],),
        )
        user = await cursor.fetchone()
        if not user or not user["stripe_customer_id"]:
            raise HTTPException(status_code=400, detail="No active subscription")

    stripe.api_key = config.STRIPE_SECRET_KEY

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=user["stripe_customer_id"],
            return_url=f"{config.APP_BASE_URL}/dashboard",
        )
        return {"url": portal_session.url}
    except stripe.AuthenticationError:
        raise HTTPException(status_code=503, detail="Payment system is not configured. Please contact support.")
    except stripe.StripeError as e:
        raise HTTPException(status_code=400, detail="Payment processing error. Please try again later.")


# Landing page HTML
LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DraftSpring — AI Content for Ghost Blogs</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: #0A0F1E;
            color: #F8FAFC;
            min-height: 100vh;
            overflow-x: hidden;
        }
        .aurora {
            position: fixed; top: -200px; left: -200px;
            width: 600px; height: 600px;
            background: radial-gradient(circle, rgba(59,130,246,0.15), transparent 70%);
            filter: blur(80px);
            z-index: 0;
        }
        .aurora-2 {
            position: fixed; top: 100px; right: -200px;
            width: 500px; height: 500px;
            background: radial-gradient(circle, rgba(139,92,246,0.12), transparent 70%);
            filter: blur(80px);
            z-index: 0;
        }
        .container {
            max-width: 800px; margin: 0 auto; padding: 80px 24px;
            position: relative; z-index: 1;
            text-align: center;
        }
        h1 {
            font-size: 3rem; font-weight: 800;
            letter-spacing: -0.03em; line-height: 1.1;
            margin-bottom: 24px;
            background: linear-gradient(135deg, #3B82F6, #8B5CF6, #06B6D4);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .subtitle {
            font-size: 1.25rem; color: #94a3b8; line-height: 1.7;
            max-width: 600px; margin: 0 auto 48px;
        }
        .price-card {
            background: #111827;
            border: 1px solid rgba(148,163,184,0.08);
            border-radius: 24px; padding: 40px;
            max-width: 400px; margin: 0 auto 40px;
            backdrop-filter: blur(16px);
        }
        .price { font-size: 3rem; font-weight: 800; }
        .price span { font-size: 1rem; color: #94a3b8; font-weight: 400; }
        .features { list-style: none; text-align: left; margin: 24px 0; }
        .features li {
            padding: 8px 0; color: #94a3b8; font-size: 0.95rem;
        }
        .features li::before { content: "✓ "; color: #10B981; font-weight: 700; }
        .cta-btn {
            display: inline-block; padding: 16px 40px;
            background: linear-gradient(135deg, #3B82F6, #8B5CF6);
            color: white; font-weight: 700; font-size: 1.1rem;
            border: none; border-radius: 12px; cursor: pointer;
            text-decoration: none;
            box-shadow: 0 4px 24px rgba(59,130,246,0.25);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .cta-btn:hover { transform: translateY(-2px); box-shadow: 0 8px 32px rgba(59,130,246,0.35); }
        .login-link {
            display: block; margin-top: 16px;
            color: #64748b; font-size: 0.9rem; text-decoration: none;
        }
        .login-link:hover { color: #94a3b8; }
        .steps {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 24px; margin: 64px 0; text-align: left;
        }
        .step {
            background: #111827;
            border: 1px solid rgba(148,163,184,0.08);
            border-radius: 16px; padding: 24px;
        }
        .step-num {
            width: 32px; height: 32px; border-radius: 8px;
            background: linear-gradient(135deg, #3B82F6, #8B5CF6);
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; font-size: 0.85rem; margin-bottom: 12px;
        }
        .step h3 { font-size: 1rem; font-weight: 600; margin-bottom: 6px; }
        .step p { font-size: 0.85rem; color: #94a3b8; line-height: 1.5; }
    </style>
</head>
<body>
    <div class="aurora"></div>
    <div class="aurora-2"></div>
    <div class="container">
        <h1>DraftSpring</h1>
        <p class="subtitle">
            AI-powered content automation for your Ghost blog.
            Submit topics, we research, write, edit, generate images, and publish — with your approval at every step.
        </p>
        <div class="price-card">
            <div class="price">$9<span>/mo</span></div>
            <ul class="features">
                <li>8 articles per billing cycle</li>
                <li>AI research, writing & editing</li>
                <li>Auto-generated images</li>
                <li>SEO optimization built in</li>
                <li>Two human checkpoints</li>
                <li>Direct Ghost publishing</li>
                <li>7-day free trial</li>
            </ul>
        </div>
        <a href="/login" class="cta-btn">Start Free Trial</a>
        <a href="/login" class="login-link">Already have an account? Log in →</a>

        <div class="steps">
            <div class="step">
                <div class="step-num">1</div>
                <h3>Submit Topics</h3>
                <p>Give us your seed topics or URLs. We'll generate article ideas for you to choose from.</p>
            </div>
            <div class="step">
                <div class="step-num">2</div>
                <h3>Review & Approve</h3>
                <p>Pick the ideas you like. We research, write, humanize, and edit each article automatically.</p>
            </div>
            <div class="step">
                <div class="step-num">3</div>
                <h3>Publish to Ghost</h3>
                <p>Review the final article with images. Approve it and we publish on your schedule.</p>
            </div>
        </div>
    </div>
</body>
</html>"""


@router.get("/")
async def root(request: Request):
    """Root route: authenticated → redirect to runway, unauthenticated → landing."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if session:
            return RedirectResponse(url="/dashboard", status_code=307)

    return HTMLResponse(content=LANDING_HTML, status_code=200)
