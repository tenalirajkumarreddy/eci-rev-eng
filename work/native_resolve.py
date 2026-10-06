#!/usr/bin/env python3
"""
native_resolve.py - resolve what each JNI getter in libnative_lib.so really returns.

Pure Python: parses the ELF64 (no objdump/readelf/capstone on this box), finds the
`Java_*` symbols, then scans each function's ARM64 body for ADRP+ADD / ADRP+LDR
pairs that compute an address inside the image, and prints the NUL-terminated
string living there.

Why: extract_native_key.py can only see *candidate* strings near the JNI names.
Near-duplicate decoys exist (kd8cf08abc5bd64a vs ed8cf08edc5bd64a ...), so the
only reliable answer for `tc` is the address the real function loads.

Usage:
    python work/native_resolve.py work/out/native/libnative_lib.so
    python work/native_resolve.py path/to/lib.so --filter getNT
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_DYNSYM = 11


def sign_extend(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value ^ sign) - sign


class Elf:
    def __init__(self, blob: bytes):
        self.b = blob
        if blob[:4] != b"\x7fELF":
            raise ValueError("not an ELF file")
        self.is64 = blob[4] == 2
        if not self.is64:
            raise ValueError("only ELF64 supported")
        self.little = blob[5] == 1
        self.end = "<" if self.little else ">"
        (self.e_shoff,) = struct.unpack_from(self.end + "Q", blob, 0x28)
        (self.e_shentsize,) = struct.unpack_from(self.end + "H", blob, 0x3A)
        (self.e_shnum,) = struct.unpack_from(self.end + "H", blob, 0x3C)
        (self.e_shstrndx,) = struct.unpack_from(self.end + "H", blob, 0x3E)
        self.sections = self._sections()
        self.shstrtab = self.sections[self.e_shstrndx]["data"]
        for s in self.sections:
            end = self.shstrtab.find(b"\x00", s["name_off"])
            s["name"] = self.shstrtab[s["name_off"]:end].decode("latin1")

    def _sections(self) -> list[dict]:
        out = []
        for i in range(self.e_shnum):
            off = self.e_shoff + i * self.e_shentsize
            name, typ, flags, addr, offset, size, link, info, align, entsize = \
                struct.unpack_from(self.end + "IIQQQQIIQQ", self.b, off)
            out.append({
                "name_off": name, "type": typ, "flags": flags, "addr": addr,
                "offset": offset, "size": size, "link": link, "info": info,
                "entsize": entsize,
                "data": self.b[offset:offset + size],
            })
        return out

    def symbols(self) -> list[dict]:
        out = []
        for sec in self.sections:
            if sec["type"] not in (SHT_SYMTAB, SHT_DYNSYM) or not sec["entsize"]:
                continue
            strtab = self.sections[sec["link"]]["data"]
            for off in range(0, sec["size"], sec["entsize"]):
                nameoff, info, other, shndx, value, size = struct.unpack_from(
                    self.end + "IBBHQQ", sec["data"], off)
                end = strtab.find(b"\x00", nameoff)
                name = strtab[nameoff:end].decode("latin1") if nameoff else ""
                if name:
                    out.append({"name": name, "value": value, "size": size,
                                "info": info, "shndx": shndx, "table": sec["name"]})
        return out

    def section_at(self, addr: int) -> dict | None:
        for sec in self.sections:
            if sec["addr"] and sec["addr"] <= addr < sec["addr"] + sec["size"]:
                return sec
        return None

    def read_at(self, addr: int, n: int) -> bytes | None:
        sec = self.section_at(addr)
        if not sec:
            return None
        off = addr - sec["addr"]
        return sec["data"][off:off + n]

    def cstring_at(self, addr: int, limit: int = 4096) -> str | None:
        raw = self.read_at(addr, limit)
        if not raw:
            return None
        nul = raw.find(b"\x00")
        if nul < 0:
            return None
        chunk = raw[:nul]
        if not chunk or len(chunk) > 1200:
            return None
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if any(c in text for c in "\r\n\t"):
            return None
        return text if text.isprintable() else None


def adrp_target(pc: int, word: int) -> tuple[int, int] | None:
    if (word & 0x9F000000) != 0x90000000:
        return None
    rd = word & 0x1F
    immlo = (word >> 29) & 0x3
    immhi = (word >> 5) & 0x7FFFF
    imm = sign_extend((immhi << 2) | immlo, 21)
    return rd, ((pc & ~0xFFF) + (imm << 12))


def add_offset(word: int) -> tuple[int, int, int, bool] | None:
    """ADD (immediate) 64-bit: returns (rn, rd, imm, is_64bit)."""
    if (word & 0x1F000000) == 0x11000000 and (word & 0x60000000) == 0:
        sf = (word >> 31) & 1
        rn = (word >> 5) & 0x1F
        rd = word & 0x1F
        imm12 = (word >> 10) & 0xFFF
        shift = (word >> 22) & 0x1
        return rn, rd, imm12 << (12 * shift), bool(sf)
    return None


def resolve_function(elf: Elf, start: int, end: int) -> list[tuple[int, str]]:
    """Return [(instruction_addr, string)] for string addresses the body computes."""
    found: list[tuple[int, str]] = []
    pc = start
    while pc + 4 <= end:
        word = struct.unpack_from(elf.end + "I", elf.b,
                                  (elf.section_at(pc) or {}).get("offset", 0) +
                                  (pc - (elf.section_at(pc) or {}).get("addr", 0)))[0]
        ap = adrp_target(pc, word)
        if ap:
            rd, page = ap
            # look ahead for ADD/LDR using the same register
            for step in range(1, 5):
                pc2 = pc + step * 4
                if pc2 + 4 > end:
                    break
                sec2 = elf.section_at(pc2)
                if not sec2:
                    break
                w2 = struct.unpack_from(elf.end + "I", elf.b,
                                        sec2["offset"] + (pc2 - sec2["addr"]))[0]
                ao = add_offset(w2)
                if ao and ao[0] == rd and ao[3]:
                    target = page + ao[2]
                    text = elf.cstring_at(target)
                    if text:
                        found.append((pc, text))
                    break
                # ADRP + LDR (unsigned offset): LDR (imm) 64-bit, opc=01
                if (w2 & 0xFFC00000) == 0xF9400000:
                    rn = (w2 >> 5) & 0x1F
                    if rn == rd:
                        imm12 = ((w2 >> 10) & 0xFFF) * 8
                        slot = page + imm12
                        ptr = elf.read_at(slot, 8)
                        if ptr:
                            (val,) = struct.unpack(elf.end + "Q", ptr)
                            text = elf.cstring_at(val) or elf.cstring_at(slot)
                            if text:
                                found.append((pc, text))
                        break
        pc += 4
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolve JNI getter return strings")
    ap.add_argument("so", type=Path)
    ap.add_argument("--filter", default="Java_",
                    help="only symbols containing this substring")
    args = ap.parse_args()

    blob = args.so.read_bytes()
    elf = Elf(blob)
    syms = elf.symbols()
    print(f"[elf] sections={len(elf.sections)} symbols={len(syms)} "
          f"({args.so.name}, {len(blob)} bytes)")
    print("[elf] section table:", ", ".join(
        s["name"] for s in elf.sections if s["name"])[:400])

    targets = [s for s in syms if args.filter in s["name"] and s["value"]]
    print(f"[elf] {len(targets)} matching symbols")
    for sym in sorted(targets, key=lambda s: s["value"]):
        start = sym["value"]
        end = start + sym["size"] if sym["size"] else start + 0x400
        refs = resolve_function(elf, start, end)
        label = sym["name"]
        if refs:
            print(f"\n=== {label} @ 0x{start:x} (size {sym['size']}) ===")
            for addr, text in refs:
                shown = text if len(text) <= 96 else text[:93] + "..."
                print(f"  0x{addr:x}: {shown}   [len {len(text)}]")
                print(f"           full: {text}")
        else:
            print(f"\n=== {label} @ 0x{start:x} (size {sym['size']}) ===")
            print("  (no direct string reference; likely runtime-decoded)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
