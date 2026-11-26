# Publishing a TWC news briefing

The scheduled publisher (TWC News Tab Publisher) adds **one small JSON file** to `dm1033/twc-news` on `main`. It does not write `feed.json`. That file is about 170 KB. The publisher cannot reproduce it in one commit, and the evening pushes on 13, 14 and 15 November 2026 replaced it with placeholder text. Those commits were restored and the briefings never arrived.

`publish-feed` (`.github/workflows/publish-feed.yml`) is the guard. It validates the push, merges a recoverable briefing, rebuilds the feeds, and restores `posts/` when the push cannot be repaired.

## File to push

| | |
| --- | --- |
| Repository | `dm1033/twc-news` |
| Branch | `main` |
| Path | `incoming/<id>.json` |
| Also accepted | `posts/<id>.json` |
| Commit | one file, message `News: <date> <short title>` |

`incoming/<id>.json` is preferred. The workflow validates it, stores a clean copy at `posts/<id>.json`, deletes the incoming file, and rebuilds the feeds. Do not edit or delete a file that is already in `posts/`.

Do not write `feed.json`, `archive/feed.json`, or `latest.json` in this repository or in `dm1033/twc-website` or `dm1033/twc-netlify-clean-pages`. A single briefing object that lands in `feed.json` is still merged, then the 20-post window is rebuilt. A placeholder is not merged.

Read `latest.json` (ten newest: id, date, kicker, title, first source URL) before writing. Do not read `feed.json`.

- If `latest.json` already has a briefing dated today (Europe/London), publish nothing.
- If the story's first source URL is the source of the newest briefing, choose another story or stop.

## JSON object

Exactly these keys, and no others. Unknown keys are removed; a missing key is refused.

```json
{
  "id": "2026-11-10-cross-1588-girder-lift-sequence",
  "date": "2026-11-10",
  "kicker": "CROSS-UK",
  "title": "One sentence, 240 characters at most",
  "excerpt": "Two or three sentences that summarise the briefing.",
  "tags": ["CROSS-UK", "Lifting", "Temporary works"],
  "linkedinReady": "The LinkedIn post text for this briefing.",
  "body": [
    {"h": "What was reported", "p": "The full account, in complete sentences."},
    {"h": "Why it matters for contractors", "p": "The full explanation."},
    {"h": "Practical TWC and TWS control points", "ul": ["First point.", "Second point."]},
    {"h": "Sources", "sources": [{"label": "Name of the source and its date", "url": "https://..."}]},
    {"h": "Limitations", "p": "Educational briefing only. Not for construction."}
  ]
}
```

- `id` is `YYYY-MM-DD` plus a slug of lowercase letters, digits and hyphens. The file name is `<id>.json`. `date` is the same `YYYY-MM-DD`, and `id` starts with it.
- `kicker` is 80 characters at most. `title` is 240 characters at most. `tags` is 1 to 24 short strings.
- `body` is 1 to 30 blocks. A block may use only `h`, `p`, `ul` and `sources`. Each source has a `label` and an `http` or `https` `url`.
- The body is the whole briefing: at least 800 characters of real text. About 4 to 14 KB for the file.
- One object. A markdown fence or a leading BOM is stripped. A list of objects, or `{"posts":[...]}`, is accepted and each briefing is stored under its own id.

## What the workflow does with a recoverable file

- Validates the object.
- Drops unknown keys.
- Duplicate ids in the same push: the longer body is kept.
- Same calendar date and the same title or the same first source URL as a stored briefing: not added. A source used on a different date is allowed.
- A longer body replaces a shorter stored copy of the same id. A shorter copy does not.
- `posts/<id>.json` is the stored copy. `archive/feed.json` is every briefing, newest first. `feed.json` is the newest 20. `latest.json` is the newest 10.
- The incoming file is removed after a successful merge.

## What is refused

The public files are restored from `posts/` and the run shows a warning. Nothing new is published from that push. If no briefing dated that day has arrived by 21:30 UTC, `daily-briefing-check` fails and notes it on an issue.

- Placeholder text, including `PLACEHOLDER`, `PLACEHOLDER_AGAIN`, `PLACEHOLDER_WILL_REPLACE`, an empty file, or a note that the full JSON is in a local file.
- A file that is not JSON.
- A stub whose body is under 800 characters.
- A push that deletes a stored briefing or replaces it with a shorter body.

To remove a briefing on purpose, delete its file under `posts/` and run `python3 scripts/publish.py rebuild` in the same commit.

## Never publish

Job-hunting, CV, or salary material. Temporary-works briefings only: HSE, CROSS, BS 5975, scaffolding, formwork, excavation, sequence and stability.

If the whole briefing will not fit in the tool call, publish nothing. Do not push a placeholder and do not retry with a shorter body.
