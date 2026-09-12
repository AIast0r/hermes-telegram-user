from __future__ import annotations

import logging
import os
from typing import Any

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType

from .shared import disconnect_client, entity_label, get_client

logger = logging.getLogger(__name__)


class TelegramUserAdapter(BasePlatformAdapter):
    """MTProto user-account adapter.

    Only explicit outgoing `.h ...` messages are admitted into Hermes. Session identity is
    platform + chat_id; forum topic IDs are metadata only, so all topics in one Telegram chat
    share one Hermes session.
    """

    MAX_MESSAGE_LENGTH = 4096
    SUPPORTS_MESSAGE_EDITING = True

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("telegram_user"))
        extra = config.extra or {}
        self.command = (os.getenv("HERMES_TG_USER_COMMAND") or extra.get("command") or ".h").strip()
        self.thinking_text = str(extra.get("thinking_text") or "💭 Думаю…")
        self._client = None
        self._handler = None
        self._me = None
        self._pending: dict[str, dict[str, Any]] = {}

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        try:
            from telethon import events

            self._client = await get_client()
            self._me = await self._client.get_me()

            async def on_outgoing(event):
                await self._on_outgoing(event)

            self._handler = on_outgoing
            self._client.add_event_handler(on_outgoing, events.NewMessage(outgoing=True))
            self._mark_connected()
            logger.info("Telegram user plugin connected as %s", entity_label(self._me))
            return True
        except Exception:
            logger.exception("Telegram user plugin failed to connect")
            return False

    async def disconnect(self) -> None:
        if self._client is not None and self._handler is not None:
            try:
                self._client.remove_event_handler(self._handler)
            except Exception:
                pass
        await disconnect_client()
        self._client = None
        self._handler = None
        self._mark_disconnected()

    async def _on_outgoing(self, event) -> None:
        text = (getattr(event.message, "message", None) or "").strip()
        prefix = self.command
        if not text.startswith(prefix):
            return
        # Avoid matching `.hello` when command is `.h`.
        if len(text) > len(prefix) and not text[len(prefix)].isspace():
            return
        prompt = text[len(prefix):].strip()
        if not prompt:
            return

        chat_id = str(event.chat_id)
        message_id = int(event.message.id)
        try:
            chat = await event.get_chat()
        except Exception:
            chat = None

        topic_id = None
        reply = getattr(event.message, "reply_to", None)
        if reply is not None:
            topic_id = getattr(reply, "reply_to_top_id", None) or getattr(reply, "reply_to_msg_id", None)

        try:
            input_chat = await event.get_input_chat()
        except Exception:
            input_chat = int(chat_id)
        self._pending[chat_id] = {"message_id": message_id, "peer": input_chat}
        try:
            await event.message.edit(self.thinking_text)
        except Exception:
            logger.exception("Failed to edit .h message to thinking state")

        chat_type = "dm"
        if getattr(chat, "broadcast", False):
            chat_type = "channel"
        elif getattr(chat, "megagroup", False) or getattr(chat, "gigagroup", False):
            chat_type = "group"
        elif getattr(chat, "title", None):
            chat_type = "group"

        # Use Hermes' platform-aware source builder rather than constructing
        # SessionSource directly. This preserves profile/multiplex routing state.
        source = self.build_source(
            chat_id=chat_id,
            chat_name=entity_label(chat) if chat is not None else chat_id,
            chat_type=chat_type,
            user_id=str(getattr(self._me, "id", "")),
            user_name=entity_label(self._me),
            # Deliberately omit thread_id: one Hermes session per Telegram chat,
            # not one session per forum topic.
            message_id=str(message_id),
        )
        incoming = MessageEvent(
            text=prompt,
            message_type=MessageType.TEXT,
            user_id=source.user_id,
            user_name=source.user_name,
            source=source,
            raw_message=event.message,
            message_id=str(message_id),
            metadata={
                "telegram_user_command": True,
                "telegram_topic_id": str(topic_id) if topic_id else None,
                "telegram_original_chat_id": chat_id,
            },
            # `.h` is conversational; don't let text accidentally become a gateway slash control.
            allow_gateway_control=False,
        )
        try:
            await self.handle_message(incoming)
        except Exception as exc:
            logger.exception("Hermes failed to handle .h request")
            try:
                await self._edit_mtproto(chat_id, message_id, f"Ошибка Hermes: {exc}")
            except Exception:
                pass
            self._pending.pop(chat_id, None)

    async def _edit_mtproto(self, chat_id: str, message_id: int, content: str) -> SendResult:
        try:
            pending = self._pending.get(str(chat_id)) or {}
            peer = pending.get("peer", int(chat_id))
            msg = await self._client.edit_message(peer, int(message_id), content)
            return SendResult(success=True, message_id=str(getattr(msg, "id", message_id)))
        except Exception as exc:
            logger.exception("Telegram MTProto edit failed")
            return SendResult(success=False, error=str(exc))

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        """For an active `.h` turn, keep `Думаю…` until the final delivery.

        Gateway streaming first-send gets a successful synthetic message id pointing at the
        original `.h` message. Interim previews therefore stay invisible. A direct/non-streaming
        final carries metadata.notify=True and edits immediately.
        """
        cid = str(chat_id)
        pending = self._pending.get(cid)
        pending_id = int(pending["message_id"]) if pending else None
        meta = metadata or {}
        if pending_id is not None:
            if meta.get("notify") is True:
                result = await self._edit_mtproto(cid, pending_id, str(content))
                if result.success:
                    self._pending.pop(cid, None)
                return result
            return SendResult(success=True, message_id=str(pending_id))

        # Host-driven/proactive send path; not used by `.h`, but keeps adapter contract valid.
        try:
            msg = await self._client.send_message(int(cid), str(content))
            return SendResult(success=True, message_id=str(msg.id))
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def edit_message(self, chat_id, message_id, content, finalize=False, metadata=None):
        cid = str(chat_id)
        pending = self._pending.get(cid)
        pending_id = int(pending["message_id"]) if pending else None
        target = pending_id if pending_id is not None else int(message_id)
        if pending_id is not None and not finalize:
            # Suppress streaming previews/tool-progress edits; user keeps seeing "💭 Думаю…".
            return SendResult(success=True, message_id=str(target))
        result = await self._edit_mtproto(cid, target, str(content))
        if finalize and result.success:
            self._pending.pop(cid, None)
        return result

    async def send_typing(self, chat_id, metadata=None) -> None:
        # The visible state is the edited "💭 Думаю…" message, so no chat-action spam is needed.
        return None

    async def get_chat_info(self, chat_id):
        try:
            entity = await self._client.get_entity(int(str(chat_id)))
            return {
                "name": entity_label(entity),
                "type": "group" if getattr(entity, "title", None) else "dm",
            }
        except Exception:
            return {"name": str(chat_id), "type": "dm"}


