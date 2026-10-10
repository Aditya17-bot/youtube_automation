"""Licensed source footage: fetch, shot index, and what is left to cut.

The `clips_short` channel does not generate its pictures, it cuts them out of
real film. That makes licensing the load-bearing part of this module rather than
an afterthought, so the gate is at the door:

  - `channels/footage_<bank>.yaml` declares a licence per source.
  - `ALLOWED_LICENSES` rejects anything non-commercial or no-derivatives, since
    a monetised Short is a commercial derivative of whatever it was cut from.
  - the claim is then checked against the HOST's own metadata before the file is
    written. A source may only enter the bank if both agree.

That second check is not paranoia. Several films everyone calls "public domain"
are served by archive.org under CC BY-NC - `TheLastManOnEarth` is one - and a
YAML written from memory would have sailed straight past a whitelist that only
read itself.

**What it does not do.** archive.org's `licenseurl` is set by whoever uploaded
the item, not by archive.org, so it catches an honest mistake and not a false
claim: a search of the same collection turns up "The Texas Chain Saw Massacre"
tagged CC0 and a Blu-ray rip of a current TV series tagged public domain, and
neither is either. The check is a second line. The first is that this bank is
hand-curated and every film in it is public domain on its own facts - published
before 1930, or released without notice, or never renewed - which is a claim
about the FILM rather than about one upload of it.

For the same reason the bank prefers archive.org's own 480p derivatives over
the 1080p Blu-ray transfers that also exist: a restoration is a new work with
its own rights-holder, and those are the uploads Content ID is trained on.

A shot index is built once per source with a single ffmpeg pass, and every shot
records which video used it so no two Shorts ever carry the same footage.

    python -m core.footage fetch   --source carnival_of_souls
    python -m core.footage index   --source carnival_of_souls
    python -m core.footage contact --source carnival_of_souls
    python -m core.footage runway
    python -m core.footage release --slug the-hallway-light
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from core.config import PATHS, ffmpeg_bin, ffprobe_bin

# Licences under which a monetised derivative is permitted. NC (non-commercial)
# and ND (no derivatives) are both disqualifying and are absent on purpose: an
# ad-supported Short cut from a film is exactly the commercial derivative those
# terms forbid.
ALLOWED_LICENSES = {"public-domain", "cc0", "cc-by", "cc-by-sa"}

FOOTAGE_DIR = PATHS.assets / "footage"
UA = "youtube_auto/1.0 (+archive.org metadata check)"

# Container preference. archive.org keeps a large original (.mpeg/.avi, often
# 2-3 GB) plus an h.264 derivative a fifth the size. The derivative is what we
# want: identical shots, and the shot index has to decode the whole file.
PREFERRED_EXT = (".mp4", ".m4v")
FALLBACK_EXT = (".ogv", ".mpeg", ".mpg", ".avi", ".mkv", ".webm")


class UnlicensedSource(RuntimeError):
    """Raised instead of downloading. Never downgraded to a warning."""


class IncompleteDownload(RuntimeError):
    """A short read. The `.part` file survives so the next fetch resumes it."""


class NoFootage(RuntimeError):
    """The bank has no unused run long enough for the video being made."""


# --------------------------------------------------------------------------
# bank
# --------------------------------------------------------------------------

def bank_path(bank: str) -> Path:
    return PATHS.channels / f"footage_{bank}.yaml"


def load_bank(bank: str) -> dict:
    path = bank_path(bank)
    if not path.exists():
        raise FileNotFoundError(f"no footage bank at {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def sources(bank: str) -> list[dict]:
    return list(load_bank(bank).get("sources") or [])


def source_entry(bank: str, source_id: str) -> dict:
    for entry in sources(bank):
        if entry.get("id") == source_id:
            return entry
    raise UnlicensedSource(
        f"{source_id!r} is not in channels/footage_{bank}.yaml. Footage that is "
        "not in the bank has no licence on record, so it cannot be used."
    )


def source_path(source_id: str) -> Path:
    return FOOTAGE_DIR / f"{source_id}.mp4"


# --------------------------------------------------------------------------
# licence verification
# --------------------------------------------------------------------------

# archive.org reports a licence as a URL. Order matters: the NC and ND tests run
# before the permissive ones, because "by-nc-sa" contains "by-sa".
_LICENSE_URL_MAP = [
    ("/publicdomain/", "public-domain"),
    ("/licenses/publicdomain", "public-domain"),
    ("/licenses/by-sa/", "cc-by-sa"),
    ("/licenses/by/", "cc-by"),
]

# CC0 and public domain grant identical rights, and archive.org uses the two
# interchangeably across items of the same vintage. Treating a disagreement
# between them as a licence conflict would be pedantry that blocks a legal
# source; a disagreement between, say, cc-by and public domain would not be,
# because attribution is owed in one case and not the other.
_EQUIVALENT = [{"public-domain", "cc0"}]


def _same_license(a: str, b: str) -> bool:
    return a == b or any({a, b} <= group for group in _EQUIVALENT)


def classify_license_url(url: str) -> str | None:
    """Our label for a licence URL, or None if it grants us nothing usable."""
    if not url:
        return None
    u = url.lower()
    if "-nc" in u or "-nd" in u:
        return None                      # non-commercial or no-derivatives
    if "zero" in u:
        return "cc0"
    for needle, label in _LICENSE_URL_MAP:
        if needle in u:
            return label
    return None


def archive_metadata(ref: str) -> dict:
    req = urllib.request.Request(
        f"https://archive.org/metadata/{urllib.parse.quote(ref)}",
        headers={"User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _declared(entry: dict) -> str:
    claimed = str(entry.get("license") or "").strip().lower()
    if claimed not in ALLOWED_LICENSES:
        raise UnlicensedSource(
            f"{entry.get('id')}: licence {claimed or '(none)'!r} is not one of "
            f"{sorted(ALLOWED_LICENSES)}. Non-commercial and no-derivatives "
            "licences are refused: a monetised Short is a commercial derivative."
        )
    if not str(entry.get("license_proof") or "").startswith("http"):
        raise UnlicensedSource(
            f"{entry.get('id')}: license_proof must be a URL someone can open "
            "and check. A licence nobody can verify is not a licence."
        )
    return claimed


def verify_license(entry: dict, meta: dict | None = None) -> str:
    """Confirm the bank's claim against the host, and return the licence.

    The bank is checked first so an obviously wrong entry fails without a
    network round trip, then the host's own metadata has to agree.
    """
    claimed = _declared(entry)
    if entry.get("kind") != "archive":
        return claimed                    # non-archive kinds verify in fetch()

    meta = meta if meta is not None else archive_metadata(entry["ref"])
    if not meta:
        raise UnlicensedSource(
            f"{entry['id']}: archive.org has no item {entry['ref']!r}, so the "
            "licence claimed in the bank cannot be checked."
        )
    md = meta.get("metadata") or {}
    actual = classify_license_url(str(md.get("licenseurl") or ""))
    if actual is None:
        raise UnlicensedSource(
            f"{entry['id']}: archive.org reports licence "
            f"{md.get('licenseurl') or md.get('rights') or '(none)'!r}, which "
            f"does not permit a monetised derivative. The bank claims "
            f"{claimed!r}; the host wins."
        )
    if not _same_license(actual, claimed):
        raise UnlicensedSource(
            f"{entry['id']}: bank claims {claimed!r} but archive.org reports "
            f"{actual!r}. Fix the bank before fetching."
        )
    return actual


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------

def _height(f: dict) -> int:
    try:
        return int(f.get("height") or 0)
    except (TypeError, ValueError):
        return 0


def _pixels(f: dict) -> int:
    """Frame area. Height alone is not enough: archive.org's `.ia.mp4`
    derivative of Duck and Cover is 368x480 beside a 640x480 of the same
    height, and ranking on height picked the narrower one."""
    try:
        return int(f.get("width") or 0) * _height(f)
    except (TypeError, ValueError):
        return 0


# Biggest file we will pull for one source. The uncompressed masters and the
# Blu-ray rips run to 2-8 GB, which at the rate archive.org serves is most of a
# day per film for a picture nobody can tell apart on a phone. A 480p derivative
# upscaled into a 1080-wide crop, with grain and a grade over it, is what this
# channel actually needs.
MAX_FETCH_MB = 900


def _pick_file(meta: dict, max_mb: int = MAX_FETCH_MB) -> dict | None:
    """The best video derivative: highest resolution that fits under `max_mb`.

    Resolution first, NOT size. archive.org's "HiRes MPEG4" of Duck and Cover is
    164 MB at 320x240 - three times the bytes of the 640x480 h.264 beside it and
    a third of the picture. Ranking on size downloaded it, and the softness only
    showed up once a frame was on screen at 1080 wide.

    The ceiling matters just as much in the other direction: the same search
    that found 1080p transfers of half this bank found them at 2-8 GB apiece.
    """
    files = [
        f for f in meta.get("files", [])
        if f.get("source") != "metadata" and f.get("name")
        and f["name"].lower().endswith(PREFERRED_EXT + FALLBACK_EXT)
    ]
    if not files:
        return None

    def rank(f):
        return (_pixels(f),
                f["name"].lower().endswith(PREFERRED_EXT),   # decodes everywhere
                -int(f.get("size") or 0))                    # smallest at a tie

    affordable = [f for f in files if int(f.get("size") or 0) <= max_mb * 1048576]
    # Nothing under the ceiling means the item is only published as a master.
    # Take the smallest rather than refusing: one slow download beats no source.
    return max(affordable, key=rank) if affordable else min(
        files, key=lambda f: int(f.get("size") or 0))


def _download(url: str, dest: Path) -> Path:
    """Download to a `.part` file, resuming one if it is already there.

    These are 300-800 MB over a host that is often slow, so a dropped
    connection forty minutes in has to cost forty minutes rather than the whole
    file. archive.org serves byte ranges; if it ever answers 200 to a range
    request the partial file is discarded and the download starts again, since
    appending a full body to a partial one would produce a corrupt film that
    only shows up as a strange shot index much later.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0

    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = f"bytes={have}-"
        print(f"  resuming at {have / 1e6:.0f} MB")

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        resumed = resp.status == 206
        if have and not resumed:
            have = 0                      # host ignored the range: start over
        total = int(resp.headers.get("Content-Length") or 0) + have
        done = have
        with part.open("ab" if resumed else "wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                # Carriage-return progress only where something can redraw it.
                # daily.py's output is a log file, and a 500 MB download would
                # otherwise write five hundred progress lines into it.
                if total and sys.stdout.isatty():
                    print(f"\r  {done / 1e6:7.0f} / {total / 1e6:.0f} MB  "
                          f"{100 * done / total:5.1f}%", end="", flush=True)
    if sys.stdout.isatty():
        print()
    else:
        print(f"  {done / 1e6:.0f} MB")

    # Checked, because a short read is silent. archive.org closed the
    # connection early on both of the first two downloads and urllib reported
    # success; the truncated file was promoted to `<source>.mp4` and only
    # surfaced as an ffprobe failure at index time. A half-downloaded film that
    # DOES probe is worse - it indexes fine and is simply missing its second
    # half. The partial file is left in place, so a re-run resumes it.
    if total and done < total:
        raise IncompleteDownload(
            f"got {done / 1e6:.0f} of {total / 1e6:.0f} MB before the host "
            f"closed the connection. Run fetch again to resume from "
            f"{part.name}."
        )
    part.replace(dest)
    return dest


def fetch(source_id: str, bank: str = "horror",
          max_mb: int = MAX_FETCH_MB) -> Path:
    """Download one bank source. Refuses anything the licence gate rejects."""
    entry = source_entry(bank, source_id)
    dest = source_path(source_id)
    if dest.exists():
        return dest

    kind = entry.get("kind", "archive")
    if kind == "archive":
        meta = archive_metadata(entry["ref"])
        verify_license(entry, meta)
        chosen = _pick_file(meta, max_mb)
        if not chosen:
            raise FileNotFoundError(
                f"{source_id}: archive.org item {entry['ref']!r} has no video file"
            )
        url = (f"https://archive.org/download/"
               f"{urllib.parse.quote(entry['ref'])}/"
               f"{urllib.parse.quote(chosen['name'])}")
        print(f"  {source_id}: {chosen['name']} "
              f"({int(chosen.get('size') or 0) / 1e6:.0f} MB, "
              f"{chosen.get('width')}x{chosen.get('height')})")
        return _download(url, dest)

    if kind == "youtube":
        return _fetch_youtube(entry, dest)

    raise UnlicensedSource(f"{source_id}: unknown source kind {kind!r}")


def _fetch_youtube(entry: dict, dest: Path) -> Path:
    """Download a Creative Commons YouTube source, licence checked first.

    yt-dlp reports the licence in its metadata, and YouTube only ever sets it to
    the standard licence or CC BY. So the check is exact: anything that is not
    Creative Commons is refused before the download starts.
    """
    _declared(entry)
    probe = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--skip-download", "--print", "%(license)s",
         f"https://www.youtube.com/watch?v={entry['ref']}"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        if "No module named" in (probe.stderr or ""):
            raise RuntimeError(
                "yt-dlp is not installed in this venv: "
                ".venv/Scripts/python.exe -m pip install yt-dlp"
            )
        raise RuntimeError(f"yt-dlp could not read {entry['ref']}: {probe.stderr.strip()}")

    reported = (probe.stdout or "").strip()
    if "creative commons" not in reported.lower():
        raise UnlicensedSource(
            f"{entry['id']}: YouTube reports licence {reported!r}, not Creative "
            "Commons. Only CC-licensed YouTube sources may be cut."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "yt_dlp",
         "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
         "--merge-output-format", "mp4", "-o", str(dest),
         f"https://www.youtube.com/watch?v={entry['ref']}"],
        check=True,
    )
    return dest


