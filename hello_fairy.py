"""Hello Fairy BLE controller for the Orange Pi display coordinator.

Transport: direct D-Bus GATT through BlueZ (via PyGObject), which is the
reliable path on this Orange Pi (bleak's internal scan-based discovery is
flaky here and the unit only wakes on a directed connect-by-address).

Protocol (reverse-engineered, confirmed against a live unit):
  Packet:  AA <cmd> <payload_len> <payload...> <checksum>
  checksum = sum of preceding bytes mod 256
  ACKs:    AA 02 00 AC (power) / AA 03 00 AD (color/preset)
  Status / info replies arrive on the notify characteristic's Value property.
"""

import time

from gi.repository import Gio, GLib

# ISSC "Hello Fairy" service (0x49535343 prefix = ASCII "ISSC").
SERVICE_UUID = "49535343-fe7d-4ae5-8fa9-9fafd205e455"
# Command characteristic (write / write-without-response).
COMMAND_CHAR = "49535343-8841-43f4-a8d4-ecbe34729bb3"
# Notify characteristic -- device pushes ACKs / status here.
NOTIFY_CHAR = "49535343-1e4d-4bd9-ba61-23c647249616"

CMD_PREFIX = 0xAA
CMD_INFO = 0x00
CMD_STATUS = 0x01
CMD_POWER = 0x02
CMD_COLOR_PRESET = 0x03

MODE_COLOR = 0x01
MODE_PRESET = 0x02

# Named hello fairy presets (relevant holiday picks marked).
EFFECT_PRESETS = {
    "Blue White Dissolve": 41,
    "Blue Sparkle": 56,
    "White Sparkle": 57,
    "Blue with Pink Sparkle": 8,
    "Fireworks": 17,
    "Xmas": 18,
    "Candy Cane": 50,
    "Halloween": 20,
    "Red Gold": 40,
    "July 4th": 39,
    "Valentine": 46,
    "St. Patrick": 47,
    "May Day": 48,
    "Snow Day": 54,
}


def packet(cmd: int, payload: list[int]) -> bytes:
    body = [CMD_PREFIX, cmd, len(payload)] + list(payload)
    return bytes(body + [sum(body) % 256])


