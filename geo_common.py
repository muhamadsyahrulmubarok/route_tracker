#!/usr/bin/env python3
"""Shared routing and Google Places helpers for geomaps tools / PWA."""

from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Iterable

import polyline
import requests
from dotenv import load_dotenv
from geopy.distance import geodesic

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACES_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]

TRAVEL_MODES = {
    "motorbike": "TWO_WHEELER",
    "motorcycle": "TWO_WHEELER",
    "two-wheeler": "TWO_WHEELER",
    "car": "DRIVE",
    "drive": "DRIVE",
}

# UI / API category -> Google Places (New) Table A types (max 5 per Nearby request)
PLACE_CATEGORIES: dict[str, list[str]] = {
    "food": ["restaurant", "cafe", "fast_food_restaurant"],
    "market": ["supermarket", "convenience_store", "market"],
    "beach": ["beach"],
    "fuel": ["gas_station"],
    "interest": ["tourist_attraction", "museum", "historical_landmark"],
}

# Optional OSM fallback fragments (find_spbu --source osm)
OSM_PLACE_QUERIES: dict[str, list[str]] = {
    "food": [
        'node["amenity"="restaurant"]({bbox});',
        'way["amenity"="restaurant"]({bbox});',
        'node["amenity"="cafe"]({bbox});',
        'way["amenity"="cafe"]({bbox});',
        'node["amenity"="fast_food"]({bbox});',
        'way["amenity"="fast_food"]({bbox});',
        'node["amenity"="food_court"]({bbox});',
        'way["amenity"="food_court"]({bbox});',
    ],
    "market": [
        'node["shop"="supermarket"]({bbox});',
        'way["shop"="supermarket"]({bbox});',
        'node["shop"="convenience"]({bbox});',
        'way["shop"="convenience"]({bbox});',
        'node["shop"="marketplace"]({bbox});',
        'way["shop"="marketplace"]({bbox});',
        'node["amenity"="marketplace"]({bbox});',
        'way["amenity"="marketplace"]({bbox});',
    ],
    "beach": [
        'node["natural"="beach"]({bbox});',
        'way["natural"="beach"]({bbox});',
        'relation["natural"="beach"]({bbox});',
    ],
    "fuel": [
        'node["amenity"="fuel"]({bbox});',
        'way["amenity"="fuel"]({bbox});',
    ],
    "interest": [
        'node["tourism"="attraction"]({bbox});',
        'way["tourism"="attraction"]({bbox});',
        'node["tourism"="viewpoint"]({bbox});',
        'way["tourism"="viewpoint"]({bbox});',
        'node["tourism"="museum"]({bbox});',
        'way["tourism"="museum"]({bbox});',
        'node["historic"]({bbox});',
        'way["historic"]({bbox});',
    ],
}

CATEGORY_DEFAULT_NAMES = {
    "food": "Makanan",
    "market": "Toko / Pasar",
    "beach": "Pantai",
    "fuel": "SPBU",
    "interest": "Tempat menarik",
}

_TYPE_TO_CATEGORY = {
    gtype: cat for cat, types in PLACE_CATEGORIES.items() for gtype in types
}


@dataclass(frozen=True)
class SamplePoint:
    lat: float
    lng: float
    heading: float


@dataclass(frozen=True)
class RouteStep:
    instruction: str
    distance_m: int
    duration_s: int


@dataclass(frozen=True)
class RouteInfo:
    points: list[tuple[float, float]]
    distance_m: int
    duration_s: int
    travel_mode: str
    steps: list[RouteStep]
    description: str
    encoded_polyline: str = ""


def require_api_key(*, exit_on_missing: bool = True) -> str:
    load_dotenv()
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not key or key == "your_api_key_here":
        msg = (
            "Error: set GOOGLE_MAPS_API_KEY in .env or the environment.\n"
            "Copy .env.example to .env and paste your Google Maps API key."
        )
        if exit_on_missing:
            print(msg, file=sys.stderr)
            sys.exit(1)
        raise RuntimeError(msg)
    return key


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        dlon
    )
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def parse_duration_seconds(value: str | None) -> int:
    if not value:
        return 0
    if value.endswith("s"):
        try:
            return int(float(value[:-1]))
        except ValueError:
            return 0
    return 0


def format_duration(seconds: int) -> str:
    hours, rem = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_distance(meters: float | int) -> str:
    meters = float(meters)
    if meters >= 1000:
        return f"{meters / 1000:.1f} km"
    return f"{int(round(meters))} m"


