# Hermes Telegram User (MTProto)

A **self-contained Hermes platform plugin** for controlling Hermes from your own Telegram user account over MTProto/Telethon.

No external MCP server is required. The plugin owns its Telegram bridge, read tools, folder/unread logic, media retrieval, voice transcript cache, aliases, sanitization, and Telegram rate-limit protection.

Current plugin version: **0.7.0**.

## Core UX: `.h` inside Telegram

Send this from your own account in any Telegram chat:

```text
.h что здесь обсуждали сегодня?
```

The plugin:

1. accepts only your outgoing `.h ...` message;
2. edits that same message to `💭 Думаю…`;
3. sends the request into the normal Hermes gateway/agent loop;
4. suppresses streaming/tool-progress edits;
5. replaces the same Telegram message with the final Hermes answer.

Hermes sessions are scoped **one per Telegram chat**. Forum topic IDs are context metadata and do not split the Hermes session.

Telegram's 4096 limit is measured using Hermes' UTF-16 length function.

## Reply context and media

When `.h` is sent as a Telegram reply, the plugin injects:

- replied message id;
- author id/name;
- replied text;
- up to 3 messages from the reply chain by default;
- attachment metadata;
- the replied attachment itself when Hermes can cache it.

Examples:

```text
(reply to a message) .h он здесь прав?
(reply to a photo)   .h что на изображении?
(reply to a voice)   .h кратко что он сказал?
```

Photos/files/voice notes are placed in Hermes' normal media cache. `MessageType.VOICE` is passed to Hermes' central STT pipeline rather than implementing another Whisper client inside this plugin.

## Telegram tools

Version 0.7.0 exposes **33 tools** under the `telegram_user` toolset.

### Chats/history/search

- `tg_find_chat` — find dialogs by title, username, or saved alias.
- `tg_list_topics` — list forum topics and unread counters.
- `tg_read_messages` — read chat/topic history without marking it read.
- `tg_get_message_context` — one message + surrounding messages + reply target.
- `tg_search_messages` — search inside one chat/topic/time window.
- `tg_search_global` — search across the Telegram account.

Message results can include reply quote, forward origin, album/group id, edited/pinned state, views, forwards, comment count, hidden links, button labels, media metadata, aliases, and cached voice transcripts.

### Unread/folders

- `tg_list_folders` — list Telegram folders and their rule flags.
- `tg_get_unread` — unread messages account-wide or within a folder.
- `tg_read_folder` — read/summarize a time window across one folder.

Folder membership is evaluated locally using Telegram filter semantics: explicit include/exclude/pinned peers, contacts/non-contacts, groups, channels, bots, muted/read/archive exclusion, unread mentions, and manually marked-unread dialogs.

These operations do **not** call Telegram read-acknowledgement APIs. Reading a chat here never clears its badge — see [Marking a summary as read](#marking-a-summary-as-read) for the one tool that does.

Useful prompts:

```text
.h суммируй всё непрочитанное в папке Работа
.h что важного я пропустил за ночь?
.h дай дайджест папки Dev за сегодня
```

### Media and voice

- `tg_search_media` — search photos, voice, video, audio, GIFs and documents.
- `tg_download_media` — cache one Telegram attachment for Hermes analysis.
- `tg_transcribe_voice` — transcribe a voice/audio/video note through Hermes' configured STT.

`tg_transcribe_voice` stores successful transcripts in:

```text
~/.hermes/state/telegram-user/transcripts.sqlite3
```

The key is `(chat_id, message_id)`. A repeat request returns the saved transcript instead of spending another STT call. Pass `refresh=true` only when you intentionally want to transcribe it again.

Concurrent requests for the same uncached voice are collapsed by an in-process per-message lock.

Existing cached transcripts automatically appear in subsequent history/media results for that message.

To use Groq through Hermes, configure Hermes normally, for example:

```yaml
stt:
  enabled: true
  provider: groq
```

and provide `GROQ_API_KEY` to Hermes. The Telegram plugin does not call Groq directly.

### People and aliases

- `tg_contacts` — search/list Telegram contacts without exposing phone numbers.
- `tg_participants` — search/list members of a group/channel without phone numbers.
- `tg_set_alias` — save a local human name for a Telegram peer.
- `tg_list_aliases` — list saved aliases.
- `tg_delete_alias` — delete one alias.

Example:

```text
запомни этого пользователя как "Иска"
```

After Hermes calls `tg_set_alias`, tools accepting `chat` can resolve the exact alias too:

```text
найди, что Иска писал про P40
```

Aliases are stored locally in:

```text
~/.hermes/state/telegram-user/aliases.json
```

Alias operations modify only the plugin's local state. They do not edit Telegram contacts and never store phone numbers.

### Pinned, drafts, scheduled

- `tg_get_pinned` — a chat's (or forum topic's) pinned messages.
- `tg_get_drafts` — every unsent draft in the account, newest first. Reading them does not clear them.
- `tg_get_scheduled` — a chat's scheduled (not yet sent) messages. A message set to "send when online" comes back as `send_when_online: true` rather than as a 19 January 2038 date, and its id is marked `id_namespace: "scheduled"` because scheduled messages have their own id sequence.

