import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests
from django.conf import settings
from shapely.geometry import LineString, Point
from shapely.prepared import prep

from fuel.models import Fuelstation

logger = logging.getLogger(__name__)

# Constants
VEHICLE_RANGE_MILES = 500.0  # Max tank range in miles
MPG = 10.0  # Fuel efficiency: miles per gallon
ROUTE_BUFFER_DEGREES = 0.18  # Approximate 12 miles buffer for highway stops
DEFAULT_GAS_PRICE = 3.50  # Fallback price per gallon in USD

_CITY_COORDS_CACHE: Optional[Dict[str, List[float]]] = None


def _get_city_coords_map() -> Dict[str, List[float]]:
    """Loads and caches the US city coordinates lookup dictionary."""
    global _CITY_COORDS_CACHE
    if _CITY_COORDS_CACHE is not None:
        return _CITY_COORDS_CACHE

    data_dir = Path(__file__).resolve().parent / "data"
    json_path = data_dir / "us_cities.json"
    coords_map: Dict[str, List[float]] = {}

    if json_path.exists():
        try:
            with open(json_path, mode="r", encoding="utf-8") as f:
                coords_map = json.load(f)
        except Exception as e:
            logger.warning("Failed to load us_cities.json: %s", e)

    # Built-in fallback coordinates for key cities and unlisted locations
    manual_coords = {
        "new york|ny": [40.7128, -74.0060],
        "los angeles|ca": [34.0522, -118.2437],
        "chicago|il": [41.8781, -87.6298],
        "houston|tx": [29.7604, -95.3698],
        "austin|tx": [30.2672, -97.7431],
        "san antonio|tx": [29.4241, -98.4936],
        "dallas|tx": [32.7767, -96.7970],
        "philadelphia|pa": [39.9526, -75.1652],
        "phoenix|az": [33.4484, -112.0740],
        "san diego|ca": [32.7157, -117.1611],
        "san francisco|ca": [37.7749, -122.4194],
        "seattle|wa": [47.6062, -122.3321],
        "miami|fl": [25.7617, -80.1918],
        "atlanta|ga": [33.7490, -84.3880],
        "boston|ma": [42.3601, -71.0589],
        "denver|co": [39.7392, -104.9903],
        "port wentworth|ga": [32.1494, -81.1632],
        "henrico|va": [37.5385, -77.3486],
        "brookpark|oh": [41.3981, -81.8251],
        "university park|il": [41.4442, -87.6975],
        "evergreen|al": [31.4338, -86.9544],
        "elizabethport|nj": [40.6487, -74.1932],
    }
    coords_map.update(manual_coords)

    _CITY_COORDS_CACHE = coords_map
    return _CITY_COORDS_CACHE


def geocode_location(location_input: Union[str, List[float], Tuple[float, float]]) -> List[float]:
    """
    Geocodes a location into [longitude, latitude].
    Supports:
      1. Coordinate list/tuple: [lon, lat] or [lat, lon]
      2. Coordinate string: '40.7128, -74.0060'
      3. City, State string: 'New York, NY', 'Austin, TX'
         - Checked against local offline database first (0 network calls, 0ms latency)
         - Falls back to OpenStreetMap Nominatim if unlisted or street address
    """
    if not location_input:
        raise ValueError("Location string or coordinate cannot be empty.")

    # 1. Direct coordinate array/tuple
    if isinstance(location_input, (list, tuple)) and len(location_input) == 2:
        val1, val2 = float(location_input[0]), float(location_input[1])
        # If passed as [lat, lon] where lat is between -90 and 90
        if -90 <= val1 <= 90 and -180 <= val2 <= 180 and (abs(val1) < abs(val2) or abs(val2) > 90):
            return [val2, val1]  # Return [lon, lat]
        return [val1, val2]

    location_str = str(location_input).strip()

    # 2. String representation of coordinates (e.g. "40.7128, -74.0060")
    coord_match = re.match(r"^[-+]?([0-9]*\.[0-9]+|[0-9]+)\s*,\s*[-+]?([0-9]*\.[0-9]+|[0-9]+)$", location_str)
    if coord_match:
        c1, c2 = float(coord_match.group(1)), float(coord_match.group(2))
        # Convention: "lat, lon"
        if -90 <= c1 <= 90 and -180 <= c2 <= 180:
            return [c2, c1]
        return [c1, c2]

    # 3. Check local offline city database
    city_map = _get_city_coords_map()
    normalized = location_str.lower()
    parts = [p.strip() for p in normalized.split(",") if p.strip()]

    if len(parts) >= 2:
        city_name = parts[0]
        state_code = parts[1].split()[0]  # E.g. "TX" from "TX 78701"
        key = f"{city_name}|{state_code.upper()}"
        if key in city_map:
            lat, lon = city_map[key]
            return [lon, lat]
    elif len(parts) == 1:
        # Exact city match only if single part
        for k, coords in city_map.items():
            c_name, _ = k.split("|")
            if c_name == parts[0]:
                return [coords[1], coords[0]]

    # 4. Fallback to OpenStreetMap Nominatim API
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": location_str,
            "format": "json",
            "countrycodes": "us",
            "limit": 1,
        }
        headers = {"User-Agent": "SpotterRoutePlanner/1.0 (assessment@spotter.com)"}
        res = requests.get(url, params=params, headers=headers, timeout=6)
        if res.status_code == 200:
            data = res.json()
            if data and len(data) > 0:
                return [float(data[0]["lon"]), float(data[0]["lat"])]
    except Exception as exc:
        logger.warning("Nominatim geocoding failed for '%s': %s", location_str, exc)

    # 5. OpenRouteService geocode fallback if API key configured
    ors_api_key = getattr(settings, "ROUTING_API_KEY", "") or os.getenv("ROUTING_API_KEY", "")
    if ors_api_key and ors_api_key != "YOUR_OPENROUTESERVICE_API_KEY":
        try:
            ors_url = "https://api.openrouteservice.org/geocode/search"
            res = requests.get(
                ors_url,
                params={"api_key": ors_api_key, "text": location_str, "boundary.country": "USA"},
                timeout=6,
            )
            if res.status_code == 200:
                feats = res.json().get("features", [])
                if feats:
                    return feats[0]["geometry"]["coordinates"]
        except Exception as exc:
            logger.warning("ORS geocoding failed for '%s': %s", location_str, exc)

    raise ValueError(f"Unable to geocode location '{location_str}'. Please provide a valid US city/state or coordinates.")


