# Virtual Instrument Emulator

A Virtual Instrument Emulator developed for [Current Monitor](https://github.com/shenmeshisanpao/Current-Monitor), used for testing when a physical ammeter is unavailable.

It simulates Modbus RTU instrument responses with configurable waveforms and supports two connection modes:

1. **Network TCP Mode:** Simulates a Serial Device Server (Cross-platform).
2. **Virtual Serial Mode:** Creates paired virtual serial ports (Linux only).

## Waveform Types

Two independent channels, each configurable via the GUI parameter panel:

| Type | Description | Key Parameters |
| :--- | :--- | :--- |
| **DC** | Constant value with optional noise | Value (mA), Noise |
| **Pulse** | Trapezoidal pulse (rise → flat → fall → low) | High/Low (mA), Period (ms), Duty, Rise/Fall ratio |
| **Triangular** | Sine wave with configurable amplitude, offset, frequency | Amplitude (mA), Offset (mA), Frequency (Hz), Phase (°), Noise |

Default settings:
- Ch1: DC 5.0 mA, noise 0.05
- Ch2: DC 5.0 mA, noise 0.05

## Compatibility

| Feature | Windows | Linux | macOS |
| :--- | :---: | :---: | :---: |
| **Network TCP Mode** | ✅ | ✅ | ✅ |
| **Virtual Serial Mode** | ❌ | ✅ | ✅ |

### Pre-built Binary (Linux x86-64)

A packaged executable is available in Releases. Minimum requirements:

| Dependency | Version | Released |
| :--- | :--- | :--- |
| glibc | >= 2.14 | 2011 |
| Linux kernel | >= 3.2 | 2012 |

Compatible with Ubuntu 12.04+, Debian 8+, CentOS 7+, and any modern Linux distribution.

## How to Use

### Run from source

```bash
python3 main.py
```

No external dependencies — uses only Python standard library (tkinter, multiprocessing, sockets, pty).

### Run the pre-built binary

```bash
chmod +x Instrument_Simulator
./Instrument_Simulator
```

This project is licensed under the GNU General Public License v3.0 — see the [LICENSE](https://www.gnu.org/licenses/gpl-3.0.txt) file for details.
