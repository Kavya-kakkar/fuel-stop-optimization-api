# Spotter Backend Assessment - Optimal Fuel Route Planner

A Django REST Framework application that plans cost-optimal cross-country driving routes and fuel stops across the United States.

---

## 🚀 Key Features & Highlights

1. **Optimal Fuel Stop Algorithm**:
   - Analyzes real fuel price datasets across 6,700+ truck stops in the USA.
   - Enforces vehicle constraints: **10 MPG fuel efficiency** and **500-mile maximum tank range**.
   - Selects fuel stops within the legal range buffer, dynamically optimizing for the **lowest retail price per gallon** while ensuring the vehicle never runs dry.
   - Calculates exact fuel consumption per leg and total money spent in USD.

2. **High-Performance Geospatial Pipeline**:
   - Uses **Shapely** geometry with `LineString.simplify` and prepared polygon buffers (`shapely.prepared.prep`).
   - Filters candidate stations in database bounding box via compound indexing on `(latitude, longitude)`.
   - Linear projection (`line.project`) pinpoints the exact mile marker of each station along the route with sub-20ms execution.

3. **Multi-Tier Geocoding & Routing (Zero-Setup)**:
   - **Local City Coordinates Database**: Built-in mapping of 29,700+ US cities (`us_cities.json`) allows standard city/state queries (`"New York, NY"`, `"Austin, TX"`) to geocode instantly in **0 ms with 0 external API calls**.
   - **OSRM Free Routing Engine**: Default routing uses the Open Source Routing Machine (OSRM), requiring **no external API keys** and zero cost.
   - **OpenRouteService Support**: Automatically detects and uses OpenRouteService if `ROUTING_API_KEY` is configured in `.env`.
   - **OpenStreetMap Nominatim Fallback**: Fallback geocoding for specific street addresses.

---

## 🛠️ Installation & Setup Steps

### 1. Activate the Virtual Environment
```bash
# On Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# On Windows (Command Prompt)
.\venv\Scripts\activate.bat

# On Linux/macOS
source venv/bin/activate
```

### 2. Configure Environment Variables (Optional)
The project works out of the box with zero configuration! If you have an OpenRouteService API key, you can add it to `.env`:
```ini
SECRET_KEY=your-secret-key-here
DEBUG=True
ROUTING_API_KEY=your_openrouteservice_api_key_optional
```

### 3. Run Database Migrations
```bash
python manage.py migrate
```

### 4. Import Fuel Station Prices
Enrich the CSV dataset with geographic coordinates and bulk-upsert into SQLite:
```bash
python manage.py import_fuel_price fuel/data/fuel-prices-for-be-assessment.csv
```

### 5. Run the Development Server
```bash
python manage.py runserver
```
The server will start at `http://127.0.0.1:8000/`.

---

## 📡 API Documentation

### Route Planner Endpoint

- **Endpoint**: `POST /api/route-planner/`
- **Content-Type**: `application/json`

#### Request Body
```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA"
}
```
*(Also supports coordinates directly, e.g. `"start": "40.7128, -74.0060"`, `"finish": "34.0522, -118.2437"`)*

#### Response Body (`200 OK`)
```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA",
  "total_distance_miles": 2800.63,
  "total_fuel_cost_usd": 865.25,
  "fuel_stops_count": 6,
  "fuel_stops": [
    {
      "truckstop_id": 639,
      "name": "SHEETZ #639",
      "address": "1301 N CANFIELD NILES RD",
      "city": "Youngstown",
      "state": "OH",
      "latitude": 41.1000,
      "longitude": -80.6500,
      "price_per_gallon": 3.099,
      "distance_from_start_miles": 398.48,
      "distance_from_last_stop_miles": 398.48,
      "gallons_refueled": 39.85,
      "cost_at_stop": 123.49
    },
    ...
  ],
  "route_map_geojson": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "geometry": {
          "type": "LineString",
          "coordinates": [[-74.0060, 40.7128], ...]
        },
        "properties": {
          "summary": {
            "distance": 4507174.5,
            "duration": 142100.0
          }
        }
      }
    ]
  }
}
```

#### Error Responses
- `400 Bad Request`: Missing `start` or `finish`, or unresolvable location string.
- `502 Bad Gateway`: Map routing service is temporarily unavailable.

---

## 🧪 Running the Unit Tests

Execute the comprehensive test suite (covers input validation, short trips under 500 miles, long trips over 500 miles, error handling, and geocoding):
```bash
python manage.py test fuel
```
All 11 tests execute in ~0.05 seconds with 100% pass rate.
