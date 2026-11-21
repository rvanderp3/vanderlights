"""Device abstraction for the xLights bridge.

Each Bluetooth light target implements the ``Device`` interface. The bridge
routes DMX RGB channels to these. To add a new target type (Govee, ELK-BLEDOM,
ESP32, ...) subclass ``Device`` and register it in ``DEVICE_TYPES``.
"""
import logging

log = logging.getLogger("lights.devices")


class Device:
    """Interface all light targets implement."""

    type = "base"

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "?")
        self.cfg = cfg

    def connect(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def set_rgb(self, r: int, g: int, b: int) -> None:
        raise NotImplementedError

    def set_effect(self, name: str, brightness: int = 50) -> None:
        raise NotImplementedError

    def set_power(self, on: bool) -> None:
        raise NotImplementedError


class HelloFairyDevice(Device):
    """A Hello Fairy string light (ISSC BLE), driven via direct D-Bus GATT."""

    type = "hello_fairy"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        from hello_fairy import HelloFairy  # local import keeps this target optional
        self._hf = HelloFairy(cfg["address"], adapter=cfg.get("adapter", "hci0"))
        self._connected = False

    def connect(self) -> None:
        if not self._connected:
            self._hf.connect()
            self._connected = True
            log.info("%s: connected", self.name)

    def set_rgb(self, r: int, g: int, b: int) -> None:
        try:
            self.connect()
            self._hf.set_color_rgb(r, g, b)
        except Exception as e:
            self._connected = False
            log.warning("%s: set_rgb failed: %s", self.name, e)

    def set_effect(self, name: str, brightness: int = 50) -> None:
        try:
            self.connect()
            self._hf.set_effect(name, brightness)
        except Exception as e:
            self._connected = False
            log.warning("%s: set_effect failed: %s", self.name, e)

    def set_power(self, on: bool) -> None:
        try:
            self.connect()
            self._hf.power(on)
        except Exception as e:
            self._connected = False
            log.warning("%s: set_power failed: %s", self.name, e)

    def close(self) -> None:
        if self._connected:
            try:
                self._hf.disconnect()
            except Exception:
                pass
            self._connected = False


DEVICE_TYPES = {
    "hello_fairy": HelloFairyDevice,
}


def make_device(cfg: dict) -> Device:
    cfg = dict(cfg)
    t = cfg.pop("type")
    cls = DEVICE_TYPES.get(t)
    if cls is None:
        raise KeyError(f"unknown device type '{t}'; known: {sorted(DEVICE_TYPES)}")
    return cls(cfg)