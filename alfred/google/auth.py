"""Google OAuth: one-time authorization and loading/refreshing the token.

See docs/google-setup.md for creating the OAuth client.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from alfred.config import GoogleConfig

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


class GoogleAuthError(RuntimeError):
    pass


def _write_private(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(data)
    os.chmod(path, 0o600)


def authorize(cfg: GoogleConfig, port: int = 8765) -> Any:
    """Run the Desktop-app OAuth flow. On a headless server, tunnel `port` over SSH."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not cfg.client_secret_path.is_file():
        raise GoogleAuthError(f"OAuth client file not found: {cfg.client_secret_path} (see docs/google-setup.md)")
    flow = InstalledAppFlow.from_client_secrets_file(str(cfg.client_secret_path), SCOPES)
    creds = flow.run_local_server(
        host="localhost",
        port=port,
        open_browser=False,
        authorization_prompt_message=(
            f"Open this URL in a browser on a machine with an SSH tunnel to port {port}:\n\n{{url}}\n"
        ),
        success_message="Alfred is authorized. You can close this window.",
        # Ask for a refresh token every time so re-running this always yields one.
        access_type="offline",
        prompt="consent",
    )
    _write_private(cfg.token_path, creds.to_json())
    return creds


def load_credentials(cfg: GoogleConfig) -> Any:
    """Load the saved token, refreshing it if expired. Raises GoogleAuthError."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not cfg.token_path.is_file():
        raise GoogleAuthError("not authorized: run `alfred google-auth`")
    try:
        creds = Credentials.from_authorized_user_file(str(cfg.token_path), SCOPES)
    except ValueError as e:
        raise GoogleAuthError(f"token file unreadable ({e}): run `alfred google-auth`") from e

    granted = set(creds.scopes or [])
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        raise GoogleAuthError(f"token lacks scopes {missing}: run `alfred google-auth` again")

    if not creds.valid:
        if not creds.refresh_token:
            raise GoogleAuthError("token expired and has no refresh token: run `alfred google-auth`")
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise GoogleAuthError(
                f"token refresh failed ({e}). If the app is in Testing mode, tokens expire after 7 days; "
                "publish it (docs/google-setup.md step 3) and run `alfred google-auth`"
            ) from e
        _write_private(cfg.token_path, creds.to_json())
    return creds


def token_status(cfg: GoogleConfig) -> str:
    try:
        load_credentials(cfg)
        return "ok"
    except GoogleAuthError as e:
        return str(e)
    except Exception as e:  # network trouble during refresh, etc.
        return f"error: {type(e).__name__}: {e}"
