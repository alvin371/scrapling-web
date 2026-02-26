"""
Threads scraping via Scrapling FetcherSession.
- Threads server-side renders post data in <script type="application/json"> tags.
- Two scripts are parsed:
    - Profile script: user metadata (followers, bio, verified, etc.)
    - Posts script:   thread_items with likes, replies, captions, images, timestamps.
"""
import json
import re
from datetime import datetime, timezone
from scrapling.fetchers import FetcherSession
from loguru import logger
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


def _parse_bbox(data: dict, *path: str):
    """Navigate require[0][3][0].__bbox.require[0][3][1].__bbox.result.data then follow path."""
    try:
        result = (
            data["require"][0][3][0]["__bbox"]
                ["require"][0][3][1]["__bbox"]
                ["result"]["data"]
        )
        for key in path:
            result = result[key]
        return result
    except (KeyError, IndexError, TypeError):
        return None


def _fetch_page(username: str):
    url = f"https://www.threads.net/@{username}"
    with FetcherSession(impersonate="chrome", stealthy_headers=True) as s:
        page = s.get(url)
    return page


def scrape_threads_profile(username: str) -> dict:
    logger.info(f"[threads] Scraping profile: {username}")
    page = _fetch_page(username)

    for script in page.css('script[type="application/json"]'):
        raw = script.text or ""
        if "follower_count" not in raw or "thread_items" in raw:
            continue
        try:
            user = _parse_bbox(json.loads(raw), "user")
            if user and user.get("username"):
                return {
                    "username":     username,
                    "display_name": user.get("full_name"),
                    "bio":          user.get("biography") or user.get("text_app_biography"),
                    "followers":    user.get("follower_count", 0),
                    "is_verified":  user.get("is_verified", False),
                    "profile_pic":  user.get("profile_pic_url"),
                    "url":          f"https://www.threads.net/@{username}",
                }
        except (json.JSONDecodeError, TypeError):
            continue

    # CSS fallback
    return {
        "username":     username,
        "display_name": page.css("h1::text").get(),
        "bio":          page.css('meta[name="description"]::attr(content)').get(),
        "url":          f"https://www.threads.net/@{username}",
    }


def scrape_threads_posts(username: str, limit: int = 20) -> list[dict]:
    logger.info(f"[threads] Scraping posts for: {username}, limit={limit}")
    page = _fetch_page(username)

    # --- Profile metadata (followers, bio, etc.) ---
    profile_user = {}
    for script in page.css('script[type="application/json"]'):
        raw = script.text or ""
        if "follower_count" not in raw or "thread_items" in raw:
            continue
        try:
            user = _parse_bbox(json.loads(raw), "user")
            if user and user.get("username"):
                profile_user = user
                break
        except (json.JSONDecodeError, TypeError):
            continue

    biography        = profile_user.get("biography") or profile_user.get("text_app_biography", "")
    followers        = profile_user.get("follower_count", 0)
    is_verified      = profile_user.get("is_verified", False)
    profile_name     = profile_user.get("full_name", username)
    profile_image    = profile_user.get("profile_pic_url", "")
    author_id        = profile_user.get("pk") or profile_user.get("id", "")

    # --- Posts ---
    edges = None
    for script in page.css('script[type="application/json"]'):
        raw = script.text or ""
        if "thread_items" not in raw or "like_count" not in raw:
            continue
        try:
            edges = _parse_bbox(json.loads(raw), "mediaData", "edges")
            if edges:
                break
        except (json.JSONDecodeError, TypeError):
            continue

    if not edges:
        logger.warning(f"[threads] No embedded post JSON found for {username}")
        return []

    posts = []
    for edge in edges[:limit]:
        try:
            thread_items = edge["node"]["thread_items"]
        except (KeyError, TypeError):
            continue

        for item in thread_items:
            post = item.get("post", {})
            if not post:
                continue

            caption_obj = post.get("caption") or {}
            caption = caption_obj.get("text", "") if isinstance(caption_obj, dict) else ""

            tpai = post.get("text_post_app_info") or {}
            comments = tpai.get("direct_reply_count", 0) or 0

            candidates = (post.get("image_versions2") or {}).get("candidates", [])
            image_url     = candidates[0]["url"] if candidates else ""
            thumbnail_src = candidates[-1]["url"] if candidates else ""
            thumbnails    = [
                {"src": c["url"], "width": c.get("width"), "height": c.get("height")}
                for c in candidates
            ]

            taken_at = post.get("taken_at")
            dt = datetime.fromtimestamp(taken_at, tz=timezone.utc).isoformat() if taken_at else None

            code = post.get("code", "")
            post_url = post.get("canonical_url") or (
                f"https://www.threads.net/@{username}/post/{code}" if code else ""
            )

            media_type_map = {1: "Image", 2: "Video", 8: "Sidecar", 19: "Text"}
            media_type = media_type_map.get(post.get("media_type", 19), "Text")
            if not image_url and not candidates:
                media_type = "Text"

            posts.append({
                "account":            username,
                "caption":            caption,
                "profile_name":       profile_name,
                "profile_image_link": profile_image,
                "author_id":          str(author_id),
                "biography":          biography,
                "id":                 post.get("pk", code),
                "following":          0,
                "likes":              post.get("like_count", 0),
                "media_type":         media_type,
                "posts_count":        0,
                "followers":          followers,
                "is_verified":        is_verified,
                "datetime":           dt,
                "image_url":          image_url,
                "url":                post_url,
                "comments":           comments,
                "thumbnail_src":      thumbnail_src,
                "thumbnails":         thumbnails,
                "message":            caption,
            })

    logger.info(f"[threads] Extracted {len(posts)} posts for {username}")
    return posts[:limit]


