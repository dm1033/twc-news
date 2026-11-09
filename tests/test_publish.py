#!/usr/bin/env python3
"""Behaviour of the news archive. These tests do not publish anywhere."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish  # noqa: E402


def briefing(number: int, date: str, url: str, body: str | None = None, title: str | None = None) -> dict:
    paragraph = body if body is not None else ("Temporary works control " * 80)
    return {
        "id": f"{date}-item-{number}",
        "date": date,
        "kicker": "HSE",
        "title": title or f"Briefing number {number}",
        "excerpt": "Educational excerpt for this briefing.",
        "tags": ["HSE", "Temporary works"],
        "linkedinReady": "A complete LinkedIn draft for this briefing.",
        "body": [
            {"h": "What happened", "p": paragraph},
            {"sources": [{"label": "Source", "url": url}]},
        ],
    }


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_add_preserves_older_posts_and_keeps_a_short_window(self):
        for number in range(1, 22):
            day = f"2026-01-{number:02d}"
            publish.add_post(self.tmp, briefing(number, day, f"https://example.com/{number}"))
        archive = publish.load_archive(self.tmp)
        self.assertEqual(len(archive), 21)
        window = json.loads((self.tmp / "feed.json").read_text(encoding="utf-8"))
        full = json.loads((self.tmp / "archive" / "feed.json").read_text(encoding="utf-8"))
        self.assertEqual(len(window["posts"]), 20)
        self.assertEqual(len(full["posts"]), 21)
        self.assertEqual(window["posts"][0]["date"], "2026-01-21")
        self.assertEqual(full["posts"][-1]["id"], "2026-01-01-item-1")
        publish.check(self.tmp)

    def test_shorter_republish_does_not_replace_the_stored_body(self):
        original = briefing(1, "2026-03-01", "https://example.com/story")
        publish.add_post(self.tmp, original)
        shorter = briefing(1, "2026-03-01", "https://example.com/story", body="Temporary works control " * 40)
        self.assertEqual(publish.add_post(self.tmp, shorter), "kept")
        stored = publish.load_archive(self.tmp)["2026-03-01-item-1"]
        self.assertGreater(publish.body_text(stored), publish.body_text(shorter))

    def test_longer_correction_replaces_the_stored_body(self):
        publish.add_post(self.tmp, briefing(1, "2026-03-01", "https://example.com/story"))
        longer = briefing(1, "2026-03-01", "https://example.com/story", body="Temporary works control " * 120)
        self.assertEqual(publish.add_post(self.tmp, longer), "replaced")
        self.assertEqual(publish.load_archive(self.tmp)["2026-03-01-item-1"]["body"][0]["p"], longer["body"][0]["p"])

    def test_same_day_same_source_url_is_rejected(self):
        publish.add_post(self.tmp, briefing(1, "2026-03-02", "https://example.com/same"))
        rival = briefing(2, "2026-03-02", "https://example.com/same", title="A different headline")
        with self.assertRaises(publish.FeedError):
            publish.add_post(self.tmp, rival)
        self.assertEqual(set(publish.load_archive(self.tmp)), {"2026-03-02-item-1"})

    def test_same_source_url_on_a_later_date_is_kept(self):
        publish.add_post(self.tmp, briefing(1, "2026-03-02", "https://example.com/same"))
        publish.add_post(self.tmp, briefing(2, "2026-03-03", "https://example.com/same", title="Follow-up"))
        self.assertEqual(len(publish.load_archive(self.tmp)), 2)

    def test_placeholder_empty_and_truncated_feeds_do_not_delete_history(self):
        publish.add_post(self.tmp, briefing(1, "2026-04-01", "https://example.com/1"))
        publish.add_post(self.tmp, briefing(2, "2026-04-02", "https://example.com/2"))
        before = set(publish.load_archive(self.tmp))
        for payload in ("PLACEHOLDER", "PLACEHOLDER_FOR_FULL_JSON", "See /tmp/feed.json content too large", ""):
            result = publish.capture_feed(self.tmp, payload)
            self.assertEqual(result["added"], [])
            self.assertEqual(set(publish.load_archive(self.tmp)), before)
        empty = json.dumps({"updated": "2026-04-02T07:45:00+00:00", "source": "x", "disclaimer": "y", "posts": []})
        publish.capture_feed(self.tmp, empty)
        self.assertEqual(set(publish.load_archive(self.tmp)), before)
        truncated = json.dumps({"posts": [briefing(3, "2026-04-03", "https://example.com/3")]})
        result = publish.capture_feed(self.tmp, truncated)
        self.assertEqual(result["added"], ["2026-04-03-item-3"])
        self.assertEqual(set(publish.load_archive(self.tmp)), before | {"2026-04-03-item-3"})

    def test_capture_does_not_rewrite_the_rolling_feed(self):
        publish.add_post(self.tmp, briefing(1, "2026-04-01", "https://example.com/1"))
        rolling = self.tmp / "feed.json"
        rolling.write_text("PLACEHOLDER\n", encoding="utf-8")
        publish.capture_feed(self.tmp, json.dumps({"posts": [briefing(2, "2026-04-02", "https://example.com/2")]}))
        self.assertEqual(rolling.read_text(encoding="utf-8"), "PLACEHOLDER\n")
        self.assertIn("2026-04-02-item-2", publish.load_archive(self.tmp))

    def test_placeholder_post_is_rejected(self):
        post = briefing(1, "2026-05-01", "https://example.com/1")
        post["linkedinReady"] = "PLACEHOLDER_KEEP_ORIGINAL"
        with self.assertRaises(publish.FeedError):
            publish.add_post(self.tmp, post)
        self.assertEqual(publish.load_archive(self.tmp), {})

    def test_concurrent_adds_keep_both_posts(self):
        barrier = threading.Barrier(2)
        errors = []

        def worker(number, date):
            try:
                barrier.wait(timeout=5)
                publish.add_post(self.tmp, briefing(number, date, f"https://example.com/c{number}"))
            except Exception as exc:  # noqa: BLE001 - fail the test with the thread error
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(1, "2026-06-01")),
            threading.Thread(target=worker, args=(2, "2026-06-02")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(set(publish.load_archive(self.tmp)), {"2026-06-01-item-1", "2026-06-02-item-2"})

    def test_consumer_merge_ignores_bad_feeds_and_prefers_the_longer_body(self):
        full = briefing(1, "2026-07-01", "https://example.com/1")
        short = briefing(1, "2026-07-01", "https://example.com/1", body="Temporary works control " * 40)
        extra = briefing(2, "2026-07-02", "https://example.com/2")
        documents = [
            json.dumps({"updated": "2026-07-02T07:45:00+00:00", "posts": [full, extra]}),
            "PLACEHOLDER",
            json.dumps({"posts": []}),
            json.dumps({"posts": [short]}),
        ]
        merged = publish.consumer_merge(documents)
        self.assertEqual([post["id"] for post in merged["posts"]], ["2026-07-02-item-2", "2026-07-01-item-1"])
        self.assertEqual(merged["posts"][1]["body"][0]["p"], full["body"][0]["p"])

    def test_check_rejects_a_rewritten_archive_feed(self):
        publish.add_post(self.tmp, briefing(1, "2026-08-01", "https://example.com/1"))
        publish.add_post(self.tmp, briefing(2, "2026-08-02", "https://example.com/2"))
        (self.tmp / "archive" / "feed.json").write_text("PLACEHOLDER\n", encoding="utf-8")
        with self.assertRaises(publish.FeedError):
            publish.check(self.tmp)
        self.assertEqual(len(publish.load_archive(self.tmp)), 2)


class RepositoryTests(unittest.TestCase):
    def test_committed_archive_matches_recoverable_history(self):
        publish.check(ROOT)
        recovered = publish.genuine_posts_from_git(ROOT)
        archived = publish.load_archive(ROOT)
        recovered_ids = {post["id"] for post in recovered}
        self.assertEqual(len(recovered), 70)
        self.assertEqual(recovered_ids, set(archived))
        dates = {post["date"] for post in recovered}
        self.assertIn("2026-11-07", dates)
        self.assertIn("2026-11-03", dates)
        self.assertIn("2026-10-11", dates)
        self.assertIn("2026-05-12", dates)
        for absent in ("2026-11-04", "2026-11-05", "2026-11-06", "2026-11-08"):
            self.assertNotIn(absent, dates)
        window = json.loads((ROOT / "feed.json").read_text(encoding="utf-8"))
        archived = json.loads((ROOT / "archive" / "feed.json").read_text(encoding="utf-8"))
        self.assertEqual(len(window["posts"]), 20)
        self.assertEqual(
            [post["id"] for post in window["posts"]],
            [post["id"] for post in archived["posts"][:20]],
        )
        self.assertEqual(window["posts"][0]["id"], "2026-11-07-hse-trainee-scaffolder-fall-safe-zone")
        self.assertNotIn("2026-11-08", {post["date"] for post in window["posts"]})
        before = (ROOT / "feed.json").read_bytes()
        self.assertEqual(publish.guard(ROOT)["action"], "none")
        self.assertEqual((ROOT / "feed.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
