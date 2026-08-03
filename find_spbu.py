#!/usr/bin/env python3
"""Find SPBU (gas stations) along the current route and build an HTML map."""

from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
from tqdm import tqdm

from geo_common import (
    TRAVEL_MODES,
    fetch_places_osm,
    fetch_route,
    filter_places_to_route,
    format_distance,
    nearest_on_route,
    require_api_key,
    route_progress_m,
    sample_route,
)

PLACES_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"


@dataclass(frozen=True)
class Spbu:
    place_id: str
    name: str
    address: str
    lat: float
    lng: float
    rating: float | None
    maps_url: str
    distance_along_route_m: float
    offset_from_route_m: float
    source: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find every SPBU (gas station) near the chosen route and create a map."
    )
    parser.add_argument("--origin", default="Taman Rakyat Slawi")
    parser.add_argument("--destination", default="Yogyakarta, Indonesia")
    parser.add_argument(
        "--mode",
        default="motorbike",
        choices=sorted(set(TRAVEL_MODES)),
        help="Travel mode for routing (default: motorbike)",
    )
    parser.add_argument(
        "--source",
        choices=("auto", "osm", "google"),
        default="auto",
        help="Station data source: osm (free), google (Places API), or auto",
    )
    parser.add_argument(
        "--search-interval",
        type=float,
        default=4000.0,
        help="Sample spacing for Google Nearby Search (meters, default: 4000)",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=2500.0,
        help="Google search radius around each sample (meters, default: 2500)",
    )
    parser.add_argument(
        "--max-distance",
        type=float,
        default=0.0,
        help="Only search the first N meters of the route (0 = full route)",
    )
    parser.add_argument(
        "--max-offset",
        type=float,
        default=1500.0,
        help="Keep stations within this distance of the road (meters, default: 1500)",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.05,
        help="Delay between Google Places requests in seconds",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "spbu.json",
        help="JSON output path",
    )
    parser.add_argument(
        "--map",
        type=Path,
        default=Path("output") / "spbu_map.html",
        help="HTML map output path",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the HTML map in a browser",
    )
    return parser.parse_args()


def sample_search_centers(
    points: list[tuple[float, float]],
    interval_m: float,
    max_distance_m: float | None,
) -> list[tuple[float, float]]:
    limit = max_distance_m if max_distance_m and max_distance_m > 0 else 10**12
    samples = sample_route(points, interval_m, float(limit))
    centers = [(s.lat, s.lng) for s in samples]
    if points and max_distance_m in (None, 0) and centers and centers[-1] != points[-1]:
        centers.append(points[-1])
    return centers


def nearby_gas_stations_google(
    lat: float,
    lng: float,
    *,
    api_key: str,
    radius_m: float,
) -> list[dict]:
    body = {
        "includedTypes": ["gas_station"],
        "maxResultCount": 20,
        "rankPreference": "DISTANCE",
        "languageCode": "id",
        "regionCode": "ID",
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": float(radius_m),
            }
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": ",".join(
            [
                "places.id",
                "places.displayName",
                "places.formattedAddress",
                "places.location",
                "places.rating",
                "places.googleMapsUri",
                "places.types",
            ]
        ),
    }
    resp = requests.post(PLACES_NEARBY_URL, headers=headers, json=body, timeout=60)
    if resp.status_code != 200:
        try:
            detail = resp.json()
            message = detail.get("error", {}).get("message") or detail
        except ValueError:
            message = resp.text
        raise RuntimeError(
            f"Places API failed ({resp.status_code}): {message}\n"
            "Enable Places API (New):\n"
            "https://console.cloud.google.com/apis/library/places.googleapis.com\n"
            "Or rerun with --source osm"
        )
    return resp.json().get("places") or []


def places_to_spbu(places: list[dict]) -> list[Spbu]:
    stations: list[Spbu] = []
    for place in places:
        stations.append(
            Spbu(
                place_id=str(place.get("id") or place.get("place_id") or ""),
                name=str(place.get("name") or "SPBU"),
                address=str(place.get("address") or ""),
                lat=float(place["lat"]),
                lng=float(place["lng"]),
                rating=place.get("rating"),
                maps_url=str(
                    place.get("maps_url")
                    or f"https://www.google.com/maps/search/?api=1&query={place['lat']},{place['lng']}"
                ),
                distance_along_route_m=float(place.get("distance_along_route_m") or 0),
                offset_from_route_m=float(place.get("offset_from_route_m") or 0),
                source=str(place.get("source") or "osm"),
            )
        )
    return stations