class HelloFairy:
    """Synchronous Gio-D-Bus client for one Hello Fairy unit."""

    def __init__(self, address: str, adapter: str = "hci0"):
        self.address = address.upper()
        self.adapter = adapter
        self.dev_path = (
            f"/org/bluez/{adapter}/dev_{address.upper().replace(':', '_')}"
        )
        self.bus = None
        self.cmd_char = None
        self.notify_char = None
        self.connected = False
        # cached local state
        self.power_state = None
        self.mode = None
        self.brightness = None
        self.hsv = None
        self.current_preset = None

    # ---- D-Bus helpers -------------------------------------------------
    def _call(self, obj, iface, method, params=None, ms=-1):
        p = Gio.DBusProxy.new_sync(self.bus, Gio.DBusProxyFlags.NONE, None,
                                   "org.bluez", obj, iface, None)
        return p.call_sync(method, params, 0, ms, None)

    def _get(self, obj, iface, prop):
        r = self._call(obj, "org.freedesktop.DBus.Properties", "Get",
                       GLib.Variant("(ss)", (iface, prop)))
        v = r.get_child_value(0)
        while v.get_type_string() == 'v':
            v = v.get_variant()
        return v

    def _read_value(self):
        """Return the notify char's current Value (latest device message)."""
        v = self._get(self.notify_char, "org.bluez.GattCharacteristic1", "Value")
        if v.get_type_string() == 'ay':
            return bytes(v.unpack())
        return b""

    # ---- connection ------------------------------------------------------
    def _in_object_manager(self) -> bool:
        obj = self._call("/", "org.freedesktop.DBus.ObjectManager",
                         "GetManagedObjects", None).get_child_value(0)
        for i in range(obj.n_children()):
            if obj.get_child_value(i).get_child_value(0).get_string() == self.dev_path:
                return True
        return False

    def _scan_for_path(self, scan_timeout: float):
        """Short discovery so a dropped device is re-added to BlueZ's cache."""
        try:
            self._call(f"/org/bluez/{self.adapter}", "org.bluez.Adapter1", "StartDiscovery", ms=8000)
        except Exception:
            return False
        deadline = time.time() + scan_timeout
        while time.time() < deadline:
            if self._in_object_manager():
                break
            time.sleep(0.5)
        try:
            self._call(f"/org/bluez/{self.adapter}", "org.bluez.Adapter1", "StopDiscovery", ms=5000)
        except Exception:
            pass
        return self._in_object_manager()

    def connect(self, timeout: float = 15.0, scan_timeout: float = 8.0) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        # if the cached device object is gone, briefly scan to re-add it
        if not self._in_object_manager():
            self._scan_for_path(scan_timeout)
        # directed connect by address (works even if the unit is not advertising)
        try:
            self._call(self.dev_path, "org.bluez.Device1", "Connect")
        except Exception:
            pass  # already connected / will confirm below
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self._get(self.dev_path, "org.bluez.Device1", "Connected").get_boolean():
                    break
            except Exception:
                pass
            time.sleep(0.3)
        if not self._get(self.dev_path, "org.bluez.Device1", "Connected").get_boolean():
            raise ConnectionError(f"{self.address} did not connect on {self.adapter}")

        # locate the ISSC service's command + notify characteristics by UUID
        obj = self._call("/", "org.freedesktop.DBus.ObjectManager",
                         "GetManagedObjects", None).get_child_value(0)
        for i in range(obj.n_children()):
            entry = obj.get_child_value(i)
            path = entry.get_child_value(0).get_string()
            if not path.startswith(self.dev_path + "/"):
                continue
            if path.endswith("/char") or "/char" not in path:
                continue
            if "org.bluez.GattCharacteristic1" not in self._ifaces(entry):
                continue
            try:
                u = self._get(path, "org.bluez.GattCharacteristic1", "UUID").get_string()
            except Exception:
                continue
            if u == COMMAND_CHAR:
                self.cmd_char = path
            elif u == NOTIFY_CHAR:
                self.notify_char = path
        if not self.cmd_char or not self.notify_char:
            raise ConnectionError("could not find Hello Fairy command/notify characteristics")
        # enable notifications so the device pushes replies to Value
        try:
            self._call(self.notify_char, "org.bluez.GattCharacteristic1", "StartNotify")
        except Exception:
            pass
        self.connected = True

    def _ifaces(self, entry):
        val = entry.get_child_value(1)
        names = []
        for j in range(val.n_children()):
            names.append(val.get_child_value(j).get_child_value(0).get_string())
        return names

    def disconnect(self) -> None:
        try:
            self._call(self.dev_path, "org.bluez.Device1", "Disconnect")
        except Exception:
            pass
        self.connected = False

    # ---- protocol ---------------------------------------------------------
    def _write_command(self, cmd: int, payload: list[int]) -> bytes:
        """Write a command and wait for the device reply on the notify char."""
        pkt = packet(cmd, payload)
        before = self._read_value()
        self._call(self.cmd_char, "org.bluez.GattCharacteristic1", "WriteValue",
                   GLib.Variant("(aya{sv})", (list(pkt), {})))
        # wait for the reply: return as soon as the value changes, else after
        # a settle window the fresh reply is identical and we return it anyway.
        deadline = time.time() + 2.5
        last = before
        while time.time() < deadline:
            v = self._read_value()
            if v and v != last:
                return v
            time.sleep(0.08)
        v = self._read_value()
        if v:
            return v
        raise TimeoutError(f"no reply for cmd 0x{cmd:02x} from {self.address}")

    # ---- read-only ---------------------------------------------------------
    def request(self, cmd: int) -> bytes:
        """Send a request (info/status) and return the device reply."""
        return self._write_command(cmd, [])

    def get_info(self) -> bytes:
        return self.request(CMD_INFO)

    def get_status(self) -> bytes:
        return self.request(CMD_STATUS)

    # ---- controls ----------------------------------------------------------
    def power(self, on: bool) -> None:
        self._write_command(CMD_POWER, [0x01 if on else 0x00])
        self.power_state = on
        if not on:
            self.mode = self.brightness = self.current_preset = None
            self.hsv = None

    def set_color_hsv(self, h: int, s: int, v: int) -> None:
        if not self.power_state:
            self.power(True)
            time.sleep(0.1)
        h = max(0, min(359, h)); s = max(0, min(100, s)); v = max(0, min(100, v))
        payload = [MODE_COLOR,
                   h >> 8, h & 0xFF,
                   (s * 10) >> 8, (s * 10) & 0xFF,
                   (v * 10) >> 8, (v * 10) & 0xFF]
        self._write_command(CMD_COLOR_PRESET, payload)
        self.hsv = (h, s, v); self.brightness = v; self.mode = MODE_COLOR
        self.current_preset = None

    def set_color_rgb(self, r: int, g: int, b: int) -> None:
        import colorsys
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        self.set_color_hsv(int(h * 360), int(s * 100), int(v * 100))

    def set_preset(self, preset: int, brightness: int = 50) -> None:
        if not self.power_state:
            self.power(True)
            time.sleep(0.1)
        preset = max(1, min(58, preset)); brightness = max(0, min(100, brightness))
        payload = [MODE_PRESET, preset, (brightness * 10) >> 8, (brightness * 10) & 0xFF]
        self._write_command(CMD_COLOR_PRESET, payload)
        self.current_preset = preset; self.brightness = brightness
        self.mode = MODE_PRESET; self.hsv = None

    def set_effect(self, name: str, brightness: int = 50) -> None:
        key = name.lower()
        matches = [k for k in EFFECT_PRESETS if k.lower() == key]
        if not matches:
            raise KeyError(f"unknown effect '{name}'")
        self.set_preset(EFFECT_PRESETS[matches[0]], brightness)