# Virtual Instrument Emulator

This is a Virtual Instrument Emulator developed for [Current Monitor](https://github.com/shenmeshisanpao/Current-Monitor), used for testing when a physical ammeter is unavailable.

It generates simulated Modbus RTU data (Sine/Cosine waves) and supports two connection modes:

1.  **Network TCP Mode:** Simulates a Serial Device Server (Cross-platform).
2.  **Virtual Serial Mode:** Creates paired virtual serial ports (Linux only).

## Compatibility

| Feature | Windows | Linux | macOS |
| :--- | :---: | :---: | :---: |
| **Network TCP Mode** | ✅ | ✅ | ✅ |
| **Virtual Serial Mode** | ❌ | ✅ | ✅ |

## How to Use

1. Run the simulator:
   ```bash
   python3 main.py


This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](https://www.gnu.org/licenses/gpl-3.0.txt) file for details.
