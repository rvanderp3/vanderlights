#!/usr/bin/env python3
"""perled - Hello Fairy per-LED picture & animation uploader.

Drives the 30x20 (600 LED) curtain in "DIY" mode using the Hello Fairy
protocol (reverse-engineered from com.hsr.hellofairy1 + an HCI capture).

Frame   : AA TYPE LEN payload... SUM       SUM = low byte of sum of earlier bytes
D0      : [direction, brightness, speed, kind]
            kind 0 edit, 1 show static, 2 run anim, 3 new static, 4 save,
                 6 H-mode (1-byte colours), 7 new animation
D7      : [frame, state]   state 0 new, 1 edit, 2 save, 3 delete
DA      : [frame, {led(2 BE) colour(1)} ...]

Grid    : 30 columns x 20 rows, index = x*20 + y (column-major).
Colour  : hue/2 (0-179), 255 = white, 254 = off.  (No per-pixel RGB/dim.)

Usage:
  perled.py MAC --fill 0|255            solid colour (0 red .. 179, 255 white)
  perled.py MAC --gradient              static rainbow gradient
  perled.py MAC --checker               checkerboard (red/white)
  perled.py MAC --text "HELLO"           render text as a picture (--hue colour)
  perled.py MAC --image logo.png         put an image on the curtain (fit 30x20)
  perled.py MAC --rainbow [--frames N]  rolling rainbow animation (unreliable,
                                        M181 firmware drops multi-frame uploads)

This is "upload a picture/animation", not live streaming: the upload takes a
couple of seconds and the curtain then plays the picture / loops the animation.
"""
import argparse
import asyncio
import colorsys
import sys

from bleak import BleakClient

WRITE = "49535343-8841-43f4-a8d4-ecbe34729bb3"
NOTIFY = "49535343-1e4d-4bd9-ba61-23c647249616"

WIDTH, HEIGHT = 30, 20
LEDS = WIDTH * HEIGHT
OFF, WHITE = 254, 255

TEXT_MODES = {"static": 0, "up": 1, "down": 2, "left": 3, "right": 4, "flash": 5,
              "breath": 6, "up-loop": 7, "down-loop": 8, "left-loop": 9,
              "right-loop": 10, "jump": 11}
MAX_TEXT = 40


# ---------------------------------------------------------------------------
# Protocol primitives
# ---------------------------------------------------------------------------

def frame(kind, payload=b""):
    body = bytes([0xAA, kind, len(payload)]) + payload
    return body + bytes([sum(body) & 0xFF])


def power(on):
    return frame(0x02, bytes([1 if on else 0]))


def pic_param(direction, speed, kind, bright=100):
    """D0: picture settings. payload = [direction, brightness, speed, kind]."""
    return frame(0xD0, bytes([direction, bright, speed, kind]))


def pic_frame_state(num, state):
    """D7: frame bookkeeping."""
    return frame(0xD7, bytes([num, state]))


def da_packets(num, colours, max_payload=238):
    """DA packets: split `colours` (LEDS hue bytes) into <=79-LED packets."""
    per = (max_payload - 1) // 3
    for i in range(0, len(colours), per):
        body = bytes([num])
        for led in range(i, min(i + per, len(colours))):
            body += led.to_bytes(2, "big") + bytes([colours[led]])
        yield frame(0xDA, body)


