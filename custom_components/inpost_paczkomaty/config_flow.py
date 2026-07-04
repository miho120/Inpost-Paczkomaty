"""Config flow for InPost Paczkomaty integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectOptionDict,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    DOMAIN,
    ENTRY_PHONE_NUMBER_CONFIG,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_IN,
    CONF_TOKEN_TYPE,
)
from .exceptions import (
    InPostApiError,
)
from .inpost_auth_flow import InpostAuth
from .utils import haversine

_LOGGER = logging.getLogger(__name__)


@dataclass
class SimpleParcelLocker:
    """Simple parcel locker data container."""

    code: str
    description: str
    city: str
    street: str
    building: str
    zip_code: str
    latitude: float
    longitude: float
    distance: float


REDIRECT_SCHEMA = vol.Schema(
    {
        vol.Required(
            "redirect_url",
        ): TextSelector(TextSelectorConfig(type="text", multiline=True))
    }
)


class InPostConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle InPost Paczkomaty config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict = {}
        self._auth: InpostAuth | None = None
        self._lockers_map: dict[str, SimpleParcelLocker] = {}

    async def _cleanup_auth(self) -> None:
        """Clean up the authentication session."""
        if self._auth:
            await self._auth.close()
            self._auth = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step - external browser login.

        The user opens the InPost login URL in a browser and completes the login
        (phone number, SMS code, captcha and email confirmation). InPost then
        redirects the browser to ``.../callback?code=...``; the user pastes that
        URL (or the code) back here and we exchange it for tokens using our own
        PKCE ``code_verifier``.
        """
        errors: dict[str, str] = {}

        # Prepare an auth handler so the login URL and PKCE stay consistent
        # across form renders and submission.
        if self._auth is None:
            self._auth = InpostAuth(language=self.hass.config.language)

        if user_input is not None:
            try:
                auth_code = self._auth.extract_authorization_code(
                    user_input["redirect_url"]
                )

                # Exchange code for tokens (refresh token obtained here).
                tokens = await self._auth.exchange_code_for_tokens(auth_code)
                _LOGGER.info("Tokens obtained successfully")

                self._data[CONF_ACCESS_TOKEN] = tokens.access_token
                self._data[CONF_REFRESH_TOKEN] = tokens.refresh_token
                self._data[CONF_TOKEN_EXPIRES_IN] = tokens.expires_in
                self._data[CONF_TOKEN_TYPE] = tokens.token_type

                await self._cleanup_auth()

                # Resolve the phone number from the profile for the entry title.
                self._data[ENTRY_PHONE_NUMBER_CONFIG] = (
                    await self._fetch_phone_number()
                )

                return await self.async_step_lockers()

            except (InPostApiError, ValueError) as e:
                _LOGGER.error("Failed to authenticate with authorization code: %s", e)
                errors["base"] = "invalid_auth_response"

            except Exception as e:
                _LOGGER.exception("Unexpected error during authentication: %s", e)
                errors["base"] = "invalid_auth_response"

        return self.async_show_form(
            step_id="user",
            data_schema=REDIRECT_SCHEMA,
            errors=errors,
            description_placeholders={
                "login_url": self._auth.build_login_url(),
            },
        )

    async def _fetch_phone_number(self) -> str:
        """Fetch the account phone number from the user profile.

        Returns:
            The phone number (without country code) or empty string if
            unavailable.
        """
        from .api import InPostApiClient

        try:
            api_client = InPostApiClient(
                self.hass,
                access_token=self._data.get(CONF_ACCESS_TOKEN),
            )
            profile = await api_client.get_profile()
            await api_client.close()

            if profile.personal and profile.personal.phone_number:
                return profile.personal.phone_number
        except Exception as e:
            _LOGGER.warning("Failed to fetch phone number from profile: %s", e)

        return ""

    async def _get_favorite_lockers(self) -> list[str]:
        """Fetch favorite lockers from user profile.

        Returns:
            List of favorite locker codes, or empty list if unavailable.
        """
        from .api import InPostApiClient

        try:
            # Create a temporary API client with the access token
            class TempEntry:
                data = {CONF_ACCESS_TOKEN: self._data.get(CONF_ACCESS_TOKEN)}

            api_client = InPostApiClient(
                self.hass,
                TempEntry(),
                access_token=self._data.get(CONF_ACCESS_TOKEN),
            )

            profile = await api_client.get_profile()
            await api_client.close()

            favorite_lockers = profile.get_favorite_locker_codes()
            _LOGGER.info(
                "Found %d favorite lockers from profile", len(favorite_lockers)
            )
            return favorite_lockers

        except Exception as e:
            _LOGGER.warning("Failed to fetch favorite lockers: %s", e)
            return []

    async def async_step_lockers(self, user_input=None):
        """Handle parcel locker selection step."""
        from .api import InPostApiClient
        from .exceptions import ApiClientError

        errors: dict[str, str] = {}

        if user_input is not None:
            # User submitted locker selection - create the config entry
            phone_number = self._data.get(ENTRY_PHONE_NUMBER_CONFIG, "")
            selected_codes = user_input.get("lockers", [])
            # Build lockers list with full data
            lockers_data = []
            for code in selected_codes:
                locker = self._lockers_map.get(code)
                if locker:
                    lockers_data.append(
                        {
                            "code": locker.code,
                            "description": locker.description,
                            "city": locker.city,
                            "street": locker.street,
                            "building": locker.building,
                            "zip_code": locker.zip_code,
                            "latitude": locker.latitude,
                            "longitude": locker.longitude,
                        }
                    )
                else:
                    lockers_data.append({"code": code})
            return self.async_create_entry(
                title=f"InPost: +48 {phone_number}",
                data=self._data,
                options={"lockers": lockers_data},
            )

        # Fetch all available parcel lockers
        parcel_lockers: list[SimpleParcelLocker] = []
        api_client = InPostApiClient(self.hass)
        try:
            raw_lockers = await api_client.get_parcel_lockers_list()
            parcel_lockers = [
                SimpleParcelLocker(
                    code=locker.n,
                    description=locker.d,
                    city=locker.c,
                    street=locker.e,
                    building=locker.b,
                    zip_code=locker.o,
                    latitude=locker.l.a,
                    longitude=locker.l.o,
                    distance=haversine(
                        self.hass.config.longitude,
                        self.hass.config.latitude,
                        locker.l.o,
                        locker.l.a,
                    ),
                )
                for locker in raw_lockers
            ]
            # Store lockers for later use when saving
            self._lockers_map = {locker.code: locker for locker in parcel_lockers}
        except ApiClientError as e:
            _LOGGER.error("Failed to fetch parcel lockers: %s", e)
            errors["base"] = "cannot_fetch_lockers"
        except Exception as e:
            _LOGGER.exception("Unexpected error fetching parcel lockers: %s", e)
            errors["base"] = "cannot_fetch_lockers"
        finally:
            await api_client.close()

        # Build options sorted by distance
        locker_codes = {locker.code for locker in parcel_lockers}
        options = [
            SelectOptionDict(
                label=(
                    f"{locker.code} [{locker.distance:.2f}km] "
                    f"({locker.description} - {locker.city}, {locker.street} {locker.building})"
                ),
                value=locker.code,
            )
            for locker in sorted(parcel_lockers, key=lambda locker: locker.distance)
        ]

        # Get favorite lockers from profile API for pre-selection
        favorite_lockers = await self._get_favorite_lockers()

        # Filter to only include lockers that exist in the options
        default_lockers = [code for code in favorite_lockers if code in locker_codes]

        return self.async_show_form(
            step_id="lockers",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "lockers",
                        default=default_lockers,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            custom_value=False,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        """Get the options flow handler."""
        return InPostOptionsFlow(entry)


class InPostOptionsFlow(config_entries.OptionsFlow):
    """Handle InPost Paczkomaty options flow."""

    def __init__(self, entry):
        """Initialize options flow."""
        self.entry = entry
        self._lockers_map: dict[str, SimpleParcelLocker] = {}

    async def async_step_init(self, user_input=None):
        """Show the list of lockers fetched by coordinator."""
        from .api import InPostApiClient
        from .exceptions import ApiClientError

        errors: dict[str, str] = {}

        if user_input is not None:
            selected_codes = user_input.get("lockers", [])
            # Build lockers list with full data
            lockers_data = []
            for code in selected_codes:
                locker = self._lockers_map.get(code)
                if locker:
                    lockers_data.append(
                        {
                            "code": locker.code,
                            "description": locker.description,
                            "city": locker.city,
                            "street": locker.street,
                            "building": locker.building,
                            "zip_code": locker.zip_code,
                            "latitude": locker.latitude,
                            "longitude": locker.longitude,
                        }
                    )
                else:
                    lockers_data.append({"code": code})
            options_data = {"lockers": lockers_data}
            self.hass.config_entries.async_update_entry(
                self.entry, options=options_data
            )
            await self.hass.config_entries.async_reload(self.entry.entry_id)

            return self.async_create_entry(title="", data=options_data)

        # Fetch parcel lockers with error handling
        parcel_lockers: list[SimpleParcelLocker] = []
        api_client = InPostApiClient(self.hass)
        try:
            raw_lockers = await api_client.get_parcel_lockers_list()
            parcel_lockers = [
                SimpleParcelLocker(
                    code=locker.n,
                    description=locker.d,
                    city=locker.c,
                    street=locker.e,
                    building=locker.b,
                    zip_code=locker.o,
                    latitude=locker.l.a,
                    longitude=locker.l.o,
                    distance=haversine(
                        self.hass.config.longitude,
                        self.hass.config.latitude,
                        locker.l.o,
                        locker.l.a,
                    ),
                )
                for locker in raw_lockers
            ]
            # Store lockers for later use when saving
            self._lockers_map = {locker.code: locker for locker in parcel_lockers}
        except ApiClientError as e:
            _LOGGER.error("Failed to fetch parcel lockers: %s", e)
            errors["base"] = "cannot_fetch_lockers"
        except Exception as e:
            _LOGGER.exception("Unexpected error fetching parcel lockers: %s", e)
            errors["base"] = "cannot_fetch_lockers"
        finally:
            await api_client.close()

        # Build options for SelectSelector
        options = [
            SelectOptionDict(
                label=(
                    f"{locker.code} [{locker.distance:.2f}km] "
                    f"({locker.description} - {locker.city}, {locker.street} {locker.building})"
                ),
                value=locker.code,
            )
            for locker in sorted(parcel_lockers, key=lambda locker: locker.distance)
        ]

        # Default selection = previously selected ones (handle both old and new format)
        current_lockers = self.entry.options.get("lockers", [])
        if current_lockers and isinstance(current_lockers[0], dict):
            # New format: list of dicts with code and description
            current = [locker["code"] for locker in current_lockers]
        else:
            # Old format: list of codes (for backwards compatibility)
            current = current_lockers

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "lockers",
                        default=current,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            custom_value=False,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )
