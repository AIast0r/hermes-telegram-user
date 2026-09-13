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
        "setup_session.py",
        "README.md",
    ):
        assert (ROOT / name).exists()


def test_no_core_patch_files():
    # Project must remain a standalone Hermes plugin.
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


def test_read_only_tool_surface_contains_selected_features():
    source = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    for tool in (
        "tg_get_message_context",
        "tg_search_global",
        "tg_list_folders",
        "tg_get_unread",
        "tg_read_folder",
        "tg_search_media",
        "tg_download_media",
        "tg_contacts",
        "tg_participants",
    ):
        assert f"- {tool}" in source


def test_no_model_facing_write_tools_or_read_receipts():
    source = (ROOT / "tools.py").read_text(encoding="utf-8")
    for forbidden in (
        '"tg_send_message"',
        '"tg_reply_message"',
        '"tg_react"',
        "send_read_acknowledge(",
        "mark_read(",
    ):
        assert forbidden not in source