def fetch_route(
    origin: str,
    destination: str,
    api_key: str,
    mode: str,
) -> RouteInfo:
    if mode not in TRAVEL_MODES:
        raise ValueError(f"Unknown mode: {mode}")
    travel_mode = TRAVEL_MODES[mode]
    body: dict = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": travel_mode,
        "polylineQuality": "HIGH_QUALITY",
        "languageCode": "id",
        "units": "METRIC",
    }
    if travel_mode in ("DRIVE", "TWO_WHEELER"):
        body["routingPreference"] = "TRAFFIC_AWARE"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": ",".join(
            [
                "routes.duration",
                "routes.distanceMeters",
                "routes.description",
                "routes.polyline.encodedPolyline",
                "routes.legs.steps.navigationInstruction",
                "routes.legs.steps.distanceMeters",
                "routes.legs.steps.staticDuration",
                "routes.legs.steps.localizedValues",
            ]
        ),
    }
    resp = requests.post(ROUTES_URL, headers=headers, json=body, timeout=60)
    if resp.status_code != 200:
        try:
            detail = resp.json()
            message = detail.get("error", {}).get("message") or detail
        except ValueError:
            message = resp.text
        raise RuntimeError(
            f"Routes API failed ({resp.status_code}): {message}\n"
            "Enable Routes API for your project:\n"
            "https://console.cloud.google.com/apis/library/routes.googleapis.com"
        )

    data = resp.json()
    routes = data.get("routes") or []
    if not routes:
        raise RuntimeError("Routes API returned no routes")

    route = routes[0]
    encoded = (route.get("polyline") or {}).get("encodedPolyline")
    if not encoded:
        raise RuntimeError("Routes API returned no polyline")

    points = polyline.decode(encoded)
    if len(points) < 2:
        raise RuntimeError("Route polyline is too short to sample")

    steps: list[RouteStep] = []
    for leg in route.get("legs") or []:
        for step in leg.get("steps") or []:
            nav = step.get("navigationInstruction") or {}
            instruction = (nav.get("instructions") or "").strip()
            if not instruction:
                localized = step.get("localizedValues") or {}
                instruction = (
                    (localized.get("distance") or {}).get("text") or "Continue"
                )
            steps.append(
                RouteStep(
                    instruction=instruction,
                    distance_m=int(step.get("distanceMeters") or 0),
                    duration_s=parse_duration_seconds(step.get("staticDuration")),
                )
            )

    return RouteInfo(
        points=points,
        distance_m=int(route.get("distanceMeters") or 0),
        duration_s=parse_duration_seconds(route.get("duration")),
        travel_mode=travel_mode,
        steps=steps,
        description=(route.get("description") or "").strip(),
        encoded_polyline=encoded,
    )


def sample_route(
    points: Iterable[tuple[float, float]],
    interval_m: float,
    max_distance_m: float,
) -> list[SamplePoint]:
    pts = list(points)
    if len(pts) < 2:
        raise ValueError("Need at least two route points")
    if interval_m <= 0:
        raise ValueError("--interval must be > 0")
    if max_distance_m <= 0:
        raise ValueError("--max-distance must be > 0")

    samples: list[tuple[float, float]] = [pts[0]]
    accumulated = 0.0
    next_sample_at = interval_m
    prev = pts[0]

    for curr in pts[1:]:
        segment_len = geodesic(prev, curr).meters
        if segment_len == 0:
            prev = curr
            continue

        while accumulated + segment_len >= next_sample_at and next_sample_at <= max_distance_m:
            remain = next_sample_at - accumulated
            ratio = remain / segment_len
            lat = prev[0] + (curr[0] - prev[0]) * ratio
            lng = prev[1] + (curr[1] - prev[1]) * ratio
            samples.append((lat, lng))
            next_sample_at += interval_m

        accumulated += segment_len
        prev = curr
        if accumulated >= max_distance_m:
            break

    if not samples:
        raise RuntimeError("No sample points generated")

    result: list[SamplePoint] = []
    for i, (lat, lng) in enumerate(samples):
        if i < len(samples) - 1:
            nxt = samples[i + 1]
            hdg = bearing(lat, lng, nxt[0], nxt[1])
        elif result:
            hdg = result[-1].heading
        else:
            hdg = bearing(lat, lng, pts[1][0], pts[1][1]) if len(pts) > 1 else 0.0
        result.append(SamplePoint(lat=lat, lng=lng, heading=hdg))
    return result


def route_bbox(
    route_points: list[tuple[float, float]],
    *,
    pad_deg: float = 0.02,
) -> tuple[float, float, float, float]:
    lats = [p[0] for p in route_points]
    lngs = [p[1] for p in route_points]
    return (
        min(lats) - pad_deg,
        min(lngs) - pad_deg,
        max(lats) + pad_deg,
        max(lngs) + pad_deg,
    )


def route_progress_m(route_points: list[tuple[float, float]]) -> list[float]:
    progress = [0.0]
    for i in range(1, len(route_points)):
        progress.append(
            progress[-1] + geodesic(route_points[i - 1], route_points[i]).meters
        )
    return progress