`tg_participants` also takes `role` (`all`, `admins`, `banned`, `bots`, `recent`), and `tg_search_media` additionally accepts the kinds `url` (shared links), `mentions`, `my_mentions`, `chat_photos`, `contacts`, `geo` and `phone_calls`.

### Profile and sessions

- `tg_get_profile` — a user's bio, birthday, personal channel, premium/verified/scam flags, last-seen bucket and common-chat count; optionally the shared chats. Phone numbers are never returned, only a `phone_present` flag.
- `tg_get_sessions` — the devices the account is signed in on: device, app, coarse country/region, a truncated network (`a.b.0.0` / `2001:db8::`), first and last activity, and unconfirmed flags. Read-only: it cannot end a session.

### Local archive and offline search

`кто за последние полгода говорил про P40?` should not re-read Telegram every time. The plugin can copy history into a local SQLite archive and answer from it:

- `tg_archive_sync` — copy one chat's history into the archive. Resumable: a long backfill continues on the next call, and an interrupted run records the gap it left so the next sync closes it before taking anything newer.
- `tg_archive_search` — substring search over the archive, optionally limited to one chat and a time window. **No Telegram traffic at all.**
- `tg_archive_status` — archive path, size, per-chat message counts, and whether a chat is fully copied or still holds a gap.
- `tg_archive_forget` — drop one chat from the archive.

```text
~/.hermes/state/telegram-user/archive.sqlite3
```

An edited message **replaces** its stored row, so the archive is a copy of what Telegram holds now, not a log of every version. Nothing is written to Telegram and no read pointer moves.

### Saved chat collections

Re-listing the same chats every time is noise. A collection is a named local set of chats, with an optional exclude list, that reads can reuse as a scope:

- `tg_save_collection` — save or update a collection from chat ids, titles, usernames or aliases. Anything that did not resolve is reported back instead of being dropped silently.
- `tg_list_collections` — list collections; with a name, show its members and mark any member that no longer exists in the dialog list.
- `tg_delete_collection` — delete one.
- `tg_read_collection` — read a time window across a collection with its exclusions applied, with the same digest bounding as `tg_read_folder`. `tg_get_unread` also takes `collection=`.
- Exclusions win: a chat in both lists is treated as excluded.

```text
~/.hermes/state/telegram-user/collections.json
```

Members are stored as Telegram **peer ids**, never titles or usernames — both change, and a renamed chat has to keep resolving. Re-adding a chat or picking up a rename is just `tg_save_collection` again.

```text
.h суммаризуй коллекцию Работа
.h что нового в коллекции Доноры
```

### Digest watermarks

Watermarks remember how far a chat — or a single forum thread — has already been summarised, so a later digest can ask for what is new only:

