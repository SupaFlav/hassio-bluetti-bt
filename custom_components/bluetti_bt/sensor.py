"""Bluetti BT sensors."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import cast
from bleak import BleakClient, BleakError

from homeassistant.components import bluetooth
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from bluetti_mqtt.bluetooth.client import BluetoothClient
from bluetti_mqtt.bluetooth import (
    BadConnectionError,
    ModbusError,
    ParseError,
    build_device,
)

from . import device_info as dev_info, get_unique_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Setup sensor entities."""

    device_name = entry.data.get(CONF_NAME)
    address = entry.data.get(CONF_ADDRESS)
    if address is None:
        _LOGGER.error("Device has no address")

    # Create coordinator for polling
    coordinator = PollingCoordinator(hass, address, device_name)
    await coordinator.async_config_entry_first_refresh()

    # Generate device info
    _LOGGER.info("Creating sensors for device with address %s", address)
    device_info = dev_info(entry)
    async_add_entities([Battery(coordinator, device_info, address)])


class PollingCoordinator(DataUpdateCoordinator):
    """Polling coordinator."""

    def __init__(self, hass: HomeAssistant, address, device_name: str):
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Bluetti polling coordinator",
            update_interval=timedelta(seconds=10),
        )
        self._address = address
        self.notify_future = None
        self.command_queue = asyncio.Queue()
        self.current_command = None
        self.notify_response = bytearray()
        self.bluetti_device = build_device(address, device_name)

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        self.logger.debug("Polling data")

        device = bluetooth.async_ble_device_from_address(self.hass, self._address)
        if device is None:
            self.logger.error("Device not available")
            return

        if self.bluetti_device is None:
            self.logger.error("Device type not found")
            return

        # Fill command_queue
        for command in self.bluetti_device.polling_commands:
            await self.command_queue.put(command)

        # Polling
        client = BleakClient(device)

        try:
            await client.connect()

            await client.start_notify(
                BluetoothClient.NOTIFY_UUID, self._notification_handler
            )

            while not self.command_queue.empty():
                try:
                    # Prepare to make request
                    current_command = await self.command_queue.get()
                    self.current_command = current_command
                    self.notify_future = self.hass.loop.create_future()
                    self.notify_response = bytearray()

                    # Make request
                    await client.write_gatt_char(
                        BluetoothClient.WRITE_UUID, bytes(current_command)
                    )

                    # Wait for response
                    res = await asyncio.wait_for(
                        self.notify_future, timeout=BluetoothClient.RESPONSE_TIMEOUT
                    )

                    # Process data
                    response = cast(bytes, res)
                    body = current_command.parse_response(response)
                    parsed = self.bluetti_device.parse(command.starting_address, body)

                    # TODO: Use parsed data
                    self.logger.error("Processing parsed data")

                    self.command_queue.task_done()
                except ParseError:
                    self.logger.debug("Got a parse exception...")
                except ModbusError as err:
                    self.logger.debug(
                        "Got an invalid request error for %s: %s",
                        current_command,
                        err,
                    )
                except (BadConnectionError, BleakError) as err:
                    self.logger.debug("Needed to disconnect due to error: %s", err)
        except BleakError as err:
            self.logger.error("Bleak error: %s", err)
        finally:
            await client.disconnect()

    def _notification_handler(self, _sender: int, data: bytearray):
        """Handle bt data."""

        # Ignore notifications we don't expect
        if not self.notify_future or self.notify_future.done():
            return

        # If something went wrong, we might get weird data.
        if data == b"AT+NAME?\r" or data == b"AT+ADV?\r":
            err = BadConnectionError("Got AT+ notification")
            self.notify_future.set_exception(err)
            return

        # Save data
        self.notify_response.extend(data)

        if len(self.notify_response) == self.current_command.response_size():
            if self.current_command.is_valid_response(self.notify_response):
                self.notify_future.set_result(self.notify_response)
            else:
                self.notify_future.set_exception(ParseError("Failed checksum"))
        elif self.current_command.is_exception_response(self.notify_response):
            # We got a MODBUS command exception
            msg = f"MODBUS Exception {self.current_command}: {self.notify_response[2]}"
            self.notify_future.set_exception(ModbusError(msg))


class Battery(CoordinatorEntity, SensorEntity):
    """Bluetti battery."""

    def __init__(
        self, coordinator: PollingCoordinator, device_info: DeviceInfo, address
    ):
        """Init battery entity."""
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_name = f"{device_info.get('name')} Battery level"
        self._attr_unique_id = get_unique_id(f"{device_info.get('name')} Battery level")
        self._attr_unit_of_measurement = "%"
        self._attr_device_class = "battery"
        self._address = address

    @property
    def available(self) -> bool:
        if self._address is None:
            return False
        return bluetooth.async_address_present(self.hass, self._address)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.error("Updating state of %s", self._attr_unique_id)
        self.async_write_ha_state()