def get_route(start_coords: List[float], end_coords: List[float]) -> Dict[str, Any]:
    """
    Fetches the driving route between start_coords and end_coords as a GeoJSON FeatureCollection.
    Uses OSRM public API by default (free, no API key required), with OpenRouteService
    as an option when an API key is provided.
    """
    ors_api_key = getattr(settings, "ROUTING_API_KEY", "") or os.getenv("ROUTING_API_KEY", "")

    # 1. Attempt OpenRouteService if valid API key is present
    if ors_api_key and ors_api_key != "YOUR_OPENROUTESERVICE_API_KEY":
        try:
            url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
            headers = {"Authorization": ors_api_key, "Content-Type": "application/json"}
            body = {"coordinates": [start_coords, end_coords]}
            response = requests.post(url, json=body, headers=headers, timeout=12)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.warning("OpenRouteService request failed, falling back to OSRM: %s", e)

    # 2. Free OSRM Routing Engine (Default, fast, no API key needed)
    start_lon, start_lat = start_coords[0], start_coords[1]
    end_lon, end_lat = end_coords[0], end_coords[1]

    osrm_url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{start_lon},{start_lat};{end_lon},{end_lat}"
        f"?overview=full&geometries=geojson"
    )

    try:
        resp = requests.get(osrm_url, timeout=12)
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, dict) or data.get("code") != "Ok" or not data.get("routes"):
            msg = data.get("message", "Route not found") if isinstance(data, dict) else "Invalid response format"
            raise ValueError(f"Routing engine could not find a path: {msg}")

        route = data["routes"][0]
        line_coords = route["geometry"]["coordinates"]
        dist_meters = float(route["distance"])
        duration_seconds = float(route.get("duration", 0))

        # Return standardized GeoJSON FeatureCollection
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": line_coords,
                    },
                    "properties": {
                        "summary": {
                            "distance": dist_meters,
                            "duration": duration_seconds,
                        }
                    },
                }
            ],
        }
    except requests.RequestException as req_err:
        raise ConnectionError(f"Map routing service unavailable: {req_err}") from req_err