# --------------------------------------------------------------------------
# shot index
# --------------------------------------------------------------------------

def index_path(bank: str) -> Path:
    return PATHS.state / f"footage_{bank}.json"


def load_index(bank: str) -> dict:
    path = index_path(bank)
    if not path.exists():
        return {"bank": bank, "sources": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_index(bank: str, data: dict) -> Path:
    path = index_path(bank)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def probe_size(path: Path) -> tuple[int, int]:
    """The file's real frame size.

    Asked of the file rather than read from archive.org's metadata, which is
    wrong often enough to matter: its record for `DuckandC1951.mp4` says
    640x480 and the file that arrives is 368x480. The picker has to use the
    metadata - it chooses before downloading - so the index records the truth.
    """
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, _, h = out.stdout.strip().partition("x")
    return int(w or 0), int(h or 0)


_SHOWINFO = re.compile(
    r"pts_time:(?P<t>[\d.]+).*?mean:\[\s*(?P<y>\d+).*?stdev:\[\s*(?P<s>[\d.]+)",
    re.S,
)

# Defaults tuned on 1920s-60s film. Old stock is grainy, so the scene threshold
# sits higher than the 0.3 that works on modern footage - below about 0.35 the
# grain itself trips the detector and every "shot" is a third of a second long.
SCENE_THRESHOLD = 0.38
MIN_SHOT = 2.5          # under this there is not enough picture to narrate over
MAX_SHOT = 40.0         # longer is usually a held master; we cut from its head
MIN_MEAN_Y = 14         # darker than this is a fade or a black frame
MAX_MEAN_Y = 205        # brighter is a fade-to-white or a blown title card
MIN_STDEV_Y = 8         # flat frame: a card, a fade, or an empty sky


def detect_cuts(src: Path, threshold: float = SCENE_THRESHOLD) -> list[dict]:
    """Every scene cut in one decode pass, with the new shot's luma stats.

    `showinfo` already prints per-frame `mean:` and `stdev:`, and after `select`
    it only prints them for the frames that survived - which are exactly the
    first frames of each shot. So the brightness filter costs nothing beyond the
    pass we were making anyway. Decoding at 320px wide keeps a 90-minute film to
    a few minutes rather than an hour; scene scores are unaffected by scale.
    """
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(src),
         "-an", "-sn", "-vf",
         f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
         "-f", "null", "-"],
        capture_output=True, text=True, errors="replace",
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        raise RuntimeError(f"ffmpeg scene detection failed: {tail[-1] if tail else '?'}")

    cuts = []
    for line in (proc.stderr or "").splitlines():
        if "Parsed_showinfo" not in line:
            continue
        m = _SHOWINFO.search(line)
        if m:
            cuts.append({
                "t": float(m.group("t")),
                "mean_y": float(m.group("y")),
                "stdev_y": float(m.group("s")),
            })
    return cuts


