#!/usr/bin/env python3
"""Send a synthetic DDP packet to test the bridge (no xLights needed).

Channel layout (config.json): +0 R +1 G +2 B +3 brightness +4 mode +5 speed

Usage:
  ddptest.py --offset 0 --rgb ff0000 --brightness 255
  ddptest.py --offset 6 --channels 0,255,0,255,0,0
  ddptest.py --offset 12 --channels 255,255,255,255,5,100   # mode 5, speed 100
  ddptest.py --host 192.168.0.28   (default 127.0.0.1)
"""
import argparse
import socket
import struct


def build_ddp(offset, channels):
    payload = bytes(channels)
    hdr = bytes([0xA0, 0x00, 0x00, 0x00]) + struct.pack(">I", offset) + struct.pack(">H", len(payload))
    return hdr + payload


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4048)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--channels", help="comma list, e.g. 255,0,0,255,0,0")
    ap.add_argument("--rgb", metavar="rrggbb")
    ap.add_argument("--brightness", type=int)
    ap.add_argument("--mode", type=int)
    ap.add_argument("--speed", type=int)
    args = ap.parse_args()

    if args.channels:
        ch = [int(x) for x in args.channels.split(",")]
    else:
        ch = [0, 0, 0, 0, 0, 0]
        if args.rgb:
            rgb = bytes.fromhex(args.rgb)
            ch[0:3] = rgb
        if args.brightness is not None:
            ch[3] = args.brightness
        if args.mode is not None:
            ch[4] = args.mode
        if args.speed is not None:
            ch[5] = args.speed

    pkt = build_ddp(args.offset, ch)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(pkt, (args.host, args.port))
    print(f"sent DDP offset={args.offset} channels={ch} -> {args.host}:{args.port}")


if __name__ == "__main__":
    main()