- `tg_get_unread` / `tg_read_folder` take `since_last_digest` — read only what is not yet summarised, bounded by the mark. They write nothing.
- `tg_mark_summarized` — record that a summary covered this scope, and clear its badge (see [Marking a summary as read](#marking-a-summary-as-read)).
- `tg_list_digest_marks` / `tg_forget_digest_marks` — inspect or clear marks, for a chat or one thread.

```text
~/.hermes/state/telegram-user/digest_watermarks.json
```

The rules that keep this safe:

- a mark is keyed by Telegram **peer id**, plus a thread id for a forum topic — never by chat name;
- a mark only ever moves forward, and only `tg_mark_summarized` moves it; reading never does;
- a mark is set either to its own current recorded value (i.e. "clear the badge for what is already recorded as summarised") or to an explicit `up_to` — never to "the top of the chat";
- the badge acknowledgement is sent *before* the mark moves, so a refused call changes nothing;
- the archive and media tools never touch marks.

## Telegram FloodWait/rate-limit protection

LLM agents must not blindly retry Telegram errors. Version 0.4 adds a process-wide safety layer designed for Hermes' worker-thread/event-loop tool execution model.

### FloodWait

Every Telethon client receives a configurable `flood_sleep_threshold` (default 30 seconds). Small waits may therefore be handled by Telethon itself.

When Telegram raises a larger FloodWait, the plugin:

1. extracts the required wait duration;
2. returns an explicit "do not retry earlier" error to Hermes;
3. records a process-wide backoff deadline;
4. rejects new Telegram tool calls until that deadline passes.

This prevents an agent from repeatedly retrying a rate-limited RPC and making the penalty worse.

### Proactive tool pacing

Defaults:

```dotenv
HERMES_TG_USER_MAX_CONCURRENT_TOOLS=3
HERMES_TG_USER_TOOL_MIN_INTERVAL_MS=150
HERMES_TG_USER_FLOOD_SLEEP_THRESHOLD=30
```

This bounds simultaneous short-lived Telethon tool clients and spaces starts slightly. It is deliberately a conservative local guard, **not a claim about fixed official Telegram requests-per-day limits**. Telegram applies dynamic limits and FloodWait remains authoritative.

### Single gateway instance

The long-lived `.h` listener takes an OS file lock derived from the StringSession. Accidentally starting a second `hermes-telegram-user` gateway with the same session on the same machine fails closed rather than creating two listeners and doubling traffic.

The lock does not control unrelated Telegram clients on other machines/processes that do not use this plugin.

## Prompt-injection/data sanitization

Telegram message text, names, captions, folder titles, filenames and button labels are user-controlled data.

The plugin therefore:

- returns structured JSON where practical;
- removes dangerous control/bidi override characters and bounds string sizes;
- keeps legitimate Unicode/emoji joiners;
- sanitizes reply context before injecting it into a Hermes turn;
- tells Hermes explicitly that Telegram history/quoted content is **data, not instructions**.

There is intentionally no brittle keyword blacklist: a normal Telegram message can contain words such as "system" or "ignore" without being destroyed.

## Security model

Telegram-facing model tools are read-only, with exactly one deliberate exception:

- no `tg_send_message`;
- no arbitrary model-controlled reply;
- no reaction tool;
- **one** tool clears the unread badge: `tg_mark_summarized`. It is scoped to a single chat or a single forum thread, always bounded by an explicit message id, refuses to guess a position, and is meant to be called *after* a summary exists — never as a bulk "mark everything read";
- no background watcher/autopilot.

The read acknowledgement is built from explicit `ReadHistoryRequest` / `ReadDiscussionRequest` calls in `core/readstate.py` rather than Telethon's blanket `send_read_acknowledge`, so the peer-kind dispatch is visible; a contract test asserts that this module is the only place in the package that acknowledges anything, and that no tool name offers a bulk variant.

The platform adapter itself must edit the owner's `.h` message to implement the UX and retains the platform contract's host-driven delivery path, but those operations are not exposed as model tools.

## Marking a summary as read

A digest that summarised a chat should also stop your phone from showing it as unread — otherwise you re-read by hand exactly what Hermes already read for you.

The rule is that Hermes marks **after** the summary exists:

1. `tg_get_unread` or `tg_read_folder` with `since_last_digest=true` reads only what is not yet summarised (bounded by the per-chat mark, and it writes nothing);
2. Hermes writes the summary;
3. `tg_mark_summarized` records the position and clears the badge for that same scope.

```text
.h что нового в папке Работа?      # 1 — читает только несаммаризованное
.h суммаризуй ветку Releases      # 2 — саммари по конкретной ветке форума
tg_mark_summarized(chat="Work", topic="Releases", up_to=1234)   # 3
```

Semantics worth knowing:

- `up_to` defaults to this scope's own recorded position; if the scope has no mark yet, the tool **refuses** rather than guessing, because guessing would clear a badge for messages nobody summarised;
- the acknowledgement is sent **before** the local mark moves. If Telegram refuses (FloodWait, permissions, network), nothing moves and the next digest returns the same messages — a repeated summary instead of a silent gap;
- `topic` marks one forum thread without touching the whole-chat position or its sibling threads;
- Telegram counts **three** badges separately — plain unread, unread mentions and unread reactions — so the marking call clears all three for the same scope. Clearing only the first would leave a mention or reaction badge lit on a message no later digest would re-surface, i.e. unfixable from here;
- `acknowledge=false` records the position locally and leaves the badge alone.

`HERMES_TG_USER_SESSION` is equivalent to account access and must be treated as a password.

Local state files are created under a private state directory (`0700` where supported) and alias/transcript files are restricted to the owner (`0600` where supported). Transcripts contain personal chat text in plaintext, so include that directory in your backup/privacy threat model.

## Requirements

- current `NousResearch/hermes-agent` with platform-plugin support;
- Python 3.11+ recommended;
- `telethon>=1.44,<2`;
- Telegram `api_id` and `api_hash` from `my.telegram.org`;
- an authorized Telethon `StringSession`.

## Install

Recommended:

```bash
hermes plugins install AIast0r/hermes-telegram-user --enable
```

The repository is private, so GitHub credentials must be available non-interactively to Hermes (`gh auth login`, `GITHUB_TOKEN`, `GH_TOKEN`, or your git credential helper).

Manual installation:

```bash
mkdir -p ~/.hermes/plugins/platforms
# place/clone the repository as:
# ~/.hermes/plugins/platforms/telegram-user/

hermes plugins enable telegram-user
pip install -r ~/.hermes/plugins/platforms/telegram-user/requirements.txt
```

## Create a StringSession

```bash
cd ~/.hermes/plugins/platforms/telegram-user
export HERMES_TG_USER_API_ID='123456'
export HERMES_TG_USER_API_HASH='...'
python scripts/setup_session.py
```

Store the printed `HERMES_TG_USER_SESSION` securely.

## Environment

Minimal:

```dotenv
HERMES_TG_USER_API_ID=123456
HERMES_TG_USER_API_HASH=...
HERMES_TG_USER_SESSION=...
HERMES_TG_USER_COMMAND=.h
```

Optional:

```dotenv
HERMES_TG_USER_REPLY_DEPTH=3
HERMES_TG_USER_MAX_MEDIA_MB=50
HERMES_TG_USER_FLOOD_SLEEP_THRESHOLD=30
HERMES_TG_USER_MAX_CONCURRENT_TOOLS=3
HERMES_TG_USER_TOOL_MIN_INTERVAL_MS=150
# HERMES_TG_USER_STATE_DIR=/custom/private/path
```

## Hermes platform config

```yaml
gateway:
  platforms:
    telegram_user:
      enabled: true
      extra:
        command: ".h"
        thinking_text: "💭 Думаю…"
        reply_context_depth: 3
        max_media_mb: 50
```

## Enable tools

The toolset is `telegram_user`. Enable it only on Hermes surfaces that should be allowed to inspect your personal Telegram account.

```bash
hermes tools enable telegram_user --platform cli
hermes tools enable telegram_user --platform telegram
```

or configure it with Hermes' normal `platform_toolsets` settings.

## Tests

The suite is offline: it never contacts Telegram and never needs `telethon`.

```bash
python -m pytest tests/ -q
```

- `tests/test_smoke.py` — source-level contract checks: the tool surface matches
  `plugin.yaml` exactly (names, count, version), exactly one module may
  acknowledge a read, and the rate-limit/single-instance wiring is present.
- `tests/test_readstate.py` — the acknowledgement dispatch: which request each
  peer kind gets, and that an unbounded "mark everything read" is refused.
- `tests/test_state_archive.py` — the archive storage and search layer.
- `tests/test_watermarks.py` — mark monotonicity, gap handling, per-thread
  isolation and the explicit assertion semantics.
- `tests/test_tools_offline.py` — tool registration, every handler that can
  refuse bad input without touching Telegram, and the marking contract
  (badge and mark move together; a refused acknowledgement moves neither).
- `tests/test_collections.py` — collection storage and the rules that decide a
  scope: exclusion wins, a merge keeps stored names, resolution is by peer id.

77 tests, all offline. `telethon` is optional: when it is importable the
acknowledgement and selection tests assert against the real request classes and
peer types, and when it is not they assert the documented fallback behaviour
instead of passing vacuously.

`pytest` is not listed in `requirements.txt`; install it separately.

## Current limitations

- One active `.h` request per Telegram chat is still assumed. A second `.h` before the first finishes can replace the pending edit target.
- Telegram rate limits are dynamic; the local limiter reduces risk but cannot guarantee that Telegram never returns FloodWait.
- Transcript caching applies to `tg_transcribe_voice`; Hermes' central automatic STT for a live `.h` voice/reply has its own runtime path.
- Aliases are exact local mappings; the plugin intentionally does not fuzzy-guess a different person.
- `since_last_digest` bounds `tg_get_unread` and `tg_read_folder` at whole-chat scope. Per-thread bounding inside a folder digest is not wired yet: read a specific thread with `tg_read_messages(topic=...)` and mark it with `tg_mark_summarized(topic=...)`.
- `tg_mark_summarized` is the only tool that writes to Telegram, and it is irreversible: once a scope is marked, your unread reminder for it is gone. Marking something you did not actually summarise loses it from your attention — that is the trade the design makes deliberately, in exchange for not re-reading. Use `acknowledge=false` to record the position without touching the badge.
- The archive stores text and metadata, not attachment bytes, and it does not track deletions — a message deleted in Telegram stays in the archive until the chat is re-synced or dropped with `tg_archive_forget`.
- `tg_archive_sync` does not hold a global lock, so two concurrent syncs of the same chat can interleave. They converge, but the reported counts may overlap.
- No external MCP is required and no Hermes core patch is required.
