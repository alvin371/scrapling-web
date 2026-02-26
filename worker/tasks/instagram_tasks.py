"""
Instagram scraping via Scrapling.
- Authenticated path: uses INSTAGRAM_SESSION_ID cookie to hit the internal
  /api/v1/users/web_profile_info/ JSON endpoint — returns rich post data.
- Unauthenticated path: Instagram now requires login for all browser views,
  so results will be empty unless a session cookie is configured.
"""
import json
from datetime import datetime, timezone
from urllib.parse import unquote
from scrapling.fetchers import FetcherSession, StealthySession
from loguru import logger
from config import settings

# Instagram's internal web app ID (public, used by instagram.com itself)
_IG_APP_ID = "936619743392459"


def _get_session_id() -> str:
    """Return the URL-decoded Instagram session ID."""
    return unquote(settings.instagram_session_id.strip())


def _ig_api_headers(session_id: str) -> dict:
    return {
        "x-ig-app-id": _IG_APP_ID,
        "x-requested-with": "XMLHttpRequest",
        "cookie": f"sessionid={session_id}",
    }


def _parse_user_node(user: dict, username: str, limit: int) -> list[dict]:
    """Map the Instagram user node (from web_profile_info or embedded JSON) to the target format."""
    biography = user.get("biography", "")
    author_id = user.get("id", "")
    followers = (user.get("edge_followed_by") or {}).get("count", 0)
    following = (user.get("edge_follow") or {}).get("count", 0)
    posts_count = (user.get("edge_owner_to_timeline_media") or {}).get("count", 0)
    is_verified = user.get("is_verified", False)
    profile_name = user.get("full_name", username)
    profile_image_link = user.get("profile_pic_url_hd") or user.get("profile_pic_url", "")

    edges = (
        (user.get("edge_owner_to_timeline_media") or {}).get("edges")
        or (user.get("edge_felix_video_timeline") or {}).get("edges")
        or []
    )

    media_type_map = {"GraphImage": "Image", "GraphVideo": "Video", "GraphSidecar": "Sidecar"}
    posts = []

    for edge in edges[:limit]:
        node = edge.get("node", {})
        shortcode = node.get("shortcode", "")
        post_url = f"https://www.instagram.com/p/{shortcode}/" if shortcode else ""

        caption_edges = (node.get("edge_media_to_caption") or {}).get("edges", [])
        caption = caption_edges[0]["node"]["text"] if caption_edges else ""

        likes = (node.get("edge_liked_by") or node.get("edge_media_preview_like") or {}).get("count", 0)
        comments = (node.get("edge_media_to_comment") or node.get("edge_media_preview_comment") or {}).get("count", 0)

        image_url = node.get("display_url", "")
        thumbnail_src = node.get("thumbnail_src") or image_url
        thumbnails = [
            {"src": t.get("src", ""), "width": t.get("config_width"), "height": t.get("config_height")}
            for t in node.get("thumbnail_resources", [])
        ]

        taken_at = node.get("taken_at_timestamp")
        dt = datetime.fromtimestamp(taken_at, tz=timezone.utc).isoformat() if taken_at else None

        posts.append({
            "account": username,
            "caption": caption,
            "profile_name": profile_name,
            "profile_image_link": profile_image_link,
            "author_id": author_id,
            "biography": biography,
            "id": node.get("id", shortcode),
            "following": following,
            "likes": likes,
            "media_type": media_type_map.get(node.get("__typename", ""), "Image"),
            "posts_count": posts_count,
            "followers": followers,
            "is_verified": is_verified,
            "datetime": dt,
            "image_url": image_url,
            "url": post_url,
            "comments": comments,
            "thumbnail_src": thumbnail_src,
            "thumbnails": thumbnails,
            "message": caption,
        })

    return posts


