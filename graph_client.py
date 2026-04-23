import os
from functools import lru_cache

import requests

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_TIMEOUT_SECONDS = int(os.getenv("GRAPH_TIMEOUT_SECONDS", "15"))


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


@lru_cache(maxsize=1)
def get_access_token() -> str:
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
    if not token:
        raise GraphAuthError("Microsoft Graph token response did not include access_token.")

    return token


def _query_user(filter_expression: str) -> dict | None:
    token = get_access_token()
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

    if response.status_code != 200:
        raise GraphRequestError(
            f"Microsoft Graph user lookup failed ({response.status_code}): {response.text}"
        )

    data = response.json()
    users = data.get("value", [])
    return users[0] if users else None


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