def _map_threads_post(post: dict, username: str) -> dict:
    user = post.get("user") or {}

    caption_obj = post.get("caption") or {}
    caption = caption_obj.get("text", "") if isinstance(caption_obj, dict) else ""

    tpai = post.get("text_post_app_info") or {}
    comments = tpai.get("direct_reply_count", 0) or 0
    reposts  = tpai.get("repost_count", 0) or 0
    quotes   = tpai.get("quote_count", 0) or 0

    candidates = (post.get("image_versions2") or {}).get("candidates", [])
    image_url     = candidates[0]["url"] if candidates else ""
    thumbnail_src = candidates[-1]["url"] if candidates else ""
    thumbnails    = [
        {"src": c["url"], "width": c.get("width"), "height": c.get("height")}
        for c in candidates
    ]

    taken_at = post.get("taken_at")
    dt = datetime.fromtimestamp(taken_at, tz=timezone.utc).isoformat() if taken_at else None

    code = post.get("code", "")
    post_url = post.get("canonical_url") or (
        f"https://www.threads.net/@{username}/post/{code}" if code else ""
    )

    media_type_map = {1: "Image", 2: "Video", 8: "Sidecar", 19: "Text"}
    media_type = media_type_map.get(post.get("media_type", 19), "Text")
    if not image_url and not candidates:
        media_type = "Text"

    biography     = user.get("biography") or user.get("text_app_biography", "")
    followers     = user.get("follower_count", 0)
    is_verified   = user.get("is_verified", False)
    profile_name  = user.get("full_name", username)
    profile_image = user.get("profile_pic_url", "")
    author_id     = str(user.get("pk") or user.get("id", ""))

    return {
        "account":            username,
        "caption":            caption,
        "likes":              post.get("like_count", 0),
        "comments":           comments,
        "reposts":            reposts,
        "quotes":             quotes,
        "views":              0,   # Threads does not expose view counts in SSR JSON
        "saves":              0,   # Threads does not expose save counts in SSR JSON
        "media_type":         media_type,
        "image_url":          image_url,
        "thumbnail_src":      thumbnail_src,
        "thumbnails":         thumbnails,
        "datetime":           dt,
        "url":                post_url,
        "author_id":          author_id,
        "biography":          biography,
        "followers":          followers,
        "is_verified":        is_verified,
        "profile_name":       profile_name,
        "profile_image_link": profile_image,
        "following":          0,
        "posts_count":        0,
        "message":            caption,
        "id":                 post.get("pk", code),
    }


def _extract_thread_items_from_edges(edges) -> list | None:
    """Pull thread_items out of the first edge node."""
    try:
        return edges[0]["node"]["thread_items"]
    except (IndexError, KeyError, TypeError):
        return None


def _parse_abbreviated_count(text: str) -> int:
    """Convert '7.6K' → 7600, '1.2M' → 1200000, '1,234' → 1234. Returns 0 on failure."""
    text = text.strip().replace(",", "")
    m = re.fullmatch(r"([\d.]+)([KkMmBb]?)", text)
    if not m:
        return 0
    n = float(m.group(1))
    suffix = m.group(2).upper()
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
    return int(n * multiplier)


def _extract_views_from_html(html: str) -> int:
    """
    Parse the abbreviated view count Threads embeds in its SSR HTML.
    Handles patterns like '7.6K views', '1.2M views', '999 views'.
    Returns 0 if not found.
    """
    m = re.search(r"([\d,.]+[KkMmBb]?)\s*views", html)
    if m:
        return _parse_abbreviated_count(m.group(1))
    return 0


