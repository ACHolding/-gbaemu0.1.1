"""mewgba$ — single-file GBA emulator (files=off). Requires Python 3.14+.

See MEWGBA_ROADMAP and MEWGBA_CYTHON_GUIDE in this file for feature + accel docs.
AC Holdings 1999-2026 — maximum CatSDK vibes, zero external .pyx project files.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import struct
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

SCREEN_W = 240
SCREEN_H = 160
CYCLES_PER_FRAME = 280896
CYCLES_PER_SCANLINE = 1232
VDRAW_LINES = 160
TOTAL_LINES = 228

# I/O register offsets (from 0x04000000)
REG_DISPCNT = 0x000
REG_DISPSTAT = 0x004
REG_VCOUNT = 0x006
REG_BG0CNT = 0x008
REG_BG0HOFS = 0x010
REG_KEYINPUT = 0x130
REG_IE = 0x200
REG_IF = 0x202
REG_IME = 0x208
REG_BG2PA = 0x020
REG_WIN0H = 0x040
REG_WIN1H = 0x042
REG_WIN0V = 0x044
REG_WIN1V = 0x046
REG_WININ = 0x048
REG_WINOUT = 0x04A
REG_MOSAIC = 0x04C
REG_DMA0 = 0x0B0

GBA_KEY_MASK = 0x03FF

# GBA keypad (active low in KEYINPUT)
KEY_A, KEY_B, KEY_SELECT, KEY_START = 0, 1, 2, 3
KEY_RIGHT, KEY_LEFT, KEY_UP, KEY_DOWN = 4, 5, 6, 7
KEY_R, KEY_L = 8, 9

KEY_MAP = {
    "z": KEY_A,
    "x": KEY_B,
    "BackSpace": KEY_SELECT,
    "Return": KEY_START,
    "Right": KEY_RIGHT,
    "Left": KEY_LEFT,
    "Up": KEY_UP,
    "Down": KEY_DOWN,
    "e": KEY_R,
    "q": KEY_L,
}

MEWGBA_ROADMAP = """Next Level Moves (pick what you want):

Performance — Make Cython do even more (especially PPU and CPU hot paths)
Accuracy — Fix remaining CPU bugs so more test ROMs boot
Debug Tools — Register viewer + memory viewer (connect it to your Hex Editor!)
Save States — Super important for GBA
Sound (later)
UI Polish — FPS counter, speed control, recent files, etc."""

MEWGBA_CYTHON_GUIDE = """# mewgba$ — Cython Acceleration Guide (files=off)

**AC Holdings 1999-2026**
*Single-file GBA emulator with maximum CatSDK vibes*

## Philosophy

We keep everything **self-contained** (files=off).
No external `.pyx` files lying around — everything lives in one `.py` with smart temp caching.

## Core Strategy

1. Store Cython code as a big string (`_MEWGBA_PYX`)
2. Hash it → only recompile when changed
3. Use `pyximport` + temp cache folder (`MEWGBA_CACHE`)
4. Fallback to pure Python automatically

## How to Add More Cython Vibes

### 1. New Hot Path Function

Add to the `_MEWGBA_PYX` string:

    def fast_new_function(...):
        ...

### 2. Call it from Python

    if _ACCEL is not None:
        _ACCEL.fast_new_function(...)
    else:
        self.slow_version(...)

### 3. Recompile Trigger

Just change the string → hash changes → auto recompiles on next run.

## Current Accelerated Functions

- fast_run_cycles
- fast_run_scanline
- fast_run_frame
- fast_render_mode3
- fast_render_mode4
- fast_render_mode5
- fast_compose_fb
- fast_build_win_layers

## Pro Tips for Maximum Vibes

- Keep Cython functions small and hot
- Use cdef + typed variables aggressively
- Pass large arrays (vram, palette, etc.) directly
- Never use Python objects in inner loops
- Add `# cython: boundscheck=False, wraparound=False, cdivision=True`

## Future Cython Targets (Priority Order)

1. PPU rendering (biggest win)
2. Thumb/ARM CPU hot paths
3. Affine background math
4. Sprite rendering
5. Memory bus (read/write)

## CatSDK Official Motto

