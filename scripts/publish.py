#!/usr/bin/env python3
"""Archive TWC news briefings and rebuild the feeds the site reads.

posts/<id>.json is the last-known-good copy of each briefing.
archive/feed.json is the full feed generated from those files.
feed.json is a rolling window of the newest briefings. The scheduled
publisher is instructed to overwrite feed.json (and the same path in
two other repos) with at most 20 posts. This script never deletes a
stored briefing to satisfy that window.

The commands here are the supported publishing entry point. They do
not run on GitHub's servers by themselves, and they cannot stop a
direct push of a bad feed.json to main.
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
        if _placeholder_text(value):
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
    return post


def sort_posts(posts: list[dict]) -> list[dict]:
    return sorted(posts, key=lambda post: (post["date"], post["id"]), reverse=True)


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
    rolling = parse_feed_text(rolling_path.read_text(encoding="utf-8"))
    if len(rolling["posts"]) > ROLLING_LIMIT:
        raise FeedError(f"feed.json has more than {ROLLING_LIMIT} posts")
    seen = set()
    for raw_post in rolling["posts"]:
        post = normalize_post(raw_post)
        if post["id"] in seen:
            raise FeedError(f"feed.json repeats {post['id']}")
        seen.add(post["id"])
        stored = archive.get(post["id"])
        if stored is None:
            raise FeedError(f"feed.json post {post['id']} is not in the archive")
        if body_text(post) > body_text(stored):
            raise FeedError(f"feed.json copy of {post['id']} is fuller than the archive")
        if post["date"] != stored["date"]:
            raise FeedError(f"feed.json date for {post['id']} does not match the archive")
    dates = [post["date"] for post in rolling["posts"]]
    if dates != sorted(dates, reverse=True):
        raise FeedError("feed.json is not newest-first")


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
    except FeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
