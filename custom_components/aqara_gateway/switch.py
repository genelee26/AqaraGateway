"""Support for Aqara Switchs."""
import logging
import re

from homeassistant.components import persistent_notification
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import ATTR_VOLTAGE, STATE_OFF, STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

from . import DOMAIN, GatewayGenericDevice
from .core.const import (
    ATTR_CHIP_TEMPERATURE,
    ATTR_FW_VER,
    ATTR_IN_USE,
    ATTR_LOAD_POWER,
    ATTR_LQI,
    ATTR_POWER_CONSUMED,
    CHIP_TEMPERATURE,
    ENERGY_CONSUMED,
    FW_VER,
    IN_USE,
    LOAD_POWER,
    LOAD_VOLTAGE,
    LQI,
    SWITCH_ATTRIBUTES,
    VRF_MODELS,
    VRF_DIP_MIN,
    VRF_DIP_MAX,
)
from .core.gateway import Gateway
from .core.utils import Utils

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """ Perform the setup for Xiaomi/Aqara devices. """
    def setup(gateway: Gateway, device: dict, attr: str):
        if device.get('model') in VRF_MODELS and attr == 'vrf_scan':
            async_add_entities([
                VRFScanSwitch(gateway, device, attr)])
        else:
            feature = Utils.get_feature_suppported(device["model"])
            async_add_entities([
                GatewaySwitch(gateway, device, attr, feature)
            ])
    aqara_gateway: Gateway = hass.data[DOMAIN][config_entry.entry_id]
    aqara_gateway.add_setup('switch', setup)


async def async_unload_entry(hass, entry):
    # pylint: disable=unused-argument
    """ unload entry """
    return True


class GatewaySwitch(GatewayGenericDevice, SwitchEntity, RestoreEntity):
    """Representation of a Xiaomi/Aqara Plug."""

    def __init__(self, gateway: Gateway, device: dict, attr: str, feature):
        """Initialize the XiaomiPlug."""
        self._chip_temperature = None
        self._fw_ver = None
        self._in_use = None
        self._load_power = None
        self._lqi = None
        self._power_consumed = None
        self._voltage = None
        self._model = device['model']
        self.feature = feature
        super().__init__(gateway, device, attr)

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        if last_state := await self.async_get_last_state():
            if last_state.state == STATE_ON:
                self._attr_is_on = True
            elif last_state.state == STATE_OFF:
                self._attr_is_on = False
        await super().async_added_to_hass()

    @property
    def icon(self):
        """return icon."""
        return 'mdi:power-socket'

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        self._attrs[ATTR_FW_VER] = self._fw_ver
        if self.feature.get('support_power_consumption', False):
            self._attrs[ATTR_LOAD_POWER] = self._load_power
            self._attrs[ATTR_POWER_CONSUMED] = self._power_consumed

        if self.feature.get('support_in_use', False):
            self._attrs[ATTR_IN_USE] = self._in_use
        if self.feature.get('support_load_voltage', False):
            self._attrs[ATTR_VOLTAGE] = self._voltage
        return self._attrs

    def update(self, data: dict):
        """update switch."""
        for key, value in data.items():
            if key == CHIP_TEMPERATURE:
                self._chip_temperature = value
            if key == FW_VER or key == 'back_version':
                self._fw_ver = value
            if key == LOAD_POWER:
                self._load_power = value
            if key == LQI:
                self._lqi = value
            if key == ENERGY_CONSUMED:
                self._power_consumed = value
            if key == IN_USE:
                self._in_use = bool(value)
            if key == LOAD_VOLTAGE:
                self._voltage = format(
                    float(value) / 1000, '.3f') if isinstance(
                    value, (int, float)) else None
            if key in SWITCH_ATTRIBUTES:
                self._attrs[key] = value
            if key == self._attr:
                if self._model in ["aqara.feeder.acn001"] and self._attr == "feed_switch":
                    self._attr_is_on = False
                else:
                    self._attr_is_on = bool(value)
        self.async_write_ha_state()

    def turn_on(self, **kwargs):
        """Turn the switch on."""
        self.gateway.send(self.device, {self._attr: 1})

    def turn_off(self, **kwargs):
        """Turn the switch off."""
        self.gateway.send(self.device, {self._attr: 0})


