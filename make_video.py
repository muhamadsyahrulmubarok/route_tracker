#!/usr/bin/env python3
"""Build an MP4 from existing frames, holding each frame for a fixed duration."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a video from existing frames (default: 1.5s per frame)."
    )
    parser.add_argument(
        "--frames-dir",
        type=Path,
        default=Path("frames"),
        help="Directory with frame_*.jpg files (default: frames)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "streetview_slow.mp4",
        help="Output MP4 path",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=1.5,
        help="Seconds to show each frame (default: 1.5)",
    )
    return parser.parse_args()


def collect_frames(frames_dir: Path) -> list[Path]:
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    return [p for p in frames if p.stat().st_size > 0]


def build_video(frames: list[Path], output: Path, duration_s: float) -> None:
    if duration_s <= 0:
        print("Error: --duration must be > 0", file=sys.stderr)
        sys.exit(1)
    if shutil.which("ffmpeg") is None:
        print(
            "Error: ffmpeg not found on PATH. Install FFmpeg and try again.\n"
            "Windows: winget install ffmpeg",
            file=sys.stderr,
        )
        sys.exit(1)

    output.parent.mkdir(parents=True, exist_ok=True)
    list_path = frames[0].parent / "ffmpeg_concat_slow.txt"

    # Concat demuxer: each image gets an explicit duration.
    # The last file must be listed again without duration (ffmpeg quirk).
    with list_path.open("w", encoding="utf-8") as fh:
        fh.write("ffconcat version 1.0\n")
        for frame in frames:
            escaped = frame.resolve().as_posix().replace("'", r"'\''")
            fh.write(f"file '{escaped}'\n")
            fh.write(f"duration {duration_s:g}\n")
        last = frames[-1].resolve().as_posix().replace("'", r"'\''")
        fh.write(f"file '{last}'\n")

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-vsync",
        "vfr",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(output),
    ]
    print(f"Encoding {len(frames)} frames @ {duration_s:g}s each "
          f"(~{len(frames) * duration_s / 60:.1f} min)")
    print("Running:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Error: ffmpeg failed with exit code {exc.returncode}", file=sys.stderr)
        sys.exit(exc.returncode or 1)

    print(f"Wrote {output}")


def main() -> None:
    args = parse_args()
    if not args.frames_dir.is_dir():
        print(f"Error: frames directory not found: {args.frames_dir}", file=sys.stderr)
        sys.exit(1)

    frames = collect_frames(args.frames_dir)
    if not frames:
        print(f"Error: no frame_*.jpg files in {args.frames_dir}", file=sys.stderr)
        sys.exit(1)

    build_video(frames, args.output, args.duration)


if __name__ == "__main__":
    main()
