#!/usr/bin/env python3
"""netlightd - DDP -> BLE bridge for Hello Fairy (Hysiry) net lights.

Listens for DDP packets (UDP 4048, as sent natively by xLights) and pushes
state changes to the lights over BLE using the Hello Fairy protocol
(reverse-engineered from com.hsr.hellofairy1 + an HCI capture).

Protocol (service 49535343-fe7d-..., write 49535343-8841-..., w/o response):
    frame = AA TYPE LEN payload... SUM     SUM = low byte of the sum of every earlier byte
    02 power        [0|1]                      off / on
    03 light mode   01 H(2) S(2) V(2)          solid colour (H 0-359, S/V 0-1000)
                    02 N V(2)                  built-in scene N (1-58) + brightness
    00 device info  (reply: firmware, model, LED count via notification)

Channel layout per net (configurable, default 6 channels):
    +0 R  +1 G  +2 B  +3 brightness  +4 mode  +5 speed
  * brightness 0-255 maps to V 0-1000; brightness 0 turns the light off.
  * mode 0 = solid RGB colour; mode 1..58 = built-in scene (preset).
  * speed is reserved (the scene protocol has no separate speed; it is kept
    for future DIY-animation support).

Usage:
  netlightd.py [--config PATH]              run the daemon
  netlightd.py --probe [MAC] [--color rrggbb]   connect, query info, send colour
  netlightd.py --list                       show configured nets and layout
"""
import argparse
import asyncio
import colorsys
import json
import os
import signal
import sys
import time

from bleak import BleakClient

WRITE_UUID = "49535343-8841-43f4-a8d4-ecbe34729bb3"  # write w/o response
NOTIFY_UUID = "49535343-1e4d-4bd9-ba61-23c647249616"  # replies / status

DEFAULT_CHANNELS = ["R", "G", "B", "brightness", "mode", "speed"]

# Built-in scene presets (reverse-engineered from the Hello Fairy app).
# The controller exposes scene slots 1..58; only these themed presets are
# confidently named in the public reverse-engineering. Other numbers are valid
# but unnamed (steady/rainbow/fade-type build-ins).
SCENES = {
    8: "Blue with Pink Sparkle",
    17: "Fireworks",
    18: "Xmas",
    20: "Halloween",
    39: "July 4th",
    40: "Red Gold",
    41: "Blue White Dissolve",
    46: "Valentine",
    47: "St. Patrick",
    48: "May Day",
    50: "Candy Cane",
    54: "Snow Day",
    56: "Blue Sparkle",
    57: "White Sparkle",
}


# ---------------------------------------------------------------------------
# Hello Fairy protocol framing
# ---------------------------------------------------------------------------

def frame(kind, payload=b""):
    body = bytes([0xAA, kind, len(payload)]) + payload
    return body + bytes([sum(body) & 0xFF])


def power(on):
    return frame(0x02, bytes([1 if on else 0]))


def solid_color(r, g, b, v=1000):
    """RGB 0-255 -> HSV solid-colour frame. v (brightness) 0-1000."""
    h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return hsv_color(round(h * 360) % 360, round(s * 1000), v)


def hsv_color(h, s=1000, v=1000):
    h = max(0, min(359, int(h)))
    s = max(0, min(1000, int(s)))
    v = max(10, min(1000, int(v)))
    p = bytes([1]) + h.to_bytes(2, "big") + s.to_bytes(2, "big") + v.to_bytes(2, "big")
    return frame(0x03, p)


def scene(n, v=1000):
    n = max(1, min(58, int(n)))
    v = max(10, min(1000, int(v)))
    return frame(0x03, bytes([2, n]) + v.to_bytes(2, "big"))


def info_request():
    return frame(0x00)


# ---------------------------------------------------------------------------
# Per-net state + BLE writer
# ---------------------------------------------------------------------------