def build_shots(cuts: list[dict], duration: float, entry: dict) -> list[dict]:
    """Turn cut points into usable shot windows.

    A shot runs from one cut to the next. The head and tail of the film are
    dropped wholesale: that is where the title cards, studio logos and credits
    live, and a Short carrying someone else's title card reads as a reupload
    however much narration is over it.
    """
    head = float(entry.get("skip_head", 0) or 0)
    tail = duration - float(entry.get("skip_tail", 0) or 0)

    shots = []
    for i, cut in enumerate(cuts):
        start = cut["t"]
        end = cuts[i + 1]["t"] if i + 1 < len(cuts) else duration
        length = end - start
        if length < MIN_SHOT or start < head or end > tail:
            continue
        if not (MIN_MEAN_Y <= cut["mean_y"] <= MAX_MEAN_Y):
            continue
        if cut["stdev_y"] < MIN_STDEV_Y:
            continue
        shots.append({
            "id": f"{entry['id']}#{len(shots):04d}",
            "source": entry["id"],
            "start": round(start, 3),
            # A held master is trimmed rather than dropped: we only ever cut
            # from the head of a shot, so the rest of it is not worth tracking.
            "end": round(min(end, start + MAX_SHOT), 3),
            "mean_y": cut["mean_y"],
            "stdev_y": cut["stdev_y"],
            "used_by": None,
        })
    return shots


