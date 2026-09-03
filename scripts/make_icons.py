"""Generate small icon variants of the exercise thumbnails.

Purely additive. For every `<name>.jpg` under the media root this writes a
`<name>_icon.webp` beside it and touches nothing else - no original is
modified, renamed or deleted, and videos are left alone entirely.

Why a second file rather than shrinking the original: the same picture is used
in two places at very different sizes. List rows draw it at 42-56 pt, while the
exercise detail screen draws it full width. One file cannot serve both - shrunk
enough to make the lists cheap, it is too soft for the detail header. So the
list gets its own file and the detail screen keeps the original.

The saving is almost entirely about the lists: ~70 KB per row becomes ~6 KB,
so a screen of 20 rows drops from ~1.4 MB to ~120 KB.

Usage
-----
    python scripts/make_icons.py                    # write icons, default 256px
    python scripts/make_icons.py --dry-run          # report only, write nothing
    python scripts/make_icons.py --width 192        # smaller
    python scripts/make_icons.py --force            # redo icons that exist

Afterwards, upload the new `*_icon.webp` files (rclone), then run
`sql_script/alter_media_paths_and_icons.sql`, which derives an `icon` row from
each existing `thumbnail` row and is safe to re-run.
"""

from __future__ import annotations

import argparse
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

ICON_SUFFIX = "_icon"
ICON_EXT = ".webp"

# 256 px covers the largest slot that uses an icon (56 pt) even on a 4x screen,
# with room to spare. webp rather than jpeg because the file is new - nothing
# is keyed on its extension - and it is roughly a third of the size at the same
# visual quality.
DEFAULT_WIDTH = 256
DEFAULT_QUALITY = 80


@dataclass
class Result:
    source: Path
    icon: Path
    src_bytes: int
    icon_bytes: int
    status: str  # written | skipped | failed | would-write

    @property
    def ok(self) -> bool:
        return self.status in ("written", "would-write")


def icon_path_for(source: Path) -> Path:
    return source.with_name(f"{source.stem}{ICON_SUFFIX}{ICON_EXT}")


def build_icon(source: Path, width: int, quality: int, dry_run: bool) -> Result:
    icon = icon_path_for(source)
    src_bytes = source.stat().st_size

    try:
        with Image.open(source) as im:
            # Honour any EXIF rotation before resizing, or a portrait shot
            # would come out sideways.
            im = ImageOps.exif_transpose(im).convert("RGB")
            # Fit inside the box, preserving aspect. The UI crops with
            # BoxFit.cover; pre-cropping here would bake in a decision the
            # layout is entitled to change.
            im.thumbnail((width, width), Image.LANCZOS)

            buffer = io.BytesIO()
            im.save(buffer, "WEBP", quality=quality, method=6)
            data = buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
        return Result(source, icon, src_bytes, 0, f"failed: {exc}")

    if dry_run:
        return Result(source, icon, src_bytes, len(data), "would-write")

    # Write to a temp name in the same directory, then rename. An interrupted
    # run then leaves either no icon or a complete one, never a truncated file
    # that later looks like a corrupt upload.
    temp = icon.with_suffix(icon.suffix + ".part")
    temp.write_bytes(data)
    temp.replace(icon)
    return Result(source, icon, src_bytes, len(data), "written")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="media", type=Path)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument(
        "--force", action="store_true", help="rebuild icons that already exist"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report sizes, write nothing"
    )
    args = parser.parse_args()

    root: Path = args.root.resolve()
    if not root.is_dir():
        print(f"media root not found: {root}", file=sys.stderr)
        return 1

    sources = sorted(
        p
        for p in root.rglob("*.jpg")
        # Never treat a previously generated icon as a source.
        if not p.stem.endswith(ICON_SUFFIX)
    )

    todo = [
        p for p in sources if args.force or not icon_path_for(p).exists()
    ]
    already = len(sources) - len(todo)

    print(f"media root : {root}")
    print(f"thumbnails : {len(sources)}")
    print(f"to build   : {len(todo)}" + (f"  ({already} already present)" if already else ""))
    print(f"target     : {args.width}px webp q{args.quality}"
          + ("   [DRY RUN - nothing will be written]" if args.dry_run else ""))
    print()

    if not todo:
        print("nothing to do")
        return 0

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(
            pool.map(
                lambda p: build_icon(p, args.width, args.quality, args.dry_run), todo
            )
        )

    failures = [r for r in results if not r.ok]
    ok = [r for r in results if r.ok]

    src_total = sum(r.src_bytes for r in ok)
    icon_total = sum(r.icon_bytes for r in ok)

    print(f"built      : {len(ok)}")
    if ok:
        print(f"originals  : {src_total / 1024 / 1024:7.1f} MB  (kept, untouched)")
        print(f"icons      : {icon_total / 1024 / 1024:7.1f} MB"
              f"  ({100 - icon_total * 100 / src_total:.0f}% smaller)")
        print(f"per icon   : {icon_total / len(ok) / 1024:7.1f} KB median-ish")

    if failures:
        print(f"\nfailed     : {len(failures)}")
        for r in failures[:10]:
            print(f"  {r.source.name}: {r.status}")

    if not args.dry_run:
        print()
        print("Next: upload the new *_icon.webp files, then run")
        print("sql_script/alter_media_paths_and_icons.sql to register them.")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
