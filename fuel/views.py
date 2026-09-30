import logging
from django.http import HttpResponse
from django.views import View
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import calculate_fuel_stops, geocode_location, get_route

logger = logging.getLogger(__name__)


class RoutePlannerView(APIView):
    """
    API endpoint that accepts 'start' and 'finish' locations within the USA,
    fetches the driving route geometry, determines cost-optimal fuel stops,
    and returns total distance, fuel stops, and overall fuel expenditure.
    """

    def get(self, request):
        # Support GET with query parameters ?start=...&finish=...
        start_location = request.query_params.get("start")
        finish_location = request.query_params.get("finish")

        if start_location and finish_location:
            return self._process_route(start_location, finish_location)

        # Informational schema when accessed without query parameters
        return Response(
            {
                "service": "Optimal Fuel Route Planner API",
                "status": "online",
                "instructions": "Send a POST request with JSON payload containing 'start' and 'finish' locations, or supply query parameters ?start=...&finish=...",
                "endpoints": {
                    "POST /api/route-planner/": {
                        "payload": {
                            "start": "New York, NY",
                            "finish": "Los Angeles, CA"
                        }
                    },
                    "GET /api/route-planner/?start=Austin,+TX&finish=Dallas,+TX": "Quick URL query format",
                    "GET /": "Interactive Visual Web Dashboard"
                },
                "vehicle_parameters": {
                    "efficiency_mpg": 10.0,
                    "max_tank_range_miles": 500.0,
                    "tank_capacity_gallons": 50.0
                }
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        if not isinstance(request.data, dict):
            return Response(
                {"error": "Invalid request payload. Expected a JSON object with 'start' and 'finish' keys."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_location = request.data.get("start")
        finish_location = request.data.get("finish")

        if not start_location or not str(start_location).strip():
            return Response(
                {"error": "Field 'start' is required and cannot be empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not finish_location or not str(finish_location).strip():
            return Response(
                {"error": "Field 'finish' is required and cannot be empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return self._process_route(str(start_location).strip(), str(finish_location).strip())

    def _process_route(self, start_location: str, finish_location: str):
        try:
            # 1. Geocode locations to [lon, lat]
            start_coords = geocode_location(start_location)
            end_coords = geocode_location(finish_location)

            # 2. Get route from map service (1 API call)
            route_geojson = get_route(start_coords, end_coords)

            # 3. Calculate optimal fuel stops & total fuel cost
            fuel_data = calculate_fuel_stops(route_geojson)

            # 4. Construct response payload
            return Response(
                {
                    "start": start_location,
                    "finish": finish_location,
                    "total_distance_miles": fuel_data["total_distance_miles"],
                    "total_fuel_cost_usd": fuel_data["total_fuel_cost"],
                    "fuel_stops_count": len(fuel_data["fuel_stops"]),
                    "fuel_stops": fuel_data["fuel_stops"],
                    "route_map_geojson": route_geojson,
                },
                status=status.HTTP_200_OK,
            )

        except ValueError as val_err:
            logger.warning("Validation error in RoutePlannerView: %s", val_err)
            return Response(
                {"error": str(val_err)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ConnectionError as conn_err:
            logger.error("Upstream routing service connection failed: %s", conn_err)
            return Response(
                {"error": "Upstream map routing service is temporarily unavailable. Please try again.", "details": str(conn_err)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            logger.exception("Unexpected error in RoutePlannerView: %s", e)
            return Response(
                {"error": "An unexpected error occurred while processing the route.", "details": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RoutePlannerDashboardView(View):
    """
    Renders an interactive web UI allowing users to visually plan routes,
    view fuel stops on Leaflet map, and inspect distance and fuel economics.
    """

    def get(self, request):
        html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Optimal Fuel Route Planner | Spotter Assessment</title>
    <!-- Leaflet CSS -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-main: #0b0f19;
            --bg-card: #131b2e;
            --bg-input: #1a243b;
            --border: #23304d;
            --primary: #3b82f6;
            --primary-hover: #2563eb;
            --accent: #10b981;
            --accent-fuel: #f59e0b;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: var(--bg-main);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        header {
            background-color: var(--bg-card);
            border-bottom: 1px solid var(--border);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .logo-area { display: flex; align-items: center; gap: 0.75rem; }
        .logo-badge {
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            padding: 0.4rem 0.75rem;
            border-radius: 8px;
            font-weight: 700;
            font-size: 0.9rem;
            letter-spacing: 0.5px;
        }
        .main-container {
            display: grid;
            grid-template-columns: 420px 1fr;
            flex: 1;
            height: calc(100vh - 72px);
        }
        .sidebar {
            background-color: var(--bg-card);
            border-right: 1px solid var(--border);
            padding: 1.5rem;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 1.25rem;
        }
        .map-container {
            position: relative;
            height: 100%;
            width: 100%;
        }
        #map { height: 100%; width: 100%; background: #0e1526; }
        .form-group { display: flex; flex-direction: column; gap: 0.4rem; }
        label { font-size: 0.825rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
        input[type="text"] {
            background-color: var(--bg-input);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 0.75rem 1rem;
            border-radius: 8px;
            font-size: 0.95rem;
            outline: none;
            transition: border-color 0.2s;
        }
        input[type="text"]:focus { border-color: var(--primary); }
        .presets { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.25rem; }
        .preset-btn {
            background: rgba(59, 130, 246, 0.1);
            color: #93c5fd;
            border: 1px solid rgba(59, 130, 246, 0.25);
            padding: 0.3rem 0.6rem;
            border-radius: 6px;
            font-size: 0.75rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        .preset-btn:hover { background: rgba(59, 130, 246, 0.25); color: #fff; }
        .submit-btn {
            background: linear-gradient(135deg, var(--primary), #2563eb);
            color: #fff;
            border: none;
            padding: 0.85rem;
            border-radius: 8px;
            font-weight: 600;
            font-size: 1rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            transition: opacity 0.2s;
        }
        .submit-btn:hover { opacity: 0.95; }
        .submit-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .stats-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.75rem;
        }
        .stat-card {
            background-color: var(--bg-input);
            border: 1px solid var(--border);
            padding: 1rem;
            border-radius: 10px;
        }
        .stat-title { font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; }
        .stat-value { font-size: 1.4rem; font-weight: 700; margin-top: 0.25rem; color: #fff; }
        .stat-sub { font-size: 0.75rem; color: #10b981; margin-top: 0.2rem; }
        .stops-list {
            display: flex;
            flex-direction: column;
            gap: 0.6rem;
            max-height: 280px;
            overflow-y: auto;
            padding-right: 0.25rem;
        }
        .stop-item {
            background: var(--bg-input);
            border: 1px solid var(--border);
            border-left: 4px solid var(--accent-fuel);
            padding: 0.75rem;
            border-radius: 6px;
            font-size: 0.85rem;
        }
        .stop-item-title { font-weight: 600; color: #fff; }
        .stop-item-meta { display: flex; justify-content: space-between; margin-top: 0.3rem; color: var(--text-muted); font-size: 0.8rem; }
        .price-badge { background: rgba(245, 158, 11, 0.15); color: #fbbf24; font-weight: 600; padding: 0.15rem 0.4rem; border-radius: 4px; }
        .spinner {
            border: 3px solid rgba(255,255,255,0.2);
            border-top: 3px solid #fff;
            border-radius: 50%;
            width: 18px;
            height: 18px;
            animation: spin 0.8s linear infinite;
            display: none;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .alert {
            padding: 0.75rem 1rem;
            border-radius: 8px;
            font-size: 0.85rem;
            display: none;
        }
        .alert-error { background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: #f87171; }
        .params-note {
            background: rgba(255, 255, 255, 0.03);
            border: 1px dashed var(--border);
            padding: 0.75rem;
            border-radius: 8px;
            font-size: 0.75rem;
            color: var(--text-muted);
            line-height: 1.4;
        }
    </style>
</head>
<body>
    <header>
        <div class="logo-area">
            <span class="logo-badge">SPOTTER</span>
            <h1 style="font-size: 1.15rem; font-weight: 700;">Optimal Fuel Route Planner</h1>
        </div>
        <div style="font-size: 0.85rem; color: var(--text-muted);">
            Vehicle Spec: <strong>10 MPG</strong> | Max Tank Range: <strong>500 Miles</strong>
        </div>
    </header>

    <div class="main-container">
        <div class="sidebar">
            <div class="form-group">
                <label for="startInput">Start Location</label>
                <input type="text" id="startInput" placeholder="e.g. New York, NY" value="New York, NY" />
            </div>

            <div class="form-group">
                <label for="finishInput">Finish Location</label>
                <input type="text" id="finishInput" placeholder="e.g. Los Angeles, CA" value="Los Angeles, CA" />
            </div>

            <div>
                <label style="font-size: 0.75rem;">Preset Routes:</label>
                <div class="presets">
                    <button type="button" class="preset-btn" onclick="setRoute('New York, NY', 'Los Angeles, CA')">NY ➔ LA (Cross-Country)</button>
                    <button type="button" class="preset-btn" onclick="setRoute('Chicago, IL', 'Miami, FL')">Chicago ➔ Miami</button>
                    <button type="button" class="preset-btn" onclick="setRoute('Austin, TX', 'Dallas, TX')">Austin ➔ Dallas (&lt; 500 mi)</button>
                </div>
            </div>

            <button class="submit-btn" id="submitBtn" onclick="calculateRoute()">
                <span class="spinner" id="spinner"></span>
                <span id="btnText">Plan Optimal Route</span>
            </button>

            <div class="alert alert-error" id="errorBox"></div>

            <div id="resultsSection" style="display: none; flex-direction: column; gap: 1rem;">
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-title">Total Distance</div>
                        <div class="stat-value" id="distValue">0 mi</div>
                        <div class="stat-sub">Optimized route</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-title">Total Fuel Cost</div>
                        <div class="stat-value" id="costValue" style="color: #34d399;">$0.00</div>
                        <div class="stat-sub" id="fuelGallons">0 gal needed</div>
                    </div>
                </div>

                <div>
                    <label id="stopsHeader">Fuel Stops (0)</label>
                    <div class="stops-list" id="stopsList" style="margin-top: 0.5rem;"></div>
                </div>
            </div>

            <div class="params-note">
                💡 <strong>Engineered for Zero Latency:</strong> Geocoding uses local US cities database (0 API calls). Map routing uses free OSRM. Station selection runs linear projection with Shapely spatial indexing.
            </div>
        </div>

        <div class="map-container">
            <div id="map"></div>
        </div>
    </div>

    <!-- Leaflet JS -->
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        // High quality, 100% free tile layers with ZERO API keys or watermarks
        const osmLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            maxZoom: 19
        });

        const darkLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
            attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
            maxZoom: 16
        });

        const streetLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
            attribution: 'Tiles &copy; Esri',
            maxZoom: 19
        });

        const map = L.map('map', {
            center: [39.8283, -98.5795],
            zoom: 4,
            layers: [darkLayer] // Default to clean dark layer with zero watermarks
        });

        // Layer switcher so user can toggle between Dark, OpenStreetMap, and Street modes
        const baseMaps = {
            "Dark Theme (No Watermark)": darkLayer,
            "OpenStreetMap (Detailed)": osmLayer,
            "Satellite/Street": streetLayer
        };
        L.control.layers(baseMaps, null, { position: 'topright' }).addTo(map);

        let routeLayer = null;
        let markersLayer = L.layerGroup().addTo(map);

        function setRoute(start, finish) {
            document.getElementById('startInput').value = start;
            document.getElementById('finishInput').value = finish;
            calculateRoute();
        }

        async function calculateRoute() {
            const start = document.getElementById('startInput').value.trim();
            const finish = document.getElementById('finishInput').value.trim();
            const errorBox = document.getElementById('errorBox');
            const submitBtn = document.getElementById('submitBtn');
            const spinner = document.getElementById('spinner');
            const btnText = document.getElementById('btnText');
            const resultsSection = document.getElementById('resultsSection');

            if (!start || !finish) {
                errorBox.style.display = 'block';
                errorBox.textContent = 'Please enter both Start and Finish locations.';
                return;
            }

            errorBox.style.display = 'none';
            submitBtn.disabled = true;
            spinner.style.display = 'inline-block';
            btnText.textContent = 'Calculating optimal route...';

            try {
                const res = await fetch('/api/route-planner/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ start, finish })
                });

                const data = await res.json();

                if (!res.ok) {
                    throw new Error(data.error || 'Failed to plan route');
                }

                renderResults(data);
            } catch (err) {
                errorBox.style.display = 'block';
                errorBox.textContent = err.message;
            } finally {
                submitBtn.disabled = false;
                spinner.style.display = 'none';
                btnText.textContent = 'Plan Optimal Route';
            }
        }

        function renderResults(data) {
            const resultsSection = document.getElementById('resultsSection');
            resultsSection.style.display = 'flex';

            document.getElementById('distValue').textContent = data.total_distance_miles.toLocaleString() + ' mi';
            document.getElementById('costValue').textContent = '$' + data.total_fuel_cost_usd.toFixed(2);
            const gallons = (data.total_distance_miles / 10).toFixed(1);
            document.getElementById('fuelGallons').textContent = gallons + ' gal total';

            const stopsList = document.getElementById('stopsList');
            stopsList.innerHTML = '';
            document.getElementById('stopsHeader').textContent = `Optimal Fuel Stops (${data.fuel_stops_count})`;

            markersLayer.clearLayers();
            if (routeLayer) map.removeLayer(routeLayer);

            // Draw route line
            if (data.route_map_geojson) {
                routeLayer = L.geoJSON(data.route_map_geojson, {
                    style: { color: '#00d2ff', weight: 6, opacity: 0.9 }
                }).addTo(map);

                const coords = data.route_map_geojson.features[0].geometry.coordinates;
                if (coords.length > 0) {
                    // Start marker (Green)
                    const startCoord = [coords[0][1], coords[0][0]];
                    const startMarker = L.circleMarker(startCoord, {
                        radius: 9,
                        fillColor: '#10b981',
                        color: '#ffffff',
                        weight: 2,
                        opacity: 1,
                        fillOpacity: 1
                    }).bindPopup(`<strong>Trip Origin:</strong> ${data.start}`);
                    markersLayer.addLayer(startMarker);

                    // Destination marker (Red)
                    const endCoord = [coords[coords.length - 1][1], coords[coords.length - 1][0]];
                    const endMarker = L.circleMarker(endCoord, {
                        radius: 9,
                        fillColor: '#ef4444',
                        color: '#ffffff',
                        weight: 2,
                        opacity: 1,
                        fillOpacity: 1
                    }).bindPopup(`<strong>Trip Destination:</strong> ${data.finish}`);
                    markersLayer.addLayer(endMarker);
                }

                map.fitBounds(routeLayer.getBounds(), { padding: [50, 50] });
            }

            if (data.fuel_stops.length === 0) {
                stopsList.innerHTML = '<div style="color: #9ca3af; font-size: 0.85rem; padding: 0.5rem 0;">No fuel stops needed. Trip is under 500 miles (covered by full tank on departure).</div>';
            } else {
                data.fuel_stops.forEach((stop, idx) => {
                    const el = document.createElement('div');
                    el.className = 'stop-item';
                    el.innerHTML = `
                        <div class="stop-item-title">${idx + 1}. ${stop.name}</div>
                        <div style="color: #9ca3af; font-size: 0.78rem;">${stop.city}, ${stop.state} (Mile ${stop.distance_from_start_miles})</div>
                        <div class="stop-item-meta">
                            <span class="price-badge">$${stop.price_per_gallon.toFixed(3)} / gal</span>
                            <span>Refuel: ${stop.gallons_refueled} gal ($${stop.cost_at_stop.toFixed(2)})</span>
                        </div>
                    `;
                    stopsList.appendChild(el);

                    if (stop.latitude && stop.longitude) {
                        const marker = L.circleMarker([stop.latitude, stop.longitude], {
                            radius: 10,
                            fillColor: '#f59e0b',
                            color: '#ffffff',
                            weight: 2.5,
                            opacity: 1,
                            fillOpacity: 0.95
                        });
                        marker.bindPopup(`
                            <div style="font-family: sans-serif; font-size: 0.85rem; line-height: 1.4;">
                                <strong style="color: #1e293b; font-size: 0.95rem;">⛽ Stop ${idx + 1}: ${stop.name}</strong><br/>
                                <span style="color: #64748b;">${stop.address}, ${stop.city}, ${stop.state}</span><br/>
                                <hr style="margin: 0.4rem 0; border: none; border-top: 1px solid #e2e8f0;"/>
                                <strong>Fuel Price:</strong> <span style="color: #d97706; font-weight: 700;">$${stop.price_per_gallon} / gal</span><br/>
                                <strong>At Mile:</strong> ${stop.distance_from_start_miles} mi from origin<br/>
                                <strong>Refuel Amount:</strong> ${stop.gallons_refueled} gal ($${stop.cost_at_stop.toFixed(2)})
                            </div>
                        `);
                        markersLayer.addLayer(marker);
                    }
                });
            }
        }

        // Run initial route on load
        window.addEventListener('DOMContentLoaded', () => {
            calculateRoute();
        });
    </script>
</body>
</html>"""
        return HttpResponse(html_content, content_type="text/html")