def index(source_id: str, bank: str = "horror",
          threshold: float = SCENE_THRESHOLD) -> dict:
    """Build (or rebuild) the shot index for one source.

    Re-indexing keeps `used_by` for any shot whose start time survives, so a
    threshold tweak never republishes footage that already went out.
    """
    entry = source_entry(bank, source_id)
    src = source_path(source_id)
    if not src.exists():
        raise FileNotFoundError(f"{source_id} not downloaded: {src}")

    duration = probe_duration(src)
    width, height = probe_size(src)
    # The output crop is 1080 wide. Anything under about 360 of usable width is
    # a 3x upscale of an already soft print, which grain hides less well than
    # you would hope - worth knowing before a fortnight of Shorts is cut from it.
    if width < 360:
        print(f"  {source_id}: source is only {width}x{height}; it will be soft "
              f"at 1080 wide. A larger file may exist: "
              f"core.footage fetch --max-mb 2000")
    cuts = detect_cuts(src, threshold)
    shots = build_shots(cuts, duration, entry)

    data = load_index(bank)
    previous = {
        round(s["start"], 1): s.get("used_by")
        for s in (data["sources"].get(source_id) or {}).get("shots", [])
        if s.get("used_by")
    }
    for shot in shots:
        shot["used_by"] = previous.get(round(shot["start"], 1))

    data["sources"][source_id] = {
        "title": entry.get("title", source_id),
        "license": entry.get("license"),
        "license_proof": entry.get("license_proof"),
        "duration": round(duration, 2),
        "size": [width, height],
        "threshold": threshold,
        "shots": shots,
    }
    save_index(bank, data)
    return data["sources"][source_id]


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