def scrape_instagram_profile(username: str) -> dict:
    logger.info(f"[instagram] Scraping profile: {username}")
    url = f"https://www.instagram.com/{username}/"

    session_id = _get_session_id()
    if session_id:
        api_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        try:
            with FetcherSession(impersonate="chrome", stealthy_headers=True) as s:
                resp = s.get(api_url, headers=_ig_api_headers(session_id))
            data = resp.json()
            user = data["data"]["user"]
            return {
                "username": username,
                "display_name": user.get("full_name"),
                "bio": user.get("biography"),
                "followers": (user.get("edge_followed_by") or {}).get("count", 0),
                "following": (user.get("edge_follow") or {}).get("count", 0),
                "post_count": (user.get("edge_owner_to_timeline_media") or {}).get("count", 0),
                "is_verified": user.get("is_verified", False),
                "profile_pic_url": user.get("profile_pic_url_hd") or user.get("profile_pic_url"),
            }
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"[instagram] API profile parse failed for {username}: {e}")

    # Unauthenticated browser fallback
    with StealthySession(
        headless=True,
        solve_cloudflare=True,
        proxy=settings.scrapling_proxy.strip() or None,
    ) as session:
        page = session.fetch(url, network_idle=True)

    return {
        "username": username,
        "display_name": page.css('header h2::text').get(),
        "bio":          page.css('header section > div:last-child span::text').get(),
        "followers":    page.css('header ul li:nth-child(2) span::text').get(),
        "following":    page.css('header ul li:nth-child(3) span::text').get(),
        "post_count":   page.css('header ul li:nth-child(1) span::text').get(),
        "raw_html_len": len(page.html),
    }


def scrape_instagram_posts(username: str, limit: int = 12) -> list[dict]:
    logger.info(f"[instagram] Scraping posts for: {username}, limit={limit}")
    url = f"https://www.instagram.com/{username}/"

    with StealthySession(headless=True, solve_cloudflare=True) as session:
        page = session.fetch(url, network_idle=True)

    posts = []
    for article in page.css("article a")[:limit]:
        posts.append({
            "href":    article.attrib.get("href"),
            "img_src": article.css("img::attr(src)").get(),
            "alt":     article.css("img::attr(alt)").get(),
        })
    return posts


def scrape_instagram_posts_detailed(username: str, limit: int = 5) -> list[dict]:
    logger.info(f"[instagram] Scraping detailed posts for: {username}, limit={limit}")

    session_id = _get_session_id()
    if not session_id:
        logger.error(
            "[instagram] INSTAGRAM_SESSION_ID is not set. "
            "Instagram requires login to view profiles. "
            "Add your sessionid cookie to .env"
        )
        return [{
            "account": username,
            "error": (
                "Instagram requires authentication. "
                "Set INSTAGRAM_SESSION_ID in .env "
                "(get it from browser DevTools → Application → Cookies → instagram.com → sessionid)"
            ),
            "posts": [],
        }]

    profile_url = f"https://www.instagram.com/{username}/"
    api_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"

    media_type_map = {1: "Image", 2: "Video", 8: "Sidecar"}

    logger.info(f"[instagram] Fetching via authenticated API for {username}")
    try:
        with FetcherSession(impersonate="chrome", stealthy_headers=True) as s:
            # Step 1: get profile metadata (biography, followers, user_id, etc.)
            profile_resp = s.get(api_url, headers=_ig_api_headers(session_id))
            user = profile_resp.json()["data"]["user"]
            user_id = user["id"]

            biography        = user.get("biography", "")
            author_id        = user_id
            followers        = (user.get("edge_followed_by") or {}).get("count", 0)
            following        = (user.get("edge_follow") or {}).get("count", 0)
            posts_count      = (user.get("edge_owner_to_timeline_media") or {}).get("count", 0)
            is_verified      = user.get("is_verified", False)
            profile_name     = user.get("full_name", username)
            profile_image    = user.get("profile_pic_url_hd") or user.get("profile_pic_url", "")

            # Step 2: get actual posts from the feed API
            feed_resp = s.get(
                f"https://www.instagram.com/api/v1/feed/user/{user_id}/?count={limit}",
                headers=_ig_api_headers(session_id),
            )
            items = feed_resp.json().get("items", [])

        posts = []
        for item in items[:limit]:
            code = item.get("code", "")
            caption_obj = item.get("caption") or {}
            caption = caption_obj.get("text", "") if isinstance(caption_obj, dict) else ""

            candidates = (item.get("image_versions2") or {}).get("candidates", [])
            image_url     = candidates[0]["url"] if candidates else ""
            thumbnail_src = candidates[-1]["url"] if candidates else ""
            thumbnails    = [
                {"src": c["url"], "width": c.get("width"), "height": c.get("height")}
                for c in candidates
            ]

            taken_at = item.get("taken_at")
            dt = datetime.fromtimestamp(taken_at, tz=timezone.utc).isoformat() if taken_at else None

            posts.append({
                "account":            username,
                "caption":            caption,
                "profile_name":       profile_name,
                "profile_image_link": profile_image,
                "author_id":          author_id,
                "biography":          biography,
                "id":                 item.get("pk", code),
                "following":          following,
                "likes":              item.get("like_count", 0),
                "media_type":         media_type_map.get(item.get("media_type", 1), "Image"),
                "posts_count":        posts_count,
                "followers":          followers,
                "is_verified":        is_verified,
                "datetime":           dt,
                "image_url":          image_url,
                "url":                f"https://www.instagram.com/p/{code}/",
                "comments":           item.get("comment_count", 0),
                "thumbnail_src":      thumbnail_src,
                "thumbnails":         thumbnails,
                "message":            caption,
            })

        logger.info(f"[instagram] Extracted {len(posts)} posts for {username}")
        return posts

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"[instagram] API extraction failed for {username}: {e}")
        return []