def check_requirements() -> bool:
    try:
        import telethon  # noqa: F401
    except ImportError:
        return False
    return all(
        (os.getenv(k) or "").strip()
        for k in (
            "HERMES_TG_USER_API_ID",
            "HERMES_TG_USER_API_HASH",
            "HERMES_TG_USER_SESSION",
        )
    )


def validate_config(config) -> bool:
    return check_requirements()


def _env_enablement() -> dict | None:
    if not check_requirements():
        return None
    return {"command": (os.getenv("HERMES_TG_USER_COMMAND") or ".h").strip()}


def register(ctx):
    ctx.register_platform(
        name="telegram_user",
        label="Telegram User (MTProto)",
        adapter_factory=lambda cfg: TelegramUserAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=[
            "HERMES_TG_USER_API_ID",
            "HERMES_TG_USER_API_HASH",
            "HERMES_TG_USER_SESSION",
        ],
        install_hint="pip install telethon",
        env_enablement_fn=_env_enablement,
        max_message_length=4096,
        platform_hint=(
            "You are responding to an explicit .h command issued by the owner from their Telegram user account. "
            "The current Hermes session is scoped to the whole Telegram chat; forum topics do not create separate sessions. "
            "The event metadata may contain telegram_topic_id for situational context. Keep normal chat answers concise unless asked otherwise."
        ),
        emoji="🟦",
        pii_safe=False,
    )
