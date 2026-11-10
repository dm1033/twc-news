# TWC News feed

Public JSON feed for the News tab on [temporaryworksconsulting.com/news.html](https://www.temporaryworksconsulting.com/news.html).

## How a briefing is published

One small new file:

```
dm1033/twc-news   main   posts/<id>.json
```

The file is the briefing as one JSON object, about 4 to 14 KB. Nothing else is written by the publisher. `.github/workflows/publish-feed.yml` runs on that push and writes `archive/feed.json`, `feed.json` and `latest.json` from `posts/`, usually within a minute.

The exact instructions for the scheduled **TWC News Tab Publisher** are in [`publisher/PUBLISHER_PROMPT.txt`](publisher/PUBLISHER_PROMPT.txt). They have to be pasted into the publisher by its owner. Nothing in this repository can change them, and until they are pasted the publisher keeps following its old rules.

### Why it works this way

Until 10 November 2026 the publisher was told to read `feed.json`, add one briefing, and write the whole file back to three repositories. `feed.json` is about 175 KB. The publisher could not reproduce that much text in one tool call, and committed a placeholder in its place:

| Date | What it committed as `feed.json` |
| --- | --- |
| 8 November | `PLACEHOLDER`, `PLACEHOLDER_FIX`, then a 147-byte note |
| 9 November | `PLACEHOLDER`, then an empty file |
| 10 November | `PLACEHOLDER_TO_BE_REPLACED` twice, then `See /tmp/updated_feed_pretty.json for full content - system to load` |

Each of those failed the `feed-integrity` check and was put back by the repair job, so the site was never broken, but no briefing was published on any of those days. On the days the same publisher wrote a single briefing of 6 to 8 KB (30 and 31 October, 3 November) the write succeeded. One briefing is a size it can write. The whole feed is not.

So the publisher now writes one briefing, and the feed files are generated here.

### What the run on GitHub means

`publish-feed` runs on every push to `main` that touches `posts/`, `feed.json`, `archive/` or `latest.json`.

- **Green**: `main` is valid, and either a briefing was published or nothing was wrong with the push.
- **Red**: the push carried something that is not a briefing, and nothing was published. The feeds were still put back from `posts/`, so the site is intact. Red means today's briefing is missing. An issue is opened, or a comment added to the open one, naming what was refused.

The run's summary page lists what was published and what was refused.

## Files

| Path | Role |
| --- | --- |
| `posts/<id>.json` | One briefing each. The only thing the publisher writes. The stored copy of record. |
| `archive/feed.json` | Every briefing, newest first. Generated from `posts/`. |
| `feed.json` | The newest 20 briefings. Generated from `posts/`. The website fetches this URL. |
| `latest.json` | The ten newest briefings as id, date, kicker, title and first source URL. Generated. About 4 KB. The publisher reads this for its duplicate test instead of the feed. |
| `feed.meta.json` | `source` and `disclaimer` used when the feeds are generated. |

## What is accepted and what is refused

`python3 scripts/publish.py sync` is what the workflow runs. It works on whatever a push left on `main`.

A post is accepted when it has exactly the keys `id`, `date`, `kicker`, `title`, `excerpt`, `tags`, `linkedinReady` and `body`, an id that starts with its date, body blocks that use only `h`, `p`, `ul` and `sources`, http(s) source URLs, and at least 800 characters of body text.

Taken in:

- a new `posts/<id>.json`
- a post file with the wrong name, or wrapped in a list or a `{"posts": [...]}` object. It is stored under its own id
- a genuine post inside `feed.json`, including a short `feed.json` that holds only the new briefing. That is where the old instructions put it, so it is treated as a publish and not as a fault
- a fuller copy of a stored briefing. The longer body is kept

Refused:

- placeholder text, an empty file, a file that is not JSON, a stub with a short body. A refused post file is removed. A refused `feed.json` is regenerated
- a second post on the same date with the same title or the same first source URL as a stored one. It is not added, and this alone does not fail the run
- a push that deletes a stored briefing or replaces it with a shorter copy. The stored briefing is put back

One unreadable file in `posts/` used to make the whole archive unreadable to the older commands. `sync` removes or restores that file first, so it cannot.

Nothing here composes a briefing. A briefing that was only described in a commit subject is not in the feed and has to be published again.

To remove a briefing on purpose, delete its file and run `python3 scripts/publish.py rebuild` in the same commit.

## Commands

From a checkout:

```bash
python3 scripts/publish.py sync            # take in what is on disk, regenerate the feeds
python3 scripts/publish.py add post.json   # add one briefing by hand
python3 scripts/publish.py rebuild         # regenerate the feeds from posts/
python3 scripts/publish.py check           # fail if the feeds are not what posts/ generates
python3 -m unittest discover -s tests      # includes an end-to-end run against a temporary git remote
```

`add` refuses placeholder and stub posts, refuses a same-day repeat, and will not replace a stored briefing with a shorter body. `capture-feed` and `guard` are the older single-purpose commands. `sync` does both and they are kept for use by hand.

## Workflows

| Workflow | Runs on | Does |
| --- | --- | --- |
| `publish-feed.yml` | a push to `main` that changes `posts/`, `feed.json`, `archive/` or `latest.json` | `sync`, commits the regenerated feeds, opens an issue for anything refused, then runs `check` and the tests on what `main` now serves |
| `feed-integrity.yml` | pull requests, and pushes to `main` that change code | `check` and the tests |

`feed-integrity` does not run on a push that only changes the feed. A new `posts/<id>.json` is not in the feeds until `publish-feed` has regenerated them, so checking the pushed commit would report a state that is replaced seconds later. `publish-feed` runs the same check afterwards.

One job writes `main`. Before this, a capture job and a repair job ran on the same push and one of them was cancelled when two pushes arrived ten seconds apart.

The commit that job pushes uses the Actions `GITHUB_TOKEN`. GitHub does not start a workflow from that token, so the job cannot trigger itself. `scripts/sync-main.sh` retries against the new tip when its push loses a race, so a publisher commit that lands mid-run is taken in.

## Limits

- A direct push to `main` is public as soon as GitHub accepts it. A bad file is what `raw.githubusercontent.com` serves until the job finishes, and that host can cache it for a few minutes. The website skips a feed that does not parse.
- A ruleset cannot stop the account that owns the repository, and a rule that blocked direct pushes would block the publisher too. Repair after the push is what GitHub can apply here.
- This job only repairs `dm1033/twc-news`. The old instructions also wrote `assets/news/feed.json` in `dm1033/twc-website` and `dm1033/twc-netlify-clean-pages`. Those repositories are not visible from here. The new instructions tell the publisher to stop writing them.
- There is no recoverable briefing text for 4, 5, 6, 8, 9 or 10 November 2026. Those commits were placeholders and no story was stored for them.

## Background: checks made on 7 November 2026

- `https://www.temporaryworksconsulting.com/news.html` and `https://temporaryworksconsulting.netlify.app/news.html` are the same Netlify deploy.
- `assets/js/news.js` fetches `https://raw.githubusercontent.com/dm1033/twc-news/main/feed.json` first, then `/assets/news/feed.json`, then an embedded `#news-data` block. The first copy of an id wins. Invalid JSON is skipped. A valid short list is used.
- `posts/` and `archive/feed.json` were filled from every `feed.json` that still parsed in git, keeping the fullest genuine body. 70 briefings, the newest dated 7 November 2026.
- Rulesets listed for this repo were empty. Nothing in this repository edits rulesets, DNS, or Netlify.

## Production change required

> Note added 10 November 2026. The section below was written without sight of the website's repository and names the wrong one. `www.temporaryworksconsulting.com` is deployed from `dm1033/Temporary-Works-Consulting-Design-` by GitHub Actions. That repository reads `https://raw.githubusercontent.com/dm1033/twc-news/main/feed.json` in three places: `assets/js/news.js`, the homepage headlines, and `_build/sync-news.mjs` at build time. Each merges it by id with the site's own `/assets/news/feed.json`. It does not read `dm1033/twc-website` or `dm1033/twc-netlify-clean-pages`. A generated 20-post `feed.json` here is therefore all the live site needs from the publisher. The change below is still a sound improvement and has not been made.

In `dm1033/twc-website` (the Netlify site serving `www.temporaryworksconsulting.com`), edit `assets/js/news.js`.

Fetch the archive first, then the rolling feed, then the local fallback:

```javascript
var sources = [
  "https://raw.githubusercontent.com/dm1033/twc-news/main/archive/feed.json",
  "https://raw.githubusercontent.com/dm1033/twc-news/main/feed.json",
  "/assets/news/feed.json"
];
```

Replace the merge so that:

- a document that is not JSON, has no `posts` array, or has an empty `posts` array is ignored
- a post that is placeholder text or a stub body is ignored
- the same `id` keeps the longer body, not whichever file was fetched first
- the list is sorted newest first
- the rendered list is never smaller than the id set of `archive/feed.json` alone

`scripts/publish.py` implements that merge as `consumer_merge`. The tests cover a placeholder document, an empty document, and a shorter copy of an id. The live `news.js` does not do this yet: the first file wins per id, and a valid 20-post file is accepted.

Do not rebuild `#news-data` or `assets/news/feed.json` from the rolling 20-post `feed.json`. The embed and the local file are currently the 31-post fallback dated 2026-11-02. Point any build that refreshes them at `archive/feed.json` after this merge rule is deployed. Until that deploy, do not let the publisher overwrite the website file with a 20-post list.

`dm1033/twc-netlify-clean-pages` was not verified as a live host. Apply the same `news.js` change there only if that repo is still published. Do not switch DNS or the Netlify site to it from this work.

## Checks

`python3 scripts/publish.py check` confirms that `archive/feed.json` is the full archive generated from `posts/`, that `feed.json` is the newest 20 of those with no placeholder text, and that `latest.json` is the generated index. The tests in `tests/` cover the publish route, each kind of refused push including the exact files committed on 10 November, and the instructions in `publisher/PUBLISHER_PROMPT.txt` against the validator.
