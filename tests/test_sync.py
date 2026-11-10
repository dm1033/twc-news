#!/usr/bin/env python3
"""Publishing by one post file, and what happens to a push that is not a briefing.

The scheduled publisher could not write a file the size of feed.json and
committed placeholder text in its place. These tests cover the route it is
given instead (posts/<id>.json) and the repair of the pushes it made. The
pipeline tests run scripts/sync-main.sh against a real git remote in a
temporary directory. Nothing here publishes anywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import publish  # noqa: E402

from test_publish import briefing  # noqa: E402

# feed.json exactly as pushed to main on 10 November 2026 (3a283bd and a555ae4).
PUSHED_NOTE = "See /tmp/updated_feed_pretty.json for full content - system to load"
PUSHED_PLACEHOLDER = "PLACEHOLDER_TO_BE_REPLACED"

NEW = briefing(99, "2026-02-01", "https://example.com/cross/1588", title="Girder lift sequence splice")


def fill(root: Path, count: int) -> None:
    for number in range(1, count + 1):
        publish.add_post(root, briefing(number, f"2026-01-{number:02d}", f"https://example.com/01/{number}"))


def feed_ids(root: Path) -> list[str]:
    return [post["id"] for post in json.loads((root / "feed.json").read_text(encoding="utf-8"))["posts"]]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        fill(self.tmp, 21)

    def _post_file(self, name: str, content) -> Path:
        path = self.tmp / "posts" / name
        text = content if isinstance(content, str) else json.dumps(content)
        path.write_text(text, encoding="utf-8")
        return path

    def test_one_new_post_file_is_published(self):
        self._post_file(f"{NEW['id']}.json", NEW)
        result = publish.sync(self.tmp)
        self.assertTrue(result["ok"])
        self.assertEqual(result["published"], [NEW["id"]])
        self.assertEqual(result["rejected"], [])
        ids = feed_ids(self.tmp)
        self.assertEqual(ids[0], NEW["id"])
        self.assertEqual(len(ids), 20)
        self.assertEqual(len(publish.load_archive(self.tmp)), 22)
        latest = json.loads((self.tmp / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["count"], 22)
        self.assertEqual(latest["latest"][0]["id"], NEW["id"])
        self.assertEqual(latest["latest"][0]["source"], "https://example.com/cross/1588")
        publish.check(self.tmp)
        self.assertEqual(publish.sync_verdict(result)[0], 0)
        self.assertEqual(publish.sync_subject(result), f"News: publish {NEW['id']}")

    def test_the_push_that_failed_on_10_november_is_repaired_and_reported(self):
        before = (self.tmp / "feed.json").read_bytes()
        for pushed in (PUSHED_NOTE, PUSHED_PLACEHOLDER, ""):
            (self.tmp / "feed.json").write_text(pushed, encoding="utf-8")
            result = publish.sync(self.tmp)
            self.assertFalse(result["ok"], pushed)
            self.assertEqual(result["published"], [])
            self.assertEqual(result["action"], "restored")
            self.assertEqual([entry["path"] for entry in result["rejected"]], ["feed.json"])
            self.assertEqual((self.tmp / "feed.json").read_bytes(), before)
            publish.check(self.tmp)
            code, text = publish.sync_verdict(result)
            self.assertEqual(code, 1)
            self.assertIn("No briefing was published", text)
            self.assertIn("posts/<id>.json", text)
            self.assertEqual(
                publish.sync_subject(result),
                "fix: restore the feed after a push that failed validation",
            )

    def test_placeholder_post_file_is_refused_and_removed(self):
        for pushed in (PUSHED_PLACEHOLDER, PUSHED_NOTE, "", "{", json.dumps({"id": NEW["id"]})):
            path = self._post_file(f"{NEW['id']}.json", pushed)
            result = publish.sync(self.tmp)
            self.assertFalse(result["ok"], pushed)
            self.assertEqual([entry["path"] for entry in result["rejected"]], [f"posts/{NEW['id']}.json"])
            self.assertFalse(path.exists())
            self.assertEqual(len(publish.load_archive(self.tmp)), 21)
            publish.check(self.tmp)

    def test_a_stub_is_not_a_briefing(self):
        stub = briefing(99, "2026-02-01", "https://example.com/cross/1588", body="Full text to follow.")
        self._post_file(f"{stub['id']}.json", stub)
        result = publish.sync(self.tmp)
        self.assertFalse(result["ok"])
        self.assertIn("truncated", result["rejected"][0]["problem"])
        self.assertNotIn(stub["id"], publish.load_archive(self.tmp))

    def test_a_bad_post_file_does_not_empty_the_archive(self):
        self._post_file("2026-02-01-cross-1588.json", PUSHED_PLACEHOLDER)
        # Before sync, one unreadable file makes the whole archive unreadable.
        with self.assertRaises((publish.FeedError, json.JSONDecodeError)):
            publish.load_archive(self.tmp)
        publish.sync(self.tmp)
        self.assertEqual(len(publish.load_archive(self.tmp)), 21)
        self.assertEqual(len(feed_ids(self.tmp)), 20)

    def test_good_post_file_is_published_even_when_feed_json_is_a_placeholder(self):
        self._post_file(f"{NEW['id']}.json", NEW)
        (self.tmp / "feed.json").write_text(PUSHED_NOTE, encoding="utf-8")
        result = publish.sync(self.tmp)
        self.assertTrue(result["ok"])
        self.assertEqual(result["published"], [NEW["id"]])
        self.assertEqual([entry["path"] for entry in result["rejected"]], ["feed.json"])
        self.assertEqual(feed_ids(self.tmp)[0], NEW["id"])
        code, text = publish.sync_verdict(result)
        self.assertEqual(code, 0)
        self.assertIn("::warning title=Refused feed.json", text)

    def test_short_feed_holding_only_the_new_briefing_is_a_publish(self):
        # What the older instructions produced on the days they worked.
        meta = publish.load_meta(self.tmp)
        (self.tmp / "feed.json").write_text(
            publish.dumps(publish.feed_document([NEW], meta)), encoding="utf-8"
        )
        result = publish.sync(self.tmp)
        self.assertTrue(result["ok"])
        self.assertEqual(result["rejected"], [])
        self.assertEqual(result["published"], [NEW["id"]])
        ids = feed_ids(self.tmp)
        self.assertEqual((ids[0], len(ids)), (NEW["id"], 20))
        self.assertTrue((self.tmp / "posts" / f"{NEW['id']}.json").is_file())

    def test_wrapped_or_misnamed_post_file_is_stored_under_its_id(self):
        for name, content in (
            ("today.json", NEW),
            ("2026-02-01.json", [NEW]),
            ("feed-update.json", {"posts": [NEW]}),
            (f"{NEW['id']}.json", "```json\n" + json.dumps(NEW, indent=2) + "\n```\n"),
            (f"{NEW['id']}.json", "\ufeff" + json.dumps(NEW)),
        ):
            with self.subTest(name=name):
                root = Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, root)
                fill(root, 3)
                text = content if isinstance(content, str) else json.dumps(content)
                (root / "posts" / name).write_text(text, encoding="utf-8")
                result = publish.sync(root)
                self.assertEqual(result["published"], [NEW["id"]])
                self.assertEqual(result["rejected"], [])
                stored = root / "posts" / f"{NEW['id']}.json"
                self.assertEqual([path.name for path in (root / "posts").glob("*.json") if "02-01" in path.name or path.name == name], [stored.name])
                self.assertEqual(json.loads(stored.read_text(encoding="utf-8")), publish.normalize_post(NEW))
                publish.check(root)

    def test_same_day_repeat_of_a_stored_briefing_is_not_added(self):
        repeat = briefing(98, "2026-01-21", "https://example.com/01/21", title="Second run, same source")
        path = self._post_file(f"{repeat['id']}.json", repeat)
        result = publish.sync(self.tmp)
        self.assertTrue(result["ok"])
        self.assertEqual(result["published"], [])
        self.assertEqual(result["duplicates"][0]["of"], "2026-01-21-item-21")
        self.assertFalse(path.exists())
        self.assertEqual(len(publish.load_archive(self.tmp)), 21)

    def test_deleted_and_shortened_stored_briefings_are_put_back(self):
        deleted = self.tmp / "posts" / "2026-01-03-item-3.json"
        shortened = self.tmp / "posts" / "2026-01-04-item-4.json"
        full = shortened.read_bytes()
        deleted.unlink()
        cut = briefing(4, "2026-01-04", "https://example.com/01/4", body="Temporary works control " * 40)
        shortened.write_text(json.dumps(cut), encoding="utf-8")
        result = publish.sync(self.tmp)
        self.assertFalse(result["ok"])
        self.assertEqual(sorted(result["restored"]), ["2026-01-03-item-3", "2026-01-04-item-4"])
        self.assertTrue(deleted.is_file())
        self.assertEqual(shortened.read_bytes(), full)
        self.assertEqual(len(publish.load_archive(self.tmp)), 21)
        self.assertIn("publish.py rebuild", publish.sync_issue_body(result))

    def test_a_fuller_correction_replaces_the_stored_copy(self):
        fuller = briefing(4, "2026-01-04", "https://example.com/01/4", body="Temporary works control " * 120)
        self._post_file("2026-01-04-item-4.json", fuller)
        result = publish.sync(self.tmp)
        self.assertTrue(result["ok"])
        self.assertEqual(result["replaced"], ["2026-01-04-item-4"])
        self.assertEqual(result["published"], [])
        stored = publish.load_archive(self.tmp)["2026-01-04-item-4"]
        self.assertEqual(len(publish.body_text(stored)), len(publish.body_text(fuller)))

    def test_a_briefing_removed_on_purpose_stays_removed(self):
        (self.tmp / "posts" / "2026-01-03-item-3.json").unlink()
        self.assertEqual(publish.main(["--root", str(self.tmp), "rebuild"]), 0)
        result = publish.sync(self.tmp)
        self.assertEqual((result["restored"], result["rejected"]), ([], []))
        self.assertEqual(len(publish.load_archive(self.tmp)), 20)

    def test_second_sync_changes_nothing(self):
        self._post_file(f"{NEW['id']}.json", NEW)
        publish.sync(self.tmp)
        names = ("feed.json", "latest.json", "archive/feed.json", f"posts/{NEW['id']}.json")
        before = [(self.tmp / name).read_bytes() for name in names]
        result = publish.sync(self.tmp)
        self.assertEqual(
            (result["ok"], result["action"], result["published"], result["rejected"]),
            (True, "none", [], []),
        )
        self.assertEqual([(self.tmp / name).read_bytes() for name in names], before)
        self.assertEqual(publish.sync_subject(result), "chore: regenerate the feeds from posts/")

    def test_check_requires_the_generated_latest_index(self):
        publish.check(self.tmp)
        (self.tmp / "latest.json").write_text(PUSHED_PLACEHOLDER, encoding="utf-8")
        with self.assertRaises(publish.FeedError):
            publish.check(self.tmp)
        (self.tmp / "latest.json").unlink()
        with self.assertRaises(publish.FeedError):
            publish.check(self.tmp)
        publish.sync(self.tmp)
        publish.check(self.tmp)

    def test_latest_index_is_small_enough_to_read_whole(self):
        document = publish.latest_document(list(publish.load_archive(ROOT).values()))
        self.assertLessEqual(len(document["latest"]), publish.LATEST_LIMIT)
        self.assertLess(len(publish.dumps(document)), 8000)

    def test_longer_means_more_text_not_later_in_the_alphabet(self):
        short_but_later = briefing(1, "2026-03-01", "https://example.com/z", body="Zzz " * 250)
        long_but_earlier = briefing(1, "2026-03-01", "https://example.com/z", body="Aaa " * 600)
        self.assertTrue(publish.longer(long_but_earlier, short_but_later))
        self.assertFalse(publish.longer(short_but_later, long_but_earlier))

    def test_issue_text_points_at_the_one_file_route_and_composes_nothing(self):
        (self.tmp / "feed.json").write_text(PUSHED_NOTE, encoding="utf-8")
        result = publish.sync(self.tmp)
        body = publish.sync_issue_body(result, "3a283bd")
        self.assertIn("posts/<id>.json", body)
        self.assertIn("publisher/PUBLISHER_PROMPT.txt", body)
        self.assertIn("did not write a briefing", body)
        self.assertIn("3a283bd", body)
        self.assertNotIn("CROSS", body)


class PublisherPromptTests(unittest.TestCase):
    """The instructions handed to the publisher must describe what sync accepts."""

    def setUp(self):
        self.text = (ROOT / "publisher" / "PUBLISHER_PROMPT.txt").read_text(encoding="utf-8")

    def _example(self) -> dict:
        start = self.text.index('{\n  "id"')
        end = self.text.index("\n}\n", start) + 2
        return json.loads(self.text[start:end])

    def test_example_post_has_the_schema_the_validator_enforces(self):
        example = self._example()
        self.assertEqual(tuple(example), publish.POST_KEYS)
        self.assertRegex(example["id"], publish.ID_RE)
        self.assertTrue(example["id"].startswith(example["date"]))
        for block in example["body"]:
            self.assertLessEqual(set(block), set(publish.BODY_KEYS))
        # Written out in full, the same object is a briefing sync will publish.
        example["body"][0]["p"] = "The lift sequence was changed on site. " * 30
        example["body"][-1]["p"] = "Educational briefing only. Not for construction."
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        fill(root, 2)
        (root / "posts" / f"{example['id']}.json").write_text(json.dumps(example), encoding="utf-8")
        self.assertEqual(publish.sync(root)["published"], [example["id"]])

    def test_limits_quoted_to_the_publisher_are_the_real_ones(self):
        self.assertIn(f"at least {publish.MIN_BODY_TEXT} characters", self.text)
        self.assertIn("80 characters at most", self.text)
        self.assertIn("240 characters at most", self.text)
        self.assertIn("1 to 24", self.text)
        self.assertIn("1 to 30", self.text)
        self.assertIn("ten newest", self.text)
        self.assertEqual(publish.LATEST_LIMIT, 10)

    def test_it_asks_for_one_post_file_and_no_feed_file(self):
        self.assertIn("posts/<id>.json", self.text)
        self.assertIn("latest.json", self.text)
        self.assertIn("Never write feed.json, archive/feed.json or latest.json", self.text)
        self.assertIn("Do not read feed.json or archive/feed.json", self.text)
        # The old instructions told it to rewrite the whole feed in three repositories.
        self.assertNotIn("prepend", self.text)
        self.assertFalse((ROOT / "publisher" / "SAFETY_OVERRIDE.txt").exists())


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class PipelineTests(unittest.TestCase):
    """scripts/sync-main.sh, as the workflow runs it, against a real remote."""

    ENV = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }

    def _run(self, cwd: Path, *command: str, check: bool = True) -> subprocess.CompletedProcess:
        env = {**os.environ, **self.ENV}
        env.pop("SYNC_RESULT", None)
        return subprocess.run(command, cwd=cwd, env=env, check=check, capture_output=True, text=True)

    def _clone(self, name: str) -> Path:
        path = self.tmp / name
        self._run(self.tmp, "git", "clone", "--quiet", str(self.remote), str(path))
        return path

    def _remote_file(self, name: str) -> str:
        return self._run(self.tmp, "git", "--git-dir", str(self.remote), "show", f"main:{name}").stdout

    def _remote_log(self) -> list[str]:
        out = self._run(self.tmp, "git", "--git-dir", str(self.remote), "log", "--format=%s", "main").stdout
        return out.splitlines()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.remote = self.tmp / "remote.git"
        self._run(self.tmp, "git", "init", "--quiet", "--bare", "--initial-branch=main", str(self.remote))
        seed = self._clone("seed")
        self._run(seed, "git", "symbolic-ref", "HEAD", "refs/heads/main")
        (seed / "scripts").mkdir()
        for name in ("publish.py", "sync-main.sh"):
            shutil.copy(ROOT / "scripts" / name, seed / "scripts" / name)
        shutil.copy(ROOT / "feed.meta.json", seed / "feed.meta.json")
        (seed / ".gitignore").write_text("posts/.lock\nsync-result.json\n__pycache__/\n", encoding="utf-8")
        fill(seed, 21)
        self._run(seed, "git", "add", "-A")
        self._run(seed, "git", "commit", "--quiet", "-m", "seed")
        self._run(seed, "git", "push", "--quiet", "origin", "main")
        self.publisher = self._clone("publisher")
        self.runner = self._clone("runner")

    def _publisher_pushes(self, relative: str, text: str, subject: str) -> None:
        path = self.publisher / relative
        path.write_text(text, encoding="utf-8")
        self._run(self.publisher, "git", "add", "--", relative)
        self._run(self.publisher, "git", "commit", "--quiet", "-m", subject)
        self._run(self.publisher, "git", "push", "--quiet", "origin", "main")

    def _job(self) -> dict:
        done = self._run(self.runner, "bash", "scripts/sync-main.sh", "origin", "main", check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads((self.runner / "sync-result.json").read_text(encoding="utf-8"))

    def test_one_pushed_post_file_reaches_the_public_feed(self):
        self._publisher_pushes(
            f"posts/{NEW['id']}.json", json.dumps(NEW), "News: 2026-02-01 girder lift sequence splice"
        )
        # The push alone changes neither feed.
        self.assertNotIn(NEW["id"], self._remote_file("feed.json"))
        result = self._job()
        self.assertEqual(result["published"], [NEW["id"]])
        feed = json.loads(self._remote_file("feed.json"))
        self.assertEqual((feed["posts"][0]["id"], len(feed["posts"])), (NEW["id"], 20))
        self.assertEqual(len(json.loads(self._remote_file("archive/feed.json"))["posts"]), 22)
        self.assertEqual(json.loads(self._remote_file("latest.json"))["latest"][0]["id"], NEW["id"])
        self.assertEqual(self._remote_log()[0], f"News: publish {NEW['id']}")
        self.assertEqual(self._run(self.runner, "python3", "scripts/publish.py", "check").returncode, 0)
        verdict = self._run(self.runner, "python3", "scripts/publish.py", "sync-verdict", "sync-result.json", check=False)
        self.assertEqual(verdict.returncode, 0)
        # A second run finds nothing to do and adds no commit.
        commits = len(self._remote_log())
        self.assertEqual(self._job()["action"], "none")
        self.assertEqual(len(self._remote_log()), commits)

    def test_placeholder_push_is_repaired_on_the_remote_and_fails_the_run(self):
        good = self._remote_file("feed.json")
        self._publisher_pushes("feed.json", PUSHED_NOTE, "News: 2026-11-10 CROSS 1588 girder lift sequence splice")
        self.assertEqual(self._remote_file("feed.json"), PUSHED_NOTE)
        result = self._job()
        self.assertFalse(result["ok"])
        self.assertEqual(self._remote_file("feed.json"), good)
        self.assertEqual(self._remote_log()[0], "fix: restore the feed after a push that failed validation")
        verdict = self._run(self.runner, "python3", "scripts/publish.py", "sync-verdict", "sync-result.json", check=False)
        self.assertEqual(verdict.returncode, 1)
        self.assertIn("No briefing was published", verdict.stdout)

    def test_a_publisher_commit_that_lands_mid_run_is_taken_in(self):
        self._publisher_pushes("feed.json", PUSHED_PLACEHOLDER, "News: first attempt")
        # The runner has already fetched; the publisher then pushes the real briefing.
        self._run(self.runner, "git", "fetch", "--quiet", "origin", "main")
        self._publisher_pushes(f"posts/{NEW['id']}.json", json.dumps(NEW), "News: second attempt")
        result = self._job()
        self.assertEqual(result["published"], [NEW["id"]])
        self.assertTrue(result["ok"])
        feed = json.loads(self._remote_file("feed.json"))
        self.assertEqual((feed["posts"][0]["id"], len(feed["posts"])), (NEW["id"], 20))


if __name__ == "__main__":
    unittest.main()
