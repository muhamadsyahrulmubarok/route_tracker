#!/usr/bin/env python3
"""Record Google Street View along a motorbike (or car) route and stitch frames into a video."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

import requests
from tqdm import tqdm

from geo_common import (
    TRAVEL_MODES,
    RouteInfo,
    SamplePoint,
    fetch_route,
    format_distance,
    format_duration,
    require_api_key,
    sample_route,
)

STREETVIEW_URL = "https://maps.googleapis.com/maps/api/streetview"
STREETVIEW_META_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record Street View along a route and export an MP4."
    )
    parser.add_argument("--origin", default="Taman Rakyat Slawi")
    parser.add_argument("--destination", default="Yogyakarta, Indonesia")
    parser.add_argument(
        "--mode",
        default="motorbike",
        choices=sorted(set(TRAVEL_MODES)),
        help="Travel mode for routing (default: motorbike / TWO_WHEELER)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=250.0,
        help="Sample spacing in meters (default: 250)",
    )
    parser.add_argument(
        "--max-distance",
        type=float,
        default=100_000.0,
        help="Maximum route distance in meters (default: 100000 = 100 km)",
    )
    parser.add_argument("--size", default="1280x720", help="Street View image size")
    parser.add_argument("--fov", type=int, default=90)
    parser.add_argument("--pitch", type=float, default=0.0)
    parser.add_argument("--fps", type=int, default=8, help="Output video FPS")
    parser.add_argument(
        "--frames-dir",
        type=Path,
        default=Path("frames"),
        help="Directory for JPEG frames",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "streetview.mp4",
        help="Output MP4 path",
    )
    parser.add_argument(
        "--route-preview",
        type=Path,
        default=Path("output") / "route_preview.html",
        help="HTML map preview of the chosen route",
    )
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Fetch and show the route, then exit (no Street View / video)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt before downloading frames",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip downloading frames that already exist",
    )
    parser.add_argument(
        "--frames-only",
        action="store_true",
        help="Download frames only; do not run FFmpeg",
    )
    parser.add_argument(
        "--video-only",
        action="store_true",
        help="Build video from existing frames; skip download",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.05,
        help="Delay between Street View requests in seconds",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Retries for transient HTTP errors",
    )
    return parser.parse_args()


def maps_directions_url(origin: str, destination: str) -> str:
    # Maps URL scheme has no official motorbike mode; open as driving for visual check.
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={quote(origin)}"
        f"&destination={quote(destination)}"
        "&travelmode=driving"
    )


def print_route_preview(
    route: RouteInfo,
    *,
    origin: str,
    destination: str,
    mode: str,
) -> None:
    print()
    print("=" * 60)
    print("ROUTE PREVIEW")
    print("=" * 60)
    print(f"From:     {origin}")
    print(f"To:       {destination}")
    print(f"Mode:     {mode} ({route.travel_mode})")
    if route.description:
        print(f"Summary:  {route.description}")
    print(f"Distance: {format_distance(route.distance_m)}")
    print(f"Duration: {format_duration(route.duration_s)} (traffic-aware ETA)")
    print(f"Vertices: {len(route.points)}")
    print()
    if route.travel_mode == "TWO_WHEELER":
        print(
            "Note: TWO_WHEELER routing is in beta and may differ from car routes "
            "(tolls, shortcuts, local roads)."
        )
        print()

    print("Turn-by-turn:")
    for i, step in enumerate(route.steps, start=1):
        dist = format_distance(step.distance_m) if step.distance_m else "?"
        print(f"  {i:3d}. {step.instruction} ({dist})")

    maps_url = maps_directions_url(origin, destination)
    print()
    print("Google Maps (browser; car directions for visual check):")
    print(f"  {maps_url}")
    print("=" * 60)


def write_route_preview_html(route: RouteInfo, path: Path, *, origin: str, destination: str, mode: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    coords = [[lat, lng] for lat, lng in route.points]
    mid = coords[len(coords) // 2]
    payload = {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "travelMode": route.travel_mode,
        "distance": format_distance(route.distance_m),
        "duration": format_duration(route.duration_s),
        "description": route.description,
        "coords": coords,
        "center": mid,
    }
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Route preview — {origin} → {destination}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .panel {{
      position: absolute; z-index: 1000; top: 12px; left: 12px; right: 12px;
      max-width: 420px; background: rgba(255,255,255,0.95); padding: 12px 14px;
      font: 14px/1.4 system-ui, sans-serif; border-radius: 8px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.2);
    }}
    .panel h1 {{ font-size: 16px; margin: 0 0 6px; }}
    .panel p {{ margin: 2px 0; }}
  </style>
</head>
<body>
  <div class="panel">
    <h1>Route preview</h1>
    <p><strong>{origin}</strong> → <strong>{destination}</strong></p>
    <p>Mode: {mode} ({route.travel_mode})</p>
    <p>Distance: {format_distance(route.distance_m)} · ETA: {format_duration(route.duration_s)}</p>
    <p>{route.description or ""}</p>
  </div>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const data = {json.dumps(payload)};
    const map = L.map('map').setView(data.center, 10);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap'
    }}).addTo(map);
    const line = L.polyline(data.coords, {{ color: '#0b57d0', weight: 5 }}).addTo(map);
    L.marker(data.coords[0]).addTo(map).bindPopup('Origin');
    L.marker(data.coords[data.coords.length - 1]).addTo(map).bindPopup('Destination');
    map.fitBounds(line.getBounds(), {{ padding: [40, 40] }});
  </script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def confirm_continue() -> bool:
    try:
        answer = input("Continue downloading Street View along this route? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def request_with_retries(
    url: str,
    params: dict,
    *,
    max_retries: int,
    expect_json: bool = False,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=60)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            if expect_json:
                resp.json()
            return resp
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def has_streetview(lat: float, lng: float, api_key: str, max_retries: int) -> bool:
    params = {"location": f"{lat},{lng}", "key": api_key}
    resp = request_with_retries(
        STREETVIEW_META_URL, params, max_retries=max_retries, expect_json=True
    )
    data = resp.json()
    status = data.get("status")
    if status == "REQUEST_DENIED":
        raise RuntimeError(
            "Street View Static API denied: "
            f"{data.get('error_message', status)}"
        )
    return status == "OK"


def download_frames(
    samples: list[SamplePoint],
    *,
    api_key: str,
    frames_dir: Path,
    size: str,
    fov: int,
    pitch: float,
    skip_existing: bool,
    request_delay: float,
    max_retries: int,
) -> int:
    frames_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    skipped_no_imagery = 0
    skipped_existing = 0

    for i, sample in enumerate(tqdm(samples, desc="Street View", unit="frame")):
        path = frames_dir / f"frame_{i:05d}.jpg"
        if skip_existing and path.exists() and path.stat().st_size > 0:
            skipped_existing += 1
            saved += 1
            continue

        if not has_streetview(sample.lat, sample.lng, api_key, max_retries):
            skipped_no_imagery += 1
            if path.exists():
                path.unlink()
            if request_delay > 0:
                time.sleep(request_delay)
            continue

        params = {
            "size": size,
            "location": f"{sample.lat},{sample.lng}",
            "heading": f"{sample.heading:.2f}",
            "pitch": pitch,
            "fov": fov,
            "key": api_key,
        }
        resp = request_with_retries(STREETVIEW_URL, params, max_retries=max_retries)
        content_type = resp.headers.get("Content-Type", "")
        if "image" not in content_type:
            skipped_no_imagery += 1
            if request_delay > 0:
                time.sleep(request_delay)
            continue

        path.write_bytes(resp.content)
        saved += 1
        if request_delay > 0:
            time.sleep(request_delay)

    print(
        f"Frames ready: {saved} kept, "
        f"{skipped_existing} reused, "
        f"{skipped_no_imagery} skipped (no imagery)"
    )
    return saved


def collect_frame_paths(frames_dir: Path) -> list[Path]:
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    return [p for p in frames if p.stat().st_size > 0]


def build_video(frames_dir: Path, output: Path, fps: int) -> None:
    if shutil.which("ffmpeg") is None:
        print(
            "Error: ffmpeg not found on PATH. Install FFmpeg and try again.\n"
            "Windows: winget install ffmpeg",
            file=sys.stderr,
        )
        sys.exit(1)

    frames = collect_frame_paths(frames_dir)
    if not frames:
        print("Error: no frames found to encode.", file=sys.stderr)
        sys.exit(1)

    output.parent.mkdir(parents=True, exist_ok=True)

    list_path = frames_dir / "ffmpeg_concat.txt"
    with list_path.open("w", encoding="utf-8") as fh:
        for frame in frames:
            escaped = frame.resolve().as_posix().replace("'", r"'\''")
            fh.write(f"file '{escaped}'\n")

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-r",
        str(fps),
        "-i",
        str(list_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    print("Running:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Error: ffmpeg failed with exit code {exc.returncode}", file=sys.stderr)
        sys.exit(exc.returncode or 1)

    print(f"Wrote {output} ({len(frames)} frames @ {fps} fps)")


def main() -> None:
    args = parse_args()
    if args.frames_only and args.video_only:
        print("Error: use only one of --frames-only or --video-only", file=sys.stderr)
        sys.exit(1)
    if args.preview_only and args.video_only:
        print("Error: use only one of --preview-only or --video-only", file=sys.stderr)
        sys.exit(1)

    if not args.video_only:
        api_key = require_api_key()
        print(
            f"Fetching {args.mode} route: {args.origin} -> {args.destination}"
        )
        route = fetch_route(args.origin, args.destination, api_key, args.mode)
        print(f"Decoded {len(route.points)} polyline vertices")

        print_route_preview(
            route,
            origin=args.origin,
            destination=args.destination,
            mode=args.mode,
        )
        write_route_preview_html(
            route,
            args.route_preview,
            origin=args.origin,
            destination=args.destination,
            mode=args.mode,
        )
        preview_path = args.route_preview.resolve()
        print(f"\nMap preview written to: {preview_path}")
        try:
            webbrowser.open(preview_path.as_uri())
        except Exception:
            pass

        if args.preview_only:
            print("Preview only — exiting before Street View download.")
            return

        if not args.yes and not confirm_continue():
            print("Cancelled. Re-run with different options, or use --preview-only / --yes.")
            return

        samples = sample_route(route.points, args.interval, args.max_distance)
        print(
            f"Sampled {len(samples)} points "
            f"(interval={args.interval:g} m, max_distance={args.max_distance:g} m)"
        )

        download_frames(
            samples,
            api_key=api_key,
            frames_dir=args.frames_dir,
            size=args.size,
            fov=args.fov,
            pitch=args.pitch,
            skip_existing=args.skip_existing,
            request_delay=args.request_delay,
            max_retries=args.max_retries,
        )

    if not args.frames_only and not args.preview_only:
        build_video(args.frames_dir, args.output, args.fps)


if __name__ == "__main__":
    main()
