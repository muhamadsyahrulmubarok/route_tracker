#!/usr/bin/env python3
"""Route companion PWA — Flask API + static web app."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from geo_common import (
    PLACE_CATEGORIES,
    TRAVEL_MODES,
    downsample_points,
    fetch_places_google,
    fetch_route,
    filter_places_to_route,
    format_distance,
    format_duration,
    require_api_key,
)

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "geomaps-companion"})


@app.post("/api/route")
def api_route():
    data = request.get_json(silent=True) or {}
    origin = (data.get("origin") or "Taman Rakyat Slawi").strip()
    destination = (data.get("destination") or "Yogyakarta, Indonesia").strip()
    mode = (data.get("mode") or "motorbike").strip().lower()

    if mode not in TRAVEL_MODES:
        return jsonify({"error": f"Invalid mode. Choose from: {', '.join(sorted(set(TRAVEL_MODES)))}"}), 400
    if not origin or not destination:
        return jsonify({"error": "origin and destination are required"}), 400

    try:
        api_key = require_api_key(exit_on_missing=False)
        route = fetch_route(origin, destination, api_key, mode)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Route failed: {exc}"}), 500

    coords = downsample_points(route.points, max_points=3000)
    return jsonify(
        {
            "origin": origin,
            "destination": destination,
            "mode": mode,
            "travelMode": route.travel_mode,
            "description": route.description,
            "distanceMeters": route.distance_m,
            "durationSeconds": route.duration_s,
            "distanceText": format_distance(route.distance_m),
            "durationText": format_duration(route.duration_s),
            "points": coords,
            "vertexCount": len(route.points),
        }
    )


@app.post("/api/places")
def api_places():
    data = request.get_json(silent=True) or {}
    points_raw = data.get("points") or []
    categories = data.get("categories") or list(PLACE_CATEGORIES)
    max_offset_m = float(data.get("maxOffsetMeters") or 2000)
    max_distance_m = float(data.get("maxDistanceMeters") or 0)

    if not isinstance(categories, list) or not categories:
        categories = list(PLACE_CATEGORIES)
    categories = [str(c).lower() for c in categories]
    bad = [c for c in categories if c not in PLACE_CATEGORIES]
    if bad:
        return jsonify({"error": f"Unknown categories: {', '.join(bad)}"}), 400

    route_points: list[tuple[float, float]] = []
    for p in points_raw:
        try:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                route_points.append((float(p[0]), float(p[1])))
            elif isinstance(p, dict):
                route_points.append((float(p["lat"]), float(p["lng"])))
        except (KeyError, TypeError, ValueError):
            continue

    if len(route_points) < 2:
        return jsonify({"error": "Need at least 2 route points"}), 400

    search_interval_m = float(data.get("searchIntervalMeters") or 8000)
    radius_m = float(data.get("searchRadiusMeters") or 4500)

    try:
        api_key = require_api_key(exit_on_missing=False)
        raw = fetch_places_google(
            route_points,
            api_key,
            categories=categories,
            search_interval_m=search_interval_m,
            radius_m=radius_m,
        )
        places = filter_places_to_route(
            raw,
            route_points,
            max_distance_m=max_distance_m,
            max_offset_m=max_offset_m,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Places failed: {exc}"}), 500

    return jsonify(
        {
            "count": len(places),
            "categories": categories,
            "source": "google",
            "places": places,
        }
    )


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


def main() -> None:
    # Ensure key is present early for clearer startup errors on /api/route.
    try:
        require_api_key(exit_on_missing=False)
    except RuntimeError as exc:
        print(exc)
        print("Server will start, but /api/route will fail until the key is set.")
    print(f"Route companion PWA → http://127.0.0.1:5000")
    print("On a phone use your PC LAN IP (HTTPS may be required for GPS).")
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