class Net:
    def __init__(self, cfg):
        self.name = cfg["name"]
        self.mac = cfg["mac"].upper()
        self.offset = cfg.get("offset", 0)
        self.channels = cfg.get("channels", DEFAULT_CHANNELS)
        self.client = None
        self.desired = [0] * len(self.channels)
        self.last_sent = None
        self.connected = False

    # -- DDP input ---------------------------------------------------------
    def apply(self, payload, base):
        changed = False
        for i, ch in enumerate(self.channels):
            idx = self.offset - base + i
            if 0 <= idx < len(payload):
                v = payload[idx]
                if v != self.desired[i]:
                    self.desired[i] = v
                    changed = True
        return changed

    # -- state / commands --------------------------------------------------
    def state(self):
        d = {name: self.desired[i] for i, name in enumerate(self.channels)}
        r, g, b = d.get("R", 0), d.get("G", 0), d.get("B", 0)
        bright = d.get("brightness", 0)
        mode = d.get("mode", 0)
        speed = d.get("speed", 0)
        return (r, g, b, bright, mode, speed)

    def describe(self):
        r, g, b, bright, mode, speed = self.state()
        if mode:
            nm = SCENES.get(mode)
            label = f"scene {mode} ({nm})" if nm else f"scene {mode}"
        else:
            label = "solid"
        return f"({r}, {g}, {b}, bri={bright}, {label}, speed={speed})"

    def commands(self):
        r, g, b, bright, mode, speed = self.state()
        if bright == 0:
            return [power(False)]
        v = max(10, round(bright / 255 * 1000))
        if mode == 0:
            return [power(True), solid_color(r, g, b, v)]
        return [power(True), scene(mode, v)]

    # -- BLE ---------------------------------------------------------------
    async def ensure_connected(self):
        if self.client is not None and self.client.is_connected:
            return True
        c = BleakClient(self.mac)
        await c.connect()
        self.client = c
        return True

    async def push(self):
        """Write the current state to the light if it changed. Returns True on send."""
        sig = self.state()
        if sig == self.last_sent:
            return False
        try:
            await self.ensure_connected()
        except Exception:
            self.client = None
            self.connected = False
            raise
        self.connected = True
        for f in self.commands():
            await self.client.write_gatt_char(WRITE_UUID, f, response=False)
            await asyncio.sleep(0.05)
        self.last_sent = sig
        return True


# ---------------------------------------------------------------------------
# DDP (asyncio datagram)
# ---------------------------------------------------------------------------

class DdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, nets):
        self.nets = nets

    def datagram_received(self, data, addr):
        if len(data) < 10:
            return
        # DDP header: [0]=ver [1]=flags [2]=seq [3]=type [4:8]=offset [8:10]=len
        offset = int.from_bytes(data[4:8], "big")
        length = int.from_bytes(data[8:10], "big")
        payload = data[10:10 + length]
        if not payload:
            return
        for n in self.nets:
            if n.offset < offset + length and n.offset + len(n.channels) > offset:
                n.apply(payload, offset)


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

async def net_worker(net, stop, log, interval, heartbeat, first_delay=0.0):
    if first_delay:
        await asyncio.sleep(first_delay)
    backoff = 1.0
    last_change = time.time()
    while not stop.is_set():
        try:
            if net.connected and time.time() - last_change > heartbeat:
                net.last_sent = None  # force resend to keep link fresh
            if await net.push():
                log(f"{net.name}: {net.describe()}")
                last_change = time.time()
            backoff = 1.0
        except Exception as e:
            log(f"{net.name}: error: {type(e).__name__}: {e}")
            net.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            continue
        await asyncio.sleep(interval)


