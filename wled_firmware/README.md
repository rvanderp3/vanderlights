# WLED custom build: Citron / WS2805 color remap

Fix for the potted Citron light whose **RGB and WW/CW dies are wired to the
"wrong" pins** of the WS2805 driver. The light lights up, but colors come out
completely wrong, and stock WLED has no `Color Order` setting that fixes it
(because WLED can only permute within the 3 RGB outputs and swap the 2 white
outputs — it cannot send a red/blue *color* down the white pins your red/blue
dies actually sit on).

This applies a minimal one-line change to **your fork** at
`git@github.com:rvanderp3/WLED.git`, branch `dist-citron`. It changes the WS2805
physical wire layout definition so every channel lands on the correct die.

## Diagnosis (die -> wire map)

WLED's WS2805 path assumes the chip frame `{G, R, B, WW, CW}` (NeoPixelBus
`NeoGrbwwFeature`). This light is potted with the dies wired to
**`{CW, WW, G, B, R}`** (green is on the `B` frame pin; R/B on the white pins;
the two whites on the R/G pins). So, with the stored color order `5` (GBR),
substituting the layout:

```c
// wled00/bus_wrapper.h  (getLegacyChannelLayout)
- static const LegacyLayout grbwwcw = {5, {LF_G, LF_R, LF_B, LF_W, LF_CW, LF_PAD}}; // WS2805
+ static const LegacyLayout ws2805Citron = {5, {LF_CW, LF_W, LF_G, LF_R, LF_B, LF_PAD}}; // dies: CW,WW,G,B,R
...
- case TYPE_WS2805: return grbwwcw;
+ case TYPE_WS2805: return ws2805Citron;
```

which yields the channel map `{CW, WW, G, B, R}` and lights all five dies
correctly — **including green.** FW1906 / SM16825 / RGB(W) buses are untouched.

## What was done

1. Cloned your fork: `https://github.com/rvanderp3/WLED.git`.
2. Applied the change to `wled00/bus_wrapper.h`.
3. Committed it on branch **`citron-ws2805-remap`** (`d34f4668`).
4. `ws2805-citron.patch` in this folder reproduces the exact committed change
   (verified to apply cleanly onto your `main`).

## Build (on your machine — PlatformIO not installed on this box)

> ⚠️ The fork's `default_envs` is `nodemcuv2` (ESP8266). Your controller is an
> **ESP32-WROOM-32E**, so you **must** pass the env explicitly.

```sh
git clone git@github.com:rvanderp3/WLED.git
cd WLED
git checkout citron-ws2805-remap            # or: git apply wled_firmware/ws2805-citron.patch
pio run -e esp32dev                         # NOT the default env!
pio run -e esp32dev -t upload
```

Note: your fork `main` is **newer than the 0.15.1 currently on the controller**,
so this build will also upgrade WLED to that newer version (fine — the remap is
built into it). A normal `pio run -t upload` does not erase settings, so your
existing LED strip config and segments survive.

## Post-flash checks

- On the `.40` UI, the WS2805 bus must keep **Color Order: GBR (5)** — the remap
  assumes it (it's already set on the device). If you ever change it to another
  value, re-derive the layout (see below).
- Set solid **R, G, B**, then white **warm** / white **cool**:
  | Request | Should show |
  |---|---|
  | Red | Red |
  | Green | Green |
  | Blue | Blue |
  | White, CCT warm | Warm white |
  | White, CCT cool | Cool white |
- If R/B come out swapped along with WW/CW (a whole-group mirror), flip the
  first two and last two entries of the `{...}` array (e.g.
  `{LF_CW, LF_W, LF_G, LF_B, LF_R}` and recompile — each option is a one-line
  change).
- If **green still doesn't light**: its die is unwired inside the potting — a
  hardware limit; R, B, WW, CW will still be correct.

## About the fix site

Your fork uses WLED's newer layout system (`legacyColorOrderToChannelMap` in
`bus_wrapper.h`). The color order + wire layout are composed into a channel map
at bus creation (`ch.numChannels = legacyColorOrderToChannelMap(busType,
colorOrder, ch.channelMap)`), so this one definition drives the output. A plain
`git diff` of the commit shows the full change.