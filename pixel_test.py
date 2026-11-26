#!/usr/bin/env python3
"""Per-LED (DIY) control test for Hello Fairy curtains.

Reverse-engineered protocol (com.hsr.hellofairy1 + HCI capture):
  frame = AA TYPE LEN payload... SUM       SUM = low byte of sum of earlier bytes
  D0 = picture settings [direction, brightness, speed, kind]
       kind: 0 edit, 1 show static, 2 run anim, 3 new static, 4 save, 6 H-mode, 7 new anim
  D7 = frame bookkeeping [frame, state]   state 0 new 1 edit 2 save 3 delete
  DA = pixel packet [frame, {led(2 BE) colour(1)}, ...]
  D4/D3/DB/D9 = text (not used here)

Colour byte: hue/2 (0-179), 255 white, 254 off.

Usage:
  pixel_test.py MAC --fill HUE          solid fill (HUE 0-179, or 255 white)
  pixel_test.py MAC --grid              reveal indexing: index 0,1,19,20 lit distinctly
"""
import argparse
import asyncio
import colorsys
import sys

from bleak import BleakClient

WRITE = "49535343-8841-43f4-a8d4-ecbe34729bb3"
NOTIFY = "49535343-1e4d-4bd9-ba61-23c647249616"

# grid hypothesis: 30 columns x 20 rows (index = x*20 + y)
WIDTH, HEIGHT = 30, 20
LEDS = WIDTH * HEIGHT

OFF, WHITE = 254, 255


def frame(kind, payload=b""):
    body = bytes([0xAA, kind, len(payload)]) + payload
    return body + bytes([sum(body) & 0xFF])


def power(on):
    return frame(0x02, bytes([1 if on else 0]))


def pic_param(direction, speed, kind, bright=100):
    return frame(0xD0, bytes([direction, bright, speed, kind]))


def pic_frame_state(num, state):
    return frame(0xD7, bytes([num, state]))


def hue_byte(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if v < 0.1:
        return OFF
    if s < 0.1:
        return WHITE
    return min(round(h * 360 / 2), 179)


def da_packets(num, colours, max_payload=238):
    per = (max_payload - 1) // 3  # 79 LEDs per 242-byte packet
    for i in range(0, len(colours), per):
        body = bytes([num])
        for led in range(i, min(i + per, len(colours))):
            body += led.to_bytes(2, "big") + bytes([colours[led]])
        yield frame(0xDA, body)


def static_frames(colours, speed=50, direction=0, bright=100):
    """Frames for a single static picture (H-mode). colours: LEDS hue bytes."""
    out = []
    out.append((pic_param(direction, speed, 6, bright), 0.1))   # H-mode
    out.append((pic_param(0, speed, 3, bright), 0.1))           # new static
    out.append((pic_param(0, speed, 0, bright), 0.1))           # edit
    for p in da_packets(1, colours):
        out.append((p, 0.05))                                    # pixel data
    out.append((pic_param(direction, speed, 4, bright), 1.2))   # save (long delay)
    out.append((pic_param(direction, speed, 1, bright), 0.2))   # show static
    return out


async def acquire_mtu(c):
    try:
        await c._backend._acquire_mtu()
    except Exception as e:
        print(f"(mtu acquire: {e})")
    return c.mtu_size


async def upload(mac, colours, speed=50):
    c = BleakClient(mac)
    await c.connect()
    mtu = await acquire_mtu(c)
    print(f"connected {mac}  mtu={mtu}")
    seq = static_frames(colours, speed=speed)
    sent = 0
    for data, delay in seq:
        await c.write_gatt_char(WRITE, data, response=False)
        sent += 1
        await asyncio.sleep(delay)
    print(f"sent {sent} frames ({len(colours)} pixels)")
    await c.disconnect()


async def fill(mac, hue):
    print(f"solid fill hue={hue} ({LEDS} px, {WIDTH}x{HEIGHT})")
    await upload(mac, [hue] * LEDS)


async def grid_test(mac):
    cols = [OFF] * LEDS
    cols[0] = 0      # index 0   -> red
    cols[1] = 60     # index 1   -> cyan-ish
    cols[2] = 120    # index 2   -> green-ish
    idx19 = 19       # candidate: bottom of column 0
    idx20 = 20       # candidate: top of column 1
    if idx19 < LEDS:
        cols[idx19] = WHITE
    if idx20 < LEDS:
        cols[idx20] = 179  # magenta-ish
    print("grid test: idx0=red idx1=cyan idx2=green idx19=WHITE idx20=magenta")
    await upload(mac, cols)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mac")
    ap.add_argument("--fill", type=int, metavar="HUE")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--speed", type=int, default=50)
    args = ap.parse_args()
    mac = args.mac.upper()
    if args.grid:
        await grid_test(mac)
    elif args.fill is not None:
        await fill(mac, args.fill)
    else:
        print("choose --fill HUE or --grid", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