async def run_daemon(cfg, log):
    interval = cfg.get("interval", 0.1)
    heartbeat = cfg.get("heartbeat", 20.0)
    nets = [Net(n) for n in cfg["nets"]]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    for n in nets:
        log(f"  {n.name}: {n.mac} offset={n.offset} channels={n.channels}")

    # Stagger first-connect attempts so BlueZ doesn't reject them all at once.
    workers = [asyncio.create_task(net_worker(n, stop, log, interval, heartbeat,
                                              first_delay=i * 1.5))
               for i, n in enumerate(nets)]

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: DdpProtocol(nets),
        local_addr=(cfg.get("listen", "0.0.0.0"), cfg.get("port", 4048)))
    log(f"DDP listening on {cfg.get('listen','0.0.0.0')}:{cfg.get('port', 4048)}")

    try:
        await stop.wait()
    finally:
        for w in workers:
            w.cancel()
        transport.close()
        for n in nets:
            if n.client is not None:
                try:
                    await n.client.disconnect()
                except Exception:
                    pass
        log("netlightd stopped")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_config(path):
    with open(path) as f:
        return json.load(f)


async def run_probe(cfg, mac, color_hex):
    nets = [Net(n) for n in cfg["nets"]]
    net = None
    if mac:
        mac = mac.upper()
        net = next((n for n in nets if n.mac == mac), None)
        if net is None:
            print(f"unknown MAC {mac}; configured: {[n.mac for n in nets]}")
            sys.exit(1)
    elif nets:
        net = nets[0]

    print(f"probing {net.name} ({net.mac})", flush=True)
    await net.ensure_connected()
    print(f"connected (mtu={net.client.mtu_size})", flush=True)

    # query device info (firmware / model / LED count) via notification
    info = {}
    try:
        got = []

        def cb(sender, data):
            got.append(data)

        await net.client.start_notify(NOTIFY_UUID, cb)
        await net.client.write_gatt_char(WRITE_UUID, info_request(), response=False)
        await asyncio.sleep(1.5)
        await net.client.stop_notify(NOTIFY_UUID)
        if got:
            d = got[0]
            p = d[3:-1] if len(d) >= 4 else b""
            info["firmware"] = p[:5].decode("ascii", "replace")
            info["model"] = p[5:20].split(b"\x00", 1)[0].decode("ascii", "replace")
            info["led_count"] = int.from_bytes(p[20:22], "big") if len(p) >= 22 else None
    except Exception as e:
        print(f"(info query failed: {e})", flush=True)

    if info:
        print(f"device: model={info.get('model')} firmware={info.get('firmware')} "
              f"leds={info.get('led_count')}", flush=True)

    if color_hex:
        rgb = bytes.fromhex(color_hex)
        if len(rgb) != 3:
            print("color must be rrggbb")
            await net.client.disconnect()
            sys.exit(1)
        r, g, b = rgb
        await net.client.write_gatt_char(WRITE_UUID, power(True), response=False)
        await asyncio.sleep(0.1)
        await net.client.write_gatt_char(WRITE_UUID, solid_color(r, g, b, 1000), response=False)
        print(f"sent power-on + colour #{color_hex}", flush=True)
        await asyncio.sleep(1.0)

    await net.client.disconnect()
    print("done")


def run_list(cfg):
    for n in cfg["nets"]:
        print(f"{n['name']:<6} {n['mac']}  offset={n['offset']}  "
              f"channels={n.get('channels', DEFAULT_CHANNELS)}")


def run_scenes():
    print("Named scene presets (mode channel):")
    for num in sorted(SCENES):
        print(f"  {num:>3}  {SCENES[num]}")
    print("Other numbers 1..58 are valid built-in scenes (unnamed).")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.expanduser("~/netlight/config.json"))
    ap.add_argument("--probe", nargs="?", const="", metavar="MAC",
                    help="connect & optionally send a colour")
    ap.add_argument("--color", metavar="rrggbb", help="colour for --probe")
    ap.add_argument("--list", action="store_true", help="show configured nets")
    ap.add_argument("--scenes", action="store_true", help="list named scene presets")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.scenes:
        run_scenes()
        return
    if args.list:
        run_list(cfg)
        return
    if args.probe is not None:
        asyncio.run(run_probe(cfg, (args.probe or None).upper() if args.probe else None,
                              args.color))
        return

    def log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    log("netlightd starting")
    try:
        asyncio.run(run_daemon(cfg, log))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