def _get_threads_post_views(post_url: str, session_id: str) -> int:
    """
    Opens post_url in a headless browser with a Threads session cookie.
    Primary strategy: click the 'View activity' button and intercept the API
    response (only works when the session belongs to the post owner).
    Fallback: parse the abbreviated view count shown in the page body
    (e.g. '7.6K views' → 7600), which works for any post.
    Returns 0 on complete failure.
    """
    import urllib.parse
    session_id = urllib.parse.unquote(session_id)
    views = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        # --- Network interception: capture exact view_count from API response ---
        def handle_response(response):
            nonlocal views
            if views:
                return  # already captured
            try:
                if "graphql" not in response.url and "media" not in response.url:
                    return
                data = response.json()
                items = None
                if isinstance(data, dict):
                    if "data" in data:
                        for v in data["data"].values():
                            if isinstance(v, dict) and "items" in v:
                                items = v["items"]
                                break
                    if not items and "items" in data:
                        items = data["items"]
                if items and isinstance(items, list) and items:
                    vc = items[0].get("view_count") or items[0].get("play_count")
                    if vc is not None:
                        views = int(vc)
            except Exception:
                pass

        page.on("response", handle_response)

        try:
            # threads.net redirects to threads.com; inject the cookie via JS
            # after an initial navigation so the domain is correct.
            page.goto("https://www.threads.com", wait_until="domcontentloaded", timeout=15_000)
            page.evaluate(
                f"document.cookie = 'sessionid={session_id}; domain=.threads.com; path=/; secure'"
            )

            page.goto(post_url, wait_until="networkidle", timeout=30_000)

            # --- Try "View activity" element ---
            # Threads renders this as a <span> (not a <button>); the inner SVG
            # carries aria-label="View activity". We try several selectors in
            # order of reliability.
            btn = (
                page.query_selector('svg[aria-label="View activity"]')
                or page.query_selector('span:has-text("View activity")')
                or page.query_selector('[aria-label="View activity"]')
            )
            if btn:
                btn.click()
                page.wait_for_timeout(2000)  # allow API call + render

                # Fallback: parse modal DOM if network didn't yield an exact count
                if not views:
                    try:
                        dialog = page.wait_for_selector('[role="dialog"]', timeout=5000)
                        if dialog:
                            text = dialog.inner_text()
                            match = re.search(
                                r"([\d,]+)\s*\n?\s*[Vv]iews|[Vv]iews\s*\n?\s*([\d,]+)",
                                text,
                            )
                            if match:
                                raw = (match.group(1) or match.group(2)).replace(",", "")
                                views = int(raw)
                    except PlaywrightTimeout:
                        pass

            # --- Fallback: parse abbreviated count from page body text ---
            if not views:
                body_text = page.inner_text("body")
                views = _extract_views_from_html(body_text)
                if views:
                    logger.info(f"[threads] Parsed approximate views from page body: {views}")
                else:
                    logger.warning(f"[threads] Could not find view count on {post_url}")

        except Exception as exc:
            logger.warning(f"[threads] Could not retrieve views for {post_url}: {exc}")
        finally:
            browser.close()

    return views


def scrape_threads_post(username: str, post_code: str) -> dict:
    logger.info(f"[threads] Scraping single post: @{username}/post/{post_code}")
    # threads.com and threads.net both work; prefer .net for consistency
    url = f"https://www.threads.net/@{username}/post/{post_code}"
    with FetcherSession(impersonate="chrome", stealthy_headers=True) as s:
        page = s.get(url)

    for script in page.css('script[type="application/json"]'):
        raw = script.text or ""
        if "thread_items" not in raw:
            continue
        try:
            data = json.loads(raw)
            # Post page: data.data.edges  (confirmed via live inspection)
            edges = _parse_bbox(data, "data", "edges")
            thread_items = _extract_thread_items_from_edges(edges)
            if not thread_items:
                # Profile-style fallback: mediaData.edges
                edges = _parse_bbox(data, "mediaData", "edges")
                thread_items = _extract_thread_items_from_edges(edges)
            if thread_items:
                post = thread_items[0].get("post", {})
                if post:
                    logger.info(f"[threads] Post scraped: {post_code} likes={post.get('like_count')}")
                    result = _map_threads_post(post, username)

                    # Approximate view count is embedded in the SSR HTML (e.g. "7.6K views")
                    approx_views = _extract_views_from_html(page.html_content or "")
                    if approx_views:
                        result["views"] = approx_views

                    from config import settings
                    if settings.threads_session_id:
                        # Playwright path: may return exact count (post owner) or
                        # the same approximate count — use whichever is higher.
                        exact_views = _get_threads_post_views(
                            result.get("url") or url,
                            settings.threads_session_id,
                        )
                        if exact_views:
                            result["views"] = exact_views

                    return result
        except (json.JSONDecodeError, TypeError, IndexError, KeyError):
            continue

    return {"username": username, "post_code": post_code, "url": url, "error": "Could not parse post data"}
