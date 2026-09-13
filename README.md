# Hermes Telegram User (MTProto)

A **self-contained Hermes platform plugin** for controlling Hermes from your own Telegram user account over MTProto/Telethon.

No external MCP server is required. The plugin owns its Telegram bridge, read tools, folder/unread logic, media retrieval, voice transcript cache, aliases, sanitization, and Telegram rate-limit protection.

Current plugin version: **0.4.0**.

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

Version 0.4.0 exposes **17 tools** under the `telegram_user` toolset.

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

These operations do **not** call Telegram read-acknowledgement APIs.

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

Telegram-facing model tools remain read-only:

- no `tg_send_message`;
- no arbitrary model-controlled reply;
- no reaction tool;
- no mark-read/read-ack tool;
- no background watcher/autopilot.

The platform adapter itself must edit the owner's `.h` message to implement the UX and retains the platform contract's host-driven delivery path, but those operations are not exposed as model tools.

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
python setup_session.py
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

## Current limitations

- One active `.h` request per Telegram chat is still assumed. A second `.h` before the first finishes can replace the pending edit target.
- Telegram rate limits are dynamic; the local limiter reduces risk but cannot guarantee that Telegram never returns FloodWait.
- Transcript caching applies to `tg_transcribe_voice`; Hermes' central automatic STT for a live `.h` voice/reply has its own runtime path.
- Aliases are exact local mappings; the plugin intentionally does not fuzzy-guess a different person.
- No external MCP is required and no Hermes core patch is required.
