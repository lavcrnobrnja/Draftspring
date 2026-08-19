"""Ghost CMS integration service — JWT auth, image upload, post creation."""

import time

import jwt
import httpx


def generate_ghost_jwt(admin_api_key: str) -> str:
    """Generate a Ghost Admin API JWT from an API key.
    
    Key format: {id}:{secret_hex}
    Returns a signed JWT with kid=id, aud="/admin/", 5-min expiry.
    """
    if ":" not in admin_api_key:
        raise ValueError("Invalid Ghost API key format. Expected 'id:secret'")

    parts = admin_api_key.split(":")
    if len(parts) != 2:
        raise ValueError("Invalid Ghost API key format. Expected 'id:secret'")

    key_id, secret_hex = parts

    try:
        secret = bytes.fromhex(secret_hex)
    except ValueError:
        raise ValueError("Invalid Ghost API key secret (not valid hex)")

    iat = int(time.time())
    payload = {
        "iat": iat,
        "exp": iat + 300,  # 5 minutes
        "aud": "/admin/",
    }
    headers = {
        "alg": "HS256",
        "typ": "JWT",
        "kid": key_id,
    }

    return jwt.encode(payload, secret, algorithm="HS256", headers=headers)


async def validate_ghost_connection(url: str, api_key: str) -> dict:
    """Validate a Ghost API connection.
    
    Returns { valid: bool, site_title?: str, version?: str, error?: str }
    """
    if not url:
        return {"valid": False, "error": "Ghost URL is required"}

    if ":" not in api_key:
        return {"valid": False, "error": "Invalid API key format"}

    try:
        token = generate_ghost_jwt(api_key)
    except ValueError as e:
        return {"valid": False, "error": str(e)}

    try:
        base = url.rstrip('/')
        auth_header = {"Authorization": f"Ghost {token}"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Step 1: Fetch site info (may work without auth on some Ghost instances)
            resp = await client.get(f"{base}/ghost/api/admin/site/", headers=auth_header)

            if resp.status_code == 401:
                return {"valid": False, "error": "Invalid API key"}
            if resp.status_code != 200:
                return {"valid": False, "error": f"Ghost API error: {resp.status_code}"}

            data = resp.json()
            site = data.get("site", {})
            version = site.get("version", "0.0.0")

            # Check minimum version
            major = int(version.split(".")[0]) if version else 0
            if major < 5:
                return {"valid": False, "error": f"Ghost version {version} is too old. Minimum: 5.0"}

            # Step 2: Verify the key actually works by hitting an auth-required endpoint
            auth_resp = await client.get(f"{base}/ghost/api/admin/users/?limit=1", headers=auth_header)
            if auth_resp.status_code == 401:
                return {"valid": False, "error": "Invalid API key — could not authenticate"}
            if auth_resp.status_code != 200:
                return {"valid": False, "error": f"Ghost API key rejected: {auth_resp.status_code}"}

            return {
                "valid": True,
                "site_title": site.get("title", ""),
                "version": version,
            }
    except httpx.ConnectError:
        return {"valid": False, "error": "Could not connect to Ghost"}
    except Exception as e:
        return {"valid": False, "error": f"Connection error: {str(e)}"}


async def fetch_ghost_staff(url: str, api_key: str) -> dict:
    """Fetch all staff users from a Ghost blog.
    
    Returns {"staff": [...], "error": None} on success.
    Returns {"staff": [], "error": "reason"} on failure.
    Staff entries are {id, name, email, role} dicts, sorted by role weight (Owner first).
    """
    try:
        token = generate_ghost_jwt(api_key)
    except ValueError as e:
        return {"staff": [], "error": f"Invalid API key format: {e}"}

    role_order = {"Owner": 0, "Administrator": 1, "Editor": 2, "Author": 3, "Contributor": 4}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{url.rstrip('/')}/ghost/api/admin/users/?include=roles&limit=all",
                headers={"Authorization": f"Ghost {token}"},
            )

            if resp.status_code == 401:
                return {"staff": [], "error": "Ghost API key is invalid or expired. Please reconnect with a new key."}
            if resp.status_code != 200:
                return {"staff": [], "error": f"Ghost returned HTTP {resp.status_code}"}

            data = resp.json()
            users = data.get("users", [])

            staff = []
            for u in users:
                if u.get("status") != "active":
                    continue
                roles = u.get("roles", [])
                role_name = roles[0]["name"] if roles else "Unknown"
                # Skip integration users — they're API keys, not real authors
                if "integration" in role_name.lower():
                    continue
                staff.append({
                    "id": u["id"],
                    "name": u.get("name", u.get("email", "Unknown")),
                    "email": u.get("email", ""),
                    "role": role_name,
                })

            staff.sort(key=lambda s: role_order.get(s["role"], 99))
            return {"staff": staff, "error": None}
    except httpx.ConnectError:
        return {"staff": [], "error": "Could not connect to Ghost. Check your Ghost URL."}
    except Exception as e:
        return {"staff": [], "error": f"Unexpected error: {str(e)}"}


async def upload_image_to_ghost(
    ghost_url: str,
    api_key: str,
    image_data: bytes,
    filename: str,
) -> str:
    """Upload an image to Ghost. Returns the Ghost-hosted URL.
    
    Uses POST /ghost/api/admin/images/upload/ (multipart form data).
    """
    token = generate_ghost_jwt(api_key)
    url = f"{ghost_url.rstrip('/')}/ghost/api/admin/images/upload/"

    # Determine content type from filename
    content_type = "image/webp"
    if filename.endswith(".png"):
        content_type = "image/png"
    elif filename.endswith(".jpg") or filename.endswith(".jpeg"):
        content_type = "image/jpeg"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Ghost {token}"},
            files={"file": (filename, image_data, content_type)},
        )

        if resp.status_code not in (200, 201):
            raise Exception(f"Ghost image upload failed: {resp.status_code} — {resp.text[:200]}")

        data = resp.json()
        return data["images"][0]["url"]


async def create_ghost_post(
    ghost_url: str,
    api_key: str,
    post_data: dict,
) -> dict:
    """Create a post on Ghost. Returns the created post data.
    
    Uses POST /ghost/api/admin/posts/?source=html
    """
    token = generate_ghost_jwt(api_key)
    url = f"{ghost_url.rstrip('/')}/ghost/api/admin/posts/?source=html"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Ghost {token}",
                "Content-Type": "application/json",
            },
            json={"posts": [post_data]},
        )

        if resp.status_code not in (200, 201):
            raise Exception(f"Ghost post creation failed: {resp.status_code} — {resp.text[:200]}")

        data = resp.json()
        post = data["posts"][0]
        return {
            "id": post["id"],
            "url": post.get("url", ""),
            "slug": post.get("slug", ""),
        }


async def check_duplicate_post(
    ghost_url: str,
    api_key: str,
    slug: str,
) -> dict | None:
    """Check if a post with the given slug already exists on Ghost.
    
    Returns post data if found, None otherwise. Used for crash recovery.
    """
    token = generate_ghost_jwt(api_key)
    url = f"{ghost_url.rstrip('/')}/ghost/api/admin/posts/slug/{slug}/"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Ghost {token}"},
        )

        if resp.status_code != 200:
            return None

        data = resp.json()
        posts = data.get("posts", [])
        if not posts:
            return None

        return posts[0]