def hue_byte(r, g, b):
    """RGB 0-255 -> 1-byte colour (hue/2, 255 white, 254 off)."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if v < 0.1:
        return OFF
    if s < 0.1:
        return WHITE
    return min(round(h * 360 / 2), 179)


# ---------------------------------------------------------------------------
# Grid helpers
# ---------------------------------------------------------------------------

def xy(x, y):
    return x * HEIGHT + y


def flat_xy(i):
    return divmod(i, HEIGHT)  # -> (x, y)


def animation_frames(frames, speed=50, direction=0, rgb=False):
    """Full DIY frame sequence for one picture (len 1) or an animation.

    frames: list of 600-element colour lists. Returns [(data, delay_s), ...].
    """
    still = len(frames) == 1
    out = [(pic_param(direction if still else 0, speed, 6), 0.1)]       # H-mode
    out.append((pic_param(0, speed, 7 if len(frames) > 1 else 3), 0.1)) # new anim/static
    if len(frames) > 1:
        out.append((pic_frame_state(1, 0), 0.1))                        # new frame 1
    out.append((pic_param(0, speed, 0), 0.1))                           # edit
    for i, fr in enumerate(frames):
        if i:
            out += [(pic_frame_state(i, 2), 0.1),                       # save frame i
                    (pic_frame_state(i + 1, 0), 0.1)]                   # new frame i+1
        # Slow pacing matters: too-fast DA writes overflow the device buffer
        # and drop the BLE link (confirmed -> disconnect ~22 packets in).
        out += [(p, 0.15) for p in da_packets(i + 1, fr)]
    if len(frames) > 1:
        out.append((pic_frame_state(len(frames), 2), 0.1))              # save last frame
    run_dir = direction if still else 1
    out += [(pic_param(run_dir, speed, 4), 1.2),                        # save (finalize)
            (pic_param(run_dir, speed, 1 if still else 2), 0.2)]        # run
    return out


# ---------------------------------------------------------------------------
# Patterns (each returns a 600-element hue-byte list, or a list of such lists)
# ---------------------------------------------------------------------------

def fill(hue):
    return [hue] * LEDS


def gradient():
    """Static rainbow: hue varies across columns (x)."""
    cols = [OFF] * LEDS
    for x in range(WIDTH):
        hue = round(x / (WIDTH - 1) * 179)
        for y in range(HEIGHT):
            cols[xy(x, y)] = hue
    return cols


def checkerboard(a=0, b=WHITE):
    cols = [OFF] * LEDS
    for x in range(WIDTH):
        for y in range(HEIGHT):
            cols[xy(x, y)] = a if (x + y) % 2 == 0 else b
    return cols


def rainbow_roll(n_frames=24):
    """Rolling rainbow animation: hue offset advances each frame."""
    frames = []
    for f in range(n_frames):
        cols = [OFF] * LEDS
        for x in range(WIDTH):
            hue = (round(x / WIDTH * 179) + f * 8) % 180
            for y in range(HEIGHT):
                cols[xy(x, y)] = hue
        frames.append(cols)
    return frames


def text_frames(text, mode=3, speed=50, bright=100, colours=None, bg=OFF, slot=0):
    """Device built-in font text: D3 settings, D4/DB per-char, D9 save slot."""
    text = text[:MAX_TEXT]
    if any(ord(c) > 127 for c in text):
        raise ValueError("only ASCII text (device font has no other characters)")
    if colours is None:
        colours = [0]
    if isinstance(colours, int):
        colours = [colours]
    cols = [colours[i % len(colours)] for i in range(len(text))]
    out = [(frame(0xD3, bytes([mode, bright, speed, bg])), 0.1),
           (frame(0xD4, bytes([21, len(text), 0])), 0.1)]
    if len(text) <= 20:
        out += [(frame(0xD4, bytes([i + 1, ord(c), cols[i]])), 0.15)
                for i, c in enumerate(text)]
    else:
        for start in range(0, len(text), 79):
            chunk = text[start:start + 79]
            body = bytes([len(text)])
            for j, c in enumerate(chunk):
                body += bytes([min(start + j + 1, 40), ord(c), cols[start + j]])
            out.append((frame(0xDB, body), 0.15))
    if slot:
        out.append((frame(0xD9, bytes([slot, 2])), 0.2))
    return out


def image_to_grid(path):
    """Load an image, fit it to the 30x20 grid, quantize RGB -> hue bytes."""
    from PIL import Image
    img = Image.open(path).convert("RGB").resize((WIDTH, HEIGHT))
    px = img.load()
    cols = [OFF] * LEDS
    for x in range(WIDTH):
        for y in range(HEIGHT):
            r, g, b = px[x, y]
            cols[xy(x, y)] = hue_byte(r, g, b)
    return cols


def text_to_grid(text, hue=WHITE):
    """Render ASCII text to the 30x20 grid (software-rendered) via PIL.

    The device's built-in font (D3/D4) is unreliable on the M181 (shows a
    white screen and no text), so we render text to pixels and upload it as a
    normal picture instead. Static (non-scrolling), centred, auto-sized.
    """
    from PIL import Image, ImageDraw, ImageFont
    scale = 8
    W, H = WIDTH * scale, HEIGHT * scale
    img = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(img)
    fontpath = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    size = 1
    while size < H:
        f = ImageFont.truetype(fontpath, size)
        bbox = draw.textbbox((0, 0), text, font=f)
        if (bbox[2] - bbox[0]) > W * 0.92:
            break
        size += 1
    f = ImageFont.truetype(fontpath, size - 1)
    bbox = draw.textbbox((0, 0), text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((W - tw) // 2 - bbox[0], (H - th) // 2 - bbox[1]), text, fill=255, font=f)
    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    px = img.load()
    cols = [OFF] * LEDS
    for x in range(WIDTH):
        for y in range(HEIGHT):
            cols[xy(x, y)] = hue if px[x, y] > 128 else OFF
    return cols


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

async def acquire_mtu(c):
    try:
        await c._backend._acquire_mtu()
    except Exception as e:
        print(f"(mtu acquire: {e})", file=sys.stderr)
    return c.mtu_size


async def upload(mac, frames, speed=50, direction=0):
    """frames: one 600-list (static) or a list of 600-lists (animation)."""
    if isinstance(frames[0], int):
        frames = [frames]  # a single picture
    c = BleakClient(mac)
    await c.connect()
    mtu = await acquire_mtu(c)
    print(f"connected {mac}  mtu={mtu}  frames={len(frames)}")
    # NB: do NOT send power-on here — a type-02 power command drops the device
    # out of DIY mode and the picture fails to show. The D0 "show" command
    # below turns the display on itself.
    seq = animation_frames(frames, speed, direction)
    for i, (data, delay) in enumerate(seq):
        if not c.is_connected:
            print("ERROR: device dropped the BLE link mid-upload "
                  f"(write {i}/{len(seq)}). Multiple frames are unreliable on "
                  "this firmware; static pictures work fine.", file=sys.stderr)
            return
        await c.write_gatt_char(WRITE, data, response=False)
        await asyncio.sleep(delay)
    print(f"uploaded {len(frames)} frame(s) ({LEDS} LEDs)")
    await c.disconnect()


async def upload_text(mac, text, mode=3, speed=50, hue=0):
    """Upload scrolling/static text using the device's built-in font."""
    seq = [(power(True), 0.2)] + text_frames(text, mode, speed, 100, [hue])
    c = BleakClient(mac)
    await c.connect()
    mtu = await acquire_mtu(c)
    print(f"connected {mac}  mtu={mtu}  text={text!r} mode={mode}")
    for data, delay in seq:
        if not c.is_connected:
            print("ERROR: device dropped the link mid-text", file=sys.stderr)
            return
        await c.write_gatt_char(WRITE, data, response=False)
        await asyncio.sleep(delay)
    print("sent text")
    await c.disconnect()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mac")
    ap.add_argument("--fill", type=int, metavar="HUE", help="solid fill (0-179, 255 white)")
    ap.add_argument("--gradient", action="store_true", help="static rainbow gradient")
    ap.add_argument("--checker", action="store_true", help="red/white checkerboard")
    ap.add_argument("--rainbow", action="store_true", help="rolling rainbow animation")
    ap.add_argument("--frames", type=int, default=24, help="frames for --rainbow")
    ap.add_argument("--speed", type=int, default=50, help="playback speed 0-100")
    ap.add_argument("--image", metavar="PATH", help="upload an image (fit to 30x20)")
    ap.add_argument("--text", metavar="STR", help="render text as a picture")
    ap.add_argument("--hue", type=int, default=255, help="text colour (hue 0-179, 255 white)")
    args = ap.parse_args()

    mac = args.mac.upper()
    if args.text is not None:
        pattern = text_to_grid(args.text, args.hue)
        asyncio.run(upload(mac, pattern, speed=args.speed))
        return
    if args.image:
        pattern = image_to_grid(args.image)
    elif args.fill is not None:
        pattern = fill(args.fill)
    elif args.gradient:
        pattern = gradient()
    elif args.checker:
        pattern = checkerboard()
    elif args.rainbow:
        pattern = rainbow_roll(args.frames)
    else:
        print("choose --fill/--gradient/--checker/--rainbow/--image/--text",
              file=sys.stderr)
        sys.exit(1)

    asyncio.run(upload(mac, pattern, speed=args.speed))


if __name__ == "__main__":
    main()
