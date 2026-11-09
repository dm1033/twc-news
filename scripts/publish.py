#!/usr/bin/env python3
"""Archive TWC news briefings and rebuild the feeds the site reads.

posts/<id>.json is the last-known-good copy of each briefing.
archive/feed.json is the full feed generated from those files.
feed.json is a rolling window of the newest briefings. The scheduled
publisher is instructed to overwrite feed.json (and the same path in
two other repos) with at most 20 posts. This script never deletes a
stored briefing to satisfy that window.

The commands here are the supported publishing entry point. A direct
push of feed.json does not run them. `.github/workflows/restore-feed.yml`
runs `guard` on the push that already landed and puts the rolling
window back when that file fails validation.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLLING_LIMIT = 20
MIN_BODY_TEXT = 800
POST_KEYS = ("id", "date", "kicker", "title", "excerpt", "tags", "linkedinReady", "body")
BODY_KEYS = ("h", "p", "ul", "sources")
SOURCE_KEYS = ("label", "url")

ID_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
URL_RE = re.compile(r"^https?://")
PLACEHOLDER_VALUE = re.compile(
    r"(?is)^\s*(?:"
    r"placeholder(?:[\s_:-].*)?"
    r"|see[\s_-]*file\b.*"
    r"|see artifacts"
    r"|loading"
    r"|full_content_from_file"
    r"|<?\s*file_content_here\s*>?"
    r"|content too large\b.*"
    r"|will[-_ ]replace\b.*"
    r")\s*$"
)
# Substrings that have already been pushed in place of a feed, including
# the 147-byte note on main at 6650f27. These are matched inside JSON so
# wrapping the note in an object does not hide it. They are not wording
# that appears in a briefing.
PLACEHOLDER_MARKERS = (
    "insert the full content here",
    "full string from the file",
    "too large for this example",
    "full_content_from_file",
    "file_content_here",
    "placeholder_for_full_json",
    "placeholder_keep_original",
    "placeholder_fix",
)
FEED_KEYS = ("updated", "source", "disclaimer", "posts")
UPDATED_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\+00:00|Z)$"
)


class FeedError(Exception):
    """A feed or post must not be published."""


def dumps(document) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def load_meta(root: Path) -> dict:
    path = root / "feed.meta.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        source = data.get("source")
        disclaimer = data.get("disclaimer")
        if not isinstance(source, str) or not source.strip():
            raise FeedError("feed.meta.json source is empty")
        if not isinstance(disclaimer, str) or not disclaimer.strip():
            raise FeedError("feed.meta.json disclaimer is empty")
        return {"source": source, "disclaimer": disclaimer}
    return {
        "source": "Temporary Works Consulting & Design Ltd daily briefing automation",
        "disclaimer": (
            "Educational briefing only. Not for construction. Subject to "
            "project-specific verification and acceptance under the project "
            "temporary works procedure."
        ),
    }


def body_text(post: dict) -> str:
    parts: list[str] = []
    for block in post.get("body") or []:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("h"), str):
            parts.append(block["h"])
        if isinstance(block.get("p"), str):
            parts.append(block["p"])
        for item in block.get("ul") or []:
            if isinstance(item, str):
                parts.append(item)
    return "\n".join(parts)


def top_source_url(post: dict) -> str:
    for block in post.get("body") or []:
        if not isinstance(block, dict):
            continue
        for source in block.get("sources") or []:
            if isinstance(source, dict) and isinstance(source.get("url"), str):
                return source["url"]
    return ""


def _placeholder_text(value: str) -> bool:
    return bool(PLACEHOLDER_VALUE.match(value.strip()))


def marker_in(text: str) -> str | None:
    """Return the placeholder fragment found in text, if any."""
    lowered = text.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker in lowered:
            return marker
    return None


def normalize_post(raw: dict, *, strict_urls: bool = True) -> dict:
    if not isinstance(raw, dict):
        raise FeedError("post is not an object")
    missing = [key for key in POST_KEYS if key not in raw]
    extra = [key for key in raw if key not in POST_KEYS]
    if missing or extra:
        raise FeedError(f"post schema mismatch missing={missing} extra={extra}")
    post_id = raw["id"]
    date = raw["date"]
    if not isinstance(post_id, str) or not ID_RE.match(post_id):
        raise FeedError(f"invalid id {post_id!r}")
    if not isinstance(date, str) or not DATE_RE.match(date) or not post_id.startswith(date):
        raise FeedError(f"id {post_id} does not start with its date")
    text_fields = {}
    for key in ("kicker", "title", "excerpt", "linkedinReady"):
        value = raw[key]
        if not isinstance(value, str) or not value.strip():
            raise FeedError(f"{post_id} has an empty {key}")
        if _placeholder_text(value) or marker_in(value):
            raise FeedError(f"{post_id} {key} is placeholder text")
        text_fields[key] = value
    if len(text_fields["title"]) > 240 or len(text_fields["kicker"]) > 80:
        raise FeedError(f"{post_id} title or kicker is too long")
    if len(text_fields["excerpt"]) > 4000 or len(text_fields["linkedinReady"]) > 8000:
        raise FeedError(f"{post_id} excerpt or linkedinReady is too long")
    tags = raw["tags"]
    if (
        not isinstance(tags, list)
        or not tags
        or len(tags) > 24
        or not all(isinstance(tag, str) and tag.strip() for tag in tags)
    ):
        raise FeedError(f"{post_id} tags must be a short list of strings")
    body_in = raw["body"]
    if not isinstance(body_in, list) or not body_in or len(body_in) > 30:
        raise FeedError(f"{post_id} body is empty or not a list of blocks")
    body = []
    for block in body_in:
        if not isinstance(block, dict):
            raise FeedError(f"{post_id} body block is not an object")
        unknown = [key for key in block if key not in BODY_KEYS]
        if unknown:
            raise FeedError(f"{post_id} body has unsupported keys {unknown}")
        cleaned = {}
        if isinstance(block.get("h"), str) and block["h"].strip():
            cleaned["h"] = block["h"]
        if isinstance(block.get("p"), str) and block["p"].strip():
            cleaned["p"] = block["p"]
        if "ul" in block:
            items = block["ul"]
            if not isinstance(items, list) or not items or not all(isinstance(item, str) and item.strip() for item in items):
                raise FeedError(f"{post_id} has a malformed list")
            cleaned["ul"] = items
        if "sources" in block:
            sources = []
            raw_sources = block["sources"]
            if not isinstance(raw_sources, list) or not raw_sources:
                raise FeedError(f"{post_id} has malformed sources")
            for source in raw_sources:
                if not isinstance(source, dict):
                    raise FeedError(f"{post_id} source is not an object")
                if [key for key in source if key not in SOURCE_KEYS]:
                    raise FeedError(f"{post_id} source has unsupported keys")
                label = source.get("label")
                url = source.get("url")
                if not isinstance(label, str) or not label.strip():
                    raise FeedError(f"{post_id} source is missing a label")
                if not isinstance(url, str) or (strict_urls and not URL_RE.match(url)):
                    raise FeedError(f"{post_id} source URL is not http(s)")
                sources.append({"label": label, "url": url})
            cleaned["sources"] = sources
        if not cleaned:
            raise FeedError(f"{post_id} has an empty body block")
        body.append(cleaned)
    post = {
        "id": post_id,
        "date": date,
        "kicker": text_fields["kicker"],
        "title": text_fields["title"],
        "excerpt": text_fields["excerpt"],
        "tags": tags,
        "linkedinReady": text_fields["linkedinReady"],
        "body": body,
    }
    if len(body_text(post)) < MIN_BODY_TEXT:
        raise FeedError(f"{post_id} body is truncated ({len(body_text(post))} characters)")
    hit = marker_in(json.dumps(post))
    if hit:
        raise FeedError(f"{post_id} contains placeholder text ({hit})")
    return post


def sort_posts(posts: list[dict]) -> list[dict]:
    return sorted(posts, key=lambda post: (post["date"], post["id"]), reverse=True)


def required_feed_count(archive_count: int) -> int:
    """How many posts a replacement feed.json must contain.

    The public file is a window of 20. Once that many briefings exist,
    a shorter file is the direct-push failure this guard exists for
    (a placeholder, an empty list, or a one-post stand-in). A younger
    archive cannot be asked for briefings it does not have. An empty
    archive still requires one real post so a placeholder file fails.
    """
    if archive_count < 1:
        return 1
    return min(ROLLING_LIMIT, archive_count)


def feed_document(posts: list[dict], meta: dict) -> dict:
    ordered = sort_posts(posts)
    newest = ordered[0]["date"] if ordered else "1970-01-01"
    return {
        "updated": f"{newest}T07:45:00+00:00",
        "source": meta["source"],
        "disclaimer": meta["disclaimer"],
        "posts": ordered,
    }


def parse_feed_text(raw: str) -> dict:
    stripped = raw.strip()
    if not stripped or _placeholder_text(stripped) or stripped[0] not in "{[":
        raise FeedError("feed is empty or placeholder text")
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise FeedError(f"feed is not JSON ({exc.msg})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("posts"), list):
        raise FeedError("feed is missing a posts array")
    if not data["posts"]:
        raise FeedError("feed has no posts")
    return data


def validate_feed_text(raw: str, *, minimum: int) -> dict:
    """Accept a rolling feed, or raise FeedError.

    The file must be JSON of the feed schema, newest first, free of
    placeholder text, and at least `minimum` posts long (never more
    than the 20-post window). This does not require the posts to be in
    the archive yet: a direct publish of a genuine new briefing is valid.
    """
    if minimum < 1:
        raise FeedError("minimum post count must be at least 1")
    hit = marker_in(raw)
    if hit:
        raise FeedError(f"feed contains placeholder text ({hit})")
    document = parse_feed_text(raw)
    missing = [key for key in FEED_KEYS if key not in document]
    extra = [key for key in document if key not in FEED_KEYS]
    if missing or extra:
        raise FeedError(f"feed schema mismatch missing={missing} extra={extra}")
    updated = document["updated"]
    if not isinstance(updated, str) or not UPDATED_RE.match(updated):
        raise FeedError("feed updated is not a UTC timestamp")
    for key in ("source", "disclaimer"):
        value = document[key]
        if not isinstance(value, str) or not value.strip() or _placeholder_text(value) or marker_in(value):
            raise FeedError(f"feed {key} is empty or placeholder text")
    count = len(document["posts"])
    if count < minimum:
        raise FeedError(f"feed has {count} posts; at least {minimum} are required")
    if count > ROLLING_LIMIT:
        raise FeedError(f"feed has {count} posts; the rolling window is {ROLLING_LIMIT}")
    posts = []
    seen: set[str] = set()
    for raw_post in document["posts"]:
        post = normalize_post(raw_post)
        if post["id"] in seen:
            raise FeedError(f"feed repeats {post['id']}")
        seen.add(post["id"])
        posts.append(post)
    actual = [post["id"] for post in posts]
    if actual != [post["id"] for post in sort_posts(posts)]:
        raise FeedError("feed is not newest-first")
    return {
        "updated": updated,
        "source": document["source"],
        "disclaimer": document["disclaimer"],
        "posts": posts,
    }


def shortened_ids(posts: list[dict], archive: dict[str, dict]) -> list[str]:
    """Ids whose feed copy is a shorter body than the stored briefing."""
    found = []
    for post in posts:
        stored = archive.get(post["id"])
        if stored is not None and len(body_text(post)) < len(body_text(stored)):
            found.append(post["id"])
    return found


def load_archive(root: Path) -> dict[str, dict]:
    posts_dir = root / "posts"
    archive: dict[str, dict] = {}
    if not posts_dir.is_dir():
        return archive
    for path in sorted(posts_dir.glob("*.json")):
        post = normalize_post(json.loads(path.read_text(encoding="utf-8")))
        if path.stem != post["id"]:
            raise FeedError(f"{path.name} does not match id {post['id']}")
        archive[post["id"]] = post
    return archive


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_archive(root: Path, posts: dict[str, dict], *, rolling: bool = True) -> None:
    """Rewrite post files and archive/feed.json. Never deletes a post file.

    The rolling feed.json is rewritten only when rolling is true. Capturing
    posts out of a publisher push leaves that push's feed.json in place so
    this script does not race the publisher back and forth.
    """
    meta = load_meta(root)
    posts_dir = root / "posts"
    posts_dir.mkdir(parents=True, exist_ok=True)
    keep = set()
    for post in posts.values():
        path = posts_dir / f"{post['id']}.json"
        _atomic_write(path, dumps(post))
        keep.add(path.name)
    for path in posts_dir.glob("*.json"):
        if path.name not in keep:
            raise FeedError(f"refusing to delete {path.name}")
    ordered = sort_posts(list(posts.values()))
    _atomic_write(root / "archive" / "feed.json", dumps(feed_document(ordered, meta)))
    if rolling:
        _atomic_write(root / "feed.json", dumps(feed_document(ordered[:ROLLING_LIMIT], meta)))


@contextmanager
def archive_lock(root: Path):
    posts_dir = root / "posts"
    posts_dir.mkdir(parents=True, exist_ok=True)
    handle = open(posts_dir / ".lock", "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def duplicate_of(existing: dict[str, dict], post: dict) -> dict | None:
    """Return the stored briefing a new post would repeat.

    The scheduled task treats a briefing as already published when the
    Europe/London date matches or the new item's first source URL matches
    the newest item. Historical briefings cite the same reports on later
    days, so a shared URL on a different date is not a duplicate.
    """
    if post["id"] in existing:
        return existing[post["id"]]
    title = post["title"].strip().lower()
    url = top_source_url(post)
    for stored in existing.values():
        if stored["date"] != post["date"]:
            continue
        if stored["title"].strip().lower() == title:
            return stored
        if url and top_source_url(stored) == url:
            return stored
    return None


def merge_post(existing: dict[str, dict], incoming: dict) -> str:
    """Union one genuine post into the archive. Never shortens a stored body."""
    post = normalize_post(incoming)
    current = existing.get(post["id"])
    if current is None:
        other = duplicate_of(existing, post)
        if other is not None and other["id"] != post["id"]:
            return "duplicate"
        existing[post["id"]] = post
        return "added"
    if body_text(post) > body_text(current):
        existing[post["id"]] = post
        return "replaced"
    return "kept"


def add_post(root: Path, raw_post: dict) -> str:
    with archive_lock(root):
        existing = load_archive(root)
        outcome = merge_post(existing, raw_post)
        if outcome == "duplicate":
            stored = duplicate_of(load_archive(root), normalize_post(raw_post))
            raise FeedError(
                f"duplicate of {stored['id']} on {stored['date']}; history was not changed"
            )
        write_archive(root, existing)
        return outcome


def capture_feed(root: Path, feed_text: str | None = None) -> dict:
    """Copy genuine new posts out of the rolling feed into the archive.

    Placeholder, empty, and truncated files are ignored. Stored posts are
    not deleted and a shorter body does not replace a longer one.
    """
    with archive_lock(root):
        existing = load_archive(root)
        before = set(existing)
        raw = feed_text if feed_text is not None else (root / "feed.json").read_text(encoding="utf-8")
        try:
            document = parse_feed_text(raw)
        except FeedError:
            return {"added": [], "replaced": [], "kept": [], "skipped": ["feed ignored"]}
        added, replaced, kept, skipped = [], [], [], []
        for raw_post in document["posts"]:
            try:
                outcome = merge_post(existing, raw_post)
            except FeedError as exc:
                label = raw_post.get("id") if isinstance(raw_post, dict) else "?"
                skipped.append(f"{label}: {exc}")
                continue
            post_id = raw_post.get("id") if isinstance(raw_post, dict) else "?"
            if outcome == "added":
                added.append(post_id)
            elif outcome == "replaced":
                replaced.append(post_id)
            elif outcome == "duplicate":
                skipped.append(f"{post_id}: duplicate")
            else:
                kept.append(post_id)
        if set(existing) != before or replaced:
            write_archive(root, existing, rolling=False)
        removed = before - set(existing)
        if removed:
            raise FeedError(f"capture removed posts: {sorted(removed)}")
        return {"added": added, "replaced": replaced, "kept": kept, "skipped": skipped}


def check(root: Path) -> None:
    meta = load_meta(root)
    archive = load_archive(root)
    if not archive:
        raise FeedError("archive has no posts")
    ordered = sort_posts(list(archive.values()))
    full_path = root / "archive" / "feed.json"
    rolling_path = root / "feed.json"
    if not full_path.is_file():
        raise FeedError("archive/feed.json is missing")
    full = parse_feed_text(full_path.read_text(encoding="utf-8"))
    expected = feed_document(ordered, meta)
    if full != expected:
        raise FeedError("archive/feed.json is not the generated full feed")
    if [post["id"] for post in full["posts"]] != [post["id"] for post in ordered]:
        raise FeedError("archive/feed.json is not newest-first")
    if not rolling_path.is_file():
        raise FeedError("feed.json is missing")
    rolling = validate_feed_text(
        rolling_path.read_text(encoding="utf-8"),
        minimum=required_feed_count(len(archive)),
    )
    short = shortened_ids(rolling["posts"], archive)
    if short:
        raise FeedError(f"feed.json shortens archived briefings: {', '.join(short)}")
    for post in rolling["posts"]:
        stored = archive.get(post["id"])
        if stored is None:
            raise FeedError(f"feed.json post {post['id']} is not in the archive")
        if body_text(post) > body_text(stored):
            raise FeedError(f"feed.json copy of {post['id']} is fuller than the archive")
        if post["date"] != stored["date"]:
            raise FeedError(f"feed.json date for {post['id']} does not match the archive")


def consumer_merge(documents: list[str]) -> dict:
    """Merge rule the production page needs.

    Invalid documents are ignored. A shorter copy of an id does not replace
    a longer genuine copy. The result is newest-first and keeps every
    accepted id. This is not what news.js does today.
    """
    chosen: dict[str, dict] = {}
    updated = ""
    for raw in documents:
        try:
            document = parse_feed_text(raw)
        except FeedError:
            continue
        stamp = document.get("updated")
        if isinstance(stamp, str) and stamp > updated:
            updated = stamp
        for raw_post in document["posts"]:
            try:
                post = normalize_post(raw_post)
            except FeedError:
                continue
            current = chosen.get(post["id"])
            if current is None or body_text(post) > body_text(current):
                chosen[post["id"]] = post
    posts = sort_posts(list(chosen.values()))
    return {"updated": updated, "posts": posts}


def _archive_or_empty(root: Path) -> dict[str, dict]:
    try:
        return load_archive(root)
    except (FeedError, json.JSONDecodeError, OSError):
        return {}


def _salvage(root: Path, raw: str) -> dict:
    try:
        return capture_feed(root, raw)
    except (FeedError, json.JSONDecodeError, OSError) as exc:
        return {"added": [], "replaced": [], "kept": [], "skipped": [str(exc)]}


def last_valid_feed_text(root: Path) -> str | None:
    """Newest git copy of feed.json that still passes validation.

    A full 20-post window wins over an older short feed. Placeholder
    commits are skipped. Returns None when git has no valid copy or
    this directory is not a repository.
    """
    try:
        log = subprocess.run(
            ["git", "-C", str(root), "log", "--format=%H", "--", "feed.json"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    short: str | None = None
    for rev in log.stdout.split():
        show = subprocess.run(
            ["git", "-C", str(root), "show", f"{rev}:feed.json"],
            capture_output=True,
        )
        if show.returncode != 0:
            continue
        text = show.stdout.decode("utf-8")
        try:
            validate_feed_text(text, minimum=ROLLING_LIMIT)
        except FeedError:
            pass
        else:
            return text
        if short is None:
            try:
                validate_feed_text(text, minimum=1)
            except FeedError:
                continue
            short = text
    return short


def guard(root: Path) -> dict:
    """Validate feed.json. When it fails, restore a real rolling window.

    Genuine new posts inside the rejected file are copied into the
    archive first. The replacement window is then built from posts/.
    If the archive itself is empty, the last valid feed.json in git is
    written back. Nothing in this function composes a briefing.
    """
    feed_path = root / "feed.json"
    raw = feed_path.read_text(encoding="utf-8") if feed_path.is_file() else ""
    archive = _archive_or_empty(root)
    minimum = required_feed_count(len(archive))
    try:
        document = validate_feed_text(raw, minimum=minimum)
    except FeedError as exc:
        problem = str(exc)
    else:
        short = shortened_ids(document["posts"], archive)
        if not short:
            return {
                "ok": True,
                "action": "none",
                "problems": [],
                "posts": len(document["posts"]),
            }
        problem = "feed shortens archived briefings: " + ", ".join(short)

    salvage = _salvage(root, raw)
    archive = _archive_or_empty(root)
    if archive:
        meta = load_meta(root)
        window = sort_posts(list(archive.values()))[:ROLLING_LIMIT]
        text = dumps(feed_document(window, meta))
        validate_feed_text(text, minimum=required_feed_count(len(archive)))
        _atomic_write(feed_path, text)
        return {
            "ok": False,
            "action": "restored",
            "restored_from": "archive",
            "problems": [problem],
            "salvaged": salvage,
            "posts": len(window),
            "ids": [post["id"] for post in window],
        }

    previous = last_valid_feed_text(root)
    if previous is None:
        return {
            "ok": False,
            "action": "unrestored",
            "problems": [problem, "no valid feed.json in git history and the archive has no posts"],
            "salvaged": salvage,
        }
    if not previous.endswith("\n"):
        previous += "\n"
    _atomic_write(feed_path, previous)
    restored = validate_feed_text(previous, minimum=1)
    return {
        "ok": False,
        "action": "restored",
        "restored_from": "git",
        "problems": [problem],
        "salvaged": salvage,
        "posts": len(restored["posts"]),
        "ids": [post["id"] for post in restored["posts"]],
    }


def guard_issue_body(result: dict, commit: str | None = None) -> str:
    """Markdown for the issue opened when a push fails the feed guard.

    The body names the validation failure and the repair. It does not
    include or invent briefing text.
    """
    problems = result.get("problems") or ["feed.json failed validation"]
    lines = [
        "A push to `main` left `feed.json` unfit for the public news feed.",
        "",
        "Validation:",
    ]
    for problem in problems:
        lines.append(f"- {problem}")
    action = result.get("action")
    lines.append("")
    if action == "restored" and result.get("restored_from") == "archive":
        lines.append(
            "The rolling feed was rebuilt from `posts/` "
            "(the newest archived briefings, at most 20). "
            "This check did not write a briefing."
        )
    elif action == "restored":
        lines.append(
            "The archive had no usable posts, so `feed.json` was replaced "
            "with the last valid copy in git history. "
            "This check did not write a briefing."
        )
    else:
        lines.append(
            "The feed could not be restored automatically. "
            "`main` may still be serving the rejected file."
        )
    ids = result.get("ids") or []
    if action == "restored" and ids:
        lines.append("")
        lines.append(f"The restored window starts at `{ids[0]}` and has {len(ids)} posts.")
    added = (result.get("salvaged") or {}).get("added") or []
    if added:
        lines.append("")
        lines.append(
            "Posts copied into the archive from the rejected file: "
            + ", ".join(f"`{item}`" for item in added)
            + "."
        )
    lines.append("")
    lines.append(
        "A briefing that was only described in the commit subject, or only "
        "promised by placeholder text, is not in the feed. Republish it with "
        "`python3 scripts/publish.py add` and a full post file. "
        "Do not push a placeholder, a short list, or a note that the content "
        "will be inserted later."
    )
    if commit:
        lines.append("")
        lines.append(f"Triggering commit: `{commit}`.")
    return "\n".join(lines) + "\n"


def genuine_posts_from_git(root: Path) -> list[dict]:
    """Longest genuine copy of every briefing that ever parsed in feed.json."""
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--all", "--format=%H", "--", "feed.json"],
        check=True,
        capture_output=True,
        text=True,
    )
    versions: dict[str, list[dict]] = {}
    for rev in log.stdout.split():
        show = subprocess.run(
            ["git", "-C", str(root), "show", f"{rev}:feed.json"],
            check=True,
            capture_output=True,
        )
        try:
            document = parse_feed_text(show.stdout.decode("utf-8"))
        except FeedError:
            continue
        for raw_post in document["posts"]:
            try:
                post = normalize_post(raw_post)
            except FeedError:
                continue
            versions.setdefault(post["id"], []).append(post)
    chosen = []
    for copies in versions.values():
        chosen.append(max(copies, key=body_text))
    return sort_posts(chosen)


def materialise_history(root: Path) -> int:
    """Write the archive from git history. Refuses to shorten a stored post."""
    recovered = genuine_posts_from_git(root)
    with archive_lock(root):
        existing = load_archive(root)
        for post in recovered:
            merge_post(existing, post)
        write_archive(root, existing)
    return len(existing)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish TWC news without dropping history")
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("rebuild")
    sub.add_parser("materialise-history")
    add = sub.add_parser("add")
    add.add_argument("path", type=Path)
    sub.add_parser("capture-feed")
    sub.add_parser("guard")
    issue = sub.add_parser("guard-issue")
    issue.add_argument("result", type=Path)
    issue.add_argument("commit", nargs="?", default=None)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "check":
            check(root)
            archive = load_archive(root)
            print(f"ok {len(archive)} archived posts; feed.json window {ROLLING_LIMIT}")
        elif args.command == "rebuild":
            with archive_lock(root):
                write_archive(root, load_archive(root))
            print("rebuilt archive/feed.json and the rolling feed.json")
        elif args.command == "materialise-history":
            count = materialise_history(root)
            print(f"archived {count} briefings")
        elif args.command == "add":
            outcome = add_post(root, json.loads(args.path.read_text(encoding="utf-8")))
            print(outcome)
        elif args.command == "capture-feed":
            result = capture_feed(root)
            print(json.dumps(result, indent=2))
        elif args.command == "guard":
            result = guard(root)
            print(json.dumps(result, indent=2))
            return 0 if result.get("action") != "unrestored" else 1
        elif args.command == "guard-issue":
            result = json.loads(args.result.read_text(encoding="utf-8"))
            print(guard_issue_body(result, args.commit), end="")
    except FeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
