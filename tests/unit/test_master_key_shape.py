"""``Settings.master_key`` accepts a key and refuses a passphrase.

The key is not stretched — ``EnvelopeCipher`` runs it through HKDF, which derives rather than adds
work — so a passphrase here puts the whole token table one offline dictionary attack away, and
nothing downstream would ever notice. This validator is the only place that can notice.
"""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID = base64.urlsafe_b64encode(b"7" * 32).decode()


def test_a_generated_key_is_accepted() -> None:
    assert Settings(master_key=VALID).master_key == VALID


def test_an_unset_key_is_accepted() -> None:
    """A worker/bot deployment that never touches account tokens configures nothing; the API
    refuses to start on an empty key separately, in ``app.main``."""
    assert Settings(master_key="  ").master_key == ""


@pytest.mark.parametrize(
    "passphrase",
    [
        # 43 characters plus the padding `=`. This is the one the length check could not see:
        # `urlsafe_b64decode` skips the characters outside its alphabet rather than refusing them,
        # so this decoded to exactly 32 bytes and passed for a key.
        "correct-horse-battery-staple-is-not-a-key!!=",
        "a" * 43 + "=",
        "hunter2",
        # 32 bytes, right alphabet, but not a canonical encoding of them — the trailing bits of
        # the last character are not zero, so this is a second spelling of one key.
        VALID[:42] + "B" + VALID[43:],
    ],
)
def test_a_passphrase_is_refused(passphrase: str) -> None:
    with pytest.raises(ValidationError):
        Settings(master_key=passphrase)
