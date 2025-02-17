"""Bluetti Bluetooth Config Flow"""

from __future__ import annotations

import re
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.const import CONF_ADDRESS, CONF_TYPE, CONF_NAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from . import get_type_by_bt_name
from .const import CONF_PERSISTENT_CONN, CONF_POLLING_INTERVAL, DATA_COORDINATOR, DOMAIN, CONF_USE_CONTROLS

_LOGGER = logging.getLogger(__name__)


class BluettiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Bluetti BT devices."""

    def __init__(self) -> None:
        _LOGGER.info("Initialize config flow")
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user input."""

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices[address]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            dev_type = get_type_by_bt_name(discovery_info.name)
            return self.async_create_entry(
                title=discovery_info.name,
                data={
                    CONF_ADDRESS: discovery_info.address,
                    CONF_TYPE: dev_type,
                    CONF_NAME: re.sub("[^A-Z0-9]+", "", discovery_info.name),
                },
            )

        if discovery := self._discovery_info:
            self._discovered_devices[discovery.address] = discovery
        else:
            current_addresses = self._async_current_ids()
            for discovery in async_discovered_service_info(self.hass):
                address = discovery.address
                if address in current_addresses or address in self._discovered_devices:
                    continue
                self._discovered_devices[discovery.address] = discovery

        if not self._discovered_devices:
            return self.async_abort(reason="no_unconfigured_devices")

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): vol.In(
                    {
                        service_info.address: service_info.name
                        for service_info in self._discovered_devices.values()
                    }
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle a option flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:

            # Validate update interval
            if user_input[CONF_POLLING_INTERVAL] < 5:
                return self.async_abort(reason="invalid_interval")

            changed = self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    **{
                        CONF_USE_CONTROLS: user_input[CONF_USE_CONTROLS],
                        CONF_PERSISTENT_CONN: user_input[CONF_PERSISTENT_CONN],
                        CONF_POLLING_INTERVAL: user_input[CONF_POLLING_INTERVAL],
                    },
                },
            )
            if changed is False:
                _LOGGER.error(
                    "Method OptionsFlowHandler.async_step_init: Config entry %s has not been changed",
                    self.config_entry.entry_id,
                )
            return self.async_create_entry(
                title="",
                data={
                    CONF_USE_CONTROLS: user_input[CONF_USE_CONTROLS],
                    CONF_PERSISTENT_CONN: user_input[CONF_PERSISTENT_CONN],
                    CONF_POLLING_INTERVAL: user_input[CONF_POLLING_INTERVAL],
                },
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USE_CONTROLS,
                        default=self.config_entry.data.get(CONF_USE_CONTROLS, False),
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_PERSISTENT_CONN,
                        default=self.config_entry.data.get(CONF_PERSISTENT_CONN, False),
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_POLLING_INTERVAL,
                        default=self.config_entry.data.get(CONF_POLLING_INTERVAL, 20),
                    ): int,
                }
            ),
        )
