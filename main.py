# main.py
import numpy as np
import curses
import time
import subprocess
import sys
import os

CHUNK = 2048
RATE = 44100
HALF_CHUNK = CHUNK // 2
NORM_FACTOR = 1.0 / (128 * CHUNK)
FRAME_TIME = 0.016  # ~60fps
NUM_BARS = 50

# ANSI escape sequences
CSI = "\033["
HIDE_CURSOR = f"{CSI}?25l"
SHOW_CURSOR = f"{CSI}?25h"
RESET = f"{CSI}0m"
BOLD = f"{CSI}1m"


def move(y, x):
    return f"{CSI}{y + 1};{x + 1}H"


def fg256(color_id):
    return f"{CSI}38;5;{color_id}m"


def hue_to_256(h):
    """Convert hue (0-1) to a 256-color code using the 6x6x6 cube."""
    h = h % 1.0
    c = 1.0
    x = c * (1 - abs((h * 6) % 2 - 1))
    sector = int(h * 6)
    if sector == 0:   r, g, b = c, x, 0
    elif sector == 1: r, g, b = x, c, 0
    elif sector == 2: r, g, b = 0, c, x
    elif sector == 3: r, g, b = 0, x, c
    elif sector == 4: r, g, b = x, 0, c
    else:             r, g, b = c, 0, x
    ri = min(5, int(r * 6))
    gi = min(5, int(g * 6))
    bi = min(5, int(b * 6))
    return 16 + 36 * ri + 6 * gi + bi


