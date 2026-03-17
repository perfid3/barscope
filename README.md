# Barscope

A terminal-based spectrum bar visualizer that captures system audio output (like cava) and renders real-time frequency bars.

## Dependencies

### System (must be installed)

- **PipeWire** with `pw-record` (default on Fedora 34+, Ubuntu 22.10+, Debian 13+, Arch)
- **WirePlumber** with `wpctl` (usually installed with PipeWire)
- **Python 3**

### Python

- **numpy**

## Install

```bash
./build.sh
```

This creates a standalone binary at `~/.local/bin/barscope`, then cleans up all build artifacts.

## Run

```bash
barscope
```

Make sure `~/.local/bin` is in your `$PATH`.

## Controls

| Key     | Action                        |
|---------|-------------------------------|
| `q`     | Quit                          |
| `+`/`-` | Increase/decrease sensitivity |
| `l`/`L` | Raise/lower low freq cutoff   |
| `h`/`H` | Raise/lower high freq cutoff  |
| `r`/`R` | Increase/decrease recovery    |
| `Space` | Pause/resume                  |

## Defaults

- Low cutoff: 50 Hz
- High cutoff: 10000 Hz
- Recovery: 0.80
- Sensitivity: 1.0

## Development

To work on the source, create a venv:

```bash
python3 -m venv venv
source venv/bin/activate
pip install numpy
python main.py
```

Then rebuild with `./build.sh` when done.

## License

See [LICENSE](LICENSE).
