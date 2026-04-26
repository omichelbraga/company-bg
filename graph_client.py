import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import requests

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_TIMEOUT_SECONDS = int(os.getenv("GRAPH_TIMEOUT_SECONDS", "15"))
TOKEN_REFRESH_SKEW_SECONDS = 60

logger = logging.getLogger(__name__)


class GraphError(Exception):
    """Base Microsoft Graph error."""


class GraphConfigError(GraphError):
    """Graph environment/configuration is missing or invalid."""


class GraphAuthError(GraphError):
    """Failed to obtain Graph access token."""


class GraphUserNotFoundError(GraphError):
    """No Entra user matched the submitted email."""


class GraphRequestError(GraphError):
    """Graph request failed unexpectedly."""


@lru_cache(maxsize=1)
def _graph_settings() -> tuple[str, str, str]:
    tenant_id = os.getenv("GRAPH_TENANT_ID", "").strip()
    client_id = os.getenv("GRAPH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GRAPH_CLIENT_SECRET", "").strip()

    if not tenant_id or not client_id or not client_secret:
        raise GraphConfigError(
            "Microsoft Graph is not configured. Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID, and GRAPH_CLIENT_SECRET."
        )

    return tenant_id, client_id, client_secret


# Hand-rolled token cache with expiry. DO NOT replace with @lru_cache —
# Graph tokens expire (~1h) and lru_cache has no TTL. Past incident:
# token cached forever, every call after the first hour failed with
# "InvalidAuthenticationToken: Lifetime validation failed".
_token_cache: dict = {"token": None, "expires_at": None}
_token_lock = threading.Lock()


def _fetch_new_token() -> tuple[str, datetime]:
    tenant_id, client_id, client_secret = _graph_settings()
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    response = requests.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": GRAPH_SCOPE,
            "grant_type": "client_credentials",
        },
        timeout=GRAPH_TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        raise GraphAuthError(
            f"Failed to obtain Microsoft Graph token ({response.status_code}): {response.text}"
        )

    data = response.json()
    token = data.get("access_token")
    expires_in = int(data.get("expires_in", 3600))
    if not token:
        raise GraphAuthError("Microsoft Graph token response did not include access_token.")

    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=max(60, expires_in - TOKEN_REFRESH_SKEW_SECONDS)
    )
    return token, expires_at


def get_access_token(force_refresh: bool = False) -> str:
    with _token_lock:
        cached = _token_cache["token"]
        expires_at = _token_cache["expires_at"]
        if (
            not force_refresh
            and cached is not None
            and expires_at is not None
            and datetime.now(timezone.utc) < expires_at
        ):
            return cached

        token, new_expires_at = _fetch_new_token()
        _token_cache["token"] = token
        _token_cache["expires_at"] = new_expires_at
        logger.info(
            "Microsoft Graph token refreshed (valid until %s, force_refresh=%s)",
            new_expires_at.isoformat(timespec="seconds"),
            force_refresh,
        )
        return token


def _query_user(filter_expression: str) -> dict | None:
    for attempt in (0, 1):
        token = get_access_token(force_refresh=attempt == 1)
        response = requests.get(
            f"{GRAPH_BASE_URL}/users",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params={
                "$filter": filter_expression,
                "$select": "displayName,jobTitle,mail,userPrincipalName",
                "$top": 1,
            },
            timeout=GRAPH_TIMEOUT_SECONDS,
        )

        if response.status_code == 401 and attempt == 0:
            logger.warning("Microsoft Graph returned 401 — invalidating token cache and retrying once")
            continue

        if response.status_code != 200:
            suffix = " after token refresh" if attempt == 1 else ""
            raise GraphRequestError(
                f"Microsoft Graph user lookup failed{suffix} ({response.status_code}): {response.text}"
            )

        data = response.json()
        users = data.get("value", [])
        return users[0] if users else None

    return None  # unreachable: loop either returns or raises


def get_user_profile_by_email(email: str) -> dict:
    normalized_email = email.strip().lower()
    if not normalized_email:
        raise GraphUserNotFoundError("Email is required for Microsoft Teams background generation.")

    safe_email = normalized_email.replace("'", "''")

    user = _query_user(f"mail eq '{safe_email}'")
    source = "mail"
    if not user:
        user = _query_user(f"userPrincipalName eq '{safe_email}'")
        source = "userPrincipalName"

    if not user:
        raise GraphUserNotFoundError(f"No Entra user found for email: {normalized_email}")

    display_name = (user.get("displayName") or "").strip()
    job_title = (user.get("jobTitle") or "").strip()

    if not display_name:
        raise GraphRequestError(f"Entra user record for {normalized_email} is missing displayName.")

    return {
        "display_name": display_name,
        "job_title": job_title,
        "mail": user.get("mail"),
        "user_principal_name": user.get("userPrincipalName"),
        "source": source,
    }
