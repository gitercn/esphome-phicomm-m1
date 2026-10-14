#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
串口原始数据 dump 工具（hexdump + ASCII 双显示）
================================
- 读取指定 COM 口的原始字节，实时以 hexdump 格式显示：
      00000000  23 02 64 01 00 00 00 00 00 00 00 21        |#.d.........!|
  左边 = hex，右边 = 可打印 ASCII 字符（0x20~0x7E 显示原文，其余显示 '.'）
  不做任何字符编码 / CR/LF / 换行转换，.bin 永远保存原始二进制。
- 同时保存两份：
    <prefix>.bin  原始二进制，字节流原样（绝对避免编码问题）
    <prefix>.hex  hexdump 格式日志（带相对时间戳 + ASCII 侧，方便阅读）
用法示例：
    python serial_dump.py COM3 115200 --out m1_pin9
    python serial_dump.py COM3 115200 --out m1_pin24 --no-ascii   # 只要纯 hex
    python serial_dump.py COM3 115200 --out m1_pin9 --no-ts       # 不带时间戳
    python serial_dump.py COM3 115200 --out m1_pin9 --quiet       # 只存文件不打印
注意：
    - 一个 USB-TTL 只有一路 RX，一次只能监听一根线（9 / 10 / 24 之一）。
    - USB-TTL 的 RX 接目标引脚，TX 悬空，GND 必须与模块共地。
    - Ctrl+C 停止，文件会自动 flush。
"""
import argparse
import sys
import time

import serial


def hexdump(data: bytes, offset: int, with_ascii: bool) -> list[str]:
    """bytes -> hexdump 行列表，每行 16 字节，带全局偏移，可选 ASCII 侧"""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        hex_part = hex_part.ljust(16 * 3 - 1)  # 与 16 字节行的最大宽度对齐
        if with_ascii:
            ascii_part = "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in chunk)
            lines.append(f"{offset + i:08X}  {hex_part}  |{ascii_part}|")
        else:
            lines.append(f"{offset + i:08X}  {hex_part}")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description="serial raw dump (hexdump + ASCII)")
    ap.add_argument("port", help="COM port, e.g. COM3")
    ap.add_argument("baud", type=int, nargs="?", default=115200, help="baud rate (default 115200)")
    ap.add_argument("--out", default="serial_dump", help="output prefix (default: serial_dump)")
    ap.add_argument("--no-ascii", action="store_true", help="disable ASCII column (hex only)")
    ap.add_argument("--no-ts", action="store_true", help="do not prepend timestamps")
    ap.add_argument("--quiet", action="store_true", help="suppress live console output (files only)")
    args = ap.parse_args()

    bin_path = f"{args.out}.bin"
    hex_path = f"{args.out}.hex"
    with_ascii = not args.no_ascii

    print(f"[*] Opening {args.port} @ {args.baud} 8N1")
    print(f"[*] Raw bytes -> {bin_path}")
    print(f"[*] Hex log    -> {hex_path}  (ascii={'on' if with_ascii else 'off'})")
    print("[*] Press Ctrl+C to stop\n")

    try:
        ser = serial.Serial(
            args.port,
            args.baud,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.2,   # 非阻塞轮询，保证 Ctrl+C 及时响应
        )
    except serial.SerialException as exc:
        print(f"[!] Cannot open {args.port}: {exc}", file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    total = 0
    try:
        with open(bin_path, "wb") as fbin, open(hex_path, "w", encoding="ascii", errors="backslashreplace") as fhex:
            while True:
                data = ser.read(4096)
                if not data:
                    continue

                # 1) 原始二进制原样落盘
                fbin.write(data)
                fbin.flush()

                # 2) hexdump 格式日志（控制台 + .hex 文件）
                lines = hexdump(data, total, with_ascii)
                dt = time.time() - t0
                for idx, line in enumerate(lines):
                    prefix = f"[+{dt:010.3f}] " if (not args.no_ts and idx == 0) else "             "
                    fhex.write(f"{prefix}{line}\n")
                    if not args.quiet:
                        sys.stdout.write(f"{prefix}{line}\n")
                fhex.flush()
                if not args.quiet:
                    sys.stdout.flush()

                total += len(data)
    except KeyboardInterrupt:
        print(f"\n[*] Stopped. {total} bytes dumped -> {bin_path}, {hex_path}")
    finally:
        ser.close()


if __name__ == "__main__":
    main()