# Instagram shortcode alphabet (same base64url table Instagram uses)
_IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _shortcode_to_media_id(shortcode: str) -> str:
    """Decode an Instagram shortcode to its numeric media ID."""
    n = 0
    for char in shortcode:
        n = n * 64 + _IG_ALPHABET.index(char)
    return str(n)


def scrape_instagram_post(shortcode: str) -> dict:
    """Fetch a single Instagram post via the internal media/info/ API."""
    logger.info(f"[instagram] Scraping single post: {shortcode}")
    session_id = _get_session_id()
    if not session_id:
        return {"error": "Instagram requires authentication. Set INSTAGRAM_SESSION_ID in .env"}

    post_url = f"https://www.instagram.com/p/{shortcode}/"
    media_id = _shortcode_to_media_id(shortcode)
    api_url = f"https://www.instagram.com/api/v1/media/{media_id}/info/"

    try:
        with FetcherSession(impersonate="chrome", stealthy_headers=True) as s:
            resp = s.get(api_url, headers=_ig_api_headers(session_id))
        data = resp.json()
    except (json.JSONDecodeError, TypeError) as e:
        logger.error(f"[instagram] media/info parse error for {shortcode}: {e}")
        return {"shortcode": shortcode, "url": post_url, "error": f"API parse error: {e}"}

    items = data.get("items", [])
    if not items:
        logger.warning(f"[instagram] media/info returned no items for {shortcode}: {data.get('message')}")
        return {"shortcode": shortcode, "url": post_url, "error": data.get("message") or "No media found"}

    item = items[0]
    user = item.get("user") or {}

    caption_obj = item.get("caption") or {}
    caption = caption_obj.get("text", "") if isinstance(caption_obj, dict) else ""

    candidates = (item.get("image_versions2") or {}).get("candidates", [])
    image_url     = candidates[0]["url"] if candidates else ""
    thumbnail_src = candidates[-1]["url"] if candidates else ""
    thumbnails    = [
        {"src": c["url"], "width": c.get("width"), "height": c.get("height")}
        for c in candidates
    ]

    taken_at = item.get("taken_at")
    dt = datetime.fromtimestamp(taken_at, tz=timezone.utc).isoformat() if taken_at else None

    media_type_map = {1: "Image", 2: "Video", 8: "Sidecar"}
    media_type = media_type_map.get(item.get("media_type", 1), "Image")
    if item.get("product_type") == "clips":
        media_type = "Reel"

    code = item.get("code", shortcode)

    logger.info(f"[instagram] Post scraped: {shortcode} likes={item.get('like_count')} views={item.get('view_count')}")
    return {
        "account":            user.get("username", ""),
        "caption":            caption,
        "likes":              item.get("like_count", 0),
        "comments":           item.get("comment_count", 0),
        "views":              item.get("view_count") or item.get("play_count", 0),
        "shares":             item.get("reshare_count", 0),
        "saves":              item.get("saved_count", 0),
        "media_type":         media_type,
        "image_url":          image_url,
        "thumbnail_src":      thumbnail_src,
        "thumbnails":         thumbnails,
        "datetime":           dt,
        "url":                f"https://www.instagram.com/p/{code}/",
        "author_id":          str(user.get("pk") or user.get("id", "")),
        "biography":          user.get("biography", ""),
        "followers":          user.get("follower_count", 0),
        "following":          user.get("following_count", 0),
        "posts_count":        user.get("media_count", 0),
        "is_verified":        user.get("is_verified", False),
        "profile_name":       user.get("full_name", user.get("username", "")),
        "profile_image_link": user.get("profile_pic_url", ""),
        "message":            caption,
    }
