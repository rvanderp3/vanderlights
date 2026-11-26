# Hello Fairy BLE protocol

Reverse-engineered from the vendor app (`com.hsr.hellofairy1`) and an HCI
capture, and confirmed against a real **M181** / firmware **2.2.9** light.

## Transport

- Service `49535343-fe7d-4ae5-8fa9-9fafd205e455` (a transparent-UART service).
- **Write** to `49535343-8841-43f4-a8d4-ecbe34729bb3` (write-without-response).
- **Notify** on `49535343-1e4d-4bd9-ba61-23c647249616` (replies/status).
- Request an **ATT MTU ≥ 245** before DIY pixel data (the Pi negotiates 512).
- One connection at a time; advertised name starts with `Hello Fairy`.

## Frames

Every command is:

```
AA  TYPE  LEN  payload...  SUM
```

where `SUM` is the low byte of the sum of all preceding bytes
(`sum(body) & 0xFF`). `LEN` is the payload length in bytes.

## Commands

| Type | Meaning | Payload |
|---|---|---|
| `00` | device info | — (reply has firmware, model, LED count, grid) |
| `01` | status | — |
| `02` | power | `[0/1]` off / on |
| `03` | light mode | `01 H(2) S(2) V(2)` solid colour (H 0–359, S/V 0–1000, big-endian) · `02 N V(2)` built-in scene N (1–58) + brightness |
| `0C` | scene speed | `[speed 0–100]` |
| `D0` | picture settings | `[direction, brightness, speed, kind]` |
| `D7` | frame bookkeeping | `[frame, state]` state 0 new, 1 edit, 2 save, 3 delete |
| `DA` | pixel packet | `[frame, {led(2 BE) colour(1)} …]` |
| `D3/D4/DB/D9` | text (device font) | not reliable on M181 |

### `D0` picture `kind`

`0` edit · `1` show static · `2` run animation · `3` new static · `4` save ·
`6` H-mode (one-byte colours) · `7` new animation.

`D0` direction: `0` static, `1` up, `2` down, `3` left, `4` right, `5` flash,
`6` breath, `7–10` loop up/down/left/right.

## Device info reply

`AA 00 27 ...` → payload: firmware (5 ASCII bytes), model (15-byte field),
LED count (u16 BE at offset 20), then grid hints. M181 reports `"2.2.9"`,
`"M181"`, `600`.

## Grid & colour

- **30 columns × 20 rows = 600 LEDs.** Column-major: `index = x * 20 + y`
  (x = column 0–29, y = row 0–19, top-to-bottom).
- Colour byte: **`hue/2`** 0–179 (saturated), `255` white, `254` off.
  Sending true-colour (3-byte) packets makes the light drop the connection.
- A frame is 600 LEDs = 8 `DA` packets (7×79 + 47 LEDs), 242 bytes each,
  sent ~50 ms apart.

## Static picture sequence

```
D0(direction, speed, kind=6)   # H-mode
D0(0, speed, kind=3)           # new static
D0(0, speed, kind=0)           # edit
DA × 8                          # pixel data
D0(direction, speed, kind=4)   # save
D0(direction, speed, kind=1)   # show
```

## Animations (unreliable on M181)

Multi-frame uploads make the **M181 firmware drop the BLE link** ~2 frames in
(regardless of pacing or exact sequence), so animations are not used. The
frame sequence otherwise is: `D0(kind 7)`, `D7(1,0)`, `D0(kind 0)`, per frame
`DA` packets + `D7(save)/D7(new)`, `D7(last,2)`, `D0(kind 2)`, `DC[0]`.

## Scenes (named)

See `netlightd.py --scenes`; themed presets include Fireworks (17), Xmas (18),
Halloween (20), July 4th (39), Candy Cane (50), Snow Day (54), and others.

## References

- `Craterbrain/fairy-lights` — protocol notes, H-mode + animation framing
- `nzrutman/hello_fairy_ble` — static/two-picture capture, native controls
- `Malti2/hello-fairy` — packet builders, preset map