def find_spbu_google(
    route_points: list[tuple[float, float]],
    *,
    api_key: str,
    search_interval_m: float,
    radius_m: float,
    max_distance_m: float,
    max_offset_m: float,
    request_delay: float,
) -> list[Spbu]:
    centers = sample_search_centers(
        route_points, search_interval_m, max_distance_m or None
    )
    progress = route_progress_m(route_points)
    found: dict[str, Spbu] = {}

    for lat, lng in tqdm(centers, desc="Searching SPBU (Google)", unit="point"):
        places = nearby_gas_stations_google(
            lat, lng, api_key=api_key, radius_m=radius_m
        )
        for place in places:
            place_id = place.get("id") or ""
            if not place_id or place_id in found:
                continue
            loc = place.get("location") or {}
            plat = loc.get("latitude")
            plng = loc.get("longitude")
            if plat is None or plng is None:
                continue
            along_m, offset_m = nearest_on_route(
                float(plat), float(plng), route_points, progress
            )
            if max_distance_m > 0 and along_m > max_distance_m:
                continue
            if offset_m > max_offset_m:
                continue
            name = ((place.get("displayName") or {}).get("text") or "SPBU").strip()
            address = (place.get("formattedAddress") or "").strip()
            maps_url = (place.get("googleMapsUri") or "").strip()
            if not maps_url:
                maps_url = (
                    f"https://www.google.com/maps/search/?api=1&query={plat},{plng}"
                )
            found[place_id] = Spbu(
                place_id=place_id,
                name=name,
                address=address,
                lat=float(plat),
                lng=float(plng),
                rating=place.get("rating"),
                maps_url=maps_url,
                distance_along_route_m=along_m,
                offset_from_route_m=offset_m,
                source="google",
            )
        if request_delay > 0:
            time.sleep(request_delay)

    return sorted(found.values(), key=lambda s: s.distance_along_route_m)


def find_spbu_osm(
    route_points: list[tuple[float, float]],
    *,
    max_distance_m: float,
    max_offset_m: float,
) -> list[Spbu]:
    print("Querying OpenStreetMap for fuel/SPBU in route area...")
    raw = fetch_places_osm(route_points, categories=["fuel"])
    print(f"OSM returned {len(raw)} fuel points in bbox; filtering to road corridor...")
    filtered = filter_places_to_route(
        raw,
        route_points,
        max_distance_m=max_distance_m,
        max_offset_m=max_offset_m,
    )
    return places_to_spbu(filtered)


def print_spbu_list(stations: list[Spbu]) -> None:
    print()
    print("=" * 70)
    print(f"SPBU ALONG ROUTE ({len(stations)} found)")
    print("=" * 70)
    if not stations:
        print("No gas stations found near this route.")
        print("=" * 70)
        return
    for i, s in enumerate(stations, start=1):
        rating = f"{s.rating:.1f}" if s.rating is not None else "-"
        print(
            f"{i:3d}. {s.name}\n"
            f"     @ {format_distance(int(s.distance_along_route_m))} along route "
            f"(~{s.offset_from_route_m:.0f} m off road) · {s.source} · rating {rating}\n"
            f"     {s.address or '(no address)'}\n"
            f"     {s.maps_url}"
        )
    print("=" * 70)