# How far apart, in seconds of the source film, the first and last shot of one
# video may sit. Proximity is the point: shots from the same few minutes share a
# location, a light and a cast, so cutting between them reads as coverage of one
# scene rather than as a compilation of unrelated stills.
MAX_SPAN = 240.0


def plan(bank: str, count: int, seed: str, *, source: str | None = None,
         max_span: float = MAX_SPAN) -> list[dict]:
    """`count` unused shots from one neighbourhood of one film.

    Near each other rather than strictly adjacent. Adjacency was the first rule
    and it broke the moment a shot was blacklisted: dropping the classroom shots
    out of a nine-minute film left no eight in a row anywhere in it, and the
    channel would have gone quiet over footage it still had. A gap in the
    numbering is a shot somebody rejected, not a jump to another location.

    Deterministic on `seed` (the topic id) so the same topic always draws the
    same footage. The plugin contract hands the script stage and the validation
    stage no shared state, and recomputing beats smuggling it through a global.
    """
    data = load_index(bank)
    names = [source] if source else sorted(data["sources"])
    runs: list[list[dict]] = []
    for name in names:
        free = [s for s in (data["sources"].get(name) or {}).get("shots", [])
                if not s.get("used_by")]
        for i in range(len(free) - count + 1):
            window = free[i:i + count]
            if window[-1]["end"] - window[0]["start"] <= max_span:
                runs.append(window)
    if not runs:
        raise NoFootage(
            f"footage bank {bank!r} has no run of {count} unused shots left "
            f"inside {max_span:.0f}s of one film. Add a source to "
            f"channels/footage_{bank}.yaml, then `python -m core.footage fetch` "
            "and `index` it."
        )
    return random.Random(seed).choice(runs)