class VRFScanSwitch(GatewayGenericDevice, SwitchEntity):
    """Switch entity to scan for active VRF indoor units.

    When turned on, collects VRF heartbeat data and identifies
    active indoor units by their DIP switch IDs. Results are
    reported via persistent notification.
    """

    _RE_RES = re.compile(r'^0\.(\d+)\.85$')

    def __init__(self, gateway: Gateway, device: dict, attr: str):
        super().__init__(gateway, device, attr)
        self._attr_is_on = False
        self._attr_icon = "mdi:radar"
        self._scanning = False
        # Track which DIP IDs we've seen and their temperature values
        self._seen_ids = {}       # {dip_id: max_temperature_value}
        self._expected = set(range(VRF_DIP_MIN, VRF_DIP_MAX + 1))
        self._scanned_ids = set()

    async def async_turn_on(self, **kwargs):
        """Start VRF scan."""
        if self._scanning:
            return
        self._scanning = True
        self._attr_is_on = True
        self._seen_ids = {}
        self._scanned_ids = set()
        self.async_write_ha_state()

        persistent_notification.async_create(
            self.hass,
            message="VRF scan started. Collecting data from indoor units "
                    "(this may take 1-5 minutes). You will be notified "
                    "when the scan is complete.",
            title="VRF Scan",
            notification_id="vrf_scan_progress"
        )

    async def async_turn_off(self, **kwargs):
        """Stop VRF scan and report results."""
        if self._scanning:
            self._finish_scan()

    def _finish_scan(self):
        """Analyze collected data and notify user."""
        self._scanning = False
        self._attr_is_on = False

        active_ids = sorted(
            dip_id for dip_id, temp in self._seen_ids.items()
            if temp > 0
        )

        # Dismiss progress notification
        persistent_notification.async_dismiss(
            self.hass, notification_id="vrf_scan_progress"
        )

        if active_ids:
            id_str = ", ".join(str(i) for i in active_ids)
            message = (
                f"**Scan complete!** Found **{len(active_ids)}** "
                f"active indoor unit(s).\n\n"
                f"**DIP Switch IDs:** `{id_str}`\n\n"
                f"Copy the IDs above into the VRF configuration "
                f"(Integration → Options → VRF Indoor Units)."
            )
        else:
            message = (
                "**Scan complete.** No active indoor units found.\n\n"
                "Make sure the VRF system is powered on and try again."
            )

        persistent_notification.async_create(
            self.hass,
            message=message,
            title="VRF Scan Result",
            notification_id="vrf_scan_result"
        )

        self.async_write_ha_state()

    def update(self, data: dict = None):
        """Collect VRF data during scan."""
        if not data:
            return
        if not self._scanning:
            return

        # Look for temperature resource IDs in two formats:
        # 1. Raw: '0.N.85' (unmapped by params)
        # 2. Mapped: 'current_temperature_N' (mapped by params)
        for key, value in data.items():
            dip_id = None

            # Check raw resource ID format
            match = self._RE_RES.match(key)
            if match:
                dip_id = int(match.group(1))

            # Check mapped format
            if key.startswith('current_temperature_'):
                # This is already mapped — we need to reverse-lookup
                # the DIP ID from the device params
                try:
                    zone = int(key.split('_')[-1])
                    # Find the resource ID for this zone from params
                    for param in self.device.get('params', []):
                        if param[1] == key and param[0].startswith('0.'):
                            dip_id = int(param[0].split('.')[1])
                            break
                except (ValueError, IndexError):
                    pass

            if dip_id is not None and VRF_DIP_MIN <= dip_id <= VRF_DIP_MAX:
                self._scanned_ids.add(dip_id)
                if isinstance(value, (int, float)):
                    prev = self._seen_ids.get(dip_id, 0)
                    if value > prev:
                        self._seen_ids[dip_id] = value

        # VRF controller reports IDs sequentially from 1 upward.
        # Finish when we've seen the max expected ID.
        if self._scanned_ids and max(self._scanned_ids) >= VRF_DIP_MAX:
            self._finish_scan()
