# TWC News feed

Public JSON feed for the News tab on [temporaryworksconsulting.com/news.html](https://www.temporaryworksconsulting.com/news.html).

The scheduled **TWC News Tab Publisher** is instructed to read `feed.json`, prepend one briefing, keep at most 20 posts, and `push_files` straight to `main` on three paths:

- `dm1033/twc-news` `feed.json`
- `dm1033/twc-website` `assets/news/feed.json`
- `dm1033/twc-netlify-clean-pages` `assets/news/feed.json`

Those instructions are not proof of what is deployed. Checked on 7 November 2026:

- `https://www.temporaryworksconsulting.com/news.html` and `https://temporaryworksconsulting.netlify.app/news.html` are the same Netlify deploy (etag `ee89d875cdf9c7beaa2e0a5d027c34ac-ssl`).
- `assets/js/news.js` fetches `https://raw.githubusercontent.com/dm1033/twc-news/main/feed.json` first, then `/assets/news/feed.json`, then an embedded `#news-data` block. The first copy of an id wins. Invalid JSON is skipped. A valid short list is used.
- GitHub `feed.json` had 20 posts, newest `2026-11-07`. The live `/assets/news/feed.json` and the embed had 31 posts, newest `2026-11-02`. They are not the same file. The only GitHub id missing from the live file was `2026-11-07-hse-trainee-scaffolder-fall-safe-zone`.
- `https://twc-netlify-clean-pages.netlify.app/` returned 404. `https://twc-website.netlify.app/` returned 401. This repo cannot see those private repositories, so their current files were not verified.
- Rulesets listed for this repo were empty. Classic branch protection was not readable with the token used here (HTTP 403). Nothing in this change edits rulesets, DNS, or Netlify.

A second copy on any of those three `feed.json` paths is not a backup. The task is told to write all three.

## What is durable in this repo

| Path | Role |
| --- | --- |
| `posts/<id>.json` | Last-known-good briefing. One file each. Not deleted by the publish commands. |
| `archive/feed.json` | Full feed generated from `posts/`. The publisher is not instructed to write this path. |
| `feed.json` | Rolling window of at most 20 posts. Production fetches this URL today. The publisher overwrites it. |
| `feed.meta.json` | `source` and `disclaimer` used when the feeds are generated. |

Supported entry point, from a checkout:

```bash
python3 scripts/publish.py add path/to/post.json
python3 scripts/publish.py capture-feed
python3 scripts/publish.py rebuild
python3 scripts/publish.py check
python3 scripts/publish.py guard
```

`add` locks the archive, refuses placeholder and stub posts, and will not replace a stored briefing with a shorter body. A second post on the same Europe/London `date` with the same title or the same first source URL is rejected. The same source URL on a later date is kept: later briefings cite the same HSE and CROSS reports. Concurrent adds of different posts both remain.

`capture-feed` copies any genuine new post out of the rolling `feed.json` into the archive. Placeholder text, empty lists, and unreadable JSON are ignored. It does not rewrite `feed.json` and it does not delete history.

`check` fails when `archive/feed.json` is not the full archive, when `feed.json` fails the rules below, or when a rolling post is not already archived. `guard` applies those feed rules and rebuilds a bad `feed.json` from `posts/`. A window of 20 valid posts that includes one new complete briefing is left as it is. Run `guard` locally before a direct push if you are not using `add`.

`posts/` and `archive/feed.json` were filled from every `feed.json` that still parsed in git. The fullest genuine body was kept. There is no recoverable briefing text for 4, 5, or 6 November 2026: those commits are placeholder files, and no story was written for them. There is also no recoverable briefing for 8 November 2026. Three commits that day (`4eca8d1`, `19be357`, `6650f27`, subjects "News: 2026-11-08 CROSS 1588 sequence change splice") replaced `feed.json` with `PLACEHOLDER`, then `PLACEHOLDER_FIX`, then a 147-byte non-JSON note. That note is not in `posts/`, `archive/feed.json`, `feed.meta.json`, or any older `feed.json`. It was not reconstructed from the commit subject. Publish that briefing again with `add` if it should be on the site.

`feed.json` on this branch is again the newest 20 archived briefings, newest first, through `2026-11-07-hse-trainee-scaffolder-fall-safe-zone`. The other 50 stay in `posts/` and `archive/feed.json`. The public file is that 20-post window so the daily task is not handed the whole archive (that size is what the task has been replacing with placeholder text).

The pasteable task change is `publisher/SAFETY_OVERRIDE.txt`. Pasting it into the publisher is optional. The validation below does not depend on it.

## Validation on every push

A direct `push_files` to `main` updates `https://raw.githubusercontent.com/dm1033/twc-news/main/feed.json` as soon as GitHub accepts it. `news.js` renders that JSON when it parses, and skips it when it does not. The 8 November note did not parse, so the site stayed on its own copy, whose newest item on the 7 November check was 2026-11-02. Readers then missed every briefing that existed only on GitHub, including 2026-11-03 and 2026-11-07.

`scripts/publish.py add` never saw those commits. The publisher writes the file through the GitHub contents API. The check that matters is the one GitHub runs after the push, in `.github/workflows/restore-feed.yml`. It calls `python3 scripts/publish.py guard`.

`feed.json` must:

- parse as one JSON object with `updated`, `source`, `disclaimer`, and `posts`
- use the post schema in `scripts/publish.py` (`id`, `date`, `kicker`, `title`, `excerpt`, `tags`, `linkedinReady`, `body`), with a real body rather than a stub
- contain no placeholder text. The rejects include `PLACEHOLDER`, `PLACEHOLDER_FIX`, "insert the full content here", "too large for this example", and "full string from the file", including when that note is wrapped in JSON
- list distinct ids, newest first
- not replace a stored briefing with a shorter body
- contain at least 20 posts once 20 briefings are archived. The floor is the archive size when that is smaller. One post, or an empty list, is rejected while this archive has 70

A file that fails is not left on `main`. The job copies any new post that is itself a complete briefing into `posts/`, rebuilds the newest 20 from that archive, pushes the repair, and opens a GitHub issue. It does not compose a briefing to fill a gap. A valid 20-post file that includes one new complete briefing is left as pushed. Put `posts/<id>.json` in that same commit so the archive keeps it.

Pull requests and pushes also run `.github/workflows/feed-integrity.yml` (`check` and the unit tests). A red check still does not stop GitHub accepting the push. `restore-feed.yml` is the repair. `capture-feed-posts.yml` still only archives genuine posts; it does not rewrite `feed.json`.

This is the repair GitHub can actually apply to this publisher. The writes are contents-API commits by the account that owns the repository, so a ruleset that required a check would not hold: that account can bypass rulesets, and the 8 November commits were made by it. A ruleset that blocked direct pushes would also block the legitimate daily `push_files`. The gap that remains is time. The bad file is what `raw.githubusercontent.com` serves until the job finishes, and that host can cache the bad response for a few minutes. The repair is pushed with the Actions `GITHUB_TOKEN`. GitHub does not start a new workflow from that token, so the job validates the rebuilt JSON before it commits and the repair cannot loop. If a later rule stops that token from pushing, the job opens an issue and the feed-integrity check stays red.

No rule here stops those same credentials from deleting `posts/` or `archive/feed.json`. If the archive is gone as well as the feed, the job puts back the last `feed.json` in git that still validates, and it does not invent the missing post files. Finding and disabling a writer that deletes history is still required. This guard only repairs `dm1033/twc-news`. It does not protect `feed.json` in the website repositories.

## Production change required

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

`python3 scripts/publish.py check` confirms `archive/feed.json` is the full archive, newest first, and that `feed.json` is a valid window of the newest posts (20 once that many exist) with no placeholder text. `python3 scripts/publish.py guard` does that validation and, locally, rewrites a bad `feed.json` from `posts/`. `.github/workflows/feed-integrity.yml` runs `check` and the unit tests on pull requests and on pushes to `main`. `.github/workflows/restore-feed.yml` runs `guard` after a push to `main` that changes `feed.json`, and pushes a repair when the file fails. A red integrity check does not unpublish a file by itself. The restore job does.
