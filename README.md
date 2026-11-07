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
```

`add` locks the archive, refuses placeholder and stub posts, and will not replace a stored briefing with a shorter body. A second post on the same Europe/London `date` with the same title or the same first source URL is rejected. The same source URL on a later date is kept: later briefings cite the same HSE and CROSS reports. Concurrent adds of different posts both remain.

`capture-feed` copies any genuine new post out of the rolling `feed.json` into the archive. Placeholder text, empty lists, and unreadable JSON are ignored. It does not rewrite `feed.json` and it does not delete history.

`posts/` and `archive/feed.json` were filled from every `feed.json` that still parsed in git. The fullest genuine body was kept. There is no recoverable briefing text for 4, 5, or 6 November 2026: those commits are placeholder files, and no story was written for them.

The pasteable task change is `publisher/SAFETY_OVERRIDE.txt`. It is not installed. Until someone installs it, the existing task can still push a 20-post or placeholder `feed.json` to `main`.

## What this does not protect

A direct `push_files` to `main` updates `https://raw.githubusercontent.com/dm1033/twc-news/main/feed.json` as soon as GitHub accepts it. `news.js` then renders that JSON if it parses. The `feed-integrity` workflow only reports status. It does not refuse the push. The capture workflow stores new valid posts after they have already landed; it is not a revert and it is not prevention.

No rule here stops the same credentials from deleting `posts/` or `archive/feed.json` on a later push. Commits that damaged `feed.json` were made as David John Miller, so a bypass granted to that account does not contain the publisher. Finding and disabling that writer is still required.

The rolling window stays at 20 posts so the current task can keep reading `feed.json` without being handed the whole archive (that size is what the task has been replacing with placeholder text). The window committed here is the 20 newest briefings, through 11 October 2026. Eleven of those were not in the live 20-post or 31-post files (`2026-11-03` and 11–21 October, with no 16 October post ever recovered). Eleven older posts leave the GitHub window; each is already the same body on the live 31-post fallback, so the current page does not swap in a shorter copy. Together that is 43 posts the current page can show. The other 27 stay only in `posts/` and `archive/feed.json` until the page below is changed.

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

`python3 scripts/publish.py check` confirms `archive/feed.json` is the full archive, newest first, and that `feed.json` is a valid window of at most 20 archived posts. `.github/workflows/feed-integrity.yml` runs that check and the unit tests on pull requests and on pushes to `main`. A red check does not unpublish a file that is already on `main`.
