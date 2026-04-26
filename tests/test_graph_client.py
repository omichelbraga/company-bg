"""Tests for graph_client token caching + 401 retry behaviour."""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import graph_client


@pytest.fixture(autouse=True)
def reset_token_cache(monkeypatch):
    """Each test starts with a clean cache and stub credentials."""
    monkeypatch.setenv("GRAPH_TENANT_ID", "tenant-id")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "client-secret")
    graph_client._graph_settings.cache_clear()
    graph_client._token_cache["token"] = None
    graph_client._token_cache["expires_at"] = None
    yield
    graph_client._token_cache["token"] = None
    graph_client._token_cache["expires_at"] = None


def _make_token_response(token="tok-A", expires_in=3600):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"access_token": token, "expires_in": expires_in}
    return response


def _make_user_response(status_code=200, users=None, text="error"):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.json.return_value = {"value": users or []}
    return response


# ── get_access_token ─────────────────────────────────────────────────────────

def test_first_call_fetches_token():
    with patch("graph_client.requests.post", return_value=_make_token_response("tok-1")) as post:
        assert graph_client.get_access_token() == "tok-1"
    assert post.call_count == 1


def test_second_call_within_expiry_uses_cache():
    with patch("graph_client.requests.post", return_value=_make_token_response("tok-1", 3600)) as post:
        graph_client.get_access_token()
        graph_client.get_access_token()
        graph_client.get_access_token()
    assert post.call_count == 1, "expected cache hit, got fresh fetch"


def test_expired_token_triggers_refresh():
    # First call returns a token that "expired" 10s ago
    response = _make_token_response("tok-old", expires_in=3600)
    with patch("graph_client.requests.post", return_value=response):
        graph_client.get_access_token()

    # Manually expire the cache to simulate clock passing
    graph_client._token_cache["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=10)

    with patch("graph_client.requests.post", return_value=_make_token_response("tok-new")) as post:
        assert graph_client.get_access_token() == "tok-new"
    assert post.call_count == 1


def test_force_refresh_bypasses_cache():
    with patch("graph_client.requests.post", return_value=_make_token_response("tok-1")):
        graph_client.get_access_token()

    with patch("graph_client.requests.post", return_value=_make_token_response("tok-2")) as post:
        assert graph_client.get_access_token(force_refresh=True) == "tok-2"
    assert post.call_count == 1


def test_skew_seconds_applied_to_expiry():
    """Token cache should expire ~60s before Microsoft's stated expiry."""
    with patch("graph_client.requests.post", return_value=_make_token_response("tok", 3600)):
        before = datetime.now(timezone.utc)
        graph_client.get_access_token()
        after = datetime.now(timezone.utc)

    expires_at = graph_client._token_cache["expires_at"]
    # Expected window: now + (3600 - 60) seconds, +/- 1s for test runtime
    assert before + timedelta(seconds=3539) <= expires_at <= after + timedelta(seconds=3540)


def test_token_endpoint_failure_raises_auth_error():
    response = MagicMock(status_code=400, text="bad creds")
    with patch("graph_client.requests.post", return_value=response):
        with pytest.raises(graph_client.GraphAuthError):
            graph_client.get_access_token()


def test_missing_access_token_raises_auth_error():
    response = MagicMock(status_code=200)
    response.json.return_value = {"expires_in": 3600}
    with patch("graph_client.requests.post", return_value=response):
        with pytest.raises(graph_client.GraphAuthError):
            graph_client.get_access_token()


# ── _query_user (401 retry) ──────────────────────────────────────────────────

def test_query_user_returns_first_match():
    user = {"displayName": "Mike", "jobTitle": "Eng", "mail": "m@x.com", "userPrincipalName": "m@x.com"}
    with patch("graph_client.requests.post", return_value=_make_token_response()):
        with patch("graph_client.requests.get", return_value=_make_user_response(users=[user])):
            assert graph_client._query_user("mail eq 'm@x.com'") == user


def test_query_user_returns_none_when_empty():
    with patch("graph_client.requests.post", return_value=_make_token_response()):
        with patch("graph_client.requests.get", return_value=_make_user_response(users=[])):
            assert graph_client._query_user("mail eq 'nobody@x.com'") is None


def test_query_user_401_then_200_succeeds_after_refresh():
    """The exact bug we just fixed: cached token rejected, retry with fresh succeeds."""
    user = {"displayName": "Mike", "jobTitle": "Eng", "mail": "m@x.com", "userPrincipalName": "m@x.com"}
    token_responses = [_make_token_response("tok-stale"), _make_token_response("tok-fresh")]
    user_responses = [_make_user_response(401, text="expired"), _make_user_response(200, users=[user])]

    with patch("graph_client.requests.post", side_effect=token_responses) as post:
        with patch("graph_client.requests.get", side_effect=user_responses) as get:
            result = graph_client._query_user("mail eq 'm@x.com'")

    assert result == user
    assert post.call_count == 2, "expected two token fetches (initial + force_refresh after 401)"
    assert get.call_count == 2, "expected two Graph calls (original + retry)"
    # Verify the second Graph call used the fresh token
    second_call_headers = get.call_args_list[1].kwargs["headers"]
    assert second_call_headers["Authorization"] == "Bearer tok-fresh"


def test_query_user_401_twice_raises():
    token_responses = [_make_token_response("tok-1"), _make_token_response("tok-2")]
    user_responses = [_make_user_response(401, text="bad"), _make_user_response(401, text="still bad")]

    with patch("graph_client.requests.post", side_effect=token_responses):
        with patch("graph_client.requests.get", side_effect=user_responses):
            with pytest.raises(graph_client.GraphRequestError, match="after token refresh"):
                graph_client._query_user("mail eq 'x@x.com'")


def test_query_user_non_401_failure_does_not_retry():
    with patch("graph_client.requests.post", return_value=_make_token_response()) as post:
        with patch("graph_client.requests.get", return_value=_make_user_response(500, text="boom")) as get:
            with pytest.raises(graph_client.GraphRequestError):
                graph_client._query_user("mail eq 'x@x.com'")
    assert post.call_count == 1, "no token refresh should occur for 5xx"
    assert get.call_count == 1, "no retry should occur for 5xx"


# ── Regression guard: make sure get_access_token isn't lru_cache-decorated ───

def test_get_access_token_is_not_lru_cached():
    """If someone re-wraps get_access_token with @lru_cache, the bug returns."""
    assert not hasattr(graph_client.get_access_token, "cache_info"), (
        "get_access_token must NOT be wrapped with @lru_cache — tokens expire."
    )