def nearest_on_route(
    lat: float,
    lng: float,
    route_points: list[tuple[float, float]],
    progress: list[float],
) -> tuple[float, float]:
    """Return (distance_along_route_m, offset_from_route_m)."""
    point = (lat, lng)
    step = max(1, len(route_points) // 2000)
    coarse_best_i = 0
    best_offset = float("inf")
    for i in range(0, len(route_points), step):
        offset = geodesic(point, route_points[i]).meters
        if offset < best_offset:
            best_offset = offset
            coarse_best_i = i

    start = max(0, coarse_best_i - step)
    end = min(len(route_points), coarse_best_i + step + 1)
    best_i = coarse_best_i
    for i in range(start, end):
        offset = geodesic(point, route_points[i]).meters
        if offset < best_offset:
            best_offset = offset
            best_i = i
    return progress[best_i], best_offset


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def nearest_on_route_fast(
    lat: float,
    lng: float,
    route_points: list[tuple[float, float]],
    progress: list[float],
) -> tuple[float, float]:
    """Faster nearest-vertex using haversine (good enough for corridor filter)."""
    step = max(1, len(route_points) // 2500)
    coarse_best_i = 0
    best_offset = float("inf")
    for i in range(0, len(route_points), step):
        rp = route_points[i]
        offset = _haversine_m(lat, lng, rp[0], rp[1])
        if offset < best_offset:
            best_offset = offset
            coarse_best_i = i

    start = max(0, coarse_best_i - step)
    end = min(len(route_points), coarse_best_i + step + 1)
    best_i = coarse_best_i
    for i in range(start, end):
        rp = route_points[i]
        offset = _haversine_m(lat, lng, rp[0], rp[1])
        if offset < best_offset:
            best_offset = offset
            best_i = i
    return progress[best_i], best_offset


def _classify_osm_tags(tags: dict) -> str | None:
    amenity = tags.get("amenity")
    shop = tags.get("shop")
    natural = tags.get("natural")
    tourism = tags.get("tourism")
    if amenity in {"restaurant", "cafe", "fast_food", "food_court"}:
        return "food"
    if shop in {"supermarket", "convenience", "marketplace"} or amenity == "marketplace":
        return "market"
    if natural == "beach":
        return "beach"
    if amenity == "fuel":
        return "fuel"
    if tourism in {"attraction", "viewpoint", "museum"} or "historic" in tags:
        return "interest"
    return None


def _element_to_place(el: dict, category: str | None = None) -> dict | None:
    if el.get("type") == "node":
        lat, lng = el.get("lat"), el.get("lon")
    else:
        center = el.get("center") or {}
        lat, lng = center.get("lat"), center.get("lon")
    if lat is None or lng is None:
        return None
    tags = el.get("tags") or {}
    cat = category or _classify_osm_tags(tags)
    if not cat:
        return None
    name = (
        tags.get("name")
        or tags.get("brand")
        or tags.get("operator")
        or CATEGORY_DEFAULT_NAMES.get(cat, cat)
    )
    address_parts = [
        tags.get("addr:street"),
        tags.get("addr:housenumber"),
        tags.get("addr:city") or tags.get("addr:district"),
    ]
    address = ", ".join(p for p in address_parts if p)
    osm_type = el.get("type")
    osm_id = el.get("id")
    return {
        "id": f"osm:{osm_type}/{osm_id}",
        "name": name,
        "category": cat,
        "address": address,
        "lat": float(lat),
        "lng": float(lng),
        "maps_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "source": "osm",
        "rating": None,
    }


def overpass_query(query: str) -> list[dict]:
    last_error: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(
                url,
                data={"data": query},
                timeout=120,
                headers={"User-Agent": "geomaps-companion/1.0"},
            )
            resp.raise_for_status()
            return resp.json().get("elements") or []
        except (requests.RequestException, ValueError, KeyError) as exc:
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(f"Overpass/OSM query failed: {last_error}")


def fetch_places_osm(
    route_points: list[tuple[float, float]],
    categories: list[str] | None = None,
    *,
    pad_deg: float = 0.02,
) -> list[dict]:
    """Fetch OSM places for selected categories in the route bounding box."""
    cats = categories or list(OSM_PLACE_QUERIES)
    unknown = [c for c in cats if c not in OSM_PLACE_QUERIES]
    if unknown:
        raise ValueError(f"Unknown categories: {', '.join(unknown)}")

    south, west, north, east = route_bbox(route_points, pad_deg=pad_deg)
    bbox = f"{south},{west},{north},{east}"
    parts: list[str] = []
    for cat in cats:
        for frag in OSM_PLACE_QUERIES[cat]:
            parts.append(frag.format(bbox=bbox))

    query = f"""
    [out:json][timeout:90];
    (
      {"".join(parts)}
    );
    out center tags;
    """
    elements = overpass_query(query)
    places: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        place = _element_to_place(el)
        if not place:
            continue
        if place["category"] not in cats:
            continue
        if place["id"] in seen:
            continue
        seen.add(place["id"])
        places.append(place)
    return places


def _google_place_to_dict(place: dict, category: str) -> dict | None:
    loc = place.get("location") or {}
    lat = loc.get("latitude")
    lng = loc.get("longitude")
    if lat is None or lng is None:
        return None
    place_id = place.get("id") or ""
    if not place_id:
        return None
    name = ((place.get("displayName") or {}).get("text") or CATEGORY_DEFAULT_NAMES.get(category, category)).strip()
    address = (place.get("formattedAddress") or "").strip()
    maps_url = (place.get("googleMapsUri") or "").strip()
    if not maps_url:
        maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    # Prefer category from request; refine from types when possible.
    cat = category
    for t in place.get("types") or []:
        if t in _TYPE_TO_CATEGORY:
            cat = _TYPE_TO_CATEGORY[t]
            break
    return {
        "id": place_id,
        "name": name,
        "category": cat,
        "address": address,
        "lat": float(lat),
        "lng": float(lng),
        "maps_url": maps_url,
        "source": "google",
        "rating": place.get("rating"),
    }


def _nearby_search_google(
    lat: float,
    lng: float,
    *,
    api_key: str,
    included_types: list[str],
    radius_m: float,
) -> list[dict]:
    body = {
        "includedTypes": included_types[:5],
        "maxResultCount": 20,
        "rankPreference": "DISTANCE",
        "languageCode": "id",
        "regionCode": "ID",
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": float(min(radius_m, 50000.0)),
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
            "https://console.cloud.google.com/apis/library/places.googleapis.com"
        )
    return resp.json().get("places") or []


def _sample_centers(
    route_points: list[tuple[float, float]],
    interval_m: float,
) -> list[tuple[float, float]]:
    samples = sample_route(route_points, interval_m, 10**12)
    centers = [(s.lat, s.lng) for s in samples]
    if route_points and centers and centers[-1] != route_points[-1]:
        centers.append(route_points[-1])
    return centers


def fetch_places_google(
    route_points: list[tuple[float, float]],
    api_key: str,
    categories: list[str] | None = None,
    *,
    search_interval_m: float = 8000.0,
    radius_m: float = 4500.0,
    request_delay: float = 0.05,
) -> list[dict]:
    """
    Find places near the route using Google Places Nearby Search (New).

    Samples along the polyline and queries each category's place types.
    """
    cats = categories or list(PLACE_CATEGORIES)
    unknown = [c for c in cats if c not in PLACE_CATEGORIES]
    if unknown:
        raise ValueError(f"Unknown categories: {', '.join(unknown)}")
    if search_interval_m <= 0 or radius_m <= 0:
        raise ValueError("search_interval_m and radius_m must be > 0")

    centers = _sample_centers(route_points, search_interval_m)
    found: dict[str, dict] = {}

    for lat, lng in centers:
        for cat in cats:
            types = PLACE_CATEGORIES[cat]
            places = _nearby_search_google(
                lat, lng, api_key=api_key, included_types=types, radius_m=radius_m
            )
            for place in places:
                item = _google_place_to_dict(place, cat)
                if not item:
                    continue
                if item["category"] not in cats:
                    continue
                if item["id"] in found:
                    continue
                found[item["id"]] = item
            if request_delay > 0:
                time.sleep(request_delay)

    return list(found.values())


def filter_places_to_route(
    raw_places: list[dict],
    route_points: list[tuple[float, float]],
    *,
    max_distance_m: float = 0.0,
    max_offset_m: float = 2000.0,
) -> list[dict]:
    """Keep places near the road corridor; attach along-route distance."""
    if not route_points:
        return []
    progress = route_progress_m(route_points)
    out: list[dict] = []
    for place in raw_places:
        along_m, offset_m = nearest_on_route_fast(
            float(place["lat"]),
            float(place["lng"]),
            route_points,
            progress,
        )
        if max_distance_m > 0 and along_m > max_distance_m:
            continue
        if offset_m > max_offset_m:
            continue
        item = dict(place)
        item["distance_along_route_m"] = round(along_m, 1)
        item["offset_from_route_m"] = round(offset_m, 1)
        out.append(item)
    out.sort(key=lambda p: p.get("distance_along_route_m", 0))
    return out


def downsample_points(
    points: list[tuple[float, float]],
    max_points: int = 2500,
) -> list[list[float]]:
    if len(points) <= max_points:
        return [[lat, lng] for lat, lng in points]
    step = max(1, len(points) // max_points)
    coords = [[lat, lng] for lat, lng in points[::step]]
    if coords[-1] != [points[-1][0], points[-1][1]]:
        coords.append([points[-1][0], points[-1][1]])
    return coords
