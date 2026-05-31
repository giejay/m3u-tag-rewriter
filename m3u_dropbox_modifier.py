#!/usr/bin/env python3
"""
m3u_dropbox_modifier.py
-----------------------
Downloads one or more M3U playlists from direct Dropbox URLs, injects
  tvc-stream-timestamps="rewrite"
into every #EXTINF line, then uploads each result back to your Dropbox
with a configurable suffix appended to the original filename.

Auth tokens are cached in TOKEN_FILE and reused / auto-refreshed on
subsequent (headless) runs.  The first run triggers an interactive
OAuth2 PKCE flow that asks you to paste an authorisation code.

Configuration (env vars or a .env file next to this script):
  DROPBOX_CLIENT_ID      – your app's client key
  DROPBOX_CLIENT_SECRET  – your app's client secret
  DROPBOX_PLAYLISTS      – comma-separated list of direct-download M3U URLs
                           e.g. https://dl.dropbox.com/.../foo.m3u?dl=1,...
  DROPBOX_OUTPUT_SUFFIX  – suffix inserted before .m3u in the output filename
                           (default: -include-tvstream-tag)
  DROPBOX_TOKEN_FILE     – where to persist the OAuth token
                           (defaults to ~/.dropbox_m3u_token.json)
"""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
import dropbox
from dropbox.oauth import DropboxOAuth2FlowNoRedirect
from dropbox.exceptions import AuthError, ApiError

# ── defaults ────────────────────────────────────────────────────────────────

DEFAULT_OUTPUT_SUFFIX = "-include-tvstream-tag"
DEFAULT_TOKEN_FILE = Path.home() / ".dropbox_m3u_token.json"

# ── .env loader (simple key=value, no external deps) ────────────────────────

def load_dotenv(path: Path) -> None:
    """Load a .env file into os.environ (only if key not already set)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# ── config ───────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).with_name(".env"))

CLIENT_ID     = os.environ.get("DROPBOX_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("DROPBOX_CLIENT_SECRET", "")
TOKEN_FILE    = Path(os.environ.get("DROPBOX_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)))
OUTPUT_SUFFIX = os.environ.get("DROPBOX_OUTPUT_SUFFIX", DEFAULT_OUTPUT_SUFFIX)

_raw_playlists = os.environ.get("DROPBOX_PLAYLISTS", "")
PLAYLISTS: list[str] = [u.strip() for u in _raw_playlists.split(",") if u.strip()]


# ── token persistence ────────────────────────────────────────────────────────

def save_token(token_result) -> None:
    TOKEN_FILE.write_text(
        json.dumps(
            {
                "access_token":  token_result.access_token,
                "refresh_token": getattr(token_result, "refresh_token", None),
                "account_id":    getattr(token_result, "account_id", None),
            },
            indent=2,
        )
    )
    print(f"[auth] Token saved to {TOKEN_FILE}")


def load_token() -> dict | None:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return None


# ── OAuth2 PKCE flow (no redirect URI needed) ────────────────────────────────

def authenticate() -> dropbox.Dropbox:
    """
    Return an authenticated Dropbox client.

    1. If a stored token exists, try to use it (the SDK auto-refreshes
       short-lived tokens when a refresh_token is present).
    2. Otherwise run the interactive PKCE authorisation-code flow:
       the user opens a URL, authorises the app, then pastes the code
       back at the prompt.
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        sys.exit(
            "ERROR: DROPBOX_CLIENT_ID and DROPBOX_CLIENT_SECRET must be set "
            "(env var or .env file)."
        )

    stored = load_token()
    if stored:
        print("[auth] Found stored token – attempting to reuse …")
        try:
            dbx = dropbox.Dropbox(
                oauth2_access_token=stored["access_token"],
                oauth2_refresh_token=stored.get("refresh_token"),
                app_key=CLIENT_ID,
                app_secret=CLIENT_SECRET,
            )
            dbx.users_get_current_account()   # validates / triggers refresh
            print("[auth] Token OK.")
            return dbx
        except AuthError:
            print("[auth] Stored token is invalid – starting fresh OAuth flow.")

    # ── interactive flow ──────────────────────────────────────────────────
    auth_flow = DropboxOAuth2FlowNoRedirect(
        consumer_key=CLIENT_ID,
        consumer_secret=CLIENT_SECRET,
        token_access_type="offline",   # requests a refresh_token
    )

    auth_url = auth_flow.start()
    print("\n" + "=" * 60)
    print("DROPBOX AUTHORISATION REQUIRED")
    print("=" * 60)
    print(f"\n1. Open this URL in your browser:\n\n   {auth_url}\n")
    print("2. Click \"Allow\" (or log in first if needed).")
    print("3. Copy the authorisation code shown on the page.")
    print("=" * 60)
    auth_code = input("\nPaste the authorisation code here: ").strip()

    try:
        token_result = auth_flow.finish(auth_code)
    except Exception as exc:
        sys.exit(f"ERROR: Could not exchange authorisation code: {exc}")

    save_token(token_result)

    return dropbox.Dropbox(
        oauth2_access_token=token_result.access_token,
        oauth2_refresh_token=getattr(token_result, "refresh_token", None),
        app_key=CLIENT_ID,
        app_secret=CLIENT_SECRET,
    )