def write_spbu_json(
    stations: list[Spbu],
    path: Path,
    *,
    origin: str,
    destination: str,
    mode: str,
    source: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "source": source,
        "count": len(stations),
        "stations": [asdict(s) for s in stations],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_spbu_map(
    stations: list[Spbu],
    route_points: list[tuple[float, float]],
    path: Path,
    *,
    origin: str,
    destination: str,
    mode: str,
    source: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Downsample route line for a lighter HTML file.
    step = max(1, len(route_points) // 2500)
    coords = [[lat, lng] for lat, lng in route_points[::step]]
    if route_points and coords[-1] != list(route_points[-1]):
        coords.append(list(route_points[-1]))

    markers = [
        {
            "name": s.name,
            "address": s.address,
            "lat": s.lat,
            "lng": s.lng,
            "along": format_distance(int(s.distance_along_route_m)),
            "offset": f"{s.offset_from_route_m:.0f} m",
            "url": s.maps_url,
            "source": s.source,
        }
        for s in stations
    ]
    center = coords[len(coords) // 2] if coords else [-7.5, 110.0]
    list_items = "".join(
        (
            "<li>"
            f"<a href=\"{m['url']}\" target=\"_blank\" rel=\"noopener\">{m['name']}</a>"
            f" · {m['along']}"
            "</li>"
        )
        for m in markers
    )
    html = f"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SPBU along route — {origin} → {destination}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    :root {{
      --ink: #1c1917;
      --panel: rgba(255, 250, 240, 0.94);
      --accent: #0b57d0;
      --fuel: #c2410c;
    }}
    html, body, #map {{ height: 100%; margin: 0; }}
    body {{ font-family: "Segoe UI", system-ui, sans-serif; color: var(--ink); }}
    .panel {{
      position: absolute; z-index: 1000; top: 12px; left: 12px;
      width: min(380px, calc(100vw - 24px)); max-height: 75vh; overflow: auto;
      background: var(--panel); padding: 14px 16px;
      border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,0.18);
      backdrop-filter: blur(6px);
    }}
    .panel h1 {{ font-size: 16px; margin: 0 0 6px; }}
    .panel .meta {{ font-size: 12px; opacity: 0.8; margin-bottom: 8px; }}
    .panel ol {{ margin: 8px 0 0; padding-left: 18px; font-size: 12px; }}
    .panel li {{ margin: 5px 0; }}
    .panel a {{ color: var(--fuel); }}
    .legend {{
      display: flex; gap: 12px; font-size: 11px; margin-top: 8px;
    }}
    .swatch {{
      display: inline-block; width: 10px; height: 10px; border-radius: 50%;
      margin-right: 4px; vertical-align: middle;
    }}
  </style>
</head>
<body>
  <div class="panel">
    <h1>SPBU along route ({len(stations)})</h1>
    <div class="meta">
      <div><strong>{origin}</strong> → <strong>{destination}</strong></div>
      <div>Mode: {mode} · Source: {source}</div>
      <div class="legend">
        <span><span class="swatch" style="background:#0b57d0"></span>Route</span>
        <span><span class="swatch" style="background:#f4b400;border:1px solid #b06000"></span>SPBU</span>
      </div>
    </div>
    <ol>{list_items}</ol>
  </div>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const route = {json.dumps(coords)};
    const stations = {json.dumps(markers, ensure_ascii=False)};
    const map = L.map('map').setView({json.dumps(center)}, 9);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap'
    }}).addTo(map);
    const line = L.polyline(route, {{ color: '#0b57d0', weight: 5, opacity: 0.9 }}).addTo(map);
    L.marker(route[0]).addTo(map).bindPopup('Origin');
    L.marker(route[route.length - 1]).addTo(map).bindPopup('Destination');
    for (const s of stations) {{
      L.circleMarker([s.lat, s.lng], {{
        radius: 7, color: '#9a3412', fillColor: '#f4b400', fillOpacity: 0.95, weight: 2
      }}).addTo(map).bindPopup(
        `<strong>${{s.name}}</strong><br>${{s.address || ''}}<br>` +
        `Along route: ${{s.along}} · ${{s.offset}} off road<br>` +
        `<a href="${{s.url}}" target="_blank" rel="noopener">Open details</a>`
      );
    }}
    map.fitBounds(line.getBounds(), {{ padding: [40, 40] }});
  </script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> None:
    args = parse_args()
    api_key = require_api_key()

    print(f"Fetching {args.mode} route: {args.origin} -> {args.destination}")
    route = fetch_route(args.origin, args.destination, api_key, args.mode)
    print(
        f"Route: {format_distance(route.distance_m)} · "
        f"{len(route.points)} vertices"
        + (
            f" · searching first {format_distance(int(args.max_distance))}"
            if args.max_distance > 0
            else " · full route"
        )
    )

    used_source = args.source
    stations: list[Spbu] = []

    if args.source in ("google", "auto"):
        try:
            stations = find_spbu_google(
                route.points,
                api_key=api_key,
                search_interval_m=args.search_interval,
                radius_m=args.radius,
                max_distance_m=args.max_distance,
                max_offset_m=args.max_offset,
                request_delay=args.request_delay,
            )
            used_source = "google"
        except RuntimeError as exc:
            if args.source == "google":
                raise
            print(f"Google Places unavailable, falling back to OSM.\n({exc})")
            stations = find_spbu_osm(
                route.points,
                max_distance_m=args.max_distance,
                max_offset_m=args.max_offset,
            )
            used_source = "osm"
    else:
        stations = find_spbu_osm(
            route.points,
            max_distance_m=args.max_distance,
            max_offset_m=args.max_offset,
        )
        used_source = "osm"

    print_spbu_list(stations)

    write_spbu_json(
        stations,
        args.output,
        origin=args.origin,
        destination=args.destination,
        mode=args.mode,
        source=used_source,
    )
    write_spbu_map(
        stations,
        route.points,
        args.map,
        origin=args.origin,
        destination=args.destination,
        mode=args.mode,
        source=used_source,
    )
    print(f"JSON: {args.output.resolve()}")
    print(f"Map:  {args.map.resolve()}")

    if not args.no_open:
        try:
            webbrowser.open(args.map.resolve().as_uri())
        except Exception:
            pass


if __name__ == "__main__":
    main()
