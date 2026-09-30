from decimal import Decimal
from unittest.mock import patch
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from fuel.models import Fuelstation
from fuel.services import (
    calculate_fuel_stops,
    geocode_location,
    get_route,
)


class RoutePlannerAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        # Create sample fuel stations along a simulated route
        cls.station_a = Fuelstation.objects.create(
            truckstop_id=101,
            name="Stop A - Cheap",
            address="100 Highway 1",
            city="Columbus",
            state="OH",
            retail_price=Decimal("2.850"),
            latitude=39.8000,
            longitude=-83.0000,
        )
        cls.station_b = Fuelstation.objects.create(
            truckstop_id=102,
            name="Stop B - Expensive",
            address="200 Highway 1",
            city="Indianapolis",
            state="IN",
            retail_price=Decimal("3.950"),
            latitude=39.6000,
            longitude=-86.0000,
        )
        cls.station_c = Fuelstation.objects.create(
            truckstop_id=103,
            name="Stop C - Mid",
            address="300 Highway 1",
            city="St. Louis",
            state="MO",
            retail_price=Decimal("3.100"),
            latitude=39.3300,
            longitude=-90.0000,
        )
        cls.station_d = Fuelstation.objects.create(
            truckstop_id=104,
            name="Stop D - Cheap",
            address="400 Highway 1",
            city="Kansas City",
            state="MO",
            retail_price=Decimal("2.900"),
            latitude=39.0300,
            longitude=-94.5000,
        )

    def test_missing_start_parameter_returns_400(self):
        url = reverse("route-planner")
        response = self.client.post(url, {"finish": "Austin, TX"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)
        self.assertIn("start", response.data["error"])

    def test_missing_finish_parameter_returns_400(self):
        url = reverse("route-planner")
        response = self.client.post(url, {"start": "Austin, TX"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)
        self.assertIn("finish", response.data["error"])

    def test_empty_payload_returns_400(self):
        url = reverse("route-planner")
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_invalid_location_string_returns_400(self):
        url = reverse("route-planner")
        # Unrecognized city string that won't match local city DB and won't match Nominatim
        with patch("fuel.services.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = []

            response = self.client.post(
                url,
                {"start": "TotallyNonExistentCityXYZ9999", "finish": "Austin, TX"},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("error", response.data)

    def test_short_route_under_500_miles(self):
        """Routes under 500 miles should return 0 stops and valid distance/cost."""
        url = reverse("route-planner")

        # Mock route GeoJSON representing a 200-mile trip
        mock_route = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-97.7431, 30.2672], [-98.4936, 29.4241]],
                    },
                    "properties": {
                        "summary": {
                            "distance": 321868.8,  # 200 miles in meters
                            "duration": 10800,
                        }
                    },
                }
            ],
        }

        with patch("fuel.views.get_route", return_value=mock_route):
            response = self.client.post(
                url,
                {"start": "Austin, TX", "finish": "San Antonio, TX"},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["total_distance_miles"], 200.0)
            self.assertEqual(len(response.data["fuel_stops"]), 0)
            self.assertEqual(response.data["fuel_stops_count"], 0)
            # Cost at 10 MPG = 200 / 10 * 3.50 = 70.00
            self.assertAlmostEqual(response.data["total_fuel_cost_usd"], 70.0, places=1)
            self.assertEqual(response.data["route_map_geojson"], mock_route)

    def test_long_cross_country_route_over_500_miles(self):
        """Routes over 500 miles should select fuel stops and ensure no leg exceeds 500 miles."""
        url = reverse("route-planner")

        # Simulated 1,200 mile route: (-80.0, 40.0) -> (-95.0, 39.0)
        # 1,200 miles in meters = 1,200 * 1609.344 = 1,931,212.8 meters
        num_pts = 50
        coords = [
            [-80.0 - (15.0 * i / num_pts), 40.0 - (1.0 * i / num_pts)]
            for i in range(num_pts + 1)
        ]

        mock_route = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords,
                    },
                    "properties": {
                        "summary": {
                            "distance": 1931212.8,  # 1200 miles
                            "duration": 64800,
                        }
                    },
                }
            ],
        }

        with patch("fuel.views.get_route", return_value=mock_route):
            response = self.client.post(
                url,
                {"start": "Pittsburgh, PA", "finish": "Kansas City, MO"},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertGreater(response.data["total_distance_miles"], 500)
            self.assertGreater(len(response.data["fuel_stops"]), 0)

            # Validate each stop schema
            prev_marker = 0.0
            for stop in response.data["fuel_stops"]:
                self.assertIn("truckstop_id", stop)
                self.assertIn("name", stop)
                self.assertIn("address", stop)
                self.assertIn("city", stop)
                self.assertIn("state", stop)
                self.assertIn("price_per_gallon", stop)
                self.assertIn("distance_from_start_miles", stop)
                self.assertIn("gallons_refueled", stop)
                self.assertIn("cost_at_stop", stop)

                # Ensure distance from previous stop does not exceed tank range (500 miles)
                step_dist = stop["distance_from_start_miles"] - prev_marker
                self.assertLessEqual(step_dist, 500.0)
                prev_marker = stop["distance_from_start_miles"]

            # Final leg to destination must also be within 500 miles
            final_leg = response.data["total_distance_miles"] - prev_marker
            self.assertLessEqual(final_leg, 500.0)

    def test_routing_service_failure_returns_502(self):
        url = reverse("route-planner")
        with patch("fuel.views.get_route", side_effect=ConnectionError("Routing timeout")):
            response = self.client.post(
                url,
                {"start": "Austin, TX", "finish": "Dallas, TX"},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
            self.assertIn("error", response.data)

    def test_direct_coordinate_input_support(self):
        url = reverse("route-planner")
        coords = [[-97.7431, 30.2672], [-96.7970, 32.7767]]
        mock_route = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords,
                    },
                    "properties": {
                        "summary": {
                            "distance": 313822.0,  # ~195 miles
                            "duration": 10500,
                        }
                    },
                }
            ],
        }

        with patch("fuel.views.get_route", return_value=mock_route):
            response = self.client.post(
                url,
                {"start": "30.2672, -97.7431", "finish": "32.7767, -96.7970"},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertAlmostEqual(response.data["total_distance_miles"], 195.0, places=0)


class FuelServicesUnitTests(APITestCase):
    def test_geocode_location_offline(self):
        coords_austin = geocode_location("Austin, TX")
        self.assertAlmostEqual(coords_austin[0], -97.74, delta=0.5)
        self.assertAlmostEqual(coords_austin[1], 30.26, delta=0.5)

        coords_ny = geocode_location("New York, NY")
        self.assertAlmostEqual(coords_ny[0], -74.00, delta=0.5)
        self.assertAlmostEqual(coords_ny[1], 40.71, delta=0.5)

    def test_geocode_location_empty_raises_value_error(self):
        with self.assertRaises(ValueError):
            geocode_location("")

    def test_calculate_fuel_stops_zero_distance(self):
        empty_route = {
            "features": [
                {
                    "geometry": {"coordinates": [[0, 0], [0, 0]]},
                    "properties": {"summary": {"distance": 0}},
                }
            ]
        }
        res = calculate_fuel_stops(empty_route)
        self.assertEqual(res["total_distance_miles"], 0.0)
        self.assertEqual(res["total_fuel_cost"], 0.0)
        self.assertEqual(len(res["fuel_stops"]), 0)
