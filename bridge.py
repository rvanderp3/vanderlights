#!/usr/bin/env python3
"""xLights -> Bluetooth bridge (sACN / E1.31 -> light devices).

Listens for sACN universes on the LAN and routes RGB channel groups to the
configured Bluetooth lights. One JSON config maps a DMX address group (3
channels: R,G,B) to a device.

Config (config.json)::
{
  "throttle_ms": 400,          // min time between sends to a light
  "delta_threshold": 4,        // ignore changes smaller than this (0-255)
  "bind_addr": "0.0.0.0",      // multicast listen interface
  "listen_on": "127.0.0.1:5568", // optional: unicast listen (host:port)
  "devices": {
    "tree": { "type": "hello_fairy", "address": "BE:29:D8:00:02:FC",
              "adapter": "hci0" },
    "aurora": { "type": "govee", "address": "C2:..." }   // future targets
  },
  "rgb": [
    { "universe": 1, "address": 1, "device": "tree" },
    { "universe": 1, "address": 4, "device": "aurora" }
  ]
}

Each "rgb" entry = one DMX group of 3 channels (R at address, G, B) driving
one device. Add more devices by subclassing Device in devices.py and mapping
more channel groups.

Run:
  .venv/bin/python bridge.py -c config.json            # live
  .venv/bin/python bridge.py -c config.json --dry-run  # log only, no BLE
  .venv/bin/python bridge.py -c config.json --selftest # push test frames locally
"""
import json
import logging
import threading
import time
import argparse

import sacn

from devices import make_device

log = logging.getLogger("lights.bridge")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")


class Output:
    """One DMX RGB group bound to one device, with throttle + deadband."""

    def __init__(self, device, throttle_ms=400, delta=4, dry=False):
        self.device = device
        self.throttle = throttle_ms / 1000.0
        self.delta = delta
        self.dry = dry
        self.last = None
        self.last_t = 0.0

    def update(self, rgb) -> bool:
        now = time.monotonic()
        if self.last is not None:
            elapsed = now - self.last_t
            changed = any(abs(c - o) > self.delta for c, o in zip(rgb, self.last))
            if not changed or elapsed < self.throttle:
                return False
        self.last = rgb
        self.last_t = now
        if self.dry:
            log.info("DRY  %-16s rgb=(%3d,%3d,%3d)", self.device.name, *rgb)
        else:
            self.device.set_rgb(*rgb)
        return True


class Bridge:
    def __init__(self, config, dry=False):
        self.cfg = config
        self.dry = dry
        self.throttle_ms = config.get("throttle_ms", 400)
        self.delta = config.get("delta_threshold", 4)
        self.bind_addr = config.get("bind_addr", "0.0.0.0")
        self.listen_on = config.get("listen_on")
        if isinstance(self.listen_on, str):
            host, port = self.listen_on.rsplit(":", 1)
            self.listen_on = (host, int(port))

        self.devices = {}
        for name, dcfg in config.get("devices", {}).items():
            dcfg = dict(dcfg)
            dcfg.setdefault("name", name)
            self.devices[name] = make_device(dcfg)

        self.outputs = {}            # (universe, address) -> Output
        self.universes = set()
        for e in config.get("rgb", []):
            u = int(e["universe"]); a = int(e["address"])
            dev = self.devices[e["device"]]
            self.universes.add(u)
            self.outputs[(u, a)] = Output(dev, self.throttle_ms, self.delta, dry)

        self.lock = threading.RLock()
        self.receiver = sacn.sACNreceiver()
        for u in self.universes:
            self.receiver.register_listener("universe", self._on_universe, universe=u)
        self.sends = 0

    # ---- events ---------------------------------------------------------
    def _on_universe(self, packet):
        uni = packet.universe
        data = packet.dmxData or ()
        with self.lock:
            for (u, a), out in self.outputs.items():
                if u != uni:
                    continue
                base = a - 1
                if base + 2 < len(data):
                    rgb = (data[base], data[base + 1], data[base + 2])
                    try:
                        if out.update(rgb):
                            self.sends += 1
                    except Exception as e:
                        log.error("update %s failed: %s", out.device.name, e)

    # ---- lifecycle ------------------------------------------------------
    def start(self):
        if self.listen_on:
            self.receiver.listen_on(self.listen_on)
        else:
            for u in self.universes:
                self.receiver.join_multicast(u)
        self.receiver.start()
        log.info("listening on universes: %s", sorted(self.universes))
        if not self.dry:
            for name, dev in self.devices.items():
                try:
                    dev.connect()
                except Exception as e:
                    log.warning("connect %s failed: %s (will retry on first use)", name, e)

    def stop(self):
        self.receiver.stop()
        for dev in self.devices.values():
            try:
                dev.close()
            except Exception:
                pass

    def run(self, until=None):
        self.start()
        try:
            if until:
                deadline = time.time() + until
                while time.time() < deadline:
                    time.sleep(0.2)
            else:
                while True:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            log.info("stopped; %d BLE sends", self.sends)


def selftest(config, dry, uni=1, duration=5.0):
    """Send real sACN frames over UDP to exercise the full receive path."""
    import socket
    from sacn.messages.data_packet import DataPacket

    # force unicast localhost listen for the self-test (same-host multicast is
    # not looped by this OS; a real xLights host on the LAN uses multicast/unicast)
    cfg = dict(config)
    cfg["listen_on"] = config.get("listen_on") or "127.0.0.1:5568"
    bridge = Bridge(cfg, dry=dry)
    bridge.start()

    host, port = bridge.listen_on
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    zero = [0] * 512
    frames = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (50, 120, 200), (0, 0, 0)]
    try:
        for idx, rgb in enumerate(frames, 1):
            d = zero.copy()
            d[0], d[1], d[2] = rgb
            pkt = DataPacket((0x54, 0x49, 0x54, 0x41) + (0x54, 0x49, 0x54, 0x41) * 3,
                             "bridge-selftest", uni, dmxData=tuple(d),
                             sequence=(idx % 255) + 1)   # xLights increments the sequence
            sock.sendto(bytes(pkt.getBytes()), (host, port))
            log.info("selftest frame -> uni %d ch1-3 = %s", uni, rgb)
            time.sleep(duration / len(frames))
    finally:
        sock.close()
        bridge.stop()
    log.info("selftest done; %d BLE sends (dry=%s)", bridge.sends, dry)


def main():
    ap = argparse.ArgumentParser(description="xLights -> Bluetooth bridge")
    ap.add_argument("-c", "--config", default="config.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="log intended BLE sends instead of sending")
    ap.add_argument("--selftest", action="store_true",
                    help="push local test frames, then exit")
    ap.add_argument("--run-secs", type=float, default=0,
                    help="run for N seconds then exit (0 = forever)")
    args = ap.parse_args()

    with open(args.config) as fh:
        config = json.load(fh)

    if args.selftest:
        selftest(config, dry=args.dry_run)
        return

    bridge = Bridge(config, dry=args.dry_run)
    bridge.run(until=args.run_secs or None)


if __name__ == "__main__":
    main()