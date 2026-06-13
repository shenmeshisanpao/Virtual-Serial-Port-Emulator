# CODEBUDDY.md

This file provides guidance to CodeBuddy Code when working with code in this repository.

## Project Overview

Virtual Instrument Emulator — a single-file Python/Tkinter application that simulates Modbus RTU instrument responses (sine/cosine waveforms). Used for testing the [Current Monitor](https://github.com/shenmeshisanpao/Current-Monitor) application when a physical ammeter is unavailable. Licensed under GPL v3.0.

## Commands

```bash
# Run the simulator (requires Python 3 with tkinter)
python3 main.py
```

**Packaging (Docker-based PyInstaller):**
```bash
# One-time setup: build the packer container
sudo docker run -it --name sim_packer -v "$(pwd)":/io continuumio/miniconda3 /bin/bash
# Inside container: conda create -n build python=3.10 pyinstaller -y && conda activate build
# Install tkinter if missing: conda install -c anaconda tk -y

# Subsequent builds (container stopped/restarted):
sudo docker start sim_packer
sudo docker exec -it sim_packer /bin/bash
export PATH="/opt/miniconda/bin:$PATH"
cd /io
pyinstaller --clean --noconfirm --windowed --onefile --name "Instrument_Simulator" main.py
```

There is no test suite, linter config, or CI pipeline in this repository.

## Architecture

All code lives in a single file: **`main.py`** (~410 lines). The application has zero external dependencies — everything uses Python's standard library.

### Module Layout (in file order)

1. **CRC and data conversion** (lines 37–56): `calculate_crc()` implements Modbus CRC-16 (polynomial 0xA001), `float_to_registers()` converts a float to two 16-bit Modbus holding registers using `ctypes`.

2. **Waveform generation** (lines 57–63): `generate_wave_value(channel_name, elapsed_time)` — Channel 1 produces `5.0 + 3.0*sin(0.5*t)`, Channel 2 produces `2.0 + 1.0*cos(0.8*t)` with ±0.1 uniform random noise. A commented-out alternative waveform implements a stepped test pattern (10s → spike → 20s → spike → 5s zero, looping every 55s).

3. **Simulator process — Serial mode** (lines 102–171): `simulator_process_serial(output_queue, channel_name)` runs as a `multiprocessing.Process` on Linux only. Uses `pty.openpty()` to create a PTY master/slave pair, sets both to raw mode with `tty.setraw()`, sends the slave device path (e.g., `/dev/pts/4`) back to the GUI via `output_queue`, then enters a `select.select()` loop reading Modbus RTU requests from the PTY master and responding with the current waveform value wrapped in a proper Modbus RTU frame.

4. **Simulator process — TCP mode** (lines 175–243): `simulator_process_tcp(port, channel_name)` runs as a `multiprocessing.Process`, cross-platform. Binds a TCP server socket to `0.0.0.0:<port>` (ports 1030/1031 hardcoded), accepts one client connection, reads raw bytes (same Modbus RTU frame format), and responds with simulated data. Reconnects on disconnect.

5. **GUI** (lines 247–398): `SimulatorApp` class using `tkinter`:
   - `setup_start_screen()`: Mode selection (Virtual Serial / Network TCP buttons)
   - `show_running_screen()`: Displays connection info (PTY path or `host:port`) for both channels
   - `start_serial_mode()`: Spawns two `simulator_process_serial` processes, retrieves PTY paths via `multiprocessing.Queue`
   - `start_network_mode()`: Spawns two `simulator_process_tcp` processes on ports 1030/1031
   - `cleanup_and_exit()`: Terminates all child processes, destroys root window

6. **Entry point** (lines 400–409): `main()` calls `multiprocessing.freeze_support()` (required for PyInstaller bundling), creates the Tk root, registers window close handler, starts main loop.

### Key Design Decisions

- **No external serial libraries**: Avoids `pyserial` — uses raw PTY (`os.openpty`, `os.read`, `os.write`) and raw TCP sockets. This eliminates native library dependencies and simplifies PyInstaller packaging.
- **Multiprocessing, not threading**: Each simulator channel runs as a separate process (`multiprocessing.Process`) to avoid GIL contention and ensure clean termination.
- **Modbus RTU framing**: Request frames are parsed byte-by-byte (slave ID=1, function code=3 = read holding registers). Responses are 9-byte frames: `[slave_id, func_code, byte_count, reg_high, reg_low, crc_low, crc_high]`.
- **Platform compatibility**: Serial mode uses Linux-only `pty`/`tty`/`select` modules; TCP mode uses cross-platform `socket` module. The GUI (tkinter) is cross-platform.

## File Structure

```
.
├── main.py                  # Entire application source (410 lines)
├── README.md                # Usage guide, waveform specs, compatibility matrix
├── Docker打包步骤.md         # Docker packaging instructions (Chinese)
├── miniconda.sh             # Miniconda installer (~89MB, for Docker-based packaging)
├── LICENSE                  # GNU GPL v3.0
├── build/                   # PyInstaller build artifacts
│   └── Instrument_Simulator/
└── dist/                    # PyInstaller output directory
```
