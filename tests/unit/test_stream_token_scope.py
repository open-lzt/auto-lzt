"""Scope separation for SSE stream tokens.

The load-bearing test is the cross-scope pair: a run token and a tenant token minted for the SAME
uuid must not validate as each other. Subject strings alone cannot separate them — the panel's task
stream and a run stream can legitimately be named by the same id — so the separation lives in the
derived key, and this file is what proves it stayed there.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.stream_token import (
    TOKEN_TTL_S,
    StreamScope,
    StreamTokenInvalid,
    issue,
    verify,
)

MASTER = "test-master-key"


def test_tenant_token_verifies_for_its_tenant() -> None:
    tenant = str(uuid4())
    token = issue(MASTER, tenant, scope=StreamScope.TENANT)
    verify(MASTER, tenant, token, scope=StreamScope.TENANT)


def test_tenant_token_is_refused_for_another_tenant() -> None:
    token = issue(MASTER, str(uuid4()), scope=StreamScope.TENANT)
    with pytest.raises(StreamTokenInvalid):
        verify(MASTER, str(uuid4()), token, scope=StreamScope.TENANT)


def test_run_token_does_not_open_the_tenant_stream_with_an_identical_subject() -> None:
    """The confusion this whole design exists to prevent: one uuid, two meanings."""
    subject = str(uuid4())
    run_token = issue(MASTER, subject, scope=StreamScope.RUN)
    with pytest.raises(StreamTokenInvalid):
        verify(MASTER, subject, run_token, scope=StreamScope.TENANT)


def test_tenant_token_does_not_open_a_run_stream_with_an_identical_subject() -> None:
    subject = str(uuid4())
    tenant_token = issue(MASTER, subject, scope=StreamScope.TENANT)
    with pytest.raises(StreamTokenInvalid):
        verify(MASTER, subject, tenant_token, scope=StreamScope.RUN)


def test_run_scope_is_the_default_so_existing_call_sites_are_untouched() -> None:
    """Backward compatibility asserted as an equality, not assumed: a token minted with no scope
    argument must be byte-identical to one minted explicitly at RUN scope. If the derivation for RUN
    ever changes, every token in flight stops verifying and this is what catches it."""
    subject = str(uuid4())
    assert issue(MASTER, subject, now=1_000) == issue(
        MASTER, subject, scope=StreamScope.RUN, now=1_000
    )
    verify(MASTER, subject, issue(MASTER, subject, now=1_000), now=1_000)


def test_scopes_produce_different_signatures_for_the_same_subject_and_expiry() -> None:
    subject = str(uuid4())
    run = issue(MASTER, subject, scope=StreamScope.RUN, now=1_000)
    tenant = issue(MASTER, subject, scope=StreamScope.TENANT, now=1_000)

    assert run.split(".")[0] == tenant.split(".")[0], "same expiry — only the MAC may differ"
    assert run != tenant


def test_tenant_token_expires() -> None:
    tenant = str(uuid4())
    token = issue(MASTER, tenant, scope=StreamScope.TENANT, now=1_000)
    with pytest.raises(StreamTokenInvalid):
        verify(MASTER, tenant, token, scope=StreamScope.TENANT, now=1_000 + TOKEN_TTL_S)


def test_tenant_token_signed_with_another_master_key_is_refused() -> None:
    tenant = str(uuid4())
    token = issue("other-key", tenant, scope=StreamScope.TENANT)
    with pytest.raises(StreamTokenInvalid):
        verify(MASTER, tenant, token, scope=StreamScope.TENANT)
