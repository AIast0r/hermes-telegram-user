# Hermes Telegram User (MTProto)

A **plugin-only** Telegram user-account integration for `NousResearch/hermes-agent`.
No Hermes core patches are required.

Version `0.2.x` targets the current Hermes platform-plugin API: deferred `kind: platform` loading, `provides_tools`, current tool schemas, async tool handlers, and `BasePlatformAdapter.build_source()` routing.

## Behavior

### `.h` in any Telegram chat

1. You send `.h your question` from your own Telegram account.
2. The plugin immediately edits that same message to `💭 Думаю…`.
3. The request enters the normal Hermes gateway/agent loop, including tools and memory.
4. Streaming previews/intermediate edits are suppressed by the adapter.
5. Hermes' final answer replaces the same `💭 Думаю…` message.

**Session scope:** one Hermes session per Telegram chat. Forum topic IDs are metadata only and do not split sessions.

### Read-only MTProto tools

The plugin exposes tools usable from other Hermes sessions, for example a regular private Hermes bot or CLI session:

- `tg_find_chat`
- `tg_list_topics`
- `tg_read_messages`
- `tg_search_messages`

Example request to Hermes:

> Прочитай сегодня чат Dev, ветку Backend и кратко скажи, что обсуждали.

The tools use short-lived Telethon connections rather than the gateway adapter's long-lived client. This is intentional: current Hermes may execute async tools on worker event loops, while a Telethon client must remain on the event loop where it was connected.

## Requirements

- Current `NousResearch/hermes-agent` with platform-plugin support.
- Python 3.11+ recommended.
- `telethon`.
- Telegram `api_id` and `api_hash` from `my.telegram.org`.
- A Telethon `StringSession` for your account.

## Install

### Recommended: Hermes plugin installer

```bash
hermes plugins install AIast0r/hermes-telegram-user --enable
```

This repository is private, so Hermes needs GitHub credentials available non-interactively, for example through `gh auth login`, `GITHUB_TOKEN`, `GH_TOKEN`, or your configured git credential helper.

### Manual install

Place the plugin at:

```bash
~/.hermes/plugins/platforms/telegram-user/
```

Then explicitly enable the third-party platform plugin:

```bash
hermes plugins enable telegram-user
```

Install the dependency in the same Python environment Hermes uses:

```bash
pip install -r ~/.hermes/plugins/platforms/telegram-user/requirements.txt
```

## Create a StringSession

For a manual install:

```bash
cd ~/.hermes/plugins/platforms/telegram-user
export HERMES_TG_USER_API_ID='123456'
export HERMES_TG_USER_API_HASH='...'
python setup_session.py
```

The script will ask Telegram for login confirmation/2FA as needed and print `HERMES_TG_USER_SESSION`.
Treat this value like a password.

## Environment

Put these in the Hermes environment / `~/.hermes/.env`:

```dotenv
HERMES_TG_USER_API_ID=123456
HERMES_TG_USER_API_HASH=...
HERMES_TG_USER_SESSION=...
HERMES_TG_USER_COMMAND=.h
```

## Hermes platform config

With all three required environment variables present, the plugin can seed its platform configuration automatically. Explicit configuration is also supported:

```yaml
gateway:
  platforms:
    telegram_user:
      enabled: true
      extra:
        command: ".h"
        thinking_text: "💭 Думаю…"
```

## Enable the Telegram read tools

The toolset key is `telegram_user`. Enable it only on the Hermes surfaces that should be allowed to read your Telegram history.

Examples:

```bash
hermes tools enable telegram_user --platform cli
hermes tools enable telegram_user --platform telegram
```

Or configure the same toolset through Hermes' normal `platform_toolsets` configuration.

For the cleanest `.h` UX, keep tool-progress/reasoning display disabled for `telegram_user`; the adapter additionally suppresses streaming edits while a `.h` request is pending.

## Security model

- The listener admits only **outgoing** messages from the logged-in user account.
- It only activates on an explicit `.h` prefix.
- Telegram history tools are read-only in this version.
- There is intentionally **no generic send-message model tool** for the user account.
- `HERMES_TG_USER_SESSION` grants account access and must never be committed.
- The Telegram read toolset must be explicitly enabled on each Hermes platform where you want it available.

## Current limitations

- One active `.h` request per Telegram chat is assumed. A second `.h` in the same chat before the first completes replaces the pending edit target.
- Long-answer chunking/rich-message rendering is intentionally deferred.
- Attachments, voice notes, photos, and replied-message context through `.h` are not implemented yet.
- Read tools do not currently download Telegram media.
- Telegram's Bot API Rich Messages are not used because this integration is MTProto/user-account based.
