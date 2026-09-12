from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_required_files_exist():
    for name in ("plugin.yaml", "adapter.py", "tools.py", "shared.py", "setup_session.py", "README.md"):
        assert (ROOT / name).exists()


def test_no_core_patch_files():
    # Project must remain a standalone Hermes plugin.
    assert not (ROOT / "gateway").exists()
    assert not (ROOT / "agent").exists()
