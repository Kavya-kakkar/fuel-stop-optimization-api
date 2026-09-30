import csv
import json
from pathlib import Path
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from fuel.models import Fuelstation


class Command(BaseCommand):
    help = "Import fuel station prices from CSV and enrich with geographic coordinates"

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            type=str,
            help="Path to the fuel prices CSV file",
        )
        parser.add_argument(
            "--coords-file",
            type=str,
            default=None,
            help="Optional path to US cities coordinates JSON file",
        )

    def handle(self, *args, **options):
        csv_file_path = Path(options["csv_file"])
        if not csv_file_path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {csv_file_path}"))
            return

        # 1. Load city coordinates map
        coords_map = {}
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        coords_file = options.get("coords_file")
        if coords_file:
            coords_path = Path(coords_file)
        else:
            coords_path = data_dir / "us_cities.json"

        if coords_path.exists():
            self.stdout.write(f"Loading coordinates lookup from {coords_path}...")
            try:
                with open(coords_path, mode="r", encoding="utf-8") as f:
                    coords_map = json.load(f)
            except Exception as e:
                self.stderr.write(self.style.WARNING(f"Failed to load coordinates JSON: {e}"))

        # Fallback to us_cities.csv if JSON wasn't loaded
        if not coords_map:
            csv_cities_path = data_dir / "us_cities.csv"
            if csv_cities_path.exists():
                self.stdout.write(f"Loading coordinates lookup from {csv_cities_path}...")
                with open(csv_cities_path, mode="r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        c_key = f"{r['CITY'].strip().lower()}|{r['STATE_CODE'].strip().upper()}"
                        coords_map[c_key] = [float(r["LATITUDE"]), float(r["LONGITUDE"])]

        # Manual overrides for known unlisted US cities in dataset
        manual_overrides = {
            "port wentworth|ga": (32.1494, -81.1632),
            "henrico|va": (37.5385, -77.3486),
            "brookpark|oh": (41.3981, -81.8251),
            "university park|il": (41.4442, -87.6975),
            "evergreen|al": (31.4338, -86.9544),
            "elizabethport|nj": (40.6487, -74.1932),
        }
        for k, v in manual_overrides.items():
            if k not in coords_map:
                coords_map[k] = list(v)

        # 2. Read and deduplicate CSV rows by truckstop_id
        self.stdout.write(f"Reading fuel prices from {csv_file_path}...")
        stations_data = {}
        with open(csv_file_path, newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            for row in reader:
                try:
                    truckstop_id = int(row["OPIS Truckstop ID"])
                    city = row.get("City", "").strip()
                    state = row.get("State", "").strip().upper()
                    name = row.get("Truckstop Name", "").strip()
                    address = row.get("Address", "").strip()
                    price_str = row.get("Retail Price", "").strip()
                    retail_price = Decimal(price_str)

                    # Coordinate lookup
                    lookup_key = f"{city.lower()}|{state}"
                    coords = coords_map.get(lookup_key)
                    lat = coords[0] if coords else None
                    lon = coords[1] if coords else None

                    # If multiple entries exist with same ID, prefer one with lowest price or valid coords
                    if truckstop_id in stations_data:
                        existing = stations_data[truckstop_id]
                        if retail_price < existing["retail_price"]:
                            existing["retail_price"] = retail_price
                        if not existing["latitude"] and lat:
                            existing["latitude"] = lat
                            existing["longitude"] = lon
                    else:
                        stations_data[truckstop_id] = {
                            "truckstop_id": truckstop_id,
                            "name": name,
                            "address": address,
                            "city": city,
                            "state": state,
                            "retail_price": retail_price,
                            "latitude": lat,
                            "longitude": lon,
                        }
                except (ValueError, KeyError) as err:
                    continue

        total_stations = len(stations_data)
        coords_count = sum(1 for s in stations_data.values() if s["latitude"] is not None)
        self.stdout.write(
            f"Parsed {total_stations} unique stations ({coords_count} with lat/lon coordinates)."
        )

        # 3. Batch upsert into database inside a transaction
        existing_stations = {s.truckstop_id: s for s in Fuelstation.objects.all()}
        to_create = []
        to_update = []

        for tid, data in stations_data.items():
            if tid in existing_stations:
                station = existing_stations[tid]
                station.name = data["name"]
                station.address = data["address"]
                station.city = data["city"]
                station.state = data["state"]
                station.retail_price = data["retail_price"]
                station.latitude = data["latitude"]
                station.longitude = data["longitude"]
                to_update.append(station)
            else:
                to_create.append(Fuelstation(**data))

        with transaction.atomic():
            if to_create:
                Fuelstation.objects.bulk_create(to_create, batch_size=1000)
            if to_update:
                Fuelstation.objects.bulk_update(
                    to_update,
                    ["name", "address", "city", "state", "retail_price", "latitude", "longitude"],
                    batch_size=1000,
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully imported fuel stations: {len(to_create)} created, "
                f"{len(to_update)} updated. Total with coordinates: {coords_count}."
            )
        )