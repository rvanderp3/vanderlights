#!/usr/bin/env python3
"""Command-line tool for controlling Hello Fairy BLE lights.

Examples:
  ./hf_cli.py scan
  ./hf_cli.py --addr BE:29:D8:00:02:FC status
  ./hf_cli.py --addr BE:29:D8:00:02:FC info
  ./hf_cli.py --addr BE:29:D8:00:02:FC power on
  ./hf_cli.py --addr BE:29:D8:00:02:FC power off
  ./hf_cli.py --addr BE:29:D8:00:02:FC color 120 100 100     # HSV
  ./hf_cli.py --addr BE:29:D8:00:02:FC rgb 255 0 0
  ./hf_cli.py --addr BE:29:D8:00:02:FC preset 18             # Xmas
  ./hf_cli.py --addr BE:29:D8:00:02:FC effect Xmas
  ./hf_cli.py --effects
"""
import argparse
import sys
import time

from gi.repository import Gio, GLib

from hello_fairy import EFFECT_PRESETS, HelloFairy

PREFIX = "Hello Fairy-"


def scan():
    """Discovery helper: ask BlueZ's adapters via a short scan (best-effort)."""
    name = f"Hello Fairy scan (adapter hci0)"
    print("Scanning for Hello Fairy devices (12 s)...", flush=True)
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    ad = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None,
                                "org.bluez", "/org/bluez/hci0", "org.bluez.Adapter1", None)
    try:
        ad.call_sync("StartDiscovery", None, 0, -1, None)
    except Exception:
        pass
    time.sleep(12)
    try:
        ad.call_sync("StopDiscovery", None, 0, -1, None)
    except Exception:
        pass
    # read the cached device list, filter for Hello Fairy
    obj = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None,
                                  "org.bluez", "/", "org.freedesktop.DBus.ObjectManager", None)
    r = obj.call_sync("GetManagedObjects", None, 0, -1, None).get_child_value(0)
    found = []
    for i in range(r.n_children()):
        e = r.get_child_value(i)
        path = e.get_child_value(0).get_string()
        if not path.startswith("/org/bluez/hci0/dev_"):
            continue
        name = ""
        al = e.get_child_value(1)
        for j in range(al.n_children()):
            iface = al.get_child_value(j).get_child_value(0).get_string()
            if iface == "org.bluez.Device1":
                # find Alias / Name prop
                props = al.get_child_value(j).get_child_value(1)
                for k in range(props.n_children()):
                    pe = props.get_child_value(k)
                    if pe.get_child_value(0).get_string() in ("Name", "Alias"):
                        name = pe.get_child_value(1)
                        while name.get_type_string() == 'v':
                            name = name.get_variant()
                        name = name.get_string()
                        break
        if name.startswith(PREFIX):
            addr = path.split("dev_")[1].replace("_", ":")
            found.append((addr, name))
    if not found:
        print("No Hello Fairy units found in BlueZ's cache.")
        return 1
    for addr, name in sorted(found):
        print(f"  {addr}  {name}")
    return 0


def run(addr: str, args) -> int:
    hf = HelloFairy(addr)
    print(f"Connecting to {addr} ...", flush=True)
    try:
        hf.connect()
    except Exception as e:
        print(f"Connect failed: {e}", flush=True)
        return 1
    print("connected", flush=True)
    try:
        a = args.action
        if a == "status":
            raw = hf.get_status()
            print("status raw:", raw.hex(" ") if raw else "(empty)")
            print(f"  power={hf.power_state} mode={hf.mode} brightness={hf.brightness} "
                  f"preset={hf.current_preset} hsv={hf.hsv}")
        elif a == "info":
            raw = hf.get_info()
            print("info raw:", raw.hex(" ") if raw else "(empty)")
        elif a == "power":
            on = args.value.lower() in ("on", "1", "true")
            hf.power(on)
            print(f"power -> {'on' if on else 'off'} (ACK ok)")
        elif a == "color":
            h, s, v = (int(x) for x in args.values)
            hf.set_color_hsv(h, s, v)
            print(f"color <- HSV({h},{s},{v}) (ACK ok)")
        elif a == "rgb":
            r, g, b = (int(x) for x in args.values)
            hf.set_color_rgb(r, g, b)
            print(f"color <- RGB({r},{g},{b}) (ACK ok)")
        elif a == "preset":
            n = int(args.value)
            hf.set_preset(n, args.brightness)
            print(f"preset <- {n} (ACK ok)")
        elif a == "effect":
            hf.set_effect(args.value, args.brightness)
            print(f"effect <- {args.value} (ACK ok)")
        else:
            print(f"unknown action: {a}")
            return 2
        return 0
    finally:
        hf.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(description="Hello Fairy BLE control")
    ap.add_argument("--addr", help="device MAC address")
    ap.add_argument("action", nargs="?",
                    choices=["scan", "status", "info", "power", "color", "rgb",
                             "preset", "effect"])
    ap.add_argument("value", nargs="?")
    ap.add_argument("values", nargs="*")
    ap.add_argument("--brightness", type=int, default=50)
    ap.add_argument("--effects", action="store_true")
    args = ap.parse_args()

    if args.effects:
        for name, num in EFFECT_PRESETS.items():
            print(f"  {num:>3}  {name}")
        return 0
    if not args.action:
        ap.error("an action is required")
    if args.action == "scan":
        return scan()
    if not args.addr:
        ap.error("--addr is required")
    if args.action in ("color", "rgb") and len(args.values) != 3:
        ap.error(f"{args.action} needs 3 values")
    return run(args.addr, args)


if __name__ == "__main__":
    sys.exit(main())