# ── M3U processing ────────────────────────────────────────────────────────────

TAG_TO_INJECT = 'tvc-stream-timestamps="rewrite"'

# Regex that matches the last attribute before the comma+title at the end of
# an #EXTINF line.  We inject the new tag just before the trailing ",Title".
_EXTINF_RE = re.compile(r"(#EXTINF:[^\n]*?)(\s*,)", re.IGNORECASE)


def inject_tag(line: str) -> str:
    """Add tvc-stream-timestamps="rewrite" to an #EXTINF line if not present."""
    if TAG_TO_INJECT in line:
        return line   # already there

    # Insert the tag right before the comma+title separator
    def _insert(m: re.Match) -> str:
        attrs, comma = m.group(1), m.group(2)
        return f'{attrs} {TAG_TO_INJECT}{comma}'

    new_line, count = _EXTINF_RE.subn(_insert, line, count=1)
    if count == 0:
        # Fallback: no comma found – just append
        new_line = f"{line.rstrip()} {TAG_TO_INJECT}"
    return new_line


def process_m3u(content: str) -> tuple[str, int]:
    """Return (modified_content, number_of_lines_changed)."""
    lines = content.splitlines(keepends=True)
    changed = 0
    result = []
    for line in lines:
        if line.upper().startswith("#EXTINF"):
            new = inject_tag(line.rstrip("\r\n"))
            result.append(new + "\n")
            if new != line.rstrip("\r\n"):
                changed += 1
        else:
            result.append(line)
    return "".join(result), changed


# ── download ──────────────────────────────────────────────────────────────────

def download_m3u(url: str) -> str:
    print(f"[download] Fetching {url[:80]} …")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    print(f"[download] Got {len(resp.content):,} bytes.")
    return resp.text


# ── upload ────────────────────────────────────────────────────────────────────

def upload_to_dropbox(dbx: dropbox.Dropbox, content: str, dest_path: str) -> str:
    data = content.encode("utf-8")
    print(f"[upload] Uploading {len(data):,} bytes to {dest_path} …")
    try:
        meta = dbx.files_upload(
            data,
            dest_path,
            mode=dropbox.files.WriteMode.overwrite,
            autorename=False,
            mute=True,
        )
        print(f"[upload] Success → {meta.path_display}")

        # Create a shared link so you can hand the URL to anyone
        try:
            link_meta = dbx.sharing_create_shared_link_with_settings(
                meta.path_display
            )
            share_url = link_meta.url
        except ApiError as exc:
            # If the link already exists, fetch it via listing
            if exc.error.is_shared_link_already_exists():
                links = dbx.sharing_list_shared_links(path=meta.path_display)
                share_url = links.links[0].url if links.links else "(unavailable)"
            else:
                raise

        # Convert dl=0 → dl=1 for a direct-download link (works for both ?dl=0 and &dl=0)
        dl_url = re.sub(r"([?&]dl=)0", r"\g<1>1", share_url)
        return dl_url

    except ApiError as exc:
        sys.exit(f"ERROR during upload: {exc}")


# ── helpers ───────────────────────────────────────────────────────────────────

def derive_upload_path(source_url: str, suffix: str) -> str:
    """Turn a source URL into a Dropbox destination path.

    e.g. https://dl.dropbox.com/.../MyProject.m3u?dl=1
         → /MyProject-include-tvstream-tag.m3u
    """
    filename = Path(urlparse(source_url).path).name   # "MyProject.m3u"
    stem, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
    out_name = f"{stem}{suffix}.{ext}" if ext else f"{stem}{suffix}"
    return f"/{out_name}"


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not PLAYLISTS:
        sys.exit(
            "ERROR: DROPBOX_PLAYLISTS is not set.\n"
            "Add a comma-separated list of direct-download M3U URLs to your .env file."
        )

    # 1. Auth (once for all playlists)
    dbx = authenticate()

    results: list[tuple[str, str]] = []

    for url in PLAYLISTS:
        upload_path = derive_upload_path(url, OUTPUT_SUFFIX)
        print(f"\n{'─' * 60}")
        print(f"[playlist] {url[:70]}")
        print(f"[playlist] → {upload_path}")
        print(f"{'─' * 60}")

        # 2. Download
        original = download_m3u(url)

        # 3. Process
        modified, n_changed = process_m3u(original)
        print(f"[process] Modified {n_changed} #EXTINF line(s).")

        # 4. Upload
        dl_url = upload_to_dropbox(dbx, modified, upload_path)
        results.append((upload_path, dl_url))

    print(f"\n{'=' * 60}")
    print(f"[done] Processed {len(results)} playlist(s):")
    for path, dl_url in results:
        print(f"  {path}\n    {dl_url}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
