from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_required_files_exist():
    for name in (
        "plugin.yaml",
        "adapter.py",
        "tools.py",
        "shared.py",
        "telegram_helpers.py",
        "telegram_folders.py",
        "telegram_media.py",
        "telegram_sanitize.py",
        "telegram_limits.py",
        "telegram_state.py",
        "telegram_aliases.py",
        "telegram_transcripts.py",
        "setup_session.py",
        "README.md",
    ):
        assert (ROOT / name).exists()


def test_no_core_patch_files():
    assert not (ROOT / "gateway").exists()
    assert not (ROOT / "agent").exists()


def test_python_sources_compile():
    for path in ROOT.glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_current_hermes_tool_contract_is_present():
    source = (ROOT / "tools.py").read_text(encoding="utf-8")
    assert 'schema={"name": name, "description": description, "parameters": parameters}' in source
    assert "is_async=True" in source
    assert "check_fn=_check_requirements" in source
    assert "return json.dumps(" in source


def test_adapter_uses_current_hermes_event_surface():
    source = (ROOT / "adapter.py").read_text(encoding="utf-8")
    assert "self.build_source(" in source
    assert "SessionSource(" not in source
    assert "reply_to_text=reply_ctx" in source
    assert "media_urls=media_urls" in source
    assert "media_types=media_types" in source
    assert "MessageType.VOICE" in source
    assert "utf16_len" in source
    assert "telegram_error_message" in source
    assert "sanitize_text" in source


def test_v040_tool_surface_has_17_selected_tools():
    manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert "version: 0.4.0" in manifest
    tools = [line.strip()[2:] for line in manifest.splitlines() if line.strip().startswith("- tg_")]
    assert len(tools) == 17
    assert len(set(tools)) == 17
    for tool in (
        "tg_get_message_context",
        "tg_search_global",
        "tg_list_folders",
        "tg_get_unread",
        "tg_read_folder",
        "tg_search_media",
        "tg_download_media",
        "tg_transcribe_voice",
        "tg_contacts",
        "tg_participants",
        "tg_set_alias",
        "tg_list_aliases",
        "tg_delete_alias",
    ):
        assert tool in tools


def test_no_model_facing_telegram_write_tools_or_read_receipts():
    tools = (ROOT / "tools.py").read_text(encoding="utf-8")
    for forbidden in (
        '"tg_send_message"',
        '"tg_reply_message"',
        '"tg_react"',
        '"tg_mark_read"',
    ):
        assert forbidden not in tools
    all_source = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.glob("*.py"))
    assert "send_read_acknowledge(" not in all_source
    assert "mark_read(" not in all_source


def test_floodwait_and_single_instance_guards_are_wired():
    limits = (ROOT / "telegram_limits.py").read_text(encoding="utf-8")
    shared = (ROOT / "shared.py").read_text(encoding="utf-8")
    assert "TelegramRateLimited" in limits
    assert "note_flood_wait" in limits
    assert "threading.BoundedSemaphore" in limits
    assert "acquire_gateway_session_lock" in limits
    assert "flood_sleep_threshold=configured_flood_sleep_threshold()" in shared
    assert "async with tool_gate()" in shared


def test_voice_cache_uses_native_hermes_stt():
    tools = (ROOT / "tools.py").read_text(encoding="utf-8")
    transcripts = (ROOT / "telegram_transcripts.py").read_text(encoding="utf-8")
    assert "from tools.transcription_tools import transcribe_audio" in tools
    assert "tg_transcribe_voice" in tools
    assert "CREATE TABLE IF NOT EXISTS transcripts" in transcripts
    assert "PRIMARY KEY (chat_id, message_id)" in transcripts


def test_sanitizer_strips_bidi_override_but_keeps_emoji_joiners():
    namespace = {}
    source = (ROOT / "telegram_sanitize.py").read_text(encoding="utf-8")
    exec(compile(source, "telegram_sanitize.py", "exec"), namespace)
    sanitize_text = namespace["sanitize_text"]
    assert sanitize_text("a\u202eb") == "ab"
    assert sanitize_text("👨‍💻") == "👨‍💻"
