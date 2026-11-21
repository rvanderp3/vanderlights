#!/usr/bin/env python3
"""Small coordinator-show demo for a Hello Fairy light.

Runs a timed sequence (color -> preset -> off) so it can be scheduled from the
Orange Pi coordinator. Override ADDR/ADAPTER at the top.
"""
import time

from hello_fairy import HelloFairy

ADDR = "BE:29:D8:00:02:FC"
ADAPTER = "hci0"          # hci0 = TP-Link (external antenna), hci1 = onboard


def main():
    hf = HelloFairy(ADDR, adapter=ADAPTER)
    print(f"connecting to {ADDR} on {ADAPTER} ...")
    hf.connect()
    print("connected")
    try:
        hf.power(True)
        print("on")

        hf.set_color_rgb(255, 0, 0)      # red
        print("red, hold 3s"); time.sleep(3)

        hf.set_color_rgb(0, 200, 0)      # green
        print("green, hold 3s"); time.sleep(3)

        hf.set_effect("Xmas")            # preset 18
        print("Xmas preset, hold 5s"); time.sleep(5)

        # show current reported status for the coordinator log
        print("status:", hf.get_status().hex(" "))

        hf.power(False)
        print("off")
    finally:
        hf.disconnect()


if __name__ == "__main__":
    main()