# Hello Fairy BLE controller (Orange Pi display coordinator)

Controls **Hello Fairy** smart lights over Bluetooth from an Orange Pi.
This is the seed of the Christmas-light display coordinator: it talks to the
unit's ISSC chip (`49535343-...` = ASCII `ISSC`) over the BLE the TP-Link USB
adapter originally scanned.

## Requirements

- Orange Pi/Linux with **BlueZ** and **PyGObject** (gi) — both already present here.
  No venv / bleak needed; the transport is direct D-Bus GATT via `gi.repository.Gio`.
- A Bluetooth adapter in radio range of the lights (`hci0` = the TP-Link with
  external antenna is the default/primary; `hci1` = onboard UART).

## xLights integration (sACN -> BLE bridge)

The Orange Pi runs a small bridge that turns **xLights' sACN (E1.31) output**
into BLE commands so each Hello Fairy string is a **single RGB pixel**:

```
xLights  ->  sACN/E1.31 over LAN  ->  bridge.py  ->  BLE -> Hello Fairy
```

Run it (from `.venv`, which has the `sacn` receiver):

```sh
.venv/bin/python bridge.py -c config.json            # live
.venv/bin/python bridge.py -c config.json --dry-run  # log only, no BLE
.venv/bin/python bridge.py -c config.json --selftest # push test frames locally
```

**Config** maps each 3-channel DMX group to a device:

```json
{ "universe": 1, "address": 1, "device": "tree" }   // ch 1,2,3 = R,G,B
```

More device types = a subclass of `Device` in `devices.py` + an entry in
`DEVICE_TYPES` (Govee/ELK/ESP32 etc. can be added the same way).

### Setting it up in xLights

1. **Universe/Controller**: add a controller/output for sACN (E1.31). xLights
   sends over multicast (the bridge's default) or you can set unicast to the
   Pi's IP via `listen_on` in the config.
2. **Model**: create a **1-node RGB model** per Hello Fairy string (a single
   pixel = whole string). Wire each model's 3 channels to the matching
   `universe`/`address` in the config.
3. **Expectations**: BLE is throttled by the bridge (default 400 ms, deadband
   `delta_threshold`), so sequence these as slow-changing "dumb color" lights
   — don't expect per-frame pixel flashing.

### Notes / quirks learned
- The unit only wakes on a **direct connect-by-address**; the bridge connects
  lazily on first use and retries if the light was off. Ensure lights are
  powered on at the wall.
- xLights sends incrementing sACN **sequence numbers**; a portion of the list
  drops packets whose sequence doesn't advance (a test artifact, not a bridge
  issue).
- The discovered unit is a **string controller** (`M181`, firmware 2.2.9) —
  power / color / presets only, not per-pixel.

## Files

| File | Purpose |
|------|---------|
| `hello_fairy.py` | `HelloFairy` controller (connect / power / color / preset / effect / status / info) |
| `devices.py` | device abstraction + registry (add new BLE targets here) |
| `bridge.py` | xLights sACN/E1.31 -> device bridge |
| `config.json` | DMX universe/channel -> device mapping |
| `hf_cli.py` | command-line front end for a single light |
| `example_show.py` | short single-light demo |

## Usage

```sh
# status / info (non-destructive)
python3 hf_cli.py --addr BE:29:D8:00:02:FC status
python3 hf_cli.py --addr BE:29:D8:00:02:FC info

# controls
python3 hf_cli.py --addr BE:29:D8:00:02:FC power on|off
python3 hf_cli.py --addr BE:29:D8:00:02:FC color 120 100 100      # HSV (h s v)
python3 hf_cli.py --addr BE:29:D8:00:02:FC rgb 255 0 0
python3 hf_cli.py --addr BE:29:D8:00:02:FC preset 18              # Xmas
python3 hf_cli.py --addr BE:29:D8:00:02:FC effect "Candy Cane"

# list preset numbers
python3 hf_cli.py --effects

# discover (best-effort; the unit only wakes on a directed connect-by-address)
python3 hf_cli.py scan
```

## Protocol (reverse-engineered)

```
packet  = AA <cmd> <payload_len> <payload...> <checksum>
checksum = sum(preceding bytes) mod 256
ACKs:    power -> AA 02 00 AC      color/preset -> AA 03 00 AD
```

| Command | Payload |
|---------|---------|
| info    | `00`  (empty) |
| status  | `01`  (empty) |
| power   | `02` `[01/00]` |
| color   | `03` `[01, Hhi,Hlo, Shi,Slo, Vhi,Vlo]`  (mode 1, S*10, V*10) |
| preset  | `03` `[02, preset, Bhi,Blo]`  (mode 2, brightness*10) |

The device replies on the notify characteristic; the controller reads its
`Value` property (no fd/signal plumbing needed). Confirmed against a live
`M181` unit (firmware `2.2.9`): status, info, power and preset ACKs all
validate.

## Model / status note

The discovered unit reports raw model **M181**, firmware **2.2.9**. The fancy
DIY curtain full-frame upload (D0/DA, 400-pixel 20x20) in some Hello Fairy
docs is **not** applicable to this string unit — it supports power, color and
the preset effects (`--effects`) only.

## Adapter note

Discovery/scanning is unreliable in this environment and the unit may not
advertise continuously, but a **direct connect-by-address** always reaches it.
If the lights don't respond: power them on at the wall, then run `status`
again. To use the onboard radio instead of the TP-Link, pass a different
adapter to `HelloFairy(addr, adapter="hci1")`.