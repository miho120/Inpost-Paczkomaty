"""InPost API data coordinator."""

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CustomInpostApi
from .models import ParcelsSummary

_LOGGER = logging.getLogger(__name__)


class InpostDataCoordinator(DataUpdateCoordinator[ParcelsSummary]):
    def __init__(
        self, hass: HomeAssistant, mailbay_api_client: CustomInpostApi
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="InPost Paczkomaty data coordinator",
            update_interval=timedelta(seconds=30),
        )
        self.mailbay_api_client = mailbay_api_client

    async def _async_update_data(self):
        try:
            async with asyncio.timeout(30):
                return await self.mailbay_api_client.get_parcels()

        except Exception as err:
            _LOGGER.error("Cannot read parcels from InPost HA API: %s", err)
            raise UpdateFailed("Error fetching InPost parcels update") from err
