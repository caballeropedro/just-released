import urllib.request
import urllib.error
import json
import fnmatch
import re

GITHUB_API_BASE = "https://api.github.com"

def get_latest_release(repo: str, token: str = None) -> dict:
    """
    Fetch the latest release information from GitHub API.
    Returns dict with tag_name, name, published_at, html_url, and assets.
    """
    repo = repo.strip().strip("/")
    url = f"{GITHUB_API_BASE}/repos/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "just-release-desktop/1.0"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            assets = []
            for a in data.get("assets", []):
                assets.append({
                    "name": a.get("name"),
                    "size": a.get("size", 0),
                    "download_url": a.get("browser_download_url"),
                    "content_type": a.get("content_type"),
                    "created_at": a.get("created_at")
                })
            return {
                "success": True,
                "tag_name": data.get("tag_name"),
                "name": data.get("name") or data.get("tag_name"),
                "published_at": data.get("published_at"),
                "html_url": data.get("html_url"),
                "body": data.get("body", ""),
                "assets": assets
            }
    except urllib.error.HTTPError as e:
        error_msg = f"HTTP {e.code}: {e.reason}"
        if e.code == 404:
            error_msg = "Repository or latest release not found."
        elif e.code == 403:
            error_msg = "GitHub API rate limit exceeded or access forbidden."
        return {"success": False, "error": error_msg}
    except Exception as e:
        return {"success": False, "error": str(e)}

def match_asset(assets: list, pattern: str) -> dict:
    """
    Find matching asset from assets list using glob pattern or fallback regex.
    """
    if not assets:
        return None

    if not pattern:
        # Default fallback: pick first archive or binary
        for a in assets:
            name = a["name"].lower()
            if name.endswith((".zip", ".tar.gz", ".tgz")):
                return a
        return assets[0]

    # Try glob match
    for a in assets:
        if fnmatch.fnmatch(a["name"], pattern):
            return a

    # Try regex match
    try:
        reg = re.compile(pattern, re.IGNORECASE)
        for a in assets:
            if reg.search(a["name"]):
                return a
    except Exception:
        pass

    return None

def format_size(bytes_num: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} TB"