def find_default_sink():
    """Find the default audio output sink serial for pw-record --target."""
    try:
        result = subprocess.run(
            ['wpctl', 'inspect', '@DEFAULT_AUDIO_SINK@'],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if 'object.serial' in line:
                    return line.split('=')[-1].strip().strip('"')
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ['pactl', 'get-default-sink'],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def build_freq_indices(num_bars, freq_lo, freq_hi):
    """Pre-compute FFT bin indices for each bar using log frequency mapping."""
    hz_per_bin = RATE / CHUNK
    t = np.linspace(0, 1, num_bars)
    log_lo = np.log10(max(freq_lo, 1))
    log_hi = np.log10(max(freq_hi, 2))
    freqs = 10 ** (log_lo + t * (log_hi - log_lo))
    return np.clip((freqs / hz_per_bin).astype(int), 1, HALF_CHUNK - 1)


def run(stdscr):
    # Use curses only for input handling
    curses.curs_set(0)
    stdscr.timeout(0)

    # Direct terminal output
    out = sys.stdout
    write = out.write
    flush = out.flush
    write(HIDE_CURSOR)
    flush()

    # Audio state
    smoothed = np.zeros(HALF_CHUNK)
    sensitivity = 1.0
    pause = False

    # Frequency cutoffs (Hz)
    freq_lo = 50
    freq_hi = 10000
    freq_step = 10

    # Recovery time
    recovery = 0.8

    bytes_needed = CHUNK * 2
    block_char = "█"

    # Find sink and start pw-record
    sink = find_default_sink()
    if not sink:
        write(SHOW_CURSOR + RESET)
        flush()
        print("No audio output sink found. Make sure PipeWire is running.")
        sys.exit(1)

    pw_process = subprocess.Popen(
        ['pw-record', '--raw', '--target', sink,
         '--media-category', 'Capture',
         '--format', 's16', '--rate', str(RATE),
         '--channels', '1', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )

    # Pre-computed tables
    prev_height = prev_width = 0
    freq_indices = build_freq_indices(NUM_BARS, freq_lo, freq_hi)
    color_codes = []  # ANSI color escape per bar
    bar_width = 1
    num_bars = NUM_BARS
    max_bar_height = 1
    block = block_char
    blank = " "
    prev_bar_heights = np.zeros(NUM_BARS, dtype=int)
    freq_dirty = True
    prev_status = ""

    try:
        while True:
            # Input via curses (non-blocking)
            try:
                key = stdscr.getkey()
                if key == 'q':
                    break
                elif key == ' ':
                    pause = not pause
                elif key in ('+', '='):
                    sensitivity += 0.1
                elif key == '-':
                    sensitivity = max(0.1, sensitivity - 0.1)
                elif key == 'l':
                    freq_lo = min(freq_hi - 100, freq_lo + freq_step)
                    freq_dirty = True
                elif key == 'L':
                    freq_lo = max(20, freq_lo - freq_step)
                    freq_dirty = True
                elif key == 'h':
                    freq_hi = min(RATE // 2, freq_hi + 500)
                    freq_dirty = True
                elif key == 'H':
                    freq_hi = max(freq_lo + 100, freq_hi - 500)
                    freq_dirty = True
                elif key == 'r':
                    recovery = min(0.99, recovery + 0.02)
                elif key == 'R':
                    recovery = max(0.0, recovery - 0.02)
            except Exception:
                pass

            if pause:
                time.sleep(FRAME_TIME)
                continue

            # Read audio
            raw = pw_process.stdout.read(bytes_needed)
            if not raw or len(raw) < bytes_needed:
                data = np.zeros(CHUNK, dtype=np.int16)
            else:
                data = np.frombuffer(raw, dtype=np.int16)

            # FFT + smoothing
            spectrum = np.abs(np.fft.rfft(data)) * NORM_FACTOR
            smoothed *= recovery
            smoothed += spectrum[:HALF_CHUNK] * (1 - recovery)

            # Check terminal size
            height, width = stdscr.getmaxyx()
            if height != prev_height or width != prev_width:
                prev_height, prev_width = height, width
                bar_width = max(1, width // NUM_BARS)
                num_bars = min(NUM_BARS, width // bar_width)
                max_bar_height = max(1, height - 4)
                block = block_char * bar_width
                blank = " " * bar_width
                color_codes = [
                    fg256(hue_to_256(i / num_bars)) + BOLD
                    for i in range(num_bars)
                ]
                prev_bar_heights[:] = 0
                freq_dirty = True
                # Clear bar area (spaces preserve transparency)
                row_blank = " " * width
                buf_clear = [RESET]
                for y in range(height):
                    buf_clear.append(move(y, 0))
                    buf_clear.append(row_blank)
                write("".join(buf_clear))

            if freq_dirty:
                freq_indices = build_freq_indices(num_bars, freq_lo, freq_hi)
                freq_dirty = False

            # Compute bar heights
            amplitudes = smoothed[freq_indices] * sensitivity
            bar_heights = np.minimum(
                (amplitudes * max_bar_height * 3).astype(int),
                max_bar_height
            )

            # Build single output buffer
            buf = []
            base_y = height - 3

            for i in range(num_bars):
                new_h = int(bar_heights[i])
                old_h = int(prev_bar_heights[i])
                if new_h == old_h:
                    continue
                x = i * bar_width
                if new_h > old_h:
                    buf.append(color_codes[i])
                    for j in range(old_h, new_h):
                        buf.append(move(base_y - j, x))
                        buf.append(block)
                else:
                    buf.append(RESET)
                    for j in range(new_h, old_h):
                        buf.append(move(base_y - j, x))
                        buf.append(blank)

            prev_bar_heights[:num_bars] = bar_heights

            # Status bar
            status = (
                f"Sens: {sensitivity:.1f} | "
                f"Lo: {freq_lo}Hz | Hi: {freq_hi}Hz | "
                f"Recovery: {recovery:.2f}"
            )
            if status != prev_status:
                buf.append(RESET)
                buf.append(move(0, 0))
                buf.append(status + " " * max(0, len(prev_status) - len(status)))
                buf.append(move(1, 0))
                buf.append("[Q]uit [+/-] Sens [l/L] LoCut [h/H] HiCut [r/R] Recovery [Space] Pause")
                prev_status = status

            # Single write syscall for the entire frame
            if buf:
                write("".join(buf))
                flush()

            time.sleep(FRAME_TIME)
    finally:
        pw_process.terminate()
        pw_process.wait()
        # Clear on exit with spaces (preserves transparency)
        buf_exit = [RESET]
        row_blank = " " * prev_width if prev_width else ""
        for y in range(prev_height):
            buf_exit.append(move(y, 0))
            buf_exit.append(row_blank)
        buf_exit.append(SHOW_CURSOR + move(0, 0))
        write("".join(buf_exit))
        flush()


if __name__ == "__main__":
    curses.wrapper(run)
