"""OAuth for the YouTube Data API v3.

One-time, on your own machine:

    python -m upload.youtube_auth            # opens a browser, writes the token

That produces a token file containing a refresh token.  Keep it out of source
control (`.gitignore` already covers `*token*.json`).

For CI, skip the file entirely and set three secrets instead:
YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from common.config import Config, ConfigError, load_config

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


class AuthError(RuntimeError):
    pass


def _import_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise AuthError(
            "Google API client libraries are missing. Install them with:\n"
            "    pip install google-api-python-client google-auth google-auth-oauthlib "
            "google-auth-httplib2"
        ) from exc
    return Request, Credentials, build, HttpError, MediaFileUpload


def credentials_from_env():
    """Build credentials from environment secrets (for CI). None if unset."""
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        return None
    _, Credentials, _, _, _ = _import_google()
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )


def load_credentials(cfg: Config):
    """Environment secrets first, then the stored token file."""
    Request, Credentials, _, _, _ = _import_google()

    creds = credentials_from_env()
    if creds is None:
        token_path = Path(cfg.youtube.token_path)
        if not token_path.is_file():
            raise AuthError(
                f"no YouTube credentials found.\n"
                f"  * expected a token file at {token_path}, or\n"
                f"  * YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN "
                f"in the environment.\n"
                f"Run `python -m upload.youtube_auth` once to create the token file "
                f"(see README, 'YouTube setup')."
            )
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds.valid:
        if creds.refresh_token:
            creds.refresh(Request())
        else:
            raise AuthError(
                "stored credentials have no refresh token; delete the token file and "
                "run `python -m upload.youtube_auth` again."
            )
    return creds


def build_service(cfg: Config):
    _, _, build, _, _ = _import_google()
    return build("youtube", "v3", credentials=load_credentials(cfg), cache_discovery=False)


def run_consent_flow(cfg: Config, port: int = 0) -> Path:
    """Interactive one-time browser consent; writes the token file."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:  # pragma: no cover
        raise AuthError(
            "google-auth-oauthlib is required for the consent flow:\n"
            "    pip install google-auth-oauthlib"
        ) from exc

    secrets = Path(cfg.youtube.client_secrets_path)
    if not secrets.is_file():
        raise ConfigError(
            f"OAuth client secrets not found at {secrets}.\n"
            "Create a Google Cloud project, enable the YouTube Data API v3, create an "
            "OAuth 2.0 Client ID of type 'Desktop app', download the JSON and point "
            "youtube.client_secrets_path at it (see README)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(port=port, prompt="consent", access_type="offline")

    token_path = Path(cfg.youtube.token_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    token_path.chmod(0o600)
    return token_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-time YouTube OAuth consent flow.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--port", type=int, default=0, help="local callback port")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    cfg = load_config(args.config)
    path = run_consent_flow(cfg, args.port)
    print(f"\nToken written to {path}")
    print("Keep this file secret -- it grants upload access to your channel.")
    print("For CI, copy its \"refresh_token\" value into the YOUTUBE_REFRESH_TOKEN secret.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
