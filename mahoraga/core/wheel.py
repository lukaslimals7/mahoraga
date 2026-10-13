#!/usr/bin/env python3
"""Animated, dependency-free Mahoraga Dharma Wheel banner for terminal TUI apps.

Usage:
    from mahoraga_wheel_animated import animate_mahoraga_wheel
    animate_mahoraga_wheel()              # Ctrl+C to stop
    animate_mahoraga_wheel(seconds=3.0)   # play briefly, then return

No images, external packages, or terminal graphics libraries are used.
"""
from __future__ import annotations

import math
import os
import shutil
import sys
import time

RESET = "\033[0m"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
CLEAR_SCREEN = "\033[2J\033[H"
HOME = "\033[H"

# Monochrome manga halftone ramp, from empty space to dense ink.
RAMP = "  .,:;irsXA253hMHGS#9B&@"


def _line_points(x0: float, y0: float, x1: float, y1: float):
    steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) * 1.4))
    for i in range(steps + 1):
        t = i / steps
        yield round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t)


def _make_frame(frame: int, width: int, height: int, color: bool) -> str:
    # Use a logical 2:1 coordinate system because terminal cells are tall.
    w = max(28, min(width, 112))
    h = max(8, min(height, 34))
    canvas = [[0.0 for _ in range(w)] for _ in range(h)]
    zbuf = [[-999.0 for _ in range(w)] for _ in range(h)]
    cx, cy = (w - 1) / 2, (h - 1) / 2
    # Wheel is broad and slightly flattened, matching the manga reference.
    rx, ry = w * 0.285, h * 0.245
    spin = frame * 0.075

    def put(x: float, y: float, light: float, z: float = 0.0):
        ix, iy = int(round(x)), int(round(y))
        if 0 <= ix < w and 0 <= iy < h and z >= zbuf[iy][ix]:
            # A subtle animated grain gives the white stipple a printed texture.
            grain = ((ix * 17 + iy * 31 + frame * 13) % 19) / 110.0
            canvas[iy][ix] = max(0.0, min(1.0, light + grain))
            zbuf[iy][ix] = z

    def stroke(x0, y0, x1, y1, light=0.75, thickness=0):
        for x, y in _line_points(x0, y0, x1, y1):
            for dy in range(-thickness, thickness + 1):
                for dx in range(-thickness, thickness + 1):
                    put(x + dx, y + dy, light - (abs(dx) + abs(dy)) * 0.13, 2)

    # Eight spokes and eight outer orbs. A gentle rotation animates the wheel.
    nodes = []
    for i in range(8):
        a = spin + i * math.tau / 8 - math.pi / 2
        # Elliptical projection gives the ring a shallow 3D perspective.
        nx = cx + math.cos(a) * rx
        ny = cy + math.sin(a) * ry
        nodes.append((nx, ny, a))

    # Extruded underside: offset dark edges make the wheel read as a solid
    # metal object instead of flat line art. Draw it first so the top face
    # naturally occludes the depth layer.
    depth_x, depth_y = 2.0, 1.0
    for i, (nx, ny, a) in enumerate(nodes):
        next_nx, next_ny, _ = nodes[(i + 1) % 8]
        stroke(nx + depth_x, ny + depth_y,
               next_nx + depth_x, next_ny + depth_y, 0.20, 0)
        inner_x = cx + math.cos(a) * rx * 0.19
        inner_y = cy + math.sin(a) * ry * 0.19
        stroke(inner_x + depth_x, inner_y + depth_y,
               nx + depth_x, ny + depth_y, 0.25, 0)
        # Bright bevel on the upper edge and a darker lower side face.
        stroke(nx, ny, nx + depth_x, ny + depth_y, 0.48, 0)

    # Back ring and spokes first, so the hub and orbs sit above them.
    for i, (nx, ny, a) in enumerate(nodes):
        next_nx, next_ny, _ = nodes[(i + 1) % 8]
        stroke(nx, ny, next_nx, next_ny, 0.27, 0)
        # Twin spoke rails with a dark channel between them.
        inner_x = cx + math.cos(a) * rx * 0.19
        inner_y = cy + math.sin(a) * ry * 0.19
        dx, dy = nx - inner_x, ny - inner_y
        length = math.hypot(dx, dy) or 1
        ox, oy = -dy / length * 1.0, dx / length * 0.45
        stroke(inner_x + ox, inner_y + oy, nx + ox, ny + oy, 0.66, 0)
        stroke(inner_x - ox, inner_y - oy, nx - ox, ny - oy, 0.52, 0)
        # Hatch marks across the spoke, like engraved manga linework.
        for t in (0.34, 0.49, 0.64, 0.79, 0.9):
            px, py = inner_x + dx * t, inner_y + dy * t
            stroke(px - ox * 0.75, py - oy * 0.75,
                   px + ox * 0.75, py + oy * 0.75, 0.42, 0)

    # The hub has concentric dotted rings and an eight-point central seal.
    for radius, intensity in ((0.12, 0.72), (0.17, 0.45), (0.22, 0.30)):
        for j in range(220):
            a = j * math.tau / 220
            put(cx + math.cos(a) * rx * radius,
                cy + math.sin(a) * ry * radius,
                intensity + (0.13 if (j + frame) % 5 == 0 else 0), 4)
    for i in range(8):
        a = spin * -1.8 + i * math.tau / 8
        x0, y0 = cx + math.cos(a) * rx * 0.035, cy + math.sin(a) * ry * 0.035
        x1, y1 = cx + math.cos(a) * rx * 0.15, cy + math.sin(a) * ry * 0.15
        stroke(x0, y0, x1, y1, 0.88, 0)
    put(cx, cy, 1.0, 6)

    # Eight luminous spheres: shaded, stippled, and slightly bobbing.
    orb_rx = max(2.4, w * 0.064)
    orb_ry = max(1.5, h * 0.105)
    for i, (nx, ny, a) in enumerate(nodes):
        bob = math.sin(frame * 0.12 + i * 0.9) * 0.35
        ox, oy = nx, ny + bob
        for yy in range(-int(orb_ry) - 1, int(orb_ry) + 2):
            for xx in range(-int(orb_rx) - 1, int(orb_rx) + 2):
                # Correct for the terminal's cell aspect ratio.
                norm = (xx / orb_rx) ** 2 + (yy / orb_ry) ** 2
                if norm <= 1.08:
                    # Directional sphere lighting: light from upper-left,
                    # with a smooth terminator and a deeper lower-right shadow.
                    nxn, nyn = xx / orb_rx, yy / orb_ry
                    light_dir = max(0.0, 1.0 - (nxn + nyn * 0.72) * 0.42)
                    shade = 0.52 + 0.30 * (1.0 - norm) + 0.18 * light_dir
                    if xx > orb_rx * 0.12 and yy > orb_ry * 0.08:
                        shade -= 0.24
                    # Fine stipple and engraved hatch marks retain the manga look.
                    if (xx * 3 + yy * 5 + frame + i) % 7 == 0:
                        shade -= 0.15
                    if yy > orb_ry * 0.45 and (xx + yy + i) % 2 == 0:
                        shade -= 0.08
                    put(ox + xx, oy + yy, shade, 8)
        # Two tiny specular points and a rim highlight create a polished-metal orb.
        put(ox - orb_rx * 0.34, oy - orb_ry * 0.34,
            1.0 if frame % 8 < 5 else 0.82, 9)
        put(ox - orb_rx * 0.20, oy - orb_ry * 0.42, 0.98, 9)
        for j in range(18):
            a2 = math.pi * 1.05 + j * math.pi / 30
            put(ox + math.cos(a2) * orb_rx * 0.91,
                oy + math.sin(a2) * orb_ry * 0.91, 0.78, 9)

    # Sparse moving dust/impact flecks around the silhouette.
    for i in range(w * h // 18):
        x = (i * 37 + frame * (1 + i % 3) * 2) % w
        y = (i * 19 + frame // 2 + (i % 5) * 3) % h
        # Keep dust mostly outside the wheel.
        if ((x - cx) / (rx * 1.42)) ** 2 + ((y - cy) / (ry * 1.75)) ** 2 > 1:
            put(x, y, 0.18 + ((i + frame) % 4) * 0.08, -1)

    out = []
    for row in canvas:
        line = []
        for lum in row:
            idx = max(0, min(len(RAMP) - 1, int(lum * (len(RAMP) - 1))))
            ch = RAMP[idx]
            if color and lum > 0.12:
                # Cold white/steel monochrome, not rainbow ANSI.
                v = int(70 + lum * 185)
                line.append(f"\033[38;2;{v};{v};{min(255, v + 2)}m{ch}\033[0m")
            else:
                line.append(ch)
        out.append("".join(line))
    art = "\n".join(out)
    title = " MAHORAGA "
    if color:
        title = f"\033[1;37m{title}\033[0m"
    title = title[:w].center(w)
    return f"{title}\n{art}"


def animate_mahoraga_wheel(seconds: float | None = None, fps: float = 12,
                            width: int | None = None, height: int | None = None,
                            color: bool | None = None) -> None:
    """Animate until Ctrl+C, or return after `seconds` if provided."""
    if not sys.stdout.isatty():
        print(_make_frame(0, width or 84, height or 25, False))
        return
    term = shutil.get_terminal_size((100, 30))
    width = width or min(term.columns, 100)
    height = height or min(term.lines - 1, 28)
    if color is None:
        color = not bool(os.getenv("NO_COLOR"))
    interval = 1.0 / max(1.0, fps)
    start = time.monotonic()
    frame = 0
    sys.stdout.write(HIDE_CURSOR + CLEAR_SCREEN)
    try:
        while seconds is None or time.monotonic() - start < seconds:
            sys.stdout.write(HOME + _make_frame(frame, width, height, color) + RESET)
            sys.stdout.flush()
            frame += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(RESET + SHOW_CURSOR + "\n")
        sys.stdout.flush()


def render_mahoraga_wheel(width: int = 84, height: int = 25, frame: int = 0,
                          color: bool = True) -> str:
    """Return one frame as a string, useful for custom TUI rendering loops."""
    return _make_frame(frame, width, height, color)


if __name__ == "__main__":
    animate_mahoraga_wheel()
