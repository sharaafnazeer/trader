"""Tests for loading credentials from an environment file.

The precedence rule is the load-bearing one: an already-exported variable must win over
the file. A file quietly overriding an explicit export would make a stale `.env` impossible
to debug — you would fix the wrong thing for an hour.
"""

from __future__ import annotations

import pytest

from trader.config import DEFAULT_ENV_FILE, load_env_file, parse_env_file


def _write(tmp_path, body: str, name: str = ".env") -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


# --- Parsing --------------------------------------------------------------------


def test_simple_key_value_pairs_are_parsed() -> None:
    assert parse_env_file("A=1\nB=two\n") == {"A": "1", "B": "two"}


def test_blank_lines_and_comments_are_skipped() -> None:
    text = "\n# a comment\n\nA=1\n   # indented comment\nB=2\n"

    assert parse_env_file(text) == {"A": "1", "B": "2"}


def test_a_leading_export_is_tolerated() -> None:
    # The same file should be usable with `source` from a shell.
    assert parse_env_file("export OPENAI_API_KEY=sk-test\n") == {"OPENAI_API_KEY": "sk-test"}


@pytest.mark.parametrize("quote", ["'", '"'])
def test_surrounding_quotes_are_stripped(quote: str) -> None:
    assert parse_env_file(f"A={quote}hello world{quote}\n") == {"A": "hello world"}


def test_surrounding_whitespace_is_trimmed() -> None:
    assert parse_env_file("  A  =  value  \n") == {"A": "value"}


def test_a_value_containing_equals_is_kept_whole() -> None:
    # Base64-ish secrets routinely contain '='.
    assert parse_env_file("A=abc=def==\n") == {"A": "abc=def=="}


def test_a_hash_inside_a_value_is_not_treated_as_a_comment() -> None:
    # Truncating a credential at a '#' would cause a baffling auth failure, so inline
    # comments are deliberately not supported for unquoted values.
    assert parse_env_file("TOKEN=abc#def\n") == {"TOKEN": "abc#def"}


def test_lines_without_an_equals_sign_are_ignored() -> None:
    assert parse_env_file("nonsense\nA=1\n") == {"A": "1"}


def test_an_empty_key_is_ignored() -> None:
    assert parse_env_file("=orphan\nA=1\n") == {"A": "1"}


def test_an_empty_value_is_kept() -> None:
    assert parse_env_file("A=\n") == {"A": ""}


def test_a_later_line_wins_within_the_file() -> None:
    assert parse_env_file("A=1\nA=2\n") == {"A": "2"}


def test_an_empty_file_parses_to_nothing() -> None:
    assert parse_env_file("") == {}


# --- Loading --------------------------------------------------------------------


def test_missing_variables_are_filled_from_the_file(tmp_path) -> None:
    environ: dict[str, str] = {}
    path = _write(tmp_path, "OPENAI_API_KEY=sk-from-file\n")

    loaded = load_env_file(path, environ=environ)

    assert loaded == ("OPENAI_API_KEY",)
    assert environ["OPENAI_API_KEY"] == "sk-from-file"


def test_an_already_exported_variable_wins_over_the_file(tmp_path) -> None:
    environ = {"OPENAI_API_KEY": "sk-from-shell"}
    path = _write(tmp_path, "OPENAI_API_KEY=sk-from-file\n")

    loaded = load_env_file(path, environ=environ)

    assert environ["OPENAI_API_KEY"] == "sk-from-shell"
    assert loaded == ()


def test_an_empty_existing_value_is_treated_as_unset(tmp_path) -> None:
    # `export FOO=` leaves a variable present but useless; the file should fill it.
    environ = {"OPENAI_API_KEY": ""}
    path = _write(tmp_path, "OPENAI_API_KEY=sk-from-file\n")

    load_env_file(path, environ=environ)

    assert environ["OPENAI_API_KEY"] == "sk-from-file"


def test_only_the_gaps_are_filled(tmp_path) -> None:
    environ = {"A": "shell"}
    path = _write(tmp_path, "A=file\nB=file\n")

    loaded = load_env_file(path, environ=environ)

    assert environ == {"A": "shell", "B": "file"}
    assert loaded == ("B",)


def test_a_missing_file_is_a_no_op(tmp_path) -> None:
    environ: dict[str, str] = {}

    loaded = load_env_file(str(tmp_path / "does-not-exist"), environ=environ)

    assert loaded == ()
    assert environ == {}


def test_a_directory_in_place_of_the_file_is_a_no_op(tmp_path) -> None:
    environ: dict[str, str] = {}
    (tmp_path / "adir").mkdir()

    assert load_env_file(str(tmp_path / "adir"), environ=environ) == ()
    assert environ == {}


def test_the_default_path_is_dot_env() -> None:
    assert DEFAULT_ENV_FILE == ".env"


def test_the_returned_names_do_not_include_the_values(tmp_path) -> None:
    # The CLI prints what it loaded; that output must never carry a secret.
    environ: dict[str, str] = {}
    path = _write(tmp_path, "OPENAI_API_KEY=sk-super-secret\n")

    loaded = load_env_file(path, environ=environ)

    assert "sk-super-secret" not in "".join(loaded)
