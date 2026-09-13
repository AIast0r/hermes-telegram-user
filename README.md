# Hermes Telegram User (MTProto)

A **self-contained Hermes platform plugin** for a personal Telegram account over MTProto/Telethon.
No Hermes core patches and no external Telegram MCP server are required.

Version `0.3.x` keeps the original `.h` UX, while adding the read-side features useful for a real personal Telegram assistant: reply context, media/voice handoff, unread/folder digests, global search, historical media retrieval, contacts and group participants.

## Core UX

Send this from **your own Telegram account** in any chat:

```text
.h что здесь произошло?
```

The plugin:

1. catches only the owner's outgoing `.h ...` message;
2. edits that same message to `💭 Думаю…`;
3. sends the request through the normal Hermes agent loop, memory and tools;
4. hides streaming/tool-progress edits;
5. replaces the same Telegram message with the final Hermes answer.

One Telegram chat = one Hermes session. Forum topic IDs are kept as context metadata but do not split Hermes memory into separate sessions.

## Reply context

Reply to any Telegram message and write, for example:

```text
.h он прав?
.h объясни
.h придумай ответ
```

The plugin automatically passes Hermes:

- replied message id/text;
- author id/name;
- media metadata;
- a short reply chain (default depth `3`, configurable from `1` to `5`).

A forum topic root is not incorrectly treated as an explicit quoted reply.

## Photos, files and voice notes

If `.h` is attached to media or is sent as a reply to a Telegram photo/file/voice note, the plugin downloads the relevant attachment into Hermes' normal media cache and fills `MessageEvent.media_urls/media_types`.

Voice notes are emitted as `MessageType.VOICE`, so **transcription is handled by Hermes itself**, not by this plugin. This keeps the plugin independent of any specific STT provider.

For Groq Whisper, configure Hermes normally:

```yaml
# ~/.hermes/config.yaml
stt:
  enabled: true
  provider: groq
```

and set:

```dotenv
GROQ_API_KEY=...
```

You can later switch Hermes to local Whisper/OpenAI/Mistral/etc. without changing this plugin.

## Read-only Telegram tools

The model-facing Telegram toolset intentionally contains **no send/reply/reaction/admin/autopilot tool**.

| Tool | Purpose |
| --- | --- |
| `tg_find_chat` | Find dialogs by title/username and see unread counters |
| `tg_list_topics` | Forum topics + unread counts |
| `tg_read_messages` | Read history/time windows without read receipts |
| `tg_get_message_context` | One message + nearby context + its reply target |
| `tg_search_messages` | Search inside one chat/topic |
| `tg_search_global` | Search across the whole Telegram account |
| `tg_list_folders` | List Telegram folders and their rules |
| `tg_get_unread` | Read unread messages globally or in one folder |
| `tg_read_folder` | Read a time window across a Telegram folder |
| `tg_search_media` | Find old photos/voice/video/audio/GIF/documents |
| `tg_download_media` | Cache one historical attachment for Hermes analysis |
| `tg_contacts` | Search contacts without exposing phone numbers |
| `tg_participants` | Search/list members of a group/channel |

### Examples

```text
.h суммируй всё непрочитанное в папке Работа
.h что важного я пропустил за ночь?
.h найди где мне недавно кидали ссылку на Qwen 27B
.h найди войс Саши со вчера и скажи о чём он
.h что сегодня писал Андрей в Backend?
```

Hermes decides which read tools to call and performs the summary/reasoning itself.

## Unread semantics

Read tools **do not call** Telegram's `send_read_acknowledge`/mark-read methods.
Fetching history therefore does not intentionally move your Telegram read pointer.

`tg_get_unread` uses Telegram dialog unread state and `read_inbox_max_id`, including:

- unread message counts;
- unread mentions;
- manual **Mark as unread** state.

Folder membership evaluates explicit include/exclude/pinned peers and Telegram's category rules (`contacts`, `non_contacts`, `groups`, `broadcasts`, `bots`, `exclude_muted`, `exclude_read`, `exclude_archived`).