"If it runs at 60FPS in Python, it shall run at 300+ in Cython."
"""

MEWGBA_ACCEL_FUNCTIONS = (
    "fast_run_cycles",
    "fast_run_scanline",
    "fast_run_frame",
    "fast_render_mode3",
    "fast_render_mode4",
    "fast_render_mode5",
    "fast_compose_fb",
    "fast_build_win_layers",
)

MEWGBA_FUTURE_CYTHON_TARGETS = (
    "PPU rendering (biggest win)",
    "Thumb/ARM CPU hot paths",
    "Affine background math",
    "Sprite rendering",
    "Memory bus (read/write)",
)

MEWGBA_CACHE = os.path.join(tempfile.gettempdir(), "mewgba_emugba4k")
MEWGBA_RECENT = os.path.join(MEWGBA_CACHE, "recent.json")
MEWGBA_SAVES = os.path.join(MEWGBA_CACHE, "saves")
REG_WAITCNT = 0x204
REG_POSTFLG = 0x300

MEMORY_REGIONS = (
    ("EWRAM", 0x02000000, "ewram"),
    ("IWRAM", 0x03000000, "iwram"),
    ("I/O", 0x04000000, "io"),
    ("Palette", 0x05000000, "palette"),
    ("VRAM", 0x06000000, "vram"),
    ("OAM", 0x07000000, "oam"),
    ("ROM", 0x08000000, "rom"),
)

HEX_EDITOR_REL = "../ac'shexeditor4k/hexeditor4k.py"

def _build_demo_rom() -> bytes:
    """ARM bx stub + Thumb: mode 3 gradient demo."""
    rom = bytearray(0x200)
    struct.pack_into("<I", rom, 0x00, 0xEA00002E)  # b 0x080000C0
    rom[0xA0:0xAC] = b"MEWGBA      "
    struct.pack_into("<I", rom, 0xC0, 0xE59F0000)  # ldr r0, [pc, #0]
    struct.pack_into("<I", rom, 0xC4, 0xE12FFF10)  # bx r0
    thumb_entry = 0xCC
    struct.pack_into("<I", rom, 0xC8, 0x08000000 | thumb_entry | 1)

    demo_thumb = bytes.fromhex(
        "0648"
        "0749"
        "0880"
        "074a"
        "2300"
        "2000"
        "1846"
        "4010"
        "1080"
        "921d"
        "cb1d"
        "ff2b"
        "f8d1"
        "f8e7"
    )
    rom[thumb_entry : thumb_entry + len(demo_thumb)] = demo_thumb
    pool_off = (thumb_entry + len(demo_thumb) + 3) & ~3
    struct.pack_into("<III", rom, pool_off, 0x0403, 0x04000000, 0x06000000)
    return bytes(rom)


DEFAULT_ROM = _build_demo_rom()

# Pre-baked Cython accel (temp cache, files=off) — see MEWGBA_CYTHON_GUIDE
_MEWGBA_PYX = r'''# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
# mewgba$ embedded accel — edit parent .py _MEWGBA_PYX string, not this temp file

cdef inline tuple _c565(int c):
    return ((c & 0x1F) << 3, ((c >> 5) & 0x1F) << 3, ((c >> 10) & 0x1F) << 3)

def fast_render_mode3(vram, prio, pxbuf, win_layers, layer_bit):
    cdef int x, y, off, c
    cdef tuple rgb
    for y in range(160):
        for x in range(240):
            if not (win_layers[y][x] & layer_bit):
                continue
            off = (y * 240 + x) * 2
            c = vram[off] | (vram[off + 1] << 8)
            rgb = _c565(c)
            prio[y][x] = 0
            pxbuf[y][x] = rgb

def fast_render_mode4(vram, palette, prio, pxbuf, win_layers, layer_bit, int base):
    cdef int x, y, idx, c
    cdef tuple rgb
    for y in range(160):
        for x in range(240):
            if not (win_layers[y][x] & layer_bit):
                continue
            idx = vram[base + y * 240 + x]
            c = palette[idx * 2] | (palette[idx * 2 + 1] << 8)
            rgb = _c565(c)
            prio[y][x] = 0
            pxbuf[y][x] = rgb

def fast_compose_fb(fb, pxbuf, w, h):
    cdef int x, y, i
    cdef object p
    for y in range(h):
        for x in range(w):
            p = pxbuf[y][x]
            i = (y * w + x) * 3
            fb[i] = p[0]
            fb[i + 1] = p[1]
            fb[i + 2] = p[2]

def fast_run_cycles(obj, int budget):
    cdef int used = 0
    cdef int c
    while used < budget:
        if obj.halted:
            used += 4
            obj.cycles += 4
            obj._timer_tick(4)
            continue
        c = obj.step_cpu()
        used += c
        obj.cycles += c
        obj._timer_tick(c)

def fast_run_frame(obj, int scanlines, int cpl):
    cdef int ln, used
    cdef int c
    for ln in range(scanlines):
        obj._set_io16(6, ln)
        used = 0
        while used < cpl:
            if obj.halted:
                used += 4
                obj.cycles += 4
                obj._timer_tick(4)
                continue
            c = obj.step_cpu()
            used += c
            obj.cycles += c
            obj._timer_tick(c)

def fast_build_win_layers(out, int disp, int win0h, int win0v, int win1h, int win1v, int winin, int winout):
    cdef int x, y, left, right, top, bot, layers
    cdef int in0, in1
    cdef int outside = winout & 0x3F
    cdef int w0in = winin & 0x3F
    cdef int w1in = (winin >> 8) & 0x3F
    for y in range(160):
        for x in range(240):
            in0 = in1 = 0
            if disp & 0x2000:
                left = win0h & 0xFF
                right = win0h >> 8
                if right > 240:
                    right = 240
                top = win0v & 0xFF
                bot = win0v >> 8
                if bot > 160:
                    bot = 160
                if left <= x < right and top <= y < bot:
                    in0 = 1
            if disp & 0x4000:
                left = win1h & 0xFF
                right = win1h >> 8
                if right > 240:
                    right = 240
                top = win1v & 0xFF
                bot = win1v >> 8
                if bot > 160:
                    bot = 160
                if left <= x < right and top <= y < bot:
                    in1 = 1
            if in0 and in1:
                layers = w0in & w1in
            elif in0:
                layers = w0in
            elif in1:
                layers = w1in
            else:
                layers = outside if outside else 0x3F
            out[y][x] = layers

def fast_run_scanline(obj, int line, int budget):
    cdef int used = 0
    cdef int c
    obj._set_io16(6, line)
    while used < budget:
        if obj.halted:
            used += 4
            obj.cycles += 4
            obj._timer_tick(4)
            continue
        c = obj.step_cpu()
        used += c
        obj.cycles += c
        obj._timer_tick(c)

def fast_render_mode5(vram, prio, pxbuf, win_layers, layer_bit, int base):
    cdef int x, y, off, c
    cdef tuple rgb
    for y in range(160):
        for x in range(240):
            if not (win_layers[y][x] & layer_bit):
                continue
            off = base + (y * 240 + x) * 2
            c = vram[off] | (vram[off + 1] << 8)
            rgb = _c565(c)
            prio[y][x] = 0
            pxbuf[y][x] = rgb
'''


def _pyx_digest() -> str:
    return hashlib.sha256(_MEWGBA_PYX.encode()).hexdigest()


def _accel_status() -> dict:
    """Runtime Cython status for debug UI."""
    loaded = {name: _ACCEL is not None and hasattr(_ACCEL, name) for name in MEWGBA_ACCEL_FUNCTIONS}
    return {
        "active": _ACCEL is not None,
        "cache": MEWGBA_CACHE,
        "digest": _pyx_digest()[:16],
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "functions": loaded,
    }


def _load_mewgba_accel():
    """Compile _MEWGBA_PYX via pyximport into MEWGBA_CACHE; pure-Python fallback on failure."""
    cache = MEWGBA_CACHE
    os.makedirs(cache, exist_ok=True)
    pyx = os.path.join(cache, "mewgba_accel.pyx")
    stamp = os.path.join(cache, "mewgba_accel.hash")
    digest = _pyx_digest()
    try:
        import setuptools  # noqa: F401 — pyximport shim on Py3.12+
        if not os.path.exists(stamp) or open(stamp, encoding="utf-8").read().strip() != digest:
            with open(pyx, "w", encoding="utf-8") as f:
                f.write(_MEWGBA_PYX)
            with open(stamp, "w", encoding="utf-8") as f:
                f.write(digest)
            guide_path = os.path.join(cache, "CYTHON_GUIDE.txt")
            try:
                with open(guide_path, "w", encoding="utf-8") as gf:
                    gf.write(MEWGBA_CYTHON_GUIDE)
            except OSError:
                pass
            for name in os.listdir(cache):
                if name.startswith("mewgba_accel.") and name.endswith((".pyd", ".so", ".dll")):
                    try:
                        os.remove(os.path.join(cache, name))
                    except OSError:
                        pass
        if cache not in sys.path:
            sys.path.insert(0, cache)
        import pyximport
        pyximport.install(build_dir=cache, language_level=3)
        import mewgba_accel  # type: ignore[import-not-found]
        return mewgba_accel
    except Exception:
        return None


_ACCEL = _load_mewgba_accel()


def rgb565_to_rgb(color: int) -> tuple[int, int, int]:
    r = (color & 0x1F) << 3
    g = ((color >> 5) & 0x1F) << 3
    b = ((color >> 10) & 0x1F) << 3
    return r, g, b


class MewGBACore:
    """ARM7TDMI GBA core. See MEWGBA_ROADMAP for the feature roadmap."""

    def __init__(self) -> None:
        self.rom = bytearray()
        self.ewram = bytearray(256 * 1024)
        self.iwram = bytearray(32 * 1024)
        self.io = bytearray(1024)
        self.palette = bytearray(1024)
        self.vram = bytearray(96 * 1024)
        self.oam = bytearray(1024)
        self.r = [0] * 16
        self.cpsr = 0x0000001F
        self.spsr = 0
        self.halted = False
        self.framebuffer = bytearray(SCREEN_W * SCREEN_H * 3)
        self._prio = [[3] * SCREEN_W for _ in range(SCREEN_H)]
        self._pxbuf = [[(8, 12, 27)] * SCREEN_W for _ in range(SCREEN_H)]
        self.keys_down = 0
        self.rom_label: str | None = None
        self.is_loaded = False
        self.cycles = 0
        self.line_cycles = 0
        self.current_line = 0
        self.timers = [{"reload": 0, "count": 0, "ctrl": 0, "frac": 0} for _ in range(4)]
        self.dma = [{"src": 0, "dst": 0, "count": 0, "ctrl": 0} for _ in range(4)]
        self._win_layers = [[0x3F] * SCREEN_W for _ in range(SCREEN_H)]
        self._init_io_defaults()

    def _init_io_defaults(self) -> None:
        self.io[REG_KEYINPUT : REG_KEYINPUT + 2] = struct.pack("<H", GBA_KEY_MASK)
        self.io[REG_DISPSTAT : REG_DISPSTAT + 2] = struct.pack("<H", 0x0000)
        self.io[REG_VCOUNT : REG_VCOUNT + 2] = struct.pack("<H", 0x0000)
        self.io[REG_IE : REG_IE + 2] = struct.pack("<H", 0x0000)
        self.io[REG_IF : REG_IF + 2] = struct.pack("<H", 0x0000)
        self.io[REG_IME : REG_IME + 2] = struct.pack("<H", 0x0000)
        self.io[REG_DISPCNT : REG_DISPCNT + 2] = struct.pack("<H", 0x0080)
        self.io[REG_WININ : REG_WININ + 2] = struct.pack("<H", 0x3F3F)
        self.io[REG_WINOUT : REG_WINOUT + 2] = struct.pack("<H", 0x003F)
        self._set_io16(REG_WAITCNT, 0x0000)
        self.io[REG_POSTFLG] = 0x01

    def _boot_regs(self) -> None:
        """Post-reset register values matching GBA hardware."""
        self.r[13] = 0x03007F00
        self.r[15] = 0x08000000
        self.cpsr = 0x0000001F
        self.set_thumb(False)

    def reset_cpu(self) -> None:
        self.r = [0] * 16
        self.cpsr = 0x0000001F
        self.spsr = 0
        self.halted = False
        self.cycles = 0
        self.line_cycles = 0
        self.current_line = 0
        self.ewram[:] = b"\x00" * len(self.ewram)
        self.iwram[:] = b"\x00" * len(self.iwram)
        self.io[:] = b"\x00" * len(self.io)
        self.palette[:] = b"\x00" * len(self.palette)
        self.vram[:] = b"\x00" * len(self.vram)
        self.oam[:] = b"\x00" * len(self.oam)
        self.timers = [{"reload": 0, "count": 0, "ctrl": 0, "frac": 0} for _ in range(4)]
        self.dma = [{"src": 0, "dst": 0, "count": 0, "ctrl": 0} for _ in range(4)]
        self._win_layers = [[0x3F] * SCREEN_W for _ in range(SCREEN_H)]
        self._init_io_defaults()
        self._boot_regs()

    def _io16(self, off: int) -> int:
        return self.io[off] | (self.io[off + 1] << 8)

    def _set_io16(self, off: int, val: int) -> None:
        self.io[off] = val & 0xFF
        self.io[off + 1] = (val >> 8) & 0xFF

    def _irq_raise(self, bit: int) -> None:
        self._set_io16(REG_IF, self._io16(REG_IF) | (1 << bit))
        self._irq_dispatch()

    def _irq_dispatch(self) -> None:
        if not (self._io16(REG_IME) & 1):
            return
        pending = self._io16(REG_IE) & self._io16(REG_IF) & 0x3FFF
        if not pending:
            return
        bit = (pending & -pending).bit_length() - 1
        self.halted = False
        self.spsr = self.cpsr
        self.cpsr = (self.cpsr & ~0xFF) | 0x92
        self.r[14] = (self.r[15] - (2 if self.thumb() else 4)) & 0xFFFFFFFF
        self.set_thumb(True)
        self.r[15] = 0x03000000 + bit * 4
        self._set_io16(REG_IF, self._io16(REG_IF) & ~(1 << bit))

    def _wait_states(self, addr: int, bits: int) -> int:
        region = addr & 0xFF000000
        if region == 0x02000000:
            return 3 if bits == 32 else 2
        if region == 0x03000000:
            return 1
        if region in (0x05000000, 0x06000000, 0x07000000):
            return 1
        if region in (0x08000000, 0x09000000):
            return 5 if bits == 32 else 3
        return 1

    def _swi_hle(self, num: int) -> None:
        if num == 0x00:
            self.reset_cpu()
            self.r[15] = 0x08000000
            self.cpsr = 0x0000001F
            return
        if num == 0x01:
            flags = self.r[0] & 0xFF
            if flags & 0x01:
                self.palette[:] = b"\x00" * len(self.palette)
            if flags & 0x02:
                self.vram[:] = b"\x00" * len(self.vram)
            if flags & 0x04:
                self.oam[:] = b"\x00" * len(self.oam)
            if flags & 0x08:
                self.io[:] = b"\x00" * len(self.io)
                self._init_io_defaults()
            if flags & 0x10:
                self.iwram[:] = b"\x00" * len(self.iwram)
            if flags & 0x20:
                self.palette[512:] = b"\x00" * (len(self.palette) - 512)
            if flags & 0x40:
                self.ewram[:] = b"\x00" * len(self.ewram)
            return
        if num in (0x02, 0x03):
            self.halted = True
            return
        if num in (0x04, 0x05):
            clear = self.r[0] & 0x3FFF
            wait = self.r[1] & 0x3FFF if num == 0x04 else 0x0001
            guard = 0
            while not (self._io16(REG_IF) & wait):
                self._run_cycles(4)
                guard += 1
                if guard > CYCLES_PER_FRAME * 4:
                    break
            self._set_io16(REG_IF, self._io16(REG_IF) & ~clear)
            return
        if num == 0x08:
            val = self.r[0] & 0xFFFFFFFF
            self.r[0] = 0 if val == 0 else int(val ** 0.5)
            return
        if num in (0x0B, 0x0C):
            src = self.r[0] & 0xFFFFFFFC
            dst = self.r[1] & 0xFFFFFFFC
            ctrl = self.r[2] & 0xFFFFFFFF
            count = ctrl & 0x001FFFFF
            fill = bool(ctrl & 0x01000000)
            word = bool(ctrl & 0x04000000)
            if num == 0x0C:
                count = (count + 7) // 8
            if word:
                if fill:
                    val = self.read32(src)
                    for _ in range(count):
                        self.write32(dst, val)
                        dst = (dst + 4) & 0xFFFFFFFF
                else:
                    for _ in range(count):
                        self.write32(dst, self.read32(src))
                        src = (src + 4) & 0xFFFFFFFF
                        dst = (dst + 4) & 0xFFFFFFFF
            elif fill:
                val = self.read16(src)
                for _ in range(count):
                    self.write16(dst, val)
                    dst = (dst + 2) & 0xFFFFFFFF
            else:
                for _ in range(count):
                    self.write16(dst, self.read16(src))
                    src = (src + 2) & 0xFFFFFFFF
                    dst = (dst + 2) & 0xFFFFFFFF
            return
        if num == 0x0D:
            self.r[0] = 1

    def _timer_tick(self, cyc: int) -> None:
        for i, t in enumerate(self.timers):
            if not (t["ctrl"] & 0x80):
                continue
            if i > 0 and (t["ctrl"] & 0x04):
                continue
            prescale = (0, 6, 8, 10)[(t["ctrl"] >> 0) & 3]
            step = 1 << prescale
            t["frac"] += cyc
            while t["frac"] >= step:
                t["frac"] -= step
                t["count"] = (t["count"] - 1) & 0xFFFF
                if t["count"] == 0xFFFF:
                    t["count"] = t["reload"]
                    if t["ctrl"] & 0x40:
                        self._irq_raise(3 + i)

    def _run_cycles(self, budget: int) -> None:
        if _ACCEL is not None:
            _ACCEL.fast_run_cycles(self, budget)
            return
        used = 0
        while used < budget:
            if self.halted:
                used += 4
                self.cycles += 4
                self._timer_tick(4)
                continue
            c = self.step_cpu()
            used += c
            self.cycles += c
            self._timer_tick(c)

    def _dma_reg(self, off: int) -> tuple[int, int] | None:
        if off < REG_DMA0 or off > REG_DMA0 + 46:
            return None
        rel = off - REG_DMA0
        ch, reg = rel // 12, rel % 12
        return (ch, reg) if ch <= 3 else None

    def _dma_write(self, ch: int, reg: int, val: int) -> None:
        d = self.dma[ch]
        if reg == 0:
            d["src"] = (d["src"] & 0xFFFF0000) | val
        elif reg == 2:
            d["src"] = (d["src"] & 0x0000FFFF) | (val << 16)
        elif reg == 4:
            d["dst"] = (d["dst"] & 0xFFFF0000) | val
        elif reg == 6:
            d["dst"] = (d["dst"] & 0x0000FFFF) | (val << 16)
        elif reg == 8:
            d["count"] = val
        elif reg == 10:
            d["ctrl"] = val
            if val & 0x8000 and ((val >> 12) & 3) == 0:
                self._start_dma(ch)

    def _start_dma(self, ch: int) -> None:
        d = self.dma[ch]
        src = d["src"] & 0x0FFFFFFF
        dst = d["dst"] & 0x0FFFFFFF
        count = d["count"]
        if ch == 3:
            count &= 0xFFFF
            if count == 0:
                count = 0x10000
        elif count == 0:
            count = 0x4000
        ctrl = d["ctrl"]
        src_inc = (0, 2, -2, 0)[(ctrl >> 7) & 3]
        dst_inc = (0, 2, -2, 0)[(ctrl >> 5) & 3]
        width = 4 if ctrl & 0x0400 else 2 if ctrl & 0x0200 else 1
        for _ in range(count):
            if width == 4:
                self.write32(dst, self.read32(src))
                src = (src + src_inc) & 0xFFFFFFFF if src_inc else src
                dst = (dst + dst_inc) & 0xFFFFFFFF if dst_inc else dst
            elif width == 2:
                self.write16(dst, self.read16(src))
                src = (src + src_inc) & 0xFFFFFFFF if src_inc else src
                dst = (dst + dst_inc) & 0xFFFFFFFF if dst_inc else dst
            else:
                self.write8(dst, self.read8(src))
                src = (src + src_inc) & 0xFFFFFFFF if src_inc else src
                dst = (dst + dst_inc) & 0xFFFFFFFF if dst_inc else dst
        d["ctrl"] = ctrl & 0x7FFF
        if ctrl & 0x4000:
            self._irq_raise(8 + ch)

    def _dma_vblank_hblank(self, mode: int) -> None:
        for ch in range(4):
            ctrl = self.dma[ch]["ctrl"]
            if (ctrl & 0x8000) and ((ctrl >> 12) & 3) == mode:
                self._start_dma(ch)

    def _read_io(self, off: int) -> int:
        if off == REG_VCOUNT:
            return self._io16(REG_VCOUNT) & 0xFF
        if off == REG_VCOUNT + 1:
            return (self._io16(REG_VCOUNT) >> 8) & 0xFF
        if off in (REG_KEYINPUT, REG_KEYINPUT + 1):
            return self.io[off]
        if 0x100 <= off < 0x110:
            idx = (off - 0x100) // 4
            rem = (off - 0x100) % 4
            if rem == 0:
                return self.timers[idx]["count"] & 0xFF
            if rem == 1:
                return (self.timers[idx]["count"] >> 8) & 0xFF
        return self.io[off]

    def _write_io(self, off: int, val: int) -> None:
        if off in (REG_KEYINPUT, REG_KEYINPUT + 1):
            return
        if 0x100 <= off < 0x110:
            idx = (off - 0x100) // 4
            rem = (off - 0x100) % 4
            if rem == 0:
                self.timers[idx]["reload"] = (self.timers[idx]["reload"] & 0xFF00) | val
                self.timers[idx]["count"] = (self.timers[idx]["count"] & 0xFF00) | val
            elif rem == 1:
                self.timers[idx]["reload"] = (self.timers[idx]["reload"] & 0x00FF) | (val << 8)
                self.timers[idx]["count"] = (self.timers[idx]["count"] & 0x00FF) | (val << 8)
            elif rem == 2:
                self.timers[idx]["ctrl"] = (self.timers[idx]["ctrl"] & 0xFF00) | val
            elif rem == 3:
                self.timers[idx]["ctrl"] = (self.timers[idx]["ctrl"] & 0x00FF) | (val << 8)
                if val & 0x80:
                    self.timers[idx]["count"] = self.timers[idx]["reload"]
                    self.timers[idx]["frac"] = 0
            return
        dma = self._dma_reg(off)
        if dma is not None:
            ch, reg = dma
            self.io[off] = val
            self._dma_write(ch, reg, val)
            return
        self.io[off] = val

    def load_rom_bytes(self, rom: bytes, label: str = "Demo (built-in)") -> bool:
        if not rom:
            return False
        self.rom = bytearray(rom)
        self.reset_cpu()
        self.rom_label = label
        self.is_loaded = True
        return True

    def read_bus(self, addr: int, size: int = 1) -> int:
        addr &= 0xFFFFFFFF
        if size == 1:
            return self.read8(addr)
        if size == 2:
            return self.read16(addr)
        return self.read32(addr)

    def snapshot(self) -> dict:
        return {
            "rom": bytes(self.rom),
            "ewram": bytes(self.ewram),
            "iwram": bytes(self.iwram),
            "io": bytes(self.io),
            "palette": bytes(self.palette),
            "vram": bytes(self.vram),
            "oam": bytes(self.oam),
            "r": list(self.r),
            "cpsr": self.cpsr,
            "spsr": self.spsr,
            "halted": self.halted,
            "cycles": self.cycles,
            "timers": [dict(t) for t in self.timers],
            "dma": [dict(d) for d in self.dma],
            "keys_down": self.keys_down,
            "rom_label": self.rom_label,
        }

    def restore(self, snap: dict) -> None:
        self.rom = bytearray(snap["rom"])
        self.ewram[:] = snap["ewram"]
        self.iwram[:] = snap["iwram"]
        self.io[:] = snap["io"]
        self.palette[:] = snap["palette"]
        self.vram[:] = snap["vram"]
        self.oam[:] = snap["oam"]
        self.r = list(snap["r"])
        self.cpsr = snap["cpsr"]
        self.spsr = snap["spsr"]
        self.halted = snap["halted"]
        self.cycles = snap["cycles"]
        self.timers = [dict(t) for t in snap["timers"]]
        self.dma = [dict(d) for d in snap["dma"]]
        self.keys_down = snap["keys_down"]
        self.rom_label = snap.get("rom_label")
        self.is_loaded = True
        self.set_keys(self.keys_down)

    def memory_dump(self, region: str, max_len: int = 65536) -> tuple[int, bytes]:
        data = getattr(self, region, None)
        if data is None:
            return 0, b""
        base = next(b for name, b, attr in MEMORY_REGIONS if attr == region)
        chunk = bytes(data[:max_len])
        return base, chunk

    def set_keys(self, mask: int) -> None:
        self.keys_down = mask & GBA_KEY_MASK
        self._set_io16(REG_KEYINPUT, GBA_KEY_MASK & ~self.keys_down)

    # --- Memory bus ---

    def _rom_addr(self, addr: int) -> int | None:
        if 0x08000000 <= addr < 0x0A000000:
            off = addr - 0x08000000
            if off < len(self.rom):
                return off
        elif 0x0E000000 <= addr < 0x0E010000:
            off = addr - 0x0E000000
            if off < len(self.rom):
                return off
        return None

    def read8(self, addr: int) -> int:
        addr &= 0xFFFFFFFF
        if 0x02000000 <= addr < 0x02040000:
            return self.ewram[addr - 0x02000000]
        if 0x03000000 <= addr < 0x03008000:
            return self.iwram[addr - 0x03000000]
        if 0x04000000 <= addr < 0x04000400:
            return self._read_io(addr - 0x04000000)
        if 0x05000000 <= addr < 0x05000400:
            return self.palette[addr - 0x05000000]
        if 0x06000000 <= addr < 0x06018000:
            return self.vram[addr - 0x06000000]
        if 0x07000000 <= addr < 0x07000400:
            return self.oam[addr - 0x07000000]
        off = self._rom_addr(addr)
        if off is not None:
            return self.rom[off]
        return 0

    def read16(self, addr: int) -> int:
        addr &= 0xFFFFFFFE
        lo = self.read8(addr)
        hi = self.read8(addr + 1)
        return lo | (hi << 8)

    def read32(self, addr: int) -> int:
        addr &= 0xFFFFFFFC
        return (
            self.read8(addr)
            | (self.read8(addr + 1) << 8)
            | (self.read8(addr + 2) << 16)
            | (self.read8(addr + 3) << 24)
        )

    def write8(self, addr: int, val: int) -> None:
        addr &= 0xFFFFFFFF
        val &= 0xFF
        if 0x02000000 <= addr < 0x02040000:
            self.ewram[addr - 0x02000000] = val
        elif 0x03000000 <= addr < 0x03008000:
            self.iwram[addr - 0x03000000] = val
        elif 0x04000000 <= addr < 0x04000400:
            self._write_io(addr - 0x04000000, val)
        elif 0x05000000 <= addr < 0x05000400:
            self.palette[addr - 0x05000000] = val
        elif 0x06000000 <= addr < 0x06018000:
            self.vram[addr - 0x06000000] = val
        elif 0x07000000 <= addr < 0x07000400:
            self.oam[addr - 0x07000000] = val
        elif 0x08000000 <= addr < 0x0E000000:
            off = self._rom_addr(addr)
            if off is not None and off < len(self.rom):
                self.rom[off] = val

    def write16(self, addr: int, val: int) -> None:
        addr &= 0xFFFFFFFE
        val &= 0xFFFF
        self.write8(addr, val & 0xFF)
        self.write8(addr + 1, (val >> 8) & 0xFF)

    def write32(self, addr: int, val: int) -> None:
        addr &= 0xFFFFFFFC
        val &= 0xFFFFFFFF
        self.write8(addr, val & 0xFF)
        self.write8(addr + 1, (val >> 8) & 0xFF)
        self.write8(addr + 2, (val >> 16) & 0xFF)
        self.write8(addr + 3, (val >> 24) & 0xFF)

    # --- CPU flags ---

    def thumb(self) -> bool:
        return bool(self.cpsr & 0x20)

    def set_thumb(self, on: bool) -> None:
        if on:
            self.cpsr |= 0x20
        else:
            self.cpsr &= ~0x20

    def flag_n(self) -> bool:
        return bool(self.cpsr & 0x80000000)

    def flag_z(self) -> bool:
        return bool(self.cpsr & 0x40000000)

    def flag_c(self) -> bool:
        return bool(self.cpsr & 0x20000000)

    def flag_v(self) -> bool:
        return bool(self.cpsr & 0x10000000)

    def set_nz(self, val: int, bits: int = 32) -> None:
        val &= (1 << bits) - 1
        self.cpsr &= ~0xC0000000
        if val & (1 << (bits - 1)):
            self.cpsr |= 0x80000000
        if val == 0:
            self.cpsr |= 0x40000000

    def set_nz_sub(self, res: int, op1: int, op2: int, bits: int = 32) -> None:
        self.set_nz(res, bits)
        mask = (1 << bits) - 1
        op1 &= mask
        op2 &= mask
        res &= mask
        self.cpsr &= ~0x30000000
        if op1 >= op2:
            self.cpsr |= 0x20000000
        if ((op1 ^ op2) & (op1 ^ res)) & (1 << (bits - 1)):
            self.cpsr |= 0x10000000

    def set_nz_add(self, res: int, op1: int, op2: int, bits: int = 32) -> None:
        self.set_nz(res, bits)
        mask = (1 << bits) - 1
        op1 &= mask
        op2 &= mask
        res &= mask
        self.cpsr &= ~0x30000000
        if res < op1:
            self.cpsr |= 0x20000000
        if (~(op1 ^ op2) & (op1 ^ res)) & (1 << (bits - 1)):
            self.cpsr |= 0x10000000

    def check_cond(self, cond: int) -> bool:
        n, z, c, v = self.flag_n(), self.flag_z(), self.flag_c(), self.flag_v()
        return {
            0x0: z,
            0x1: not z,
            0x2: c,
            0x3: not c,
            0x4: n,
            0x5: not n,
            0x6: v,
            0x7: not v,
            0x8: c and not z,
            0x9: not c or z,
            0xA: n == v,
            0xB: n != v,
            0xC: not z and (n == v),
            0xD: z or (n != v),
            0xE: True,
            0xF: False,
        }.get(cond, False)

    def reg_get(self, i: int) -> int:
        i &= 15
        if i == 15:
            return (self.r[15] + (2 if self.thumb() else 4)) & 0xFFFFFFFF
        return self.r[i] & 0xFFFFFFFF

    def reg_set(self, i: int, val: int) -> None:
        i &= 15
        val &= 0xFFFFFFFF
        if i == 15:
            thumb_bit = bool(val & 1)
            if self.thumb() or thumb_bit:
                val &= ~1
                self.set_thumb(thumb_bit)
            else:
                val &= ~3
            self.r[15] = val
        else:
            self.r[i] = val

    # --- CPU execution (ARM7TDMI Thumb + ARM decode) ---
    # Thumb: fmt1-3 shifts/adds, fmt4 ALU (AND..MVN), fmt5 hi-reg, fmt6-10
    #   loads/stores, fmt11 SP, fmt12 push/pop, fmt13-16 branches, LDRH, SXTH/UXTH
    # ARM: data proc, single/half/block trans, multiply, long multiply, PSR, CLZ, BX, SWI, B/BL

    def step_cpu(self) -> int:
        if self.halted:
            return 4
        if self.thumb():
            return self._exec_thumb(self.read16(self.r[15]))
        return self._exec_arm(self.read32(self.r[15]))

    def _exec_thumb(self, op: int) -> int:
        pc = self.r[15]
        self.r[15] = (pc + 2) & 0xFFFFFFFF
        hi = (op >> 12) & 0xF

        if (op >> 13) == 0:
            if ((op >> 11) & 3) == 3:
                imm3 = (op >> 6) & 7
                rn = (op >> 3) & 7
                rd = op & 7
                res = (self.reg_get(rn) + imm3) & 0xFFFFFFFF
                self.set_nz_add(res, self.reg_get(rn), imm3)
                self.reg_set(rd, res)
                return 1

            rd, imm = op & 7, (op >> 3) & 0x1F
            if op & 0x0800:
                if op & 0x0400:
                    self.set_nz_sub((self.reg_get(rd) - imm) & 0xFFFFFFFF, self.reg_get(rd), imm)
                else:
                    res = (self.reg_get(rd) + imm) & 0xFFFFFFFF
                    self.set_nz_add(res, self.reg_get(rd), imm)
                    self.reg_set(rd, res)
            else:
                shift = (op >> 6) & 3
                if shift == 0:
                    old = self.reg_get(rd)
                    val = (old << imm) & 0xFFFFFFFF
                    if imm:
                        carry = ((old << (imm - 1)) & 0x80000000) != 0
                        self.cpsr = (self.cpsr & ~0x20000000) | (0x20000000 if carry else 0)
                elif shift == 1:
                    old = self.reg_get(rd)
                    val = (old >> imm) & 0xFFFFFFFF
                    if imm:
                        self.cpsr = (self.cpsr & ~0x20000000) | (((old >> (imm - 1)) & 1) << 29)
                elif shift == 2:
                    c = self.flag_c()
                    old = self.reg_get(rd)
                    if imm:
                        val = ((old >> (imm - 1)) | (int(c) << 31)) >> 1 if imm < 32 else 0
                        self.cpsr = (self.cpsr & ~0x20000000) | (((old >> (imm - 1)) & 1) << 29)
                    else:
                        val = old
                else:
                    c = self.flag_c()
                    old = self.reg_get(rd)
                    if imm:
                        val = (((old << (32 - imm)) | (old >> imm)) & 0xFFFFFFFF) if imm < 32 else 0
                        self.cpsr = (self.cpsr & ~0x20000000) | ((old >> (imm - 1)) & 1) << 29
                    else:
                        val = old
                self.set_nz(val)
                self.reg_set(rd, val)
            return 1

        if (op & 0xF800) == 0x1800:
            rs, rd = (op >> 3) & 7, op & 7
            if op & 0x0400:
                res = (self.reg_get(rd) - self.reg_get(rs)) & 0xFFFFFFFF
                self.set_nz_sub(res, self.reg_get(rd), self.reg_get(rs))
            else:
                res = (self.reg_get(rd) + self.reg_get(rs)) & 0xFFFFFFFF
                self.set_nz_add(res, self.reg_get(rd), self.reg_get(rs))
            self.reg_set(rd, res)
            return 1

        if (op & 0xE000) == 0x2000:
            rd, imm = (op >> 8) & 7, op & 0xFF
            fn = (op >> 11) & 3
            if fn == 0:
                self.set_nz(imm, 8)
                self.reg_set(rd, imm)
            elif fn == 1:
                self.set_nz(self.reg_get(rd) | imm, 8)
                self.reg_set(rd, self.reg_get(rd) | imm)
            elif fn == 2:
                res = (self.reg_get(rd) + imm) & 0xFFFFFFFF
                self.set_nz_add(res, self.reg_get(rd), imm)
                self.reg_set(rd, res)
            else:
                res = (self.reg_get(rd) - imm) & 0xFFFFFFFF
                self.set_nz_sub(res, self.reg_get(rd), imm)
                self.reg_set(rd, res)
            return 1

        if (op & 0xFC00) == 0x4000:
            rs, rd = (op >> 3) & 7, op & 7
            val = self.reg_get(rs)
            alu = (op >> 6) & 0x3C
            if alu == 0x00:
                res = self.reg_get(rd) & val
                self.set_nz(res)
                self.reg_set(rd, res)
            elif alu == 0x04:
                res = self.reg_get(rd) ^ val
                self.set_nz(res)
                self.reg_set(rd, res)
            elif alu == 0x08:
                shift = val & 0xFF
                old = self.reg_get(rd)
                res = (old << shift) & 0xFFFFFFFF
                if shift:
                    self.cpsr = (self.cpsr & ~0x20000000) | (((old << (shift - 1)) >> 31) & 0x20000000)
                self.set_nz(res)
                self.reg_set(rd, res)
            elif alu == 0x0C:
                shift = val & 0xFF
                old = self.reg_get(rd)
                res = (old >> shift) & 0xFFFFFFFF if shift < 32 else 0
                if shift:
                    self.cpsr = (self.cpsr & ~0x20000000) | (((old >> (shift - 1)) & 1) << 29)
                self.set_nz(res)
                self.reg_set(rd, res)
            elif alu == 0x10:
                shift = val & 0xFF
                old = self.reg_get(rd)
                c = int(self.flag_c())
                if shift:
                    res = ((old >> shift) | (c << (31 - shift + 1))) & 0xFFFFFFFF if shift <= 32 else 0
                    self.cpsr = (self.cpsr & ~0x20000000) | (((old >> (shift - 1)) & 1) << 29)
                else:
                    res = old
                self.set_nz(res)
                self.reg_set(rd, res)
            elif alu == 0x14:
                res = (self.reg_get(rd) + val + int(self.flag_c())) & 0xFFFFFFFF
                self.set_nz_add(res, self.reg_get(rd), val)
                self.reg_set(rd, res)
            elif alu == 0x18:
                res = (self.reg_get(rd) - val - (1 - int(self.flag_c()))) & 0xFFFFFFFF
                self.set_nz_sub(res, self.reg_get(rd), val)
                self.reg_set(rd, res)
            elif alu == 0x1C:
                shift = val & 0xFF
                old = self.reg_get(rd)
                c = int(self.flag_c())
                if shift:
                    res = (((old >> shift) | (old << (32 - shift))) & 0xFFFFFFFF) if shift < 32 else 0
                    self.cpsr = (self.cpsr & ~0x20000000) | ((old >> (shift - 1)) & 1) << 29
                else:
                    res = (old >> 1) | (c << 31)
                    self.cpsr = (self.cpsr & ~0x20000000) | ((old & 1) << 29)
                self.set_nz(res)
                self.reg_set(rd, res)
            elif alu == 0x20:
                self.set_nz(self.reg_get(rd) & val)
            elif alu == 0x24:
                res = (-self.reg_get(rd)) & 0xFFFFFFFF
                self.set_nz_sub(res, 0, self.reg_get(rd))
                self.reg_set(rd, res)
            elif alu == 0x28:
                self.set_nz_sub((self.reg_get(rd) - val) & 0xFFFFFFFF, self.reg_get(rd), val)
            elif alu == 0x2C:
                res = self.reg_get(rd) | val
                self.set_nz(res)
                self.reg_set(rd, res)
            elif alu == 0x30:
                res = (self.reg_get(rd) * val) & 0xFFFFFFFF
                self.set_nz(res)
                self.reg_set(rd, res)
            elif alu == 0x34:
                res = self.reg_get(rd) & ~val
                self.set_nz(res)
                self.reg_set(rd, res)
            elif alu == 0x38:
                res = (~val) & 0xFFFFFFFF
                self.set_nz(res)
                self.reg_set(rd, res)
            else:
                res = (self.reg_get(rd) + val) & 0xFFFFFFFF
                self.set_nz_add(res, self.reg_get(rd), val)
                self.reg_set(rd, res)
            return 1

        if (op & 0xFC00) == 0x4400:
            if (op >> 6) & 0xF == 11:
                rs, rd = ((op >> 3) & 7) | 8, (op & 7) | 8
                addr = self.reg_get(rs)
                self.set_thumb(bool(addr & 1))
                self.r[15] = (addr & ~1) & 0xFFFFFFFF
                return 3
            rs, rd = ((op >> 3) & 7) | 8, (op & 7) | 8
            fn = (op >> 6) & 0xF
            if fn in (1, 2, 4, 8):
                ops = {1: lambda a, b: a + b, 2: lambda a, b: a - b, 4: lambda a, b: a & b, 8: lambda a, b: a ^ b}
                a, b = self.reg_get(rd), self.reg_get(rs)
                res = ops[fn](a, b) & 0xFFFFFFFF
                if fn == 1:
                    self.set_nz_add(res, a, b)
                elif fn == 2:
                    self.set_nz_sub(res, a, b)
                else:
                    self.set_nz(res)
                self.reg_set(rd, res)
            elif fn == 0xA:
                self.reg_set(rd, self.reg_get(rs))
            elif fn == 0xB:
                self.set_nz_sub((self.reg_get(rd) - self.reg_get(rs)) & 0xFFFFFFFF, self.reg_get(rd), self.reg_get(rs))
            elif fn == 0x9:
                res = self.reg_get(rd) * self.reg_get(rs)
                self.set_nz(res)
                self.reg_set(rd, res)
            return 1

        if (op & 0xF800) == 0x4800:
            rd = (op >> 8) & 7
            off = (op & 0xFF) << 2
            base = ((pc & 0xFFFFFFFC) + 4) & 0xFFFFFFFF
            self.reg_set(rd, self.read32((base + off) & 0xFFFFFFFC))
            return 2

        if (op & 0xF800) == 0x5000:
            rb, ro, rd = (op >> 3) & 7, (op >> 6) & 7, op & 7
            addr = (self.reg_get(rb) + self.reg_get(ro)) & 0xFFFFFFFF
            if op & 0x0800:
                if op & 0x0400:
                    val = self.read8(addr)
                    if val & 0x80:
                        val |= 0xFFFFFF00
                    self.reg_set(rd, val)
                elif op & 0x0200:
                    val = self.read16(addr)
                    if val & 0x8000:
                        val |= 0xFFFF0000
                    self.reg_set(rd, val)
                else:
                    self.reg_set(rd, self.read16(addr))
            elif op & 0x0400:
                self.write8(addr, self.reg_get(rd) & 0xFF)
            else:
                self.write16(addr, self.reg_get(rd) & 0xFFFF)
            return 2 + self._wait_states(addr, 16)

        if (op & 0xF800) == 0x5800:
            rb, rd = (op >> 3) & 7, op & 7
            off = (op >> 6) & 0x1F
            addr = (self.reg_get(rb) + off) & 0xFFFFFFFF
            if op & 0x0800:
                val = self.read8(addr)
                if val & 0x80:
                    val |= 0xFFFFFF00
                self.reg_set(rd, val)
            else:
                self.write8(addr, self.reg_get(rd) & 0xFF)
            return 2

        if (op & 0xF800) == 0x6000:
            rb, rd = (op >> 3) & 7, op & 7
            off = ((op >> 6) & 0x1F) << 2
            addr = (self.reg_get(rb) + off) & 0xFFFFFFFF
            if op & 0x0800:
                self.reg_set(rd, self.read32(addr))
            else:
                self.write32(addr, self.reg_get(rd))
            return 2

        if (op & 0xF800) == 0x8000:
            rb, rd = (op >> 3) & 7, op & 7
            off = ((op >> 6) & 0x1F) << 1
            addr = (self.reg_get(rb) + off) & 0xFFFFFFFF
            if op & 0x0800:
                val = self.read16(addr)
                self.reg_set(rd, val)
            else:
                self.write16(addr, self.reg_get(rd) & 0xFFFF)
            return 2 + self._wait_states(addr, 16)

        if (op & 0xF800) == 0x9000:
            rd = (op >> 8) & 7
            off = (op & 0xFF) << 2
            addr = (self.r[13] + off) & 0xFFFFFFFF
            if op & 0x0800:
                self.reg_set(rd, self.read32(addr))
            else:
                self.write32(addr, self.reg_get(rd))
            return 2

        if (op & 0xFF00) == 0xA000:
            rd = (op >> 8) & 7
            off = (op & 0xFF) << 2
            base = ((pc & 0xFFFFFFFC) + 4) & 0xFFFFFFFF
            self.reg_set(rd, (self.reg_get(rd) + base + off) & 0xFFFFFFFF)
            return 1

        if (op & 0xF800) == 0x8800:
            rb, rd = (op >> 3) & 7, op & 7
            off = (op >> 6) & 0x1F
            addr = (self.reg_get(rb) + (off << 1)) & 0xFFFFFFFF
            if op & 0x0800:
                self.reg_set(rd, self.read16(addr))
            else:
                self.write16(addr, self.reg_get(rd) & 0xFFFF)
            return 2 + self._wait_states(addr, 16)

        if (op & 0xFF00) == 0xB000 and not (op & 0x0E00):
            imm = (op & 0x7F) << 2
            if op & 0x0080:
                self.r[13] = (self.r[13] + imm) & 0xFFFFFFFF
            else:
                self.r[13] = (self.r[13] - imm) & 0xFFFFFFFF
            return 1

        if (op & 0xF800) == 0xB200:
            rd = (op >> 8) & 7
            rm = (op >> 3) & 7
            val = self.reg_get(rm)
            if op & 0x0800:
                if val & 0x80:
                    val |= 0xFFFFFF00
            else:
                val &= 0xFF
            self.reg_set(rd, val)
            return 1

        if (op & 0xF800) == 0xB000:
            regs = op & 0xFF
            lr = 1 if op & 0x100 else 0
            sp = self.r[13]
            if op & 0x0800:
                if lr:
                    self.write32(sp, self.r[14])
                    sp += 4
                for i in range(8):
                    if regs & (1 << i):
                        self.write32(sp, self.r[i])
                        sp += 4
                self.r[13] = sp
            else:
                for i in range(7, -1, -1):
                    if regs & (1 << i):
                        sp -= 4
                        self.r[i] = self.read32(sp)
                if lr:
                    sp -= 4
                    self.r[15] = self.read32(sp)
                self.r[13] = sp
            return 2

        if (op & 0xF000) == 0xC000:
            off = op & 0xFF
            if off & 0x80:
                off = -((~off + 1) & 0xFF)
            self.r[15] = (self.r[15] + (off << 1)) & 0xFFFFFFFF
            return 3

        if (op & 0xFF00) == 0xDF00:
            self._swi_hle(op & 0xFF)
            return 3

        if (op & 0xF800) == 0x7000:
            off = op & 0x7FF
            if off & 0x400:
                off = -((~off + 1) & 0x7FF)
            self.r[15] = (self.r[15] + (off << 1)) & 0xFFFFFFFF
            return 3

        if (op & 0xF800) == 0xF000:
            if op & 0x0800:
                self.r[14] = (self.r[15] - 1) & 0xFFFFFFFF
            off = op & 0x7FF
            if off & 0x400:
                off = -((~off + 1) & 0x7FF)
            self.r[15] = (self.r[15] + (off << 1)) & 0xFFFFFFFF
            return 3

        if (op & 0xF800) == 0xE000:
            self.r[15] = (self.r[15] + struct.unpack("<h", struct.pack("<H", op & 0x7FF))[0] * 2) & 0xFFFFFFFF
            return 3

        if (op & 0xF000) == 0xD000:
            cond = hi
            if cond == 0xE:
                return 1
            if self.check_cond(cond):
                off = op & 0xFF
                if off & 0x80:
                    off = -((~off + 1) & 0xFF)
                self.r[15] = (self.r[15] + (off << 1)) & 0xFFFFFFFF
            return 3

        return 1

    def _exec_arm(self, op: int) -> int:
        cond = (op >> 28) & 0xF
        if cond != 0xE and not self.check_cond(cond):
            self.r[15] = (self.r[15] + 4) & 0xFFFFFFFF
            return 1

        if (op & 0x0FFFFFF0) == 0x012FFF10:
            rm = op & 0xF
            addr = self.reg_get(rm)
            self.set_thumb(bool(addr & 1))
            self.r[15] = (addr & ~1) & 0xFFFFFFFF
            return 3

        if (op & 0x0FB00000) == 0x01000000:
            return self._arm_psr(op)
        if (op & 0x0FF00000) == 0x01600000:
            return self._arm_clz(op)
        if (op & 0x0F8000F0) == 0x00800090:
            return self._arm_long_mult(op)
        if (op & 0x0E000000) == 0x00000000 and (op & 0xFC000000) != 0x04000000:
            return self._arm_data_proc(op)
        if (op & 0x0C000000) == 0x04000000:
            return self._arm_single_trans(op)
        if (op & 0x0F000000) == 0x02000000:
            return self._arm_multiply(op)
        if (op & 0x0E000000) == 0x08000000:
            return self._arm_block_trans(op)
        if (op & 0x0E400F90) == 0x00400090 or (op & 0x0E400F90) == 0x00500090:
            return self._arm_halfword(op)
        if (op & 0x0F000000) == 0x0F000000:
            self._swi_hle(op & 0xFFFFFF)
            self.r[15] = (self.r[15] + 4) & 0xFFFFFFFF
            return 3
        if (op & 0x0E000000) == 0x0A000000:
            return self._arm_branch(op)
        self.r[15] = (self.r[15] + 4) & 0xFFFFFFFF
        return 1

    def _arm_clz(self, op: int) -> int:
        rd = (op >> 12) & 0xF
        rm = op & 0xF
        val = self.reg_get(rm)
        count = 0
        for i in range(31, -1, -1):
            if val & (1 << i):
                count = 31 - i
                break
        else:
            count = 32
        self.reg_set(rd, count)
        self.r[15] = (self.r[15] + 4) & 0xFFFFFFFF
        return 1

    def _arm_psr(self, op: int) -> int:
        rd = (op >> 12) & 0xF
        if op & 0x00400000:
            val = self.reg_get(op & 0xF)
            mask = 0xFFFFFFFF
            if op & 0x00080000:
                mask = 0xFF000000 if op & 0x00040000 else 0x00FF0000 if op & 0x00020000 else 0x0000FF00 if op & 0x00010000 else 0x000000FF
            if op & 0x00100000:
                self.cpsr = (self.cpsr & ~mask) | (val & mask)
            else:
                self.cpsr = val & 0xF0000000 | (val & mask) | (self.cpsr & ~mask & 0x0FFFFFFF)
        else:
            self.reg_set(rd, self.cpsr if not (op & 0x00080000) else self.cpsr & 0xFF000000)
        self.r[15] = (self.r[15] + 4) & 0xFFFFFFFF
        return 1

    def _arm_long_mult(self, op: int) -> int:
        rd_lo = (op >> 12) & 0xF
        rd_hi = (op >> 16) & 0xF
        rs = op & 0xF
        rm = (op >> 8) & 0xF
        signed = bool(op & 0x00400000)
        a = self.reg_get(rm)
        b = self.reg_get(rs)
        if signed:
            if a & 0x80000000:
                a -= 0x100000000
            if b & 0x80000000:
                b -= 0x100000000
        res = a * b
        if op & 0x00200000:
            res += (self.reg_get(rd_hi) << 32) | self.reg_get(rd_lo)
        self.reg_set(rd_lo, res & 0xFFFFFFFF)
        self.reg_set(rd_hi, (res >> 32) & 0xFFFFFFFF)
        if op & 0x00100000:
            self.set_nz(res & 0xFFFFFFFF)
        self.r[15] = (self.r[15] + 4) & 0xFFFFFFFF
        return 1

    def _arm_shifter(self, op: int, c_in: bool) -> tuple[int, bool]:
        rm = op & 0xF
        val = self.reg_get(rm)
        if op & 0x02000000:
            imm = op & 0xFF
            rot = ((op >> 8) & 0xF) * 2
            if rot:
                c_out = bool((imm >> (rot - 1)) & 1)
                val = ((imm >> rot) | (imm << (32 - rot))) & 0xFFFFFFFF
            else:
                c_out = c_in
            return val, c_out
        shift = (op >> 5) & 3
        amount = (op >> 7) & 0x1F
        rs = (op >> 8) & 0xF
        if rs == 15:
            amount = (self.reg_get(15) + 4) & 0xFF if not self.thumb() else (self.reg_get(15) + 2) & 0xFF
        if shift == 0:
            if amount == 0:
                return val, c_in
            c_out = bool((val >> (amount - 1)) & 1)
            val = (val << amount) & 0xFFFFFFFF
        elif shift == 1:
            if amount == 0:
                amount = 32
            c_out = bool((val >> (amount - 1)) & 1)
            val = (val >> amount) & 0xFFFFFFFF
        elif shift == 2:
            if amount == 0:
                amount = 32
            c_out = bool((val >> (amount - 1)) & 1)
            val = (val >> amount) | (int(c_in) << (31 - amount + 1)) if amount <= 32 else 0
            val &= 0xFFFFFFFF
        else:
            if amount == 0:
                amount = 32
            c_out = bool(val & 1)
            val = ((val >> 1) | (int(c_in) << 31)) & 0xFFFFFFFF if amount == 1 else (
                ((val << (32 - amount)) | (val >> amount)) & 0xFFFFFFFF
            )
        return val, c_out

    def _arm_data_proc(self, op: int) -> int:
        rd = (op >> 12) & 0xF
        rn = (op >> 16) & 0xF
        opcode = (op >> 21) & 0xF
        s = bool(op & 0x00100000)
        op2, c_out = self._arm_shifter(op, self.flag_c())
        op1 = self.reg_get(rn)
        res = 0
        if opcode == 0x0:
            res = op1 & op2
            if s:
                self.set_nz(res)
                self.cpsr = (self.cpsr & ~0x20000000) | (int(c_out) << 29)
        elif opcode == 0x1:
            res = op1 ^ op2
            if s:
                self.set_nz(res)
                self.cpsr = (self.cpsr & ~0x20000000) | (int(c_out) << 29)
        elif opcode == 0x2:
            res = (op1 - op2) & 0xFFFFFFFF
            if s:
                self.set_nz_sub(res, op1, op2)
                self.cpsr = (self.cpsr & ~0x20000000) | (int(c_out) << 29)
        elif opcode == 0x4:
            res = (op1 + op2) & 0xFFFFFFFF
            if s:
                self.set_nz_add(res, op1, op2)
                self.cpsr = (self.cpsr & ~0x20000000) | (int(c_out) << 29)
        elif opcode == 0x3:
            res = (op1 ^ 0xFFFFFFFF) + op2 + 1
            res &= 0xFFFFFFFF
            if s:
                self.set_nz_add(res, (~op1) & 0xFFFFFFFF, op2)
        elif opcode == 0x5:
            res = (op1 + op2 + int(self.flag_c())) & 0xFFFFFFFF
            if s:
                self.set_nz_add(res, op1, op2)
        elif opcode == 0x6:
            res = (op1 - op2 - (1 - int(self.flag_c()))) & 0xFFFFFFFF
            if s:
                self.set_nz_sub(res, op1, op2)
        elif opcode == 0x7:
            res = (op2 - op1 - (1 - int(self.flag_c()))) & 0xFFFFFFFF
            if s:
                self.set_nz_sub(res, op2, op1)
        elif opcode == 0x8:
            res = op1 & op2
            if s:
                self.set_nz(res)
        elif opcode == 0xA:
            self.set_nz_sub((op1 - op2) & 0xFFFFFFFF, op1, op2)
            res = op1
        elif opcode == 0xB:
            res = (op1 + op2) & 0xFFFFFFFF
            if s:
                self.set_nz_add(res, op1, op2)
        elif opcode == 0xE:
            res = (op1 & ~op2) & 0xFFFFFFFF
            if s:
                self.set_nz(res)
        elif opcode == 0xC:
            res = (op1 | op2) & 0xFFFFFFFF
            if s:
                self.set_nz(res)
                self.cpsr = (self.cpsr & ~0x20000000) | (int(c_out) << 29)
        elif opcode == 0xD:
            res = op2
            if s:
                self.set_nz(res)
                self.cpsr = (self.cpsr & ~0x20000000) | (int(c_out) << 29)
        else:
            res = op1
        if rd == 15:
            self.r[15] = (res - 4) & 0xFFFFFFFF
        else:
            self.reg_set(rd, res)
        self.r[15] = (self.r[15] + 4) & 0xFFFFFFFF
        return 1

    def _arm_multiply(self, op: int) -> int:
        rd = (op >> 16) & 0xF
        rs = op & 0xF
        rm = (op >> 8) & 0xF
        res = (self.reg_get(rm) * self.reg_get(rs)) & 0xFFFFFFFF
        if op & 0x00200000:
            res = (res + self.reg_get(rd)) & 0xFFFFFFFF
        self.reg_set(rd, res)
        if op & 0x00100000:
            self.set_nz(res)
        self.r[15] = (self.r[15] + 4) & 0xFFFFFFFF
        return 1

    def _arm_single_trans(self, op: int) -> int:
        rd = (op >> 12) & 0xF
        rn = (op >> 16) & 0xF
        load = bool(op & 0x00100000)
        byte = bool(op & 0x00400000)
        up = bool(op & 0x00800000)
        writeback = bool(op & 0x00200000)
        pre = bool(op & 0x01000000)
        imm = op & 0xFFF
        if not (op & 0x02000000):
            imm = self.reg_get(op & 0xF)
        if rn == 15:
            base = (self.r[15] + 8) & 0xFFFFFFFF
        else:
            base = self.reg_get(rn)
        if pre:
            addr = (base + imm) & 0xFFFFFFFF if up else (base - imm) & 0xFFFFFFFF
            if writeback and rn != 15:
                self.reg_set(rn, addr)
        else:
            addr = base
            wb = (base + imm) & 0xFFFFFFFF if up else (base - imm) & 0xFFFFFFFF
        if not pre:
            eff = (addr + imm) & 0xFFFFFFFF if up else (addr - imm) & 0xFFFFFFFF
        else:
            eff = addr
        if load:
            val = self.read8(eff) if byte else self.read32(eff)
            if rd == 15:
                self.r[15] = val
            else:
                self.reg_set(rd, val)
        else:
            if byte:
                self.write8(eff, self.reg_get(rd) & 0xFF)
            else:
                self.write32(eff, self.reg_get(rd))
        if not pre and writeback and rn != 15:
            self.reg_set(rn, wb)
        self.r[15] = (self.r[15] + 4) & 0xFFFFFFFF
        return 1 + self._wait_states(eff, 8 if byte else 32)

    def _arm_halfword(self, op: int) -> int:
        rd = (op >> 12) & 0xF
        rn = (op >> 16) & 0xF
        rm = op & 0xF
        load = bool(op & 0x00100000)
        signed = bool(op & 0x00400000)
        addr = (self.reg_get(rn) + self.reg_get(rm)) & 0xFFFFFFFF
        if load:
            val = self.read16(addr)
            if signed and val & 0x8000:
                val |= 0xFFFF0000
            self.reg_set(rd, val)
        else:
            self.write16(addr, self.reg_get(rd) & 0xFFFF)
        self.r[15] = (self.r[15] + 4) & 0xFFFFFFFF
        return 1 + self._wait_states(addr, 16)

    def _arm_block_trans(self, op: int) -> int:
        rn = (op >> 16) & 0xF
        load = bool(op & 0x00100000)
        up = bool(op & 0x00800000)
        reglist = op & 0xFFFF
        addr = self.reg_get(rn)
        if not up:
            bits = reglist.bit_count()
            addr = (addr - bits * 4) & 0xFFFFFFFF
        for i in range(16):
            if reglist & (1 << i):
                if load:
                    val = self.read32(addr)
                    if i == 15:
                        val = (val - 4) & 0xFFFFFFFF
                    self.reg_set(i, val)
                else:
                    self.write32(addr, self.reg_get(i))
                addr = (addr + 4) & 0xFFFFFFFF
        if load and (op & 0x8000):
            self.reg_set(rn, addr if up else self.reg_get(rn))
        elif not load and up:
            self.reg_set(rn, addr)
        self.r[15] = (self.r[15] + 4) & 0xFFFFFFFF
        return 1 + reglist.bit_count()

    def _arm_branch(self, op: int) -> int:
        link = bool(op & 0x01000000)
        off = op & 0x00FFFFFF
        if off & 0x00800000:
            off |= ~0xFFFFFF
        target = (self.r[15] + 8 + (off << 2)) & 0xFFFFFFFF
        if link:
            self.r[14] = (self.r[15] + 4) & 0xFFFFFFFF
        self.r[15] = target
        return 3

    def _run_scanline(self, line: int) -> None:
        self._set_io16(REG_VCOUNT, line)
        stat = self._io16(REG_DISPSTAT)
        if line == VDRAW_LINES:
            self._set_io16(REG_DISPSTAT, stat | 0x0001)
            self._irq_raise(0)
            self._dma_vblank_hblank(1)
        if line < VDRAW_LINES:
            if _ACCEL is not None:
                _ACCEL.fast_run_scanline(self, line, CYCLES_PER_SCANLINE)
            else:
                self._run_cycles(CYCLES_PER_SCANLINE)
            self._dma_vblank_hblank(2)
        else:
            if _ACCEL is not None:
                _ACCEL.fast_run_scanline(self, line, CYCLES_PER_SCANLINE)
            else:
                self._run_cycles(CYCLES_PER_SCANLINE)

    # --- PPU ---

    def _pix(self, x: int, y: int, rgb: tuple[int, int, int], prio: int = 3, layer: int = 0) -> None:
        if not (0 <= x < SCREEN_W and 0 <= y < SCREEN_H):
            return
        if not (self._win_layers[y][x] & (1 << layer)):
            return
        if prio <= self._prio[y][x]:
            self._prio[y][x] = prio
            self._pxbuf[y][x] = rgb

    def _update_win_layers(self) -> None:
        disp = self._io16(REG_DISPCNT)
        if _ACCEL is not None:
            _ACCEL.fast_build_win_layers(
                self._win_layers,
                disp,
                self._io16(REG_WIN0H),
                self._io16(REG_WIN0V),
                self._io16(REG_WIN1H),
                self._io16(REG_WIN1V),
                self._io16(REG_WININ),
                self._io16(REG_WINOUT),
            )
            return
        winin = self._io16(REG_WININ)
        winout = self._io16(REG_WINOUT)
        outside = winout & 0x3F
        w0in, w1in = winin & 0x3F, (winin >> 8) & 0x3F
        for y in range(SCREEN_H):
            for x in range(SCREEN_W):
                in0 = in1 = False
                if disp & 0x2000:
                    w0h, w0v = self._io16(REG_WIN0H), self._io16(REG_WIN0V)
                    left, right = w0h & 0xFF, min(SCREEN_W, (w0h >> 8) & 0xFF)
                    top, bot = w0v & 0xFF, min(SCREEN_H, (w0v >> 8) & 0xFF)
                    if left <= x < right and top <= y < bot:
                        in0 = True
                if disp & 0x4000:
                    w1h, w1v = self._io16(REG_WIN1H), self._io16(REG_WIN1V)
                    left, right = w1h & 0xFF, min(SCREEN_W, (w1h >> 8) & 0xFF)
                    top, bot = w1v & 0xFF, min(SCREEN_H, (w1v >> 8) & 0xFF)
                    if left <= x < right and top <= y < bot:
                        in1 = True
                if in0 and in1:
                    self._win_layers[y][x] = w0in & w1in
                elif in0:
                    self._win_layers[y][x] = w0in
                elif in1:
                    self._win_layers[y][x] = w1in
                else:
                    self._win_layers[y][x] = outside if outside else 0x3F

    def _aff_param(self, off: int) -> int:
        lo = self._io16(off)
        hi = self._io16(off + 2)
        val = lo | (hi << 16)
        if val & 0x08000000:
            val |= 0xF0000000
        return val

    def _mosaic_coord(self, x: int, y: int, bg: bool) -> tuple[int, int]:
        m = self._io16(REG_MOSAIC)
        hv = (m & 0xF) if bg else ((m >> 8) & 0xF)
        vv = ((m >> 4) & 0xF) if bg else ((m >> 12) & 0xF)
        hs, vs = hv + 1, vv + 1
        return x - (x % hs), y - (y % vs)

    def render_frame(self) -> None:
        self._prio = [[3] * SCREEN_W for _ in range(SCREEN_H)]
        self._pxbuf = [[(8, 12, 27)] * SCREEN_W for _ in range(SCREEN_H)]
        self._update_win_layers()
        disp = self._io16(REG_DISPCNT)
        mode = disp & 7
        if mode == 3:
            if _ACCEL is not None:
                _ACCEL.fast_render_mode3(self.vram, self._prio, self._pxbuf, self._win_layers, 1)
            else:
                self._render_mode3()
        elif mode == 4:
            if _ACCEL is not None:
                frame = (disp >> 4) & 1
                _ACCEL.fast_render_mode4(
                    self.vram, self.palette, self._prio, self._pxbuf, self._win_layers, 1, frame * 0xA000
                )
            else:
                self._render_mode4()
        elif mode in (0, 1, 2):
            self._render_mode012(mode)
        elif mode == 5:
            if _ACCEL is not None:
                frame = (disp >> 4) & 1
                _ACCEL.fast_render_mode5(
                    self.vram, self._prio, self._pxbuf, self._win_layers, 1, frame * 0xA000
                )
            else:
                self._render_mode5()
        self._render_sprites()
        if _ACCEL is not None:
            _ACCEL.fast_compose_fb(self.framebuffer, self._pxbuf, SCREEN_W, SCREEN_H)
        else:
            for y in range(SCREEN_H):
                for x in range(SCREEN_W):
                    r, g, b = self._pxbuf[y][x]
                    i = (y * SCREEN_W + x) * 3
                    self.framebuffer[i], self.framebuffer[i + 1], self.framebuffer[i + 2] = r, g, b

    def _render_mode3(self) -> None:
        for y in range(SCREEN_H):
            for x in range(SCREEN_W):
                off = (y * SCREEN_W + x) * 2
                color = self.vram[off] | (self.vram[off + 1] << 8)
                self._pix(x, y, rgb565_to_rgb(color), 0, 0)

    def _render_mode4(self) -> None:
        frame = (self._io16(REG_DISPCNT) >> 4) & 1
        base = frame * 0xA000
        for y in range(SCREEN_H):
            for x in range(SCREEN_W):
                idx = self.vram[base + y * SCREEN_W + x]
                color = self.palette[idx * 2] | (self.palette[idx * 2 + 1] << 8)
                self._pix(x, y, rgb565_to_rgb(color), 0, 0)

    def _render_mode5(self) -> None:
        frame = (self._io16(REG_DISPCNT) >> 4) & 1
        base = frame * 0xA000
        for y in range(SCREEN_H):
            for x in range(SCREEN_W):
                off = base + (y * SCREEN_W + x) * 2
                color = self.vram[off] | (self.vram[off + 1] << 8)
                self._pix(x, y, rgb565_to_rgb(color), 0, 0)

    def _render_mode012(self, mode: int) -> None:
        layers = []
        disp = self._io16(REG_DISPCNT)
        for bg in range(4 if mode < 2 else 2):
            if not (disp & (0x0400 << bg)):
                continue
            ctrl = self._io16(REG_BG0CNT + bg * 2)
            layers.append((ctrl & 3, bg, ctrl))
        layers.sort()
        for _, bg, ctrl in layers:
            if mode >= 1 and bg >= 2:
                self._render_affine_bg(bg, ctrl, ctrl & 3)
            else:
                self._render_text_bg(bg, ctrl, ctrl & 3)

    def _render_affine_bg(self, bg: int, ctrl: int, prio: int) -> None:
        base = REG_BG2PA + (bg - 2) * 16
        pa = self._aff_param(base) >> 8
        pb = self._aff_param(base + 4) >> 8
        pc = self._aff_param(base + 8) >> 8
        pd = self._aff_param(base + 12) >> 8
        ox = self._aff_param(base + 16)
        oy = self._aff_param(base + 20)
        char_base = ((ctrl >> 2) & 3) * 0x4000
        map_base = ((ctrl >> 8) & 0x1F) * 0x800
        wrap = bool(ctrl & 0x2000)
        mx = 128 if (ctrl & 0xC00) == 0x800 else 256 if (ctrl & 0xC00) == 0xC00 else 512
        my = 128 if (ctrl & 0xC00) == 0x800 else 256 if (ctrl & 0xC00) == 0xC00 else 512
        map_w = mx // 8
        line_x, line_y = ox, oy
        mosaic_on = bool(self._io16(REG_MOSAIC) & 0xFF)
        for y in range(SCREEN_H):
            sx, sy = line_x, line_y
            for x in range(SCREEN_W):
                if mosaic_on:
                    mx_x, mx_y = self._mosaic_coord(x, y, True)
                    rx = ((pa * mx_x + pb * mx_y) >> 8) + (ox >> 8)
                    ry = ((pc * mx_x + pd * mx_y) >> 8) + (oy >> 8)
                else:
                    rx = sx >> 8
                    ry = sy >> 8
                    sx = (sx + pa) & 0xFFFFFFFF
                    sy = (sy + pc) & 0xFFFFFFFF
                if rx < 0 or ry < 0:
                    continue
                if wrap:
                    rx %= mx
                    ry %= my
                elif rx >= mx or ry >= my:
                    continue
                tx, ty = rx >> 3, ry >> 3
                map_off = map_base + (ty * map_w + tx) * 2
                if map_off + 1 >= len(self.vram):
                    continue
                entry = self.vram[map_off] | (self.vram[map_off + 1] << 8)
                tile_id = entry & 0x3FF
                px, py = rx & 7, ry & 7
                tile_off = char_base + tile_id * 64 + py * 8 + px
                if tile_off >= len(self.vram):
                    continue
                idx = self.vram[tile_off]
                if idx == 0:
                    continue
                color = self.palette[idx * 2] | (self.palette[idx * 2 + 1] << 8)
                self._pix(x, y, rgb565_to_rgb(color), prio, bg)
            line_x = (line_x + pb) & 0xFFFFFFFF
            line_y = (line_y + pd) & 0xFFFFFFFF

    def _render_text_bg(self, bg: int, ctrl: int, prio: int) -> None:
        char_base = ((ctrl >> 2) & 3) * 0x4000
        map_base = ((ctrl >> 8) & 0x1F) * 0x800
        bpp8 = bool(ctrl & 0x80)
        mx = 512 if (ctrl & 0x400) else 256
        hofs = self._io16(REG_BG0HOFS + bg * 4) & 0x1FF
        vofs = self._io16(REG_BG0HOFS + bg * 4 + 2) & 0x1FF
        for y in range(SCREEN_H):
            for x in range(SCREEN_W):
                mx_x, mx_y = self._mosaic_coord(x, y, True)
                wx = (mx_x + hofs) % mx
                wy = (mx_y + vofs) % 256
                tx, ty = wx // 8, wy // 8
                map_off = map_base + (ty * (mx // 8) + tx) * 2
                if map_off + 1 >= len(self.vram):
                    continue
                entry = self.vram[map_off] | (self.vram[map_off + 1] << 8)
                tile_id = entry & 0x3FF
                hflip = entry & 0x0400
                vflip = entry & 0x0800
                pal_bank = (entry >> 12) & 0xF
                px, py = wx % 8, wy % 8
                if hflip:
                    px = 7 - px
                if vflip:
                    py = 7 - py
                if bpp8:
                    tile_off = char_base + tile_id * 64 + py * 8 + px
                    if tile_off >= len(self.vram):
                        continue
                    idx = self.vram[tile_off]
                    color = self.palette[idx * 2] | (self.palette[idx * 2 + 1] << 8)
                else:
                    tile_off = char_base + tile_id * 32 + py * 4 + (px // 2)
                    if tile_off >= len(self.vram):
                        continue
                    byte = self.vram[tile_off]
                    idx = (byte >> 4) if px & 1 else (byte & 0xF)
                    if idx == 0:
                        continue
                    pal_off = (pal_bank * 16 + idx) * 2
                    color = self.palette[pal_off] | (self.palette[pal_off + 1] << 8)
                if bpp8 or idx != 0:
                    self._pix(x, y, rgb565_to_rgb(color), prio, bg)

    def _oam_affine(self, idx: int) -> tuple[int, int, int, int]:
        off = 0x200 + (idx & 0x1F) * 8
        pa = struct.unpack("<h", self.oam[off : off + 2])[0]
        pb = struct.unpack("<h", self.oam[off + 2 : off + 4])[0]
        pc = struct.unpack("<h", self.oam[off + 4 : off + 6])[0]
        pd = struct.unpack("<h", self.oam[off + 6 : off + 8])[0]
        return pa, pb, pc, pd

    def _render_sprites(self) -> None:
        disp = self.read16(0x4000000)
        if not (disp & 0x1000):
            return
        obj1d = bool(disp & 0x0040)
        objs = []
        for i in range(128):
            off = i * 8
            attr0 = self.oam[off] | (self.oam[off + 1] << 8)
            attr1 = self.oam[off + 2] | (self.oam[off + 3] << 8)
            attr2 = self.oam[off + 4] | (self.oam[off + 5] << 8)
            if (attr0 & 0x0300) == 0x0200:
                continue
            objs.append((attr0, attr1, attr2))
        for attr0, attr1, attr2 in reversed(objs):
            y = attr0 & 0xFF
            x = attr1 & 0x1FF
            tile = attr2 & 0x3FF
            prio = (attr2 >> 10) & 3
            pal = (attr2 >> 12) & 0xF
            shape = (attr0 >> 14) & 3
            size = (attr1 >> 14) & 3
            affine = bool(attr0 & 0x0100)
            hflip = bool(attr1 & 0x1000) and not affine
            vflip = bool(attr1 & 0x2000) and not affine
            bpp8 = bool(attr0 & 0x2000)
            dims_tbl = {
                0: [(8, 8), (16, 16), (32, 32), (64, 64)],
                1: [(16, 8), (32, 8), (32, 16), (64, 32)],
                2: [(8, 16), (8, 32), (16, 32), (32, 64)],
            }
            w, h = dims_tbl[shape][size]
            if y >= 160:
                y -= 256
            if affine:
                aidx = (attr1 >> 9) & 0x1F
                pa, pb, pc, pd = self._oam_affine(aidx)
                cx, cy = w // 2, h // 2
                for sy in range(h):
                    for sx in range(w):
                        lx, ly = sx - cx, sy - cy
                        rx = (pa * lx + pb * ly) >> 8
                        ry = (pc * lx + pd * ly) >> 8
                        px, py = x + cx + rx, y + cy + ry
                        mx, my = self._mosaic_coord(px, py, False)
                        if mx != px or my != py:
                            continue
                        if not (0 <= px < SCREEN_W and 0 <= py < SCREEN_H):
                            continue
                        tnum = tile + (sy // 8) * (32 if obj1d else (w // 8)) + (sx // 8)
                        toff = 0x10000 + tnum * (64 if bpp8 else 32)
                        subx, suby = sx % 8, sy % 8
                        if bpp8:
                            idx = self.vram[toff + suby * 8 + subx]
                            if idx == 0:
                                continue
                            color = self.palette[idx * 2] | (self.palette[idx * 2 + 1] << 8)
                        else:
                            byte = self.vram[toff + suby * 4 + subx // 2]
                            idx = (byte >> 4) if subx & 1 else (byte & 0xF)
                            if idx == 0:
                                continue
                            pal_off = (pal * 16 + idx) * 2
                            color = self.palette[pal_off] | (self.palette[pal_off + 1] << 8)
                        self._pix(px, py, rgb565_to_rgb(color), prio, 4)
            else:
                m = self._io16(REG_MOSAIC)
                obj_mosaic = ((m >> 8) & 0xF) or ((m >> 12) & 0xF)
                for sy in range(h):
                    for sx in range(w):
                        px, py = x + sx, y + sy
                        if not (0 <= px < SCREEN_W and 0 <= py < SCREEN_H):
                            continue
                        if obj_mosaic:
                            mx, my = self._mosaic_coord(px, py, False)
                            tsx, tsy = mx - x, my - y
                            if not (0 <= tsx < w and 0 <= tsy < h):
                                continue
                        else:
                            tsx, tsy = sx, sy
                        if hflip:
                            tsx = w - 1 - tsx
                        if vflip:
                            tsy = h - 1 - tsy
                        tnum = tile + (tsy // 8) * (32 if obj1d else (w // 8)) + (tsx // 8)
                        toff = 0x10000 + tnum * (64 if bpp8 else 32)
                        subx, suby = tsx % 8, tsy % 8
                        if bpp8:
                            idx = self.vram[toff + suby * 8 + subx]
                            if idx == 0:
                                continue
                            color = self.palette[idx * 2] | (self.palette[idx * 2 + 1] << 8)
                        else:
                            byte = self.vram[toff + suby * 4 + subx // 2]
                            idx = (byte >> 4) if subx & 1 else (byte & 0xF)
                            if idx == 0:
                                continue
                            pal_off = (pal * 16 + idx) * 2
                            color = self.palette[pal_off] | (self.palette[pal_off + 1] << 8)
                        self._pix(px, py, rgb565_to_rgb(color), prio, 4)

    def step_frame(self) -> None:
        if not self.is_loaded:
            return
        for line in range(TOTAL_LINES):
            self._run_scanline(line)
        self.render_frame()


class MewGBASound:
    """Sound (later) — APU stub for future PSG/DMA FIFO emulation."""

    def __init__(self) -> None:
        self.enabled = False

    def step(self, _cycles: int) -> None:
        pass


def _recent_load() -> list[str]:
    try:
        if os.path.isfile(MEWGBA_RECENT):
            data = json.load(open(MEWGBA_RECENT, encoding="utf-8"))
            return [p for p in data if isinstance(p, str) and os.path.isfile(p)][:8]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _recent_push(path: str) -> None:
    os.makedirs(MEWGBA_CACHE, exist_ok=True)
    items = [path] + [p for p in _recent_load() if p != path]
    try:
        json.dump(items[:8], open(MEWGBA_RECENT, "w", encoding="utf-8"))
    except OSError:
        pass


def _script_dir() -> str:
    return os.path.dirname(os.path.abspath(globals().get("__file__", sys.argv[0])))


def _hex_editor_path() -> str | None:
    here = _script_dir()
    for rel in (HEX_EDITOR_REL, "hexeditor4k.py"):
        p = os.path.normpath(os.path.join(here, rel))
        if os.path.isfile(p):
            return p
    return None


def _launch_hex_editor(data: bytes, title: str = "mewgba dump") -> None:
    os.makedirs(MEWGBA_CACHE, exist_ok=True)
    dump = os.path.join(MEWGBA_CACHE, f"{title.replace(' ', '_')}.bin")
    with open(dump, "wb") as f:
        f.write(data)
    editor = _hex_editor_path()
    if editor:
        subprocess.Popen([sys.executable, editor, dump], close_fds=True)
    else:
        messagebox.showinfo("Hex dump", f"Saved to:\n{dump}")


class DebugCythonGuideWindow:
    """In-app viewer for MEWGBA_CYTHON_GUIDE (no external .md)."""

    def __init__(self, root: tk.Tk) -> None:
        self.win = tk.Toplevel(root)
        self.win.title("Cython Guide — mewgba$")
        self.win.geometry("640x520")
        self.win.configure(bg="#0a192f")
        status = _accel_status()
        header = tk.Frame(self.win, bg="#1a2436")
        header.pack(fill=tk.X)
        accel_txt = "ACTIVE +Cython" if status["active"] else "fallback (pure Python)"
        tk.Label(
            header,
            text=f"Accel: {accel_txt}  |  Py {status['python']}  |  cache: {status['cache']}",
            bg="#1a2436",
            fg="#00b4d8",
            font=("Arial", 9),
            anchor="w",
            padx=10,
            pady=6,
        ).pack(fill=tk.X)
        body = tk.Text(
            self.win,
            bg="#020c1b",
            fg="#00b4d8",
            font=("Consolas", 9),
            relief="flat",
            padx=10,
            pady=8,
            wrap=tk.WORD,
        )
        body.pack(fill=tk.BOTH, expand=True)
        lines = [MEWGBA_CYTHON_GUIDE, "", "── live status ──", f"hash: {status['digest']}…"]
        for name, ok in status["functions"].items():
            lines.append(f"  {'✓' if ok else '✗'} {name}")
        lines += ["", "── future targets ──"]
        lines.extend(f"  • {t}" for t in MEWGBA_FUTURE_CYTHON_TARGETS)
        body.insert("1.0", "\n".join(lines))
        body.config(state=tk.DISABLED)


class DebugRegisterWindow:
    def __init__(self, root: tk.Tk, core: MewGBACore) -> None:
        self.core = core
        self.win = tk.Toplevel(root)
        self.win.title("Registers — mewgba$")
        self.win.geometry("380x420")
        self.win.configure(bg="#0a192f")
        self.text = tk.Text(
            self.win, bg="#020c1b", fg="#00b4d8", font=("Consolas", 10), relief="flat", padx=8, pady=8
        )
        self.text.pack(fill=tk.BOTH, expand=True)
        self._tick()

    def _tick(self) -> None:
        if not self.win.winfo_exists():
            return
        c = self.core
        flags = f"N={int(c.flag_n())} Z={int(c.flag_z())} C={int(c.flag_c())} V={int(c.flag_v())} T={int(c.thumb())}"
        lines = [
            MEWGBA_ROADMAP.split("\n")[0],
            "",
            f"CPSR {c.cpsr:08X}  SPSR {c.spsr:08X}  {flags}",
            f"Halted {c.halted}  Cycles {c.cycles}",
            "",
        ]
        for i in range(16):
            name = "SP" if i == 13 else "LR" if i == 14 else "PC" if i == 15 else f"R{i}"
            lines.append(f"{name:3} {c.r[i]:08X}")
        lines += [
            "",
            f"DISPCNT {c._io16(REG_DISPCNT):04X}  VCOUNT {c._io16(REG_VCOUNT):02X}",
            f"KEYINPUT {c._io16(REG_KEYINPUT):04X}  IE/IF {c._io16(REG_IE):04X}/{c._io16(REG_IF):04X}",
        ]
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", "\n".join(lines))
        self.win.after(250, self._tick)


class DebugMemoryWindow:
    def __init__(self, root: tk.Tk, core: MewGBACore) -> None:
        self.core = core
        self.win = tk.Toplevel(root)
        self.win.title("Memory — mewgba$")
        self.win.geometry("720x480")
        self.win.configure(bg="#121820")
        self.base = 0x02000000
        self.region = "ewram"
        top = tk.Frame(self.win, bg="#1a2436")
        top.pack(fill=tk.X)
        tk.Label(top, text="Region", bg="#1a2436", fg="#6b7f99").pack(side=tk.LEFT, padx=8, pady=6)
        self.region_var = tk.StringVar(value="ewram")
        cb = ttk.Combobox(
            top, textvariable=self.region_var, values=[r[2] for r in MEMORY_REGIONS], width=10, state="readonly"
        )
        cb.pack(side=tk.LEFT, padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        tk.Button(top, text="Refresh", command=self.refresh, bg="#243552", fg="#38b8c7").pack(side=tk.LEFT, padx=6)
        tk.Button(top, text="Open in Hex Editor", command=self._open_hex, bg="#243552", fg="#38b8c7").pack(
            side=tk.LEFT, padx=6
        )
        self.text = tk.Text(
            self.win, bg="#121820", fg="#38b8c7", font=("Consolas", 10), relief="flat", padx=8, pady=8
        )
        self.text.pack(fill=tk.BOTH, expand=True)
        self.refresh()

    def refresh(self) -> None:
        self.region = self.region_var.get()
        self.base, data = self.core.memory_dump(self.region, 4096)
        lines = []
        for off in range(0, len(data), 16):
            chunk = data[off : off + 16]
            hexpart = " ".join(f"{b:02X}" for b in chunk)
            asc = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
            lines.append(f"{self.base + off:08X}  {hexpart:<47}  {asc}")
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", "\n".join(lines) if lines else "(empty)")

    def _open_hex(self) -> None:
        _, data = self.core.memory_dump(self.region, 262144)
        _launch_hex_editor(data, f"mewgba_{self.region}")


class MewGBAEmulator:
    def __init__(self, root: tk.Tk, rom_bytes: bytes | None = None, rom_label: str = "Demo (built-in)") -> None:
        self.root = root
        accel_tag = " +Cython" if _ACCEL is not None else ""
        py_tag = f" Py{sys.version_info.major}.{sys.version_info.minor}"
        self.root.title(f"mewgba${accel_tag}{py_tag}")
        self.root.geometry("540x480")
        self.root.resizable(False, False)

        self.bg_color = "#0a192f"
        self.text_color = "#00b4d8"
        self.button_bg = "#000000"
        self.button_fg = "#00b4d8"
        self.screen_bg = "#020c1b"

        self.root.configure(bg=self.bg_color)

        self.core = MewGBACore()
        self.sound = MewGBASound()
        self.rom_bytes = rom_bytes if rom_bytes is not None else DEFAULT_ROM
        self.rom_label = rom_label
        self.rom_path: str | None = None
        self.core.load_rom_bytes(self.rom_bytes, self.rom_label)
        self.is_running = False
        self.speed = 1.0
        self._photo: tk.PhotoImage | None = None
        self._scaled: tk.PhotoImage | None = None
        self._keys = 0
        self._frame_times: list[float] = []
        self._last_frame_t = 0.0
        self._reg_win: DebugRegisterWindow | None = None
        self._mem_win: DebugMemoryWindow | None = None
        self._cython_win: DebugCythonGuideWindow | None = None
        self._save_slot = os.path.join(MEWGBA_SAVES, "quicksave.mgb")

        os.makedirs(MEWGBA_SAVES, exist_ok=True)
        self.setup_ui()
        self._bind_keys()
        self.draw_screen()

    def setup_ui(self) -> None:
        control_frame = tk.Frame(self.root, bg=self.bg_color)
        control_frame.pack(fill=tk.X, padx=10, pady=10)

        btn_style = {
            "bg": self.button_bg,
            "fg": self.button_fg,
            "activebackground": self.text_color,
            "activeforeground": self.button_bg,
            "font": ("Arial", 9, "bold"),
            "bd": 1,
            "relief": "solid",
        }

        load_btn = tk.Button(control_frame, text="Load ROM", command=self.load_rom, **btn_style)
        load_btn.pack(side=tk.LEFT, padx=5)

        recent = _recent_load()
        if recent:
            self.recent_menu = tk.Menubutton(control_frame, text="Recent ▾", **btn_style)
            self.recent_menu.pack(side=tk.LEFT, padx=2)
            menu = tk.Menu(self.recent_menu, tearoff=0)
            for p in recent:
                menu.add_command(label=os.path.basename(p), command=lambda path=p: self.load_rom_path(path))
            self.recent_menu["menu"] = menu

        self.run_btn = tk.Button(control_frame, text="Play", command=self.toggle_execution, **btn_style)
        self.run_btn.pack(side=tk.LEFT, padx=5)

        reset_btn = tk.Button(control_frame, text="Reset", command=self.reset_emulator, **btn_style)
        reset_btn.pack(side=tk.LEFT, padx=5)

        tk.Button(control_frame, text="Save", command=self.save_state, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(control_frame, text="Load", command=self.load_state, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(control_frame, text="Regs", command=self.open_registers, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(control_frame, text="Mem", command=self.open_memory, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(control_frame, text="Cython", command=self.open_cython_guide, **btn_style).pack(side=tk.LEFT, padx=2)

        row2 = tk.Frame(self.root, bg=self.bg_color)
        row2.pack(fill=tk.X, padx=10, pady=(0, 4))
        tk.Label(row2, text="Speed", bg=self.bg_color, fg=self.text_color, font=("Arial", 9)).pack(side=tk.LEFT)
        self.speed_var = tk.DoubleVar(value=1.0)
        for label, val in (("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("4x", 4.0)):
            tk.Radiobutton(
                row2,
                text=label,
                variable=self.speed_var,
                value=val,
                command=self._set_speed,
                bg=self.bg_color,
                fg=self.text_color,
                selectcolor=self.button_bg,
                activebackground=self.bg_color,
                font=("Arial", 8),
            ).pack(side=tk.LEFT, padx=4)

        self.status_label = tk.Label(
            control_frame,
            text=f"ROM: {self.rom_label} (paused)",
            bg=self.bg_color,
            fg=self.text_color,
            font=("Arial", 9),
        )
        self.status_label.pack(side=tk.LEFT, padx=6)

        self.fps_label = tk.Label(
            row2, text="FPS —", bg=self.bg_color, fg=self.text_color, font=("Arial", 9)
        )
        self.fps_label.pack(side=tk.RIGHT, padx=8)

        self.display_canvas = tk.Canvas(
            self.root,
            width=480,
            height=320,
            bg=self.screen_bg,
            highlightthickness=1,
            highlightbackground=self.text_color,
        )
        self.display_canvas.pack(pady=5)

        tk.Label(
            self.root,
            text="Keys: Z=A X=B Arrows Q=L E=R Enter=Start Backspace=Select | F5=Save F9=Load",
            bg=self.bg_color,
            fg=self.text_color,
            font=("Arial", 8),
        ).pack(pady=(0, 4))

    def _set_speed(self) -> None:
        self.speed = self.speed_var.get()

    def open_registers(self) -> None:
        if self._reg_win and self._reg_win.win.winfo_exists():
            self._reg_win.win.lift()
            return
        self._reg_win = DebugRegisterWindow(self.root, self.core)

    def open_memory(self) -> None:
        if self._mem_win and self._mem_win.win.winfo_exists():
            self._mem_win.win.lift()
            self._mem_win.refresh()
            return
        self._mem_win = DebugMemoryWindow(self.root, self.core)

    def open_cython_guide(self) -> None:
        if self._cython_win and self._cython_win.win.winfo_exists():
            self._cython_win.win.lift()
            return
        self._cython_win = DebugCythonGuideWindow(self.root)

    def save_state(self) -> None:
        os.makedirs(MEWGBA_SAVES, exist_ok=True)
        snap = self.core.snapshot()
        snap["rom_path"] = self.rom_path
        try:
            with open(self._save_slot, "wb") as f:
                pickle.dump(snap, f, protocol=pickle.HIGHEST_PROTOCOL)
            self.status_label.config(text=f"Saved state → {os.path.basename(self._save_slot)}")
        except OSError as exc:
            messagebox.showerror("Save state", str(exc))

    def load_state(self) -> None:
        if not os.path.isfile(self._save_slot):
            messagebox.showwarning("Load state", "No quicksave found (press F5 first).")
            return
        try:
            with open(self._save_slot, "rb") as f:
                snap = pickle.load(f)
            self.core.restore(snap)
            self.rom_bytes = bytes(self.core.rom)
            self.rom_label = snap.get("rom_label") or "Savestate"
            self.rom_path = snap.get("rom_path")
            self.is_running = False
            self.run_btn.config(text="Play")
            self.draw_screen()
            self.status_label.config(text=f"Loaded state ({self.rom_label})")
        except (OSError, pickle.PickleError, KeyError) as exc:
            messagebox.showerror("Load state", str(exc))

    def load_rom_path(self, path: str) -> None:
        try:
            with open(path, "rb") as f:
                rom_data = f.read()
        except OSError as exc:
            messagebox.showerror("Error", f"Failed to load ROM:\n{exc}")
            return
        if not self.core.load_rom_bytes(rom_data, os.path.basename(path)):
            messagebox.showerror("Error", "ROM is empty or invalid.")
            return
        self.is_running = False
        self.run_btn.config(text="Play")
        self.rom_bytes = rom_data
        self.rom_label = os.path.basename(path)
        self.rom_path = path
        _recent_push(path)
        self.status_label.config(text=f"ROM: {self.rom_label} (paused)")
        self.draw_screen()

    def _bind_keys(self) -> None:
        for key in KEY_MAP:
            self.root.bind(f"<KeyPress-{key}>", self._on_key_down)
            self.root.bind(f"<KeyRelease-{key}>", self._on_key_up)
        self.root.bind("<F5>", lambda _e: self.save_state())
        self.root.bind("<F9>", lambda _e: self.load_state())
        self.root.focus_set()

    def _on_key_down(self, event: tk.Event) -> None:
        bit = KEY_MAP.get(event.keysym)
        if bit is not None:
            self._keys |= 1 << bit
            self.core.set_keys(self._keys)

    def _on_key_up(self, event: tk.Event) -> None:
        bit = KEY_MAP.get(event.keysym)
        if bit is not None:
            self._keys &= ~(1 << bit)
            self.core.set_keys(self._keys)

    def draw_screen(self) -> None:
        rows = []
        fb = self.core.framebuffer
        for y in range(SCREEN_H):
            row = []
            for x in range(SCREEN_W):
                i = (y * SCREEN_W + x) * 3
                row.append(f"#{fb[i]:02x}{fb[i + 1]:02x}{fb[i + 2]:02x}")
            rows.append("{" + " ".join(row) + "}")
        if self._photo is None:
            self._photo = tk.PhotoImage(width=SCREEN_W, height=SCREEN_H)
        self._photo.put(" ".join(rows), to=(0, 0))
        self._scaled = self._photo.zoom(2, 2)
        self.display_canvas.delete("all")
        self.display_canvas.create_image(240, 160, image=self._scaled)

    def load_rom(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Open Game Boy Advance ROM",
            filetypes=[("GBA ROMs", "*.gba"), ("All Files", "*.*")],
        )
        if not file_path:
            return

        try:
            with open(file_path, "rb") as f:
                rom_data = f.read()
        except OSError as exc:
            messagebox.showerror("Error", f"Failed to load ROM:\n{exc}")
            return

        if not self.core.load_rom_bytes(rom_data, os.path.basename(file_path)):
            messagebox.showerror("Error", "ROM is empty or invalid.")
            return

        self.is_running = False
        self.run_btn.config(text="Play")
        self.rom_bytes = rom_data
        self.rom_label = os.path.basename(file_path)
        self.rom_path = file_path
        _recent_push(file_path)
        self.status_label.config(text=f"ROM: {self.rom_label} (paused)")
        self.draw_screen()

    def reset_emulator(self) -> None:
        self.is_running = False
        self.run_btn.config(text="Play")
        self.core.load_rom_bytes(self.rom_bytes, self.rom_label)
        self.status_label.config(text=f"ROM: {self.rom_label} (paused)")
        self.draw_screen()

    def toggle_execution(self) -> None:
        if not self.core.is_loaded:
            messagebox.showwarning("Warning", "No ROM loaded.")
            return
        if not self.is_running:
            self.is_running = True
            self.run_btn.config(text="Pause")
            self.status_label.config(text=f"ROM: {self.rom_label} (running)")
            self.emulator_loop()
        else:
            self.is_running = False
            self.run_btn.config(text="Play")
            self.status_label.config(text=f"ROM: {self.rom_label} (paused)")

    def emulator_loop(self) -> None:
        if not self.is_running:
            return
        t0 = time.perf_counter()
        steps = max(1, int(self.speed))
        for _ in range(steps):
            self.core.step_frame()
            self.sound.step(CYCLES_PER_FRAME)
        self.draw_screen()
        t1 = time.perf_counter()
        if self._last_frame_t:
            dt = t1 - self._last_frame_t
            self._frame_times.append(1.0 / dt if dt > 0 else 0.0)
            if len(self._frame_times) > 30:
                self._frame_times.pop(0)
            fps = sum(self._frame_times) / len(self._frame_times)
            self.fps_label.config(text=f"FPS {fps:.1f}  {self.speed:.1f}x")
        self._last_frame_t = t1
        delay = max(1, int(16 / self.speed))
        self.root.after(delay, self.emulator_loop)


def _load_startup_rom() -> tuple[bytes, str, str | None]:
    if len(sys.argv) >= 2:
        path = os.path.abspath(sys.argv[1])
        try:
            with open(path, "rb") as f:
                data = f.read()
            if data:
                _recent_push(path)
                return data, os.path.basename(path), path
        except OSError as exc:
            messagebox.showerror("ROM Error", f"Could not load ROM:\n{exc}\nUsing built-in demo.")
    return DEFAULT_ROM, "Demo (built-in)", None


if __name__ == "__main__":
    if sys.version_info < (3, 14):
        print(f"mewgba$: Python 3.14+ recommended (running {sys.version_info.major}.{sys.version_info.minor})")
    root = tk.Tk()
    rom, label, path = _load_startup_rom()
    emu = MewGBAEmulator(root, rom, label)
    emu.rom_path = path
    root.mainloop()
