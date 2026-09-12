from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_required_files_exist():
    for name in ("plugin.yaml", "adapter.py", "tools.py", "shared.py", "setup_session.py", "README.md"):
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


def test_adapter_uses_hermes_source_builder():
    source = (ROOT / "adapter.py").read_text(encoding="utf-8")
    assert "self.build_source(" in source
    assert "SessionSource(" not in source
