"""Upload approved videos to YouTube.

SETUP (once per channel, ~10 minutes):

 1. console.cloud.google.com -> new project -> enable "YouTube Data API v3".
 2. OAuth consent screen: External. Add yourself as a test user, then
    PUBLISH THE APP to Production.

    This step is not optional. While the consent screen stays in "Testing",
    Google expires refresh tokens after 7 days and every scheduled upload
    breaks until you re-authorise by hand.

 3. Credentials -> Create OAuth client ID -> Desktop app -> download JSON.
 4. Save it as  secrets/client_secret_<channel>.json
 5. Run:  python -m core.publish auth --channel finance
    A browser opens once; the refresh token is cached in secrets/.

Quota note: videos.insert moved to its own bucket of roughly 100 uploads/day,
separate from the 10,000-unit pool, so uploads no longer compete with reads.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.config import PATHS, load_channel
from core.review import (STATUS_APPROVED, STATUS_PUBLISHED, approved, human,
                         purge_published, purge_work, review_dir, set_status)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
SECRETS = PATHS.root / "secrets"


def account_for(channel: str) -> str:
    """Which OAuth identity a channel uploads with.

    A Short and its long-form parent live on the SAME YouTube channel, so they
    share one set of credentials. `youtube_account` in the Short's config points
    at the parent; without it a channel authorises for itself.
    """
    try:
        return load_channel(channel).get("youtube_account") or channel
    except Exception:  # noqa: BLE001
        return channel


def has_credentials(channel: str) -> bool:
    """Whether an authorised token is already on disk for this channel.

    Lets the daily runner skip a channel that was never authorised instead of
    discovering it halfway through an upload loop.
    """
    return _token_path(account_for(channel)).exists()


def _client_secret(channel: str) -> Path:
    path = SECRETS / f"client_secret_{channel}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"missing {path}\nSee the setup steps at the top of core/publish.py."
        )
    return path


def _token_path(channel: str) -> Path:
    return SECRETS / f"token_{channel}.json"


def get_credentials(channel: str, *, interactive: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    channel = account_for(channel)
    SECRETS.mkdir(parents=True, exist_ok=True)
    token_file = _token_path(channel)
    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json(), encoding="utf-8")
        return creds

    if not interactive:
        raise RuntimeError(
            f"no usable credentials for {channel!r}. "
            f"Run: python -m core.publish auth --channel {channel}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(_client_secret(channel)), SCOPES)
    creds = flow.run_local_server(port=0)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    print(f"saved {token_file}")
    return creds


def _service(channel: str, *, interactive: bool = False):
    from googleapiclient.discovery import build as build_service

    return build_service("youtube", "v3", credentials=get_credentials(channel, interactive=interactive))


def upload(channel: str, slug: str, *, privacy: str | None = None,
           keep_local: bool = False) -> str:
    """Upload one reviewed video. Returns the new video id.

    Local renders are purged afterwards unless `keep_local` is set: an 8-minute
    1080p story video is ~440 MB, which fills a disk quickly at three a week.
    """
    from googleapiclient.http import MediaFileUpload

    cfg = load_channel(channel)
    folder = review_dir(channel, slug)
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    video = folder / "final.mp4"
    if not video.exists():
        raise FileNotFoundError(video)

    youtube = _service(channel)
    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "categoryId": meta.get("categoryId", "27"),
        },
        "status": {
            "privacyStatus": privacy or meta.get("privacyStatus", cfg.get("privacy", "private")),
            "selfDeclaredMadeForKids": meta.get("madeForKids", False),
        },
    }

    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True,
                            mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  uploading... {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"  video id: {video_id}")

    thumb = folder / "thumbnail.png"
    if thumb.exists():
        # Custom thumbnails need a phone-verified channel. The video is already
        # up by this point, so a refusal here must not abort the run: that would
        # skip set_status below and the next run would upload a duplicate.
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb))).execute()
            print("  thumbnail set")
        except Exception as exc:  # noqa: BLE001
            print(f"  thumbnail skipped: {exc}")

    set_status(channel, slug, STATUS_PUBLISHED, note=f"https://youtu.be/{video_id}")

    # The upload keeps the mp4 open, and Windows refuses to unlink a file with a
    # live handle. Nothing needs the stream once the last chunk is acknowledged.
    try:
        media.stream().close()
    except Exception:  # noqa: BLE001
        pass

    # YouTube now holds the copy that matters, so the local renders are dead
    # weight. Metadata and status stay, which is what topic history reads.
    # Failing to reclaim disk is untidy, not fatal: the video is already up and
    # marked published, and letting it raise here would abandon every other
    # video queued behind this one.
    freed = 0
    if not keep_local:
        try:
            freed = purge_work(channel, slug) + purge_published(channel, slug)
        except OSError as exc:
            print(f"  local files kept: {exc}")
    if freed:
        print(f"  reclaimed {human(freed)}")

    return video_id


def publish_approved(channel: str, *, limit: int = 5, privacy: str | None = None,
                     keep_local: bool = False) -> list[str]:
    ready = approved(channel)[:limit]
    if not ready:
        print("nothing approved")
        return []

    ids = []
    for item in ready:
        print(f"{item['channel']}/{item['slug']}")
        ids.append(upload(item["channel"], item["slug"], privacy=privacy,
                          keep_local=keep_local))
    return ids


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="YouTube publishing")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_auth = sub.add_parser("auth", help="one-time browser authorisation")
    p_auth.add_argument("--channel", default="finance")

    p_one = sub.add_parser("upload", help="upload a single reviewed video")
    p_one.add_argument("slug")
    p_one.add_argument("--channel", default="finance")
    p_one.add_argument("--privacy", choices=["private", "unlisted", "public"])
    p_one.add_argument("--keep-local", action="store_true",
                        help="do not purge local renders after upload")

    p_all = sub.add_parser("run", help="upload everything approved")
    p_all.add_argument("--channel", default="finance")
    p_all.add_argument("--limit", type=int, default=5)
    p_all.add_argument("--privacy", choices=["private", "unlisted", "public"])
    p_all.add_argument("--keep-local", action="store_true",
                        help="do not purge local renders after upload")

    args = ap.parse_args()

    if args.cmd == "auth":
        get_credentials(args.channel, interactive=True)
        me = _service(args.channel).channels().list(part="snippet", mine=True).execute()
        for item in me.get("items", []):
            print(f"authorised as: {item['snippet']['title']}")
    elif args.cmd == "upload":
        upload(args.channel, args.slug, privacy=args.privacy, keep_local=args.keep_local)
    else:
        publish_approved(args.channel, limit=args.limit, privacy=args.privacy,
                         keep_local=args.keep_local)
