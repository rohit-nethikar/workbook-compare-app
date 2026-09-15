"""Tests for tableau_client.call_with_retry.

Covers the bug hit in real usage: a Personal Access Token shared with another
app/scheduler can have its session invalidated mid-request by that other app
signing in independently, which TSC surfaces as NotSignedInError on any call made
with the now-stale token (see the comment in call_with_retry itself, and TSC's own
"TODO: catch this... attempt to sign in again" in endpoint.py's _check_status).
"""
import contextlib

import pytest
import tableauserverclient as TSC

import tableau_client


def _fake_signed_in_server(sign_in_call_count):
    @contextlib.contextmanager
    def _fake(server_url, site_name, pat_name, pat_secret):
        sign_in_call_count.append(1)
        yield object()
    return _fake


def test_call_with_retry_succeeds_first_try(monkeypatch):
    calls = []
    monkeypatch.setattr(tableau_client, "signed_in_server", _fake_signed_in_server(calls))

    result = tableau_client.call_with_retry("url", "site", "pat", "secret", lambda server: "ok")

    assert result == "ok"
    assert len(calls) == 1


def test_call_with_retry_retries_once_on_not_signed_in_error(monkeypatch):
    calls = []
    monkeypatch.setattr(tableau_client, "signed_in_server", _fake_signed_in_server(calls))

    attempts = {"n": 0}

    def fn(server):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TSC.NotSignedInError(b"<xml/>", "https://example.com/api/x")
        return "ok-on-retry"

    result = tableau_client.call_with_retry("url", "site", "pat", "secret", fn)

    assert result == "ok-on-retry"
    assert len(calls) == 2  # signed in fresh for the retry


def test_call_with_retry_raises_after_second_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(tableau_client, "signed_in_server", _fake_signed_in_server(calls))

    def fn(server):
        raise TSC.NotSignedInError(b"<xml/>", "https://example.com/api/x")

    with pytest.raises(TSC.NotSignedInError):
        tableau_client.call_with_retry("url", "site", "pat", "secret", fn)

    assert len(calls) == 2  # tried twice, then gave up


def test_call_with_retry_does_not_retry_other_exceptions(monkeypatch):
    calls = []
    monkeypatch.setattr(tableau_client, "signed_in_server", _fake_signed_in_server(calls))

    def fn(server):
        raise ValueError("not an auth problem")

    with pytest.raises(ValueError):
        tableau_client.call_with_retry("url", "site", "pat", "secret", fn)

    assert len(calls) == 1  # no retry for a non-auth error
