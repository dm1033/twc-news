#!/usr/bin/env python3
"""The feed guard rejects a bad rolling file and restores a real window.

These tests do not publish anywhere and do not invent briefing text.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish  # noqa: E402

from test_publish import briefing  # noqa: E402


SENTENCE = (
    "I need to insert the full content here but since it's too large for this example, "
    "in practice the system will handle the full string from the file."
)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def _fill(self, count: int, month: str = "01") -> None:
        for number in range(1, count + 1):
            publish.add_post(
                self.tmp,
                briefing(number, f"2026-{month}-{number:02d}", f"https://example.com/{month}/{number}"),
            )

    def test_required_count_is_the_window_once_twenty_briefings_exist(self):
        self.assertEqual(publish.required_feed_count(0), 1)
        self.assertEqual(publish.required_feed_count(7), 7)
        self.assertEqual(publish.required_feed_count(20), 20)
        self.assertEqual(publish.required_feed_count(70), 20)

    def test_published_placeholder_sentence_is_not_json(self):
        self.assertEqual(len(SENTENCE.encode()), 147)
        with self.assertRaises(publish.FeedError) as caught:
            publish.validate_feed_text(SENTENCE, minimum=20)
        self.assertIn("placeholder", str(caught.exception))

    def test_placeholder_markers_fail_inside_a_json_object(self):
        for raw in ("PLACEHOLDER", "PLACEHOLDER_FIX", json.dumps({"posts": [SENTENCE]})):
            with self.assertRaises(publish.FeedError):
                publish.validate_feed_text(raw, minimum=1)

    def test_a_real_briefing_is_not_flagged_as_placeholder(self):
        post = briefing(1, "2026-01-01", "https://example.com/1")
        publish.normalize_post(post)
        self.assertIsNone(publish.marker_in(json.dumps(post)))

    def test_guard_replaces_the_placeholder_with_the_newest_window(self):
        self._fill(21)
        before = set(publish.load_archive(self.tmp))
        (self.tmp / "feed.json").write_text(SENTENCE, encoding="utf-8")
        result = publish.guard(self.tmp)
        self.assertEqual(result["action"], "restored")
        self.assertEqual(result["restored_from"], "archive")
        self.assertEqual(set(publish.load_archive(self.tmp)), before)
        window = json.loads((self.tmp / "feed.json").read_text(encoding="utf-8"))
        self.assertEqual(len(window["posts"]), 20)
        self.assertEqual(window["posts"][0]["id"], "2026-01-21-item-21")
        self.assertNotIn("2026-01-01-item-1", {post["id"] for post in window["posts"]})
        self.assertFalse(any(post["date"] == "2026-11-08" for post in window["posts"]))
        publish.check(self.tmp)
        self.assertEqual(publish.guard(self.tmp)["action"], "none")

    def test_guard_keeps_a_genuine_post_found_in_a_short_feed(self):
        self._fill(20)
        new = briefing(21, "2026-03-01", "https://example.com/21")
        meta = publish.load_meta(self.tmp)
        short = publish.feed_document([new], meta)
        (self.tmp / "feed.json").write_text(publish.dumps(short), encoding="utf-8")
        result = publish.guard(self.tmp)
        self.assertEqual(result["action"], "restored")
        self.assertEqual(result["salvaged"]["added"], ["2026-03-01-item-21"])
        self.assertIn("2026-03-01-item-21", publish.load_archive(self.tmp))
        self.assertIn("2026-01-01-item-1", publish.load_archive(self.tmp))
        window = json.loads((self.tmp / "feed.json").read_text(encoding="utf-8"))
        self.assertEqual(len(window["posts"]), 20)
        self.assertEqual(window["posts"][0]["id"], "2026-03-01-item-21")
        self.assertNotIn("2026-01-01-item-1", {post["id"] for post in window["posts"]})

    def test_guard_does_not_touch_a_full_window_with_a_new_post(self):
        self._fill(20)
        new = briefing(21, "2026-03-01", "https://example.com/21")
        current = json.loads((self.tmp / "feed.json").read_text(encoding="utf-8"))
        current["posts"] = [new] + current["posts"][:-1]
        current["updated"] = "2026-03-01T07:45:00+00:00"
        written = json.dumps(current)
        (self.tmp / "feed.json").write_text(written, encoding="utf-8")
        before = set(publish.load_archive(self.tmp))
        result = publish.guard(self.tmp)
        self.assertEqual(result["action"], "none")
        self.assertEqual(set(publish.load_archive(self.tmp)), before)
        self.assertEqual((self.tmp / "feed.json").read_text(encoding="utf-8"), written)

    def test_guard_restores_when_a_copy_shortens_the_stored_body(self):
        self._fill(1)
        short = briefing(1, "2026-01-01", "https://example.com/01/1", body="Temporary works control " * 40)
        meta = publish.load_meta(self.tmp)
        text = publish.dumps(publish.feed_document([publish.normalize_post(short)], meta))
        (self.tmp / "feed.json").write_text(text, encoding="utf-8")
        result = publish.guard(self.tmp)
        self.assertEqual(result["action"], "restored")
        restored = json.loads((self.tmp / "feed.json").read_text(encoding="utf-8"))
        self.assertGreater(len(restored["posts"][0]["body"][0]["p"]), len(short["body"][0]["p"]))

    def test_check_rejects_a_short_window_when_the_archive_is_full(self):
        self._fill(21)
        document = json.loads((self.tmp / "feed.json").read_text(encoding="utf-8"))
        document["posts"] = document["posts"][:3]
        (self.tmp / "feed.json").write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(publish.FeedError):
            publish.check(self.tmp)
        (self.tmp / "feed.json").write_text(SENTENCE, encoding="utf-8")
        with self.assertRaises(publish.FeedError):
            publish.check(self.tmp)
        self.assertEqual(len(publish.load_archive(self.tmp)), 21)

    def test_out_of_order_and_extra_keys_fail_validation(self):
        self._fill(2)
        document = json.loads((self.tmp / "feed.json").read_text(encoding="utf-8"))
        document["posts"] = list(reversed(document["posts"]))
        with self.assertRaises(publish.FeedError):
            publish.validate_feed_text(json.dumps(document), minimum=2)
        document = json.loads((self.tmp / "feed.json").read_text(encoding="utf-8"))
        document["note"] = "see file"
        with self.assertRaises(publish.FeedError):
            publish.validate_feed_text(json.dumps(document), minimum=2)

    def test_guard_uses_git_history_when_the_archive_is_gone(self):
        subprocess.run(["git", "init"], cwd=self.tmp, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.tmp, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.tmp, check=True)
        self._fill(1)
        good = (self.tmp / "feed.json").read_text(encoding="utf-8")
        subprocess.run(["git", "add", "feed.json", "posts", "archive"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-m", "valid feed"], cwd=self.tmp, check=True, capture_output=True)
        shutil.rmtree(self.tmp / "posts")
        shutil.rmtree(self.tmp / "archive")
        (self.tmp / "feed.json").write_text(SENTENCE, encoding="utf-8")
        subprocess.run(["git", "add", "feed.json"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-m", "placeholder"], cwd=self.tmp, check=True, capture_output=True)
        result = publish.guard(self.tmp)
        self.assertEqual(result["action"], "restored")
        self.assertEqual(result["restored_from"], "git")
        self.assertEqual((self.tmp / "feed.json").read_text(encoding="utf-8"), good)
        self.assertEqual(list((self.tmp / "posts").glob("*.json")), [])
        self.assertNotIn("2026-11-08", result["ids"][0])

    def test_issue_text_asks_for_a_republish_and_does_not_compose_one(self):
        result = {
            "action": "restored",
            "restored_from": "archive",
            "problems": ["feed contains placeholder text (insert the full content here)"],
            "salvaged": {"added": []},
            "ids": ["2026-11-07-hse-trainee-scaffolder-fall-safe-zone"],
            "posts": 20,
        }
        body = publish.guard_issue_body(result, commit="6650f27")
        self.assertIn("6650f27", body)
        self.assertIn("Republish", body)
        self.assertIn("posts/", body)
        self.assertNotIn("sequence change splice", body)
        self.assertNotIn("CROSS 1588", body)
        rendered = publish.guard_issue_body({"action": "unrestored", "problems": ["feed is not JSON"]})
        self.assertIn("could not be restored", rendered)


class WorkflowTests(unittest.TestCase):
    def _workflow(self, name: str) -> str:
        return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_publish_workflow_takes_in_posts_and_repairs_main(self):
        text = self._workflow("publish-feed.yml")
        self.assertIn("branches: [main]", text)
        for path in ("feed.json", "latest.json", '"posts/**"', '"archive/**"'):
            self.assertIn(f"- {path}", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("contents: write", text)
        self.assertIn("issues: write", text)
        self.assertIn("scripts/sync-main.sh origin main", text)
        self.assertIn("sync-issue", text)
        self.assertIn("python3 scripts/publish.py check", text)
        self.assertIn("sync-verdict", text)
        self.assertIn("news-feed-main", text)
        self.assertNotIn("--force", text)
        self.assertNotIn("pull_request:", text)

    def test_one_job_writes_main_so_a_capture_and_a_repair_cannot_race(self):
        workflows = sorted(path.name for path in (ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertEqual(workflows, ["feed-integrity.yml", "publish-feed.yml"])
        self.assertNotIn("contents: write", self._workflow("feed-integrity.yml"))

    def test_pull_requests_run_the_same_validation(self):
        text = self._workflow("feed-integrity.yml")
        self.assertIn("pull_request:", text)
        self.assertIn("branches: [main]", text)
        self.assertIn("python3 scripts/publish.py check", text)
        self.assertIn("unittest discover -s tests", text)

    def test_a_feed_push_is_checked_after_it_is_regenerated_not_before(self):
        # A new posts/<id>.json is not in the feeds until publish-feed has run.
        text = self._workflow("feed-integrity.yml")
        ignored = text[text.index("paths-ignore:"):text.index("workflow_dispatch:")]
        for path in ("feed.json", "latest.json", '"posts/**"', '"archive/**"'):
            self.assertIn(f"- {path}", ignored)

    def test_push_script_retries_on_the_new_tip_and_never_forces(self):
        text = (ROOT / "scripts" / "sync-main.sh").read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", text)
        self.assertIn('git reset --quiet --hard "$remote/$branch"', text)
        self.assertIn("python3 scripts/publish.py sync", text)
        self.assertNotIn("--force", text)


if __name__ == "__main__":
    unittest.main()