def claim(bank: str, shots: list[dict], slug: str) -> None:
    """Mark shots as spent. Idempotent: re-claiming by the same slug is a no-op."""
    wanted = {s["id"] for s in shots}
    data = load_index(bank)
    for entry in data["sources"].values():
        for shot in entry["shots"]:
            if shot["id"] in wanted and shot.get("used_by") in (None, slug):
                shot["used_by"] = slug
    save_index(bank, data)


def release(bank: str, slug: str) -> int:
    """Hand a video's shots back, for a run that failed or was thrown away."""
    data = load_index(bank)
    freed = 0
    for entry in data["sources"].values():
        for shot in entry["shots"]:
            if shot.get("used_by") == slug:
                shot["used_by"] = None
                freed += 1
    save_index(bank, data)
    return freed


def runway(bank: str, per_video: int = 8, per_day: int = 3) -> dict:
    """How much footage is left, in days rather than in shots."""
    data = load_index(bank)
    rows = []
    total = unused = 0
    for name, entry in sorted(data["sources"].items()):
        shots = entry.get("shots", [])
        free = [s for s in shots if not s.get("used_by")]
        total += len(shots)
        unused += len(free)
        rows.append({"source": name, "shots": len(shots), "unused": len(free)})
    per_day_shots = max(1, per_video * per_day)
    return {"sources": rows, "total": total, "unused": unused,
            "days": unused / per_day_shots}


# --------------------------------------------------------------------------
# contact sheet
# --------------------------------------------------------------------------

