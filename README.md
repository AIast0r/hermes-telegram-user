# Hermes Telegram User (MTProto)

A **plugin-only** Telegram user-account integration for `NousResearch/hermes-agent`.
No Hermes core patches are required.

## MVP behavior

### `.h` in any Telegram chat

1. You send `.h your question` from your own Telegram account.
2. The plugin immediately edits that same message to `💭 Думаю…`.
3. The request enters the normal Hermes gateway/agent loop, including tools and memory.
4. Streaming previews/intermediate edits are suppressed by the adapter.
5. Hermes' final answer replaces the same `💭 Думаю…` message.

**Session scope:** one Hermes session per Telegram chat. Forum topic IDs are metadata only and do not split sessions.

### Read-only MTProto tools

The plugin also exposes tools usable from other Hermes sessions (for example your regular private Hermes bot):

- `tg_find_chat`
- `tg_list_topics`
- `tg_read_messages`
- `tg_search_messages`

Example request to Hermes:

> Прочитай сегодня чат Dev, ветку Backend и кратко скажи, что обсуждали.

Hermes can resolve the chat/topic and read only the requested history through your Telegram account.

## Requirements

- Current Hermes plugin API with `kind: platform` support.
- Python 3.11+ recommended.
- `telethon`.
- Telegram `api_id` and `api_hash` from `my.telegram.org`.
- A Telethon `StringSession` for your account.

## Install

Copy this repository directory into:

```bash
~/.hermes/plugins/telegram-user/
```

Install dependency in the same Python environment Hermes uses:

```bash
pip install -r ~/.hermes/plugins/telegram-user/requirements.txt
```

## Create a StringSession

```bash
cd ~/.hermes/plugins/telegram-user
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

## Hermes config

The platform can be auto-enabled from the three required env vars. If you prefer explicit config:

```yaml
gateway:
  platforms:
    telegram_user:
      enabled: true
      extra:
        command: ".h"
        thinking_text: "💭 Думаю…"
```

Enable the read-only MTProto toolset where you want Hermes to use it, e.g. your normal Telegram bot / CLI, using the normal Hermes toolset configuration (`telegram_user`).

For the cleanest UX, keep tool-progress/reasoning display disabled for `telegram_user`; the adapter additionally suppresses streaming edits while a `.h` request is pending.

## Security model

- The listener admits only **outgoing** messages from the logged-in user account.
- It only activates on an explicit `.h` prefix.
- Telegram history tools are read-only in this MVP.
- There is intentionally **no generic send-message model tool** for the user account.
- `HERMES_TG_USER_SESSION` grants account access and must never be committed.

## Current limitations

- One active `.h` request per Telegram chat is assumed in MVP. A second `.h` in the same chat before the first completes replaces the pending edit target.
- Long-answer chunking/rich-message rendering is intentionally deferred.
- Attachments/voice/photo inputs through `.h` are not implemented yet.
- Telegram's newer Bot API Rich Messages are not used because this integration is MTProto/user-account based.
