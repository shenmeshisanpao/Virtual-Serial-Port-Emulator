# Virtual Instrument Emulator

This is a Virtual Instrument Emulator developed for [Current Monitor](https://github.com/shenmeshisanpao/Current-Monitor), used for testing when a physical ammeter is unavailable.

It generates simulated Modbus RTU data (Sine/Cosine waves) and supports two connection modes:

1.  **Network TCP Mode:** Simulates a Serial Device Server (Cross-platform).
2.  **Virtual Serial Mode:** Creates paired virtual serial ports (Linux only).

## Wave
| Feature               | Ch1 (Sine Wave)                          | Ch2 (Cosine Wave + Noise)                |
|----------------------|------------------------------------------|------------------------------------------|
| Waveform Function | `sin(0.5 × t)`                           | `cos(0.8 × t)`                           |
| DC Offset         | 5.0                                      | 2.0                                      |
| Amplitude         | 3.0                                      | 1.0                                      |
| Theoretical Range | [2.0, 8.0]                               | [0.9, 3.1] (slightly exceeds [1.0, 3.0] due to noise) |
| Angular Frequency | 0.5 rad/s                                | 0.8 rad/s                                |
| Period            | ～12.57 seconds                           | ～7.85 seconds                            |
| Noise             | None                                     | Yes (uniform random noise ±0.1)          |
| Initial Phase     | Starts at 0 (`sin(0) = 0`)               | Starts at peak (`cos(0) = 1`)            |



## Compatibility

| Feature | Windows | Linux | macOS |
| :--- | :---: | :---: | :---: |
| **Network TCP Mode** | ✅ | ✅ | ✅ |
| **Virtual Serial Mode** | ❌ | ✅ | ✅ |

## How to Use

1. Download 'main.py'

2. Run the simulator:
   ```bash
   python3 main.py


This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](https://www.gnu.org/licenses/gpl-3.0.txt) file for details.