def contact_sheet(source_id: str, bank: str = "horror", dest: Path | None = None,
                  limit: int = 120, cols: int = 10) -> Path:
    """A numbered grid of every shot, so junk shots can be spotted in one look.

    The scorer has no taste and never will: it cannot see a boom mic, a modern
    car in a period frame, or a face mid-blink. This is the human pass.
    """
    from PIL import Image, ImageDraw

    src = source_path(source_id)
    data = load_index(bank)
    shots = (data["sources"].get(source_id) or {}).get("shots", [])[:limit]
    if not shots:
        raise NoFootage(f"{source_id} has no indexed shots; run `index` first")

    tw, th = 240, 180
    rows = (len(shots) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th), (8, 8, 10))
    draw = ImageDraw.Draw(sheet)

    tmp = PATHS.work / "contact"
    tmp.mkdir(parents=True, exist_ok=True)
    for i, shot in enumerate(shots):
        frame = tmp / f"{source_id}_{i:04d}.png"
        mid = (shot["start"] + shot["end"]) / 2
        subprocess.run(
            [ffmpeg_bin(), "-y", "-loglevel", "error",
             "-ss", f"{mid:.3f}", "-i", str(src), "-frames:v", "1",
             "-vf", f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
                    f"crop={tw}:{th}",
             str(frame)],
            check=True, capture_output=True,
        )
        if not frame.exists():
            continue
        with Image.open(frame) as im:
            sheet.paste(im.convert("RGB"), ((i % cols) * tw, (i // cols) * th))
        frame.unlink(missing_ok=True)
        label = f"{i}  {shot['start']:.0f}s"
        x, y = (i % cols) * tw + 6, (i // cols) * th + 6
        draw.text((x + 1, y + 1), label, fill=(0, 0, 0))
        draw.text((x, y), label, fill=(255, 210, 60))

    dest = Path(dest) if dest else PATHS.work / f"contact_{source_id}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return dest


def drop(bank: str, source_id: str, numbers: list[int]) -> int:
    """Blacklist shots by their contact-sheet number, as if already used."""
    data = load_index(bank)
    shots = (data["sources"].get(source_id) or {}).get("shots", [])
    dropped = 0
    for n in numbers:
        if 0 <= n < len(shots):
            shots[n]["used_by"] = "dropped"
            dropped += 1
    save_index(bank, data)
    return dropped


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="licensed footage: fetch and index")
    ap.add_argument("command",
                    choices=["fetch", "index", "contact", "runway", "release",
                             "drop", "list"])
    ap.add_argument("--bank", default="horror")
    ap.add_argument("--source", help="one source id; default is every source")
    ap.add_argument("--slug", help="video slug, for release")
    ap.add_argument("--shots", help="comma-separated contact-sheet numbers, for drop")
    ap.add_argument("--threshold", type=float, default=SCENE_THRESHOLD)
    ap.add_argument("--max-mb", type=int, default=MAX_FETCH_MB,
                    help="size ceiling per source; raise it to pull a master")
    args = ap.parse_args(argv)

    if args.command == "list":
        for entry in sources(args.bank):
            have = "on disk" if source_path(entry["id"]).exists() else "-"
            print(f"  {entry['id']:24} {entry.get('license',''):14} {have:8} "
                  f"{entry.get('title','')}")
        return 0

    if args.command == "runway":
        info = runway(args.bank)
        for row in info["sources"]:
            print(f"  {row['source']:24} {row['unused']:5} unused of {row['shots']}")
        print(f"\n  {info['unused']} shots left, about {info['days']:.0f} days "
              f"at 3 videos a day")
        return 0

    if args.command == "release":
        if not args.slug:
            ap.error("release needs --slug")
        print(f"  freed {release(args.bank, args.slug)} shots from {args.slug}")
        return 0

    if args.command == "drop":
        if not (args.source and args.shots):
            ap.error("drop needs --source and --shots")
        nums = [int(n) for n in args.shots.split(",") if n.strip()]
        print(f"  dropped {drop(args.bank, args.source, nums)} shots")
        return 0

    targets = [args.source] if args.source else [e["id"] for e in sources(args.bank)]
    for sid in targets:
        try:
            if args.command == "fetch":
                print(f"{sid}:")
                print(f"  -> {fetch(sid, args.bank, args.max_mb)}")
            elif args.command == "index":
                info = index(sid, args.bank, args.threshold)
                print(f"  {sid:24} {len(info['shots']):5} usable shots from "
                      f"{info['duration'] / 60:.0f} min")
            elif args.command == "contact":
                print(f"  {sid}: {contact_sheet(sid, args.bank)}")
        except (UnlicensedSource, NoFootage, FileNotFoundError, RuntimeError) as exc:
            print(f"  {sid}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
