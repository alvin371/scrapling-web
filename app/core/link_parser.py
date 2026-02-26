from urllib.parse import urlparse
from app.models.account import Platform
from fastapi import HTTPException


def parse_link(link: str) -> tuple[Platform, str]:
    parsed = urlparse(link.strip().rstrip("/"))
    host = parsed.netloc.lower().replace("www.", "")
    path_parts = [p for p in parsed.path.split("/") if p]

    if "instagram.com" in host and path_parts:
        return Platform.instagram, path_parts[0]

    if ("threads.net" in host or "threads.com" in host) and path_parts:
        return Platform.threads, path_parts[0].lstrip("@")

    raise HTTPException(status_code=400, detail="Unsupported or invalid social media link")


def parse_post_link(link: str) -> tuple[Platform, str]:
    parsed = urlparse(link.strip().rstrip("/"))
    host = parsed.netloc.lower().replace("www.", "")
    path_parts = [p for p in parsed.path.split("/") if p]

    # Instagram: /p/{shortcode}/
    if "instagram.com" in host and len(path_parts) >= 2 and path_parts[0] == "p":
        return Platform.instagram, path_parts[1]

    # Threads: /@{username}/post/{code}
    if ("threads.net" in host or "threads.com" in host) and len(path_parts) >= 3 and path_parts[1] == "post":
        username = path_parts[0].lstrip("@")
        return Platform.threads, f"{username}/{path_parts[2]}"

    raise HTTPException(status_code=400, detail="Unsupported or unrecognized post URL")