## Media search vs direct media

There are two different workflows:

- **direct/reply media**: reply with `.h ...` to a visible voice/photo/file; it is handed directly to the current Hermes turn;
- **historical media**: Hermes first uses `tg_search_media`, then `tg_download_media`, then its normal transcription/vision/file tools.

The default attachment cache limit is 50 MB and can be changed with `HERMES_TG_USER_MAX_MEDIA_MB`.

## Installation

### Hermes plugin installer

```bash
hermes plugins install AIast0r/hermes-telegram-user --enable
```

For a private repository Hermes needs non-interactive GitHub credentials (`gh auth login`, `GITHUB_TOKEN`, `GH_TOKEN`, or a configured git credential helper).

### Manual

Place the repository at:

```bash
~/.hermes/plugins/platforms/telegram-user/
```

Then:

```bash
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

The printed `HERMES_TG_USER_SESSION` is effectively an account credential. Store it like a password.

## Environment

```dotenv
HERMES_TG_USER_API_ID=123456
HERMES_TG_USER_API_HASH=...
HERMES_TG_USER_SESSION=...
HERMES_TG_USER_COMMAND=.h
HERMES_TG_USER_REPLY_DEPTH=3
HERMES_TG_USER_MAX_MEDIA_MB=50
```

## Hermes platform config

The required environment variables are enough for automatic enablement. Explicit config is also supported:

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

## Enable the toolset

The toolset key is `telegram_user`. Enable it only on Hermes surfaces that should be allowed to inspect your Telegram account.

```bash
hermes tools enable telegram_user --platform cli
hermes tools enable telegram_user --platform telegram_user
```

Use Hermes' normal `platform_toolsets` configuration if you prefer declarative setup.

## Security model

- Only explicit **outgoing** `.h` messages are admitted by the platform listener.
- Model-facing Telegram tools are read-only with respect to Telegram state; media download only writes into Hermes' local cache.
- There is intentionally no `tg_send_message`, arbitrary reply, reaction or watcher/autopilot tool.
- Message bodies, captions and names returned by Telegram are untrusted content; tool descriptions and the platform prompt explicitly tell Hermes to treat them as data, not instructions.
- Contacts/participants intentionally omit phone numbers.
- `HERMES_TG_USER_SESSION` grants Telegram account access and must never be committed.

## Architecture

```text
Telegram personal account (MTProto)
        │
        ├── outgoing .h ──► TelegramUserAdapter ──► Hermes agent
        │                         │                    ├─ memory
        │                         │                    ├─ web/other tools
        │                         │                    └─ native STT/vision
        │                         └── edit same .h message with final answer
        │
        └── read-only tool calls ─► short-lived loop-local Telethon clients
                                   ├─ history/search
                                   ├─ unread/folders
                                   ├─ historical media
                                   └─ contacts/participants
```

The gateway adapter owns one long-lived Telethon client. Hermes may execute async tool handlers on worker event loops, so account-read tools intentionally use short-lived loop-local clients and disconnect them after each call.

## Design references

The plugin remains independent and does not import/runtime-depend on these projects, but its read-side design was informed by the public Telegram/Telethon work in:

- `chigwell/telegram-mcp` — broad Telegram tool coverage and media/message modelling;
- `stufently/telegram-ai-cli-mcp` — unread/read-pointer safety and Telegram folder semantics;
- `maximskorohod/hermes-telegram-userbot` — useful proof that a Hermes + personal-Telegram workflow is practical, although that repository is mostly setup documentation.

## Current limitations

- One active `.h` request per Telegram chat is still assumed. Sending another `.h` in the same chat before the first completes can replace the pending edit target.
- Extremely large histories are intentionally capped per tool call to keep Telegram requests and LLM context bounded.
- Large final Hermes answers still rely on Hermes' normal message chunking behavior; the primary UX remains editing the original `.h` message.
- Telegram itself may rate-limit unusual/high-volume MTProto automation. This plugin intentionally avoids bulk write operations.
