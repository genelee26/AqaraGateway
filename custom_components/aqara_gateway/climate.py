"""Support for Xiaomi Aqara Climate."""
import logging
from typing import Any

from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.components.climate import ATTR_CURRENT_TEMPERATURE, ATTR_HVAC_ACTION, ClimateEntity
from homeassistant.components.climate.const import (
    HVACAction,
    HVACMode,
    ClimateEntityFeature,
    SWING_OFF,
    SWING_ON
)
from homeassistant.helpers.restore_state import RestoreEntity

from . import DOMAIN, GatewayGenericDevice
from .core.gateway import Gateway
from .core.const import (
    HVAC_MODES,
    AC_STATE_FAN,
    AC_STATE_FAN2,
    AC_STATE_HVAC,
    FAN_MODES,
    YUBA_STATE_HVAC,
    YUBA_STATE_FAN
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Perform the setup for Xiaomi/Aqara devices."""
    def setup(gateway: Gateway, device: dict, attr: str):
        if attr == 'yuba':
            async_add_entities([
                AqaraClimateYuba(gateway, device, attr)])
        elif attr == 'towel_warmer':
            async_add_entities([AqaraTowelWarmer(gateway, device, attr)])
        else:
            async_add_entities([
                AqaraGenericClimate(gateway, device, attr)
            ])

    aqara_gateway: Gateway = hass.data[DOMAIN][config_entry.entry_id]
    aqara_gateway.add_setup('climate', setup)


async def async_unload_entry(hass, entry):
    # pylint: disable=unused-argument
    """ unload entry """
    return True


class AqaraGenericClimate(GatewayGenericDevice, ClimateEntity):
    # pylint: disable=too-many-instance-attributes
    """Initialize the AqaraGenericClimate."""

    def __init__(self, gateway: Gateway, device: dict, attr: str):
        super().__init__(gateway, device, attr)
        self.attr = attr
        self.model = device.get('model')

        # 提取索引数字，例如 "climate 1" -> 1
        try:
            self.index = int(attr.split()[-1])
        except Exception:
            self.index = 1

        self._attr_hvac_mode = HVACMode.OFF
        self._attr_hvac_modes = [
            HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, 
            HVACMode.DRY, HVACMode.FAN_ONLY
        ]

        self._attr_fan_mode = "auto"
        self._attr_fan_modes = ["auto", "low", "medium", "high"]

        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE | 
            ClimateEntityFeature.FAN_MODE
        )
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_target_temperature_step = 0.5
        self._attr_max_temp = 35
        self._attr_min_temp = 16
    
    @property
    def hvac_mode(self) -> HVACMode:
        return self._attr_hvac_mode
    
    @property
    def available(self) -> bool:
        """强制可用性逻辑：只要网关还在，实体就不许变灰"""
        return self.gateway is not None and self.gateway.available

    async def async_set_temperature(self, **kwargs) -> None:
        """针对 VRF T1 的特殊下发逻辑"""
        if ATTR_TEMPERATURE not in kwargs:
            return
        temp = kwargs[ATTR_TEMPERATURE]
        
        if self.model == 'aqara.airrtc.ecn001':
            target_key = f"target_temperature_{self.index}"
            self.gateway.send(self.device, {target_key: int(temp * 100)})
            self._attr_target_temperature = temp
        else:
            self.gateway.send(self.device, {'target_temperature': temp})
        
        if self.hass:    
            self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """针对 VRF T1 的模式与开关控制逻辑"""
        if self.model == 'aqara.airrtc.ecn001':
            # 1. 电源控制
            pwr_key = f"power_{self.index}"
            pwr_val = 0 if hvac_mode == HVACMode.OFF else 1
            self.gateway.send(self.device, {pwr_key: pwr_val})

            # 2. 模式控制
            if hvac_mode != HVACMode.OFF:
                mode_key = f"mode_{self.index}"
                mode_map = {
                    HVACMode.COOL: 1, HVACMode.DRY: 2, 
                    HVACMode.FAN_ONLY: 3, HVACMode.HEAT: 4
                }
                payload = {
                    mode_key: mode_map.get(hvac_mode, 1),
                    pwr_key: pwr_val
                }
                self.gateway.send(self.device, payload)
            
            self._attr_hvac_mode = hvac_mode
        else:
            self.gateway.send(self.device, {'power': 1 if hvac_mode != HVACMode.OFF else 0})
        
        if self.hass:    
            self.async_write_ha_state()
        return

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """针对 VRF T1 的风速设置逻辑"""
        if self.model == 'aqara.airrtc.ecn001':
            fan_key = f"fan_mode_{self.index}"
            # res_id = f"14.{self.index + 7}.85"
            fan_map = {"auto": 0, "low": 1, "medium": 2, "high": 3}
            if fan_mode in fan_map:
                self.gateway.send(self.device, {fan_key: fan_map[fan_mode]})
            self._attr_fan_mode = fan_mode
        else:
            self.gateway.send(self.device, {'fan_mode': fan_mode})
            
        if self.hass:    
            self.async_write_ha_state()
        return
    
    def update(self, data: dict = None):
        # pylint: disable=broad-except
        """ update climate """
        if not data:
            return
        _LOGGER.debug(f"VRF T1 Received Data: {data}")

        try:
            idx = self.index
            keys = {
                'cur': f'current_temperature_{idx}',
                'tar': f'target_temperature_{idx}',
                'pwr': f'power_{idx}',
                'mod': f'mode_{idx}',
                'fan': f'fan_mode_{idx}'
            }

            if keys['cur'] in data:
                val = data[keys['cur']]
                if val > 0:
                    self._attr_current_temperature = val / 100 if val > 500 else val
            
            if keys['tar'] in data:
                val = data[keys['tar']]
                if val > 0:
                    self._attr_target_temperature = val / 100 if val > 500 else val

            if keys['pwr'] in data:
                is_on = data[keys['pwr']] == 1
                if not is_on:
                    self._attr_hvac_mode = HVACMode.OFF
                else:
                    if self._attr_hvac_mode == HVACMode.OFF:    # If power is ON and display as OFF, reset the mode according to the mode_key
                        self._attr_hvac_mode = HVACMode.COOL    # Set the default mode

            if keys['mod'] in data:
                if self._attr_hvac_mode != HVACMode.OFF:
                    #0: Off  1: Cool  2: Dry  3: Fan_Only  4: Heat
                    mode_map = {1: HVACMode.COOL, 2: HVACMode.DRY, 3: HVACMode.FAN_ONLY, 4: HVACMode.HEAT}
                    self._attr_hvac_mode = mode_map.get(data[keys['mod']], HVACMode.COOL)

            if keys['fan'] in data:
                fan_map = {0: "auto", 1: "low", 2: "medium", 3: "high"}
                self._attr_fan_mode = fan_map.get(data[keys['fan']], "auto")
        
        except Exception as e:
            _LOGGER.error(f"VRF T1 Error on Climate_{self.index}: {e}")

        if self.hass:    
            self.async_write_ha_state()

class AqaraClimateYuba(AqaraGenericClimate, ClimateEntity):
    # pylint: disable=too-many-instance-attributes
    """Initialize the AqaraClimateYuba."""

    @property
    def fan_modes(self):
        """ return fan modes """
        return list(YUBA_STATE_FAN.keys())

    @property
    def hvac_modes(self):
        """ return hvac modes """
        return list(YUBA_STATE_HVAC.keys()) + [HVACMode.OFF]

    @property
    def supported_features(self):
        """ return supported features """
        return ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.SWING_MODE

    @property
    def swing_mode(self) -> str | None:
        """Return the swing setting.

        Requires ClimateEntityFeature.SWING_MODE.
        """
        return self._swing_mode

    @property
    def swing_modes(self) -> list[str] | None:
        """Return the list of available swing modes.

        Requires ClimateEntityFeature.SWING_MODE.
        """
        return [SWING_OFF, SWING_ON]

    def turn_on(self) -> None:
        """Turn the entity on."""
        self.gateway.send(self.device, {'power': 1})

    def turn_off(self) -> None:
        """Turn the entity off."""
        self.gateway.send(self.device, {'power': 0})

    def set_temperature(self, **kwargs) -> None:
        """ set temperature """
        if not self._state or kwargs[ATTR_TEMPERATURE] == 0:
            self.debug(f"Can't set climate temperature: {self._state}")
            return
        self.gateway.send(self.device, {'target_temperature': 100 * int(kwargs[ATTR_TEMPERATURE])})
        self._target_temp = int(kwargs[ATTR_TEMPERATURE])

    def set_fan_mode(self, fan_mode: str) -> None:
        """ set fan mode """
        if not self._state:
            return
        self.gateway.send(self.device, {'fan_mode': YUBA_STATE_FAN[fan_mode]})
        self._fan_mode = fan_mode

    def set_hvac_mode(self, hvac_mode: str) -> None:
        """ set hvac mode """
        self._hvac_mode = hvac_mode
        if hvac_mode == HVACMode.OFF:
            self.gateway.send(self.device, {'power': 0})
            return
        if self._is_on == 0:
            self.gateway.send(self.device, {'power': 1})
            self._is_on = 1
        self.gateway.send(self.device, {'mode': YUBA_STATE_HVAC[hvac_mode]})

    def set_swing_mode(self, swing_mode: str) -> None:
        """Set new target swing operation."""
        if not self._state:
            return
        value = 1
        if swing_mode == SWING_ON:
            value = 0
        self.gateway.send(self.device, {'swing_mode': value})

    def update(self, data: dict = None):
        # pylint: disable=broad-except
        """ update climate """
        try:
            if 'power' in data:  # 0 - off, 1 - on
                self._is_on = data['power']
                if self._is_on == 0:
                    self._hvac_mode = HVACMode.OFF
            if 'mode' in data:
                self._hvac_mode = list(YUBA_STATE_HVAC.keys())[
                    list(YUBA_STATE_HVAC.values()).index(data['mode'])]
            if 'fan_mode' in data:
                self._fan_mode = list(YUBA_STATE_FAN.keys())[
                    list(YUBA_STATE_FAN.values()).index(data['fan_mode'])]
            if 'current_temperature' in data:
                self._current_temp = data['current_temperature'] / 100
            if 'target_temperature' in data:
                self._target_temp = data['target_temperature'] / 100
            if 'swing_mode' in data:
                self._swing_mode = SWING_OFF if data['swing_mode'] == 1 else SWING_ON
            self._state = self._is_on

        except Exception:
            _LOGGER.exception(f"Can't read climate data: {data}")

        self.schedule_update_ha_state()


class AqaraTowelWarmer(GatewayGenericDevice, ClimateEntity, RestoreEntity):
    _attr_hvac_modes: list[HVACMode] = [HVACMode.OFF, HVACMode.HEAT]
    _attr_max_temp: float = 65
    _attr_min_temp: float = 45
    _attr_precision: float = PRECISION_WHOLE
    _attr_supported_features: ClimateEntityFeature = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
    _attr_target_temperature_step: float = 1
    _attr_temperature_unit: str = UnitOfTemperature.CELSIUS

    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, gateway: Gateway, device: dict, attr: str):
        self._attr = attr
        self._attr_hvac_mode: HVACMode | None = None
        super().__init__(gateway, device, attr)

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        if last_state := await self.async_get_last_state():
            if last_state.state in self.hvac_modes:
                self._attr_hvac_mode = HVACMode(last_state.state)
            if ATTR_HVAC_ACTION in last_state.attributes:
                self._attr_hvac_action = HVACAction(last_state.attributes[ATTR_HVAC_ACTION])
            self._attr_current_temperature = last_state.attributes.get(ATTR_CURRENT_TEMPERATURE)
            self._attr_target_temperature = last_state.attributes.get(ATTR_TEMPERATURE)

        await super().async_added_to_hass()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if ATTR_TEMPERATURE not in kwargs:
            return
        target_temperature = kwargs[ATTR_TEMPERATURE]
        self.gateway.send(self.device, {'target_temperature': target_temperature})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        self.gateway.send(self.device, {'power': 1 if hvac_mode == HVACMode.HEAT else 0})

    def update(self, data: dict):
        if 'current_temperature' in data:
            self._attr_current_temperature = data['current_temperature']
        if 'target_temperature' in data:
            self._attr_target_temperature = data['target_temperature']
        if 'power' in data:
            if data['power'] == 1:
                self._attr_hvac_mode = HVACMode.HEAT
                self._attr_hvac_action = HVACAction.HEATING
            else:
                self._attr_hvac_mode = HVACMode.OFF
                self._attr_hvac_action = HVACAction.IDLE
        self.schedule_update_ha_state()
