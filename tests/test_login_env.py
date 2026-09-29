"""Writing the session into the Hermes .env without disturbing anything else.

That file holds every other secret Hermes has, so the write is asserted at the
byte level: only the one line may change, and the line endings of the others have
to survive. A re-serialized dotenv would quietly rewrite the whole file, which is
exactly the failure this pins against.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _plugin_support import isolated_state, plugin_module  # noqa: E402

SESSION = "HERMES_TG_USER_SESSION"

# CRLF on purpose: the real file uses them on Windows, and a naive splitlines()
# round-trip would convert every one of them to LF.
BODY = "\r\n".join(
    [
        "# Hermes secrets",
        "OPENAI_API_KEY=sk-abc",
        "HERMES_TG_USER_API_ID=123",
        f"{SESSION}=test",
        "TELEGRAM_BOT_TOKEN=456:abc",
        "",
    ]
)


def _login():
    return plugin_module("core.login")


def _write_text(path: Path, text: str) -> None:
    """Write exactly these bytes — the default translation would distort the fixture."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def test_replacing_the_session_leaves_every_other_byte_alone():
    with isolated_state() as root:
        env = root / ".env"
        _write_text(env, BODY)
        before = env.read_bytes()

        assert _login().write_env_value(env, SESSION, "REAL") is True

        after = env.read_bytes()
        assert after == before.replace(
            b"HERMES_TG_USER_SESSION=test", b"HERMES_TG_USER_SESSION=REAL"
        )
        assert b"\r\n" in after, "the other lines kept their CRLF endings"
        assert b"OPENAI_API_KEY=sk-abc" in after


def test_appending_adds_exactly_one_line():
    with isolated_state() as root:
        env = root / ".env"
        env.write_text("A=1", encoding="utf-8")  # no trailing newline

        assert _login().write_env_value(env, SESSION, "S") is True
        assert env.read_text(encoding="utf-8") == f"A=1\n{SESSION}=S\n"


def test_a_missing_file_is_created():
    with isolated_state() as root:
        env = root / "nested" / ".env"
        assert _login().write_env_value(env, SESSION, "S") is True
        assert env.read_text(encoding="utf-8") == f"{SESSION}=S\n"


def test_export_and_quoted_forms_are_read_and_replaced():
    with isolated_state() as root:
        login = _login()
        env = root / ".env"
        env.write_text(
            f'export HERMES_TG_USER_API_ID="123"\n{SESSION}="old"\n', encoding="utf-8"
        )

        body = env.read_text(encoding="utf-8")
        assert login.read_env_value(body, "HERMES_TG_USER_API_ID") == "123"
        assert login.read_env_value(body, SESSION) == "old"

        assert login.write_env_value(env, SESSION, "new") is True
        text = env.read_text(encoding="utf-8")
        assert f"{SESSION}=new" in text
        assert "old" not in text
        assert 'export HERMES_TG_USER_API_ID="123"' in text, "the export line is untouched"


def test_reading_a_key_that_is_absent_is_empty_not_an_error():
    assert _login().read_env_value("A=1\n", SESSION) == ""


def test_hermes_home_override_names_the_env_file():
    login = _login()
    previous = os.environ.get("HERMES_HOME")
    home = Path(tempfile.mkdtemp(prefix="tgu-hermes-home-"))
    try:
        os.environ["HERMES_HOME"] = str(home)
        assert login.hermes_env_path() == home / ".env"
    finally:
        if previous is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous
