from django.db import models


class Fuelstation(models.Model):
    truckstop_id = models.IntegerField(unique=True)

    name = models.CharField(max_length=255)
    address = models.CharField(max_length=500)

    city = models.CharField(max_length=100)
    state = models.CharField(max_length=10)

    retail_price = models.DecimalField(
        max_digits=6,
        decimal_places=3,
    )

    latitude = models.FloatField(null=True, blank=True, db_index=True)
    longitude = models.FloatField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["latitude", "longitude"]),
        ]

    def __str__(self):
        return f"{self.name} - {self.city}, {self.state}"