def calculate_fuel_stops(route_geojson: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculates optimal fuel stops along the route based on fuel prices from the Fuelstation database.
    Vehicle Constraints:
      - Fuel efficiency: 10 miles per gallon (MPG).
      - Maximum tank range: 500 miles (Tank capacity: 50 gallons).
      - Starts with full tank (500 miles range).
    """
    feature = route_geojson["features"][0]
    line_coords = feature["geometry"]["coordinates"]
    total_distance_meters = feature["properties"]["summary"]["distance"]
    total_distance_miles = total_distance_meters * 0.000621371

    if total_distance_miles <= 0:
        return {
            "total_distance_miles": 0.0,
            "total_fuel_cost": 0.0,
            "fuel_stops": [],
        }

    # Construct Shapely geometry and simplify to achieve sub-millisecond distance queries
    route_line = LineString(line_coords)
    simplified_line = route_line.simplify(0.005, preserve_topology=False)
    route_buffer_polygon = prep(simplified_line.buffer(ROUTE_BUFFER_DEGREES))

    # Bounding box filter for stations in database
    lons = [c[0] for c in line_coords]
    lats = [c[1] for c in line_coords]
    min_lon, max_lon = min(lons) - 0.25, max(lons) + 0.25
    min_lat, max_lat = min(lats) - 0.25, max(lats) + 0.25

    candidate_stations = Fuelstation.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False,
        latitude__gte=min_lat,
        latitude__lte=max_lat,
        longitude__gte=min_lon,
        longitude__lte=max_lon,
    ).values("truckstop_id", "name", "address", "city", "state", "retail_price", "latitude", "longitude")

    # Filter stations along route buffer and calculate mile marker position
    stations_along_route = []
    for station in candidate_stations:
        p = Point(station["longitude"], station["latitude"])
        if route_buffer_polygon.contains(p):
            norm_distance = simplified_line.project(p, normalized=True)
            mile_marker = norm_distance * total_distance_miles
            stations_along_route.append({
                "truckstop_id": station["truckstop_id"],
                "name": station["name"],
                "address": station["address"],
                "city": station["city"],
                "state": station["state"],
                "retail_price": float(station["retail_price"]),
                "latitude": station["latitude"],
                "longitude": station["longitude"],
                "mile_marker": mile_marker,
            })

    # Sort stations sequentially along the travel direction
    stations_along_route.sort(key=lambda s: s["mile_marker"])

    # CASE 1: Route is under 500 miles (No refueling stops required)
    if total_distance_miles <= VEHICLE_RANGE_MILES:
        total_gallons_needed = total_distance_miles / MPG
        # Baseline fuel price from nearest stations or national average
        if stations_along_route:
            baseline_price = min(s["retail_price"] for s in stations_along_route[:5])
        else:
            baseline_price = DEFAULT_GAS_PRICE

        total_cost = total_gallons_needed * baseline_price
        return {
            "total_distance_miles": round(total_distance_miles, 2),
            "total_fuel_cost": round(total_cost, 2),
            "fuel_stops": [],
        }

    # CASE 2: Route exceeds 500 miles (Refueling stops required)
    stops = []
    current_pos = 0.0

    while (total_distance_miles - current_pos) > VEHICLE_RANGE_MILES:
        max_reachable_mile = current_pos + VEHICLE_RANGE_MILES

        # Candidate stations within reachable tank range
        reachable_window = [
            s for s in stations_along_route
            if current_pos < s["mile_marker"] <= max_reachable_mile
        ]

        if not reachable_window:
            # Fallback if sparse station gap: proceed to furthest station ahead if any
            ahead = [s for s in stations_along_route if s["mile_marker"] > current_pos]
            if ahead:
                reachable_window = [ahead[0]]
            else:
                break

        # Refuel window: prefer stopping when tank has used 60-95% capacity (300 to 475 miles)
        preferred_candidates = [
            s for s in reachable_window
            if s["mile_marker"] >= (current_pos + VEHICLE_RANGE_MILES * 0.60)
        ]
        pool = preferred_candidates if preferred_candidates else reachable_window

        # Select the most cost-effective station (lowest retail price, tiebreaker: further ahead)
        chosen = min(pool, key=lambda s: (s["retail_price"], -s["mile_marker"]))

        segment_distance = chosen["mile_marker"] - current_pos
        gallons_refueled = segment_distance / MPG
        cost_at_stop = gallons_refueled * chosen["retail_price"]

        stops.append({
            "truckstop_id": chosen["truckstop_id"],
            "name": chosen["name"],
            "address": chosen["address"],
            "city": chosen["city"],
            "state": chosen["state"],
            "latitude": chosen["latitude"],
            "longitude": chosen["longitude"],
            "price_per_gallon": chosen["retail_price"],
            "distance_from_start_miles": round(chosen["mile_marker"], 2),
            "distance_from_last_stop_miles": round(segment_distance, 2),
            "gallons_refueled": round(gallons_refueled, 2),
            "cost_at_stop": round(cost_at_stop, 2),
        })

        current_pos = chosen["mile_marker"]

    # Calculate fuel consumed for final segment to destination
    final_segment_distance = total_distance_miles - current_pos
    final_gallons = final_segment_distance / MPG

    if stops:
        final_price = stops[-1]["price_per_gallon"]
        final_cost = final_gallons * final_price
        total_fuel_cost = sum(s["cost_at_stop"] for s in stops) + final_cost
    else:
        total_fuel_cost = (total_distance_miles / MPG) * DEFAULT_GAS_PRICE

    return {
        "total_distance_miles": round(total_distance_miles, 2),
        "total_fuel_cost": round(total_fuel_cost, 2),
        "fuel_stops": stops,
    }