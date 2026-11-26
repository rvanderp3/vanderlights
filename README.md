# vanderlights

Control **Hello Fairy** smart lights (ISSC BLE chip) from an Orange Pi.

Reverse-engineered the BLE protocol and built two tools:

- **`netlightd.py`** — a **DDP → BLE bridge**: xLights sends DDP over the LAN,
  the Pi turns it into BLE commands so each light is one controllable unit
  (colour, brightness, or a built-in scene).
- **`perled.py`** — **per-LED** control: push pictures, text, and images onto
  the 30×20 pixel grid.

The full protocol is documented in [PROTOCOL.md](PROTOCOL.md).

## The lights

Each unit is model **M181**, firmware **2.2.9**, a **30×20 = 600 LED** curtain
(column-major, `index = x*20 + y`). Per-pixel colour is **hue-only** (1 byte:
`hue/2` 0–179, `255` white, `254` off) — no per-pixel RGB/dimming.

## Setup

```sh
python3 -m venv venv
./venv/bin/pip install bleak pillow
```

`netlightd.py` only needs `bleak`; `perled.py` also needs `pillow`.

### Bridge as a service

```sh
sudo systemctl link "$PWD/netlightd.service"
sudo systemctl daemon-reload
sudo systemctl enable --now netlightd.service
```

## netlightd — DDP bridge

Listens on UDP **`192.168.0.28:4048`** for DDP (as sent by xLights) and pushes
state changes to the lights. Each net uses 6 channels:

```
+0 R  +1 G  +2 B  +3 brightness  +4 mode  +5 speed
```

- `brightness` 0–255 → value 0–1000; `0` turns the light off.
- `mode` **0** = solid RGB colour; **1–58** = a built-in scene (see scenes below).
- `speed` is reserved (scenes expose no speed in this protocol).

`config.json` maps each net to a BT-MAC + DDP offset. Run `--list`, `--scenes`,
or `--probe MAC --color rrggbb` to inspect/test.

## perled — per-LED pictures, text, images

Stop the bridge first so it releases the BLE link, then e.g.:

```sh
sudo systemctl stop netlightd
./venv/bin/python perled.py <MAC> --fill 0            # solid red
./venv/bin/python perled.py <MAC> --gradient          # rainbow gradient
./venv/bin/python perled.py <MAC> --checker           # checkerboard
./venv/bin/python perled.py <MAC> --text "HELLO"       # text as a picture
./venv/bin/python perled.py <MAC> --image logo.png     # an image (fit 30x20)
sudo systemctl start netlightd                        # resume bridge
```

Notes:

- Colours are **hue-only**, so fine image detail and photo gradients are lost —
  bold shapes and solid colours read best.
- Multi-frame **animations are unreliable** on the M181 (the firmware drops the
  BLE link partway through an animation upload). Static pictures are solid.

## Named scene presets

Set the `mode` channel to one of these (see `netlightd.py --scenes`):

| mode | scene | mode | scene |
|---|---|---|---|
| 8 | Blue with Pink Sparkle | 40 | Red Gold |
| 17 | Fireworks | 41 | Blue White Dissolve |
| 18 | Xmas | 46 | Valentine |
| 20 | Halloween | 47 | St. Patrick |
| 39 | July 4th | 48 | May Day |
| 50 | Candy Cane | 54 | Snow Day |
| 56 | Blue Sparkle | 57 | White Sparkle |

## xLights

Add a **DDP** output → `192.168.0.28:4048`, one universe, 24 channels used
(6 per net). Wire each net's model to `R,G,B,brightness,mode,speed`. Leave mode
at 0 for plain colours; set it 1–58 for effects.

## Files

| File | Purpose |
|------|---------|
| `netlightd.py` | DDP → BLE bridge (solid colour / scene per net) |
| `perled.py` | per-LED pictures, text, images |
| `ddptest.py` | send synthetic DDP test packets |
| `pixel_test.py` | low-level DIY-upload diagnostics |
| `config.json` | net → MAC / DDP-offset mapping |
| `netlightd.service` | systemd unit |
| `PROTOCOL.md` | the reverse-engineered protocol |
