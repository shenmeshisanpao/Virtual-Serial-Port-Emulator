#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Virtual Serial Port Emulator and TCP Simulator for Instruments
# Author: ZhiCheng Zhang <zhangzhicheng@cnncmail.cn>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import subprocess
import re
import time
import multiprocessing
import struct
import math
import random
import ctypes
import tkinter as tk
from tkinter import messagebox
import socket
import os
import pty
import tty
import select
from dataclasses import dataclass
from queue import Empty


## CRC 与 数据转换

def calculate_crc(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return crc

def float_to_registers(value):
    cp = ctypes.pointer(ctypes.c_float(value))
    ip = ctypes.cast(cp, ctypes.POINTER(ctypes.c_int))
    int_val = ip.contents.value
    high = (int_val >> 16) & 0xFFFF
    low = int_val & 0xFFFF
    return high, low


## 通道配置

@dataclass
class ChannelConfig:
    signal_type: str = "dc"
    # 直流
    dc_value: float = 5.0
    dc_noise: float = 0.05
    # 脉冲（梯形）
    pulse_high: float = 5.0
    pulse_low: float = 0.0
    pulse_period: float = 1000.0  # ms
    pulse_duty: float = 0.3     # 0~1
    pulse_rise: float = 0.4     # 0~1
    pulse_fall: float = 0.4     # 0~1
    # 三角
    trig_amplitude: float = 3.0
    trig_offset: float = 5.0
    trig_frequency: float = 0.5
    trig_phase: float = 0.0      # 度
    trig_noise: float = 0.0


## 波形生成

def generate_wave_value(config, elapsed_time):
    """基于 ChannelConfig 生成模拟波形数据"""
    if config.signal_type == "dc":
        noise = random.uniform(-config.dc_noise, config.dc_noise) if config.dc_noise > 0 else 0.0
        return config.dc_value + noise

    elif config.signal_type == "pulse":
        period = max(config.pulse_period / 1000.0, 0.001)
        t = elapsed_time % period
        active_dur = period * config.pulse_duty
        rise_dur = active_dur * config.pulse_rise
        fall_dur = active_dur * config.pulse_fall
        flat_dur = active_dur - rise_dur - fall_dur

        if flat_dur < 0:
            total_ratio = config.pulse_rise + config.pulse_fall
            if total_ratio > 0:
                rise_dur = active_dur * (config.pulse_rise / total_ratio)
                fall_dur = active_dur - rise_dur
                flat_dur = 0
            else:
                rise_dur = 0
                fall_dur = 0
                flat_dur = active_dur

        high = config.pulse_high
        low = config.pulse_low
        amp = high - low

        if t < rise_dur:
            return low + amp * (t / rise_dur) if rise_dur > 0 else high
        elif t < rise_dur + flat_dur:
            return high
        elif t < active_dur:
            elapsed_fall = t - (rise_dur + flat_dur)
            return high - amp * (elapsed_fall / fall_dur) if fall_dur > 0 else low
        else:
            return low

    elif config.signal_type == "trig":
        noise = random.uniform(-config.trig_noise, config.trig_noise) if config.trig_noise > 0 else 0.0
        phase_rad = math.radians(config.trig_phase)
        return config.trig_amplitude * math.sin(
            2 * math.pi * config.trig_frequency * elapsed_time + phase_rad
        ) + config.trig_offset + noise

    return 0.0


## 模拟器进程：串口模式

def simulator_process_serial(master_fd, slave_name, config, value_queue):
    """
    串口模拟器工作进程。
    接收 GUI 传入的 master_fd（已是 os.dup 副本）和 ChannelConfig。
    """
    try:
        tty.setraw(master_fd)
        print(f"   [Serial-Sim] started on {slave_name}")

        start_time = time.time()

        while True:
            r, w, e = select.select([master_fd], [], [], 0.01)

            if master_fd in r:
                try:
                    request = os.read(master_fd, 1024)
                except OSError:
                    break

                if len(request) >= 8 and request[0] == 1 and request[1] == 3:
                    elapsed = time.time() - start_time
                    val = generate_wave_value(config, elapsed)
                    reg_h, reg_l = float_to_registers(val)

                    header = struct.pack('>B B B', 1, 3, 4)
                    data = struct.pack('>H H', reg_h, reg_l)
                    frame = header + data
                    crc = calculate_crc(frame)
                    packet = frame + struct.pack('<H', crc)

                    os.write(master_fd, packet)

                    if value_queue is not None:
                        try:
                            value_queue.put_nowait(val)
                        except:
                            pass

    except Exception as e:
        print(f"   [Error] Simulator crashed: {e}")
    finally:
        try:
            os.close(master_fd)
        except:
            pass


## 模拟器进程：网络 TCP 模式

def simulator_process_tcp(server_socket, port, config, value_queue):
    """TCP 网络模拟器工作进程。接收 GUI 传入的已绑定 server_socket"""
    print(f"   [TCP-Sim] listening on 0.0.0.0:{port}...")

    start_time = time.time()

    try:
        while True:
            try:
                conn, addr = server_socket.accept()
                print(f"   [TCP-Sim] connected by {addr}")
                conn.settimeout(None)

                while True:
                    try:
                        request = conn.recv(1024)
                        if not request:
                            break

                        if len(request) >= 8 and request[0] == 1 and request[1] == 3:
                            elapsed = time.time() - start_time
                            val = generate_wave_value(config, elapsed)
                            reg_h, reg_l = float_to_registers(val)

                            header = struct.pack('>B B B', 1, 3, 4)
                            data = struct.pack('>H H', reg_h, reg_l)
                            frame = header + data
                            crc = calculate_crc(frame)
                            packet = frame + struct.pack('<H', crc)

                            conn.sendall(packet)

                            if value_queue is not None:
                                try:
                                    value_queue.put_nowait(val)
                                except:
                                    pass

                        time.sleep(0.001)

                    except ConnectionResetError:
                        break
                    except Exception as e:
                        print(f"   [Error] TCP Loop: {e}")
                        break

                conn.close()
                print(f"   [TCP-Sim] disconnected. Waiting...")

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"   [Error] Server: {e}")
                time.sleep(1)
    finally:
        try:
            server_socket.close()
        except:
            pass


## GUI

SIGNAL_TYPES = ["dc", "pulse", "trig"]
SIGNAL_LABELS = {"dc": "DC", "pulse": "Pulse", "trig": "Trig"}

# 参数定义：每个信号类型有哪些字段
PARAM_DEFS = {
    "dc": [
        ("dc_value", "DC Value (mA)", float, 0, None),
        ("dc_noise", "Noise (mA)", float, 0, None),
    ],
    "pulse": [
        ("pulse_high", "High (mA)", float, 0, None),
        ("pulse_low",  "Low (mA)", float, 0, None),
        ("pulse_period", "Period (ms)", int, 1, None),
        ("pulse_duty",  "Duty (%)", int, 1, 99),
        ("pulse_rise",  "Rise (%)", int, 1, 49),
        ("pulse_fall",  "Fall (%)", int, 1, 49),
    ],
    "trig": [
        ("trig_amplitude", "Amplitude (mA)", float, 0, None),
        ("trig_offset",    "Offset (mA)", float, None, None),
        ("trig_frequency", "Frequency (Hz)", float, 0.001, None),
        ("trig_phase",     "Phase (°)", float, 0, 360),
        ("trig_noise",     "Noise (mA)", float, 0, None),
    ],
}

# 百分比字段（用户输入 0~100，内部存 0~1）
PCT_FIELDS = {"pulse_duty", "pulse_rise", "pulse_fall"}

# 各类型默认值（用于预填充 Entry）
DEFAULT_VALUES = {
    "dc": {"dc_value": "5.0", "dc_noise": "0.05"},
    "pulse": {
        "pulse_high": "5.0", "pulse_low": "0.0",
        "pulse_period": "1000", "pulse_duty": "30",
        "pulse_rise": "40", "pulse_fall": "40",
    },
    "trig": {
        "trig_amplitude": "3.0", "trig_offset": "5.0",
        "trig_frequency": "0.5", "trig_phase": "0.0",
        "trig_noise": "0.0",
    },
}


class SimulatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Instrument Simulator Setup")
        self.root.geometry("1000x400")
        self.processes = []
        self.running = False
        self.mode = None
        self.ch1_config = ChannelConfig()
        self.ch2_config = ChannelConfig()
        self.pty_resources = []
        self.server_sockets = []
        self.value_queues = []

        # GUI 控件引用
        self.ch1_type_var = None
        self.ch2_type_var = None
        self.ch1_value_label = None
        self.ch2_value_label = None
        self.ch1_status_canvas = None
        self.ch2_status_canvas = None
        self.btn_toggle = None
        self.btn_back = None
        self.info_label = None

        # 参数 Entry 字典 {param_key: entry_widget}
        self.ch1_entries = {}
        self.ch2_entries = {}

        # 参数 Frame
        self.ch1_dc_frame = None
        self.ch1_pulse_frame = None
        self.ch1_trig_frame = None
        self.ch2_dc_frame = None
        self.ch2_pulse_frame = None
        self.ch2_trig_frame = None

        # FocusOut 校验：存储每个 Entry 的最后有效值 {id(entry): valid_string}
        self._last_valid = {}

        # 实时轮询定时器 ID
        self._poll_after_id = None

        self.setup_start_screen()

    # ── 启动界面 ────────────────────────────────────────────

    def setup_start_screen(self):
        for widget in self.root.winfo_children():
            widget.destroy()
        self._clear_widget_refs()
        self.running = False
        self.mode = None

        self.root.protocol("WM_DELETE_WINDOW", self.cleanup_and_exit)
        self.root.geometry("1000x400")
        self.root.title("Instrument Simulator Setup")

        tk.Label(self.root, text="Select Simulation Mode", font=("Arial", 22, "bold")).pack(pady=20)

        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=20)

        btn_serial = tk.Button(btn_frame, text="Virtual Serial Mode\n",
                               font=("Arial", 14), width=20, height=3, bg="#e1f5fe",
                               command=self.start_serial_mode)
        btn_serial.pack(side="left", padx=10)

        btn_network = tk.Button(btn_frame, text="Network TCP Mode\n",
                                font=("Arial", 14), width=20, height=3, bg="#e8f5e9",
                                command=self.start_network_mode)
        btn_network.pack(side="left", padx=10)

        tk.Label(self.root, text="Native PTY Mode.\nNetwork Mode uses TCP ports 1030/1031.",
                 fg="gray").pack(side="bottom", pady=20)

    # ── 模式入口 ────────────────────────────────────────────

    def start_serial_mode(self):
        try:
            self._release_resources()
            self._create_serial_resources()

            _, _, p1_name = self.pty_resources[0]
            _, _, p2_name = self.pty_resources[1]
            self.mode = "serial"
            self.show_running_screen("Virtual Serial Mode", p1_name, p2_name,
                                     "Channel 1 Port:", "Channel 2 Port:")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start serial mode: {e}")

    def start_network_mode(self):
        try:
            self._release_resources()
            self._create_tcp_resources()

            local_ip = "127.0.0.1"
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.5)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except:
                pass

            self.mode = "network"
            p1_text = f"{local_ip}:1030"
            p2_text = f"{local_ip}:1031"
            self.show_running_screen("Network TCP Mode", p1_text, p2_text,
                                     "Channel 1 Addr:", "Channel 2 Addr:")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start network mode: {e}")

    # ── 运行/控制界面 ───────────────────────────────────────

    def show_running_screen(self, mode_name, p1_info, p2_info, p1_label, p2_label):
        for widget in self.root.winfo_children():
            widget.destroy()

        self.root.protocol("WM_DELETE_WINDOW", self.cleanup_and_exit)
        self.root.geometry("1100x700")
        self.root.title(f"Simulator - {mode_name}")

        # 重置控件引用
        self.ch1_entries = {}
        self.ch2_entries = {}
        self._last_valid = {}

        # ── 顶部：模式标题 + 连接信息 ──
        tk.Label(self.root, text=f"{mode_name} Running", font=("Arial", 24, "bold"),
                 fg="forest green").pack(pady=10)
        tk.Label(self.root, text="Configure signal parameters below, then press Start.",
                 font=("Arial", 14)).pack(pady=5)

        info_frame = tk.Frame(self.root, relief="groove", borderwidth=2)
        info_frame.pack(pady=10, padx=20, fill="x")

        tk.Label(info_frame, text=p1_label, font=("Arial", 16, "bold")).grid(row=0, column=0, sticky="e", padx=10, pady=8)
        self.info_label = tk.Entry(info_frame, font=("Arial", 16), width=30, justify="center")
        self.info_label.insert(0, p1_info)
        self.info_label.grid(row=0, column=1, padx=10)

        tk.Label(info_frame, text=p2_label, font=("Arial", 16, "bold")).grid(row=1, column=0, sticky="e", padx=10, pady=8)
        info_label2 = tk.Entry(info_frame, font=("Arial", 16), width=30, justify="center")
        info_label2.insert(0, p2_info)
        info_label2.grid(row=1, column=1, padx=10)

        # ── 中部：左右双栏（Ch1 / Ch2）──
        main_frame = tk.Frame(self.root)
        main_frame.pack(pady=10, padx=20, fill="both", expand=True)

        self._build_channel_panel(main_frame, 1, "Channel 1", side="left")
        self._build_channel_panel(main_frame, 2, "Channel 2", side="right")

        # ── 底部按钮 ──
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=15)

        self.btn_toggle = tk.Button(btn_frame, text="▶ Start", font=("Arial", 14, "bold"),
                                    width=12, height=2, bg="#a5d6a7",
                                    command=self.toggle_signals)
        self.btn_toggle.pack(side="left", padx=10)

        self.btn_back = tk.Button(btn_frame, text="↩ Back", font=("Arial", 14),
                                  width=10, height=2, bg="#eeeeee",
                                  command=self.back_to_setup)
        self.btn_back.pack(side="left", padx=10)

        # 版权
        tk.Label(self.root, text="Copyright (C) 2025 ZhiCheng Zhang. All Rights Reserved.\n"
                 "CIAE Nuclear Astrophysics Group, Beijing.",
                 font=("Arial", 10), fg="gray").pack(side="bottom", pady=5)

        # 启动实时值轮询
        self._start_polling()

    def _build_channel_panel(self, parent, ch_num, title, side):
        """构建单个通道的控制面板"""
        frame = tk.LabelFrame(parent, text=title, font=("Arial", 16, "bold"),
                              padx=10, pady=10)
        frame.pack(side=side, fill="both", expand=True, padx=10)

        # 信号类型选择
        type_label = tk.Label(frame, text="Signal Type:", font=("Arial", 15))
        type_label.grid(row=0, column=0, sticky="w", padx=5, pady=5)

        type_var = tk.StringVar(value="dc")
        type_menu = tk.OptionMenu(frame, type_var, "dc", "pulse", "trig",
                                  command=lambda t: self._on_type_change(ch_num, t))
        type_menu.config(font=("Arial", 13), width=20)
        type_menu.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        if ch_num == 1:
            self.ch1_type_var = type_var
        else:
            self.ch2_type_var = type_var

        # 参数区域（三个 Frame，按需显示）
        param_container = tk.Frame(frame)
        param_container.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)
        frame.grid_columnconfigure(1, weight=1)

        entries, dc_f, pulse_f, trig_f = self._create_all_param_frames(param_container, ch_num)

        if ch_num == 1:
            self.ch1_entries = entries
            self.ch1_dc_frame = dc_f
            self.ch1_pulse_frame = pulse_f
            self.ch1_trig_frame = trig_f
        else:
            self.ch2_entries = entries
            self.ch2_dc_frame = dc_f
            self.ch2_pulse_frame = pulse_f
            self.ch2_trig_frame = trig_f

        # 初始显示 dc frame
        dc_f.pack(fill="x", pady=5)

        # 显示转换标签
        for opt in type_menu["menu"].winfo_children():
            label = opt.cget("text")
            # Tk OptionMenu 用 value 做 label，我们替换成中文
            if label in SIGNAL_LABELS:
                pass  # already set
        type_menu["menu"].entryconfig(0, label=SIGNAL_LABELS["dc"])
        type_menu["menu"].entryconfig(1, label=SIGNAL_LABELS["pulse"])
        type_menu["menu"].entryconfig(2, label=SIGNAL_LABELS["trig"])

        # 状态灯 + 实时值
        status_row = tk.Frame(frame)
        status_row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=10)

        canvas = tk.Canvas(status_row, width=16, height=16, highlightthickness=0)
        canvas.pack(side="left", padx=(0, 5))
        self._draw_status_light(canvas, active=False)

        value_label = tk.Label(status_row, text="-- mA", font=("Arial", 14))
        value_label.pack(side="left")

        if ch_num == 1:
            self.ch1_status_canvas = canvas
            self.ch1_value_label = value_label
        else:
            self.ch2_status_canvas = canvas
            self.ch2_value_label = value_label

    def _create_all_param_frames(self, parent, ch_num):
        """预创建三个信号类型的参数 Frame，返回 (entries_dict, dc_frame, pulse_frame, trig_frame)"""
        entries = {}

        dc_frame = tk.Frame(parent)
        pulse_frame = tk.Frame(parent)
        trig_frame = tk.Frame(parent)

        # 公共入口创建函数
        def make_entry_row(num, frame, key, label_text, default_val):
            tk.Label(frame, text=label_text, font=("Arial", 13)).grid(
                row=num, column=0, sticky="e", padx=5, pady=3)
            entry = tk.Entry(frame, font=("Arial", 13), width=12, justify="center")
            entry.insert(0, default_val)
            entry.grid(row=num, column=1, sticky="w", padx=5, pady=3)
            entry.bind("<FocusOut>", lambda e, k=key, w=entry: self._validate_field(ch_num, k, w))
            entries[key] = entry

        # DC
        make_entry_row(0, dc_frame, "dc_value", "DC Value (mA):", DEFAULT_VALUES["dc"]["dc_value"])
        make_entry_row(1, dc_frame, "dc_noise", "Noise (mA):", DEFAULT_VALUES["dc"]["dc_noise"])

        # Pulse
        for i, (key, label, _, _, _) in enumerate(PARAM_DEFS["pulse"]):
            make_entry_row(i, pulse_frame, key, label + ":", DEFAULT_VALUES["pulse"][key])

        # Trig
        for i, (key, label, _, _, _) in enumerate(PARAM_DEFS["trig"]):
            make_entry_row(i, trig_frame, key, label + ":", DEFAULT_VALUES["trig"][key])

        return entries, dc_frame, pulse_frame, trig_frame

    def _on_type_change(self, ch_num, new_type):
        """下拉框切换信号类型"""
        config = self.ch1_config if ch_num == 1 else self.ch2_config
        config.signal_type = new_type

        if ch_num == 1:
            frames = (self.ch1_dc_frame, self.ch1_pulse_frame, self.ch1_trig_frame)
        else:
            frames = (self.ch2_dc_frame, self.ch2_pulse_frame, self.ch2_trig_frame)

        for f in frames:
            f.pack_forget()

        if new_type == "dc":
            frames[0].pack(fill="x", pady=5)
        elif new_type == "pulse":
            frames[1].pack(fill="x", pady=5)
        elif new_type == "trig":
            frames[2].pack(fill="x", pady=5)

    # ── 参数验证 ────────────────────────────────────────────

    def _validate_field(self, ch_num, key, entry_widget):
        """FocusOut 回调：校验单个字段，非法则标红回退"""
        val_str = entry_widget.get().strip()
        if not val_str:
            self._revert_entry(entry_widget, ch_num, key)
            return

        param_type = None
        min_v = None
        max_v = None
        stype = (self.ch1_config if ch_num == 1 else self.ch2_config).signal_type
        for name, _, ptype, pmin, pmax in PARAM_DEFS[stype]:
            if name == key:
                param_type = ptype
                min_v = pmin
                max_v = pmax
                break

        if param_type is None:
            return

        try:
            val = param_type(val_str)
        except ValueError:
            self._revert_entry(entry_widget, ch_num, key)
            return

        if min_v is not None and val < min_v:
            self._revert_entry(entry_widget, ch_num, key)
            return
        if max_v is not None and val > max_v:
            self._revert_entry(entry_widget, ch_num, key)
            return

        # 脉冲比例校验
        if ch_num == 1:
            entries = self.ch1_entries
        else:
            entries = self.ch2_entries

        if stype == "pulse" and key in ("pulse_rise", "pulse_fall"):
            rise_str = entries.get("pulse_rise")
            fall_str = entries.get("pulse_fall")
            if rise_str and fall_str:
                try:
                    r = int(rise_str.get())
                    f = int(fall_str.get())
                    if r + f >= 100:
                        self._revert_entry(entry_widget, ch_num, key)
                        return
                except ValueError:
                    pass

        # 合法：保存并恢复颜色
        entry_widget.config(fg="black")
        self._last_valid[id(entry_widget)] = val_str

    def _revert_entry(self, entry, ch_num, key):
        """标红并回退到上次有效值"""
        entry.config(fg="red")
        last = self._last_valid.get(id(entry), "")
        if last:
            entry.delete(0, tk.END)
            entry.insert(0, last)

    def _read_params_from_gui(self, ch_num):
        """从 GUI Entry 读取参数，校验后构造 ChannelConfig，失败返回 None"""
        config = ChannelConfig()
        stype = (self.ch1_config if ch_num == 1 else self.ch2_config).signal_type
        config.signal_type = stype
        entries = self.ch1_entries if ch_num == 1 else self.ch2_entries

        errors = []

        for key, label, ptype, pmin, pmax in PARAM_DEFS[stype]:
            entry = entries.get(key)
            if not entry:
                continue
            val_str = entry.get().strip()
            if not val_str:
                errors.append(f"{label}: cannot be empty")
                continue
            try:
                val = ptype(val_str)
            except ValueError:
                errors.append(f"{label}: invalid number")
                continue

            if pmin is not None and val < pmin:
                errors.append(f"{label}: cannot be less than {pmin}")
                continue
            if pmax is not None and val > pmax:
                errors.append(f"{label}: cannot be greater than {pmax}")
                continue

            # 百分比转换
            if key in PCT_FIELDS:
                val = val / 100.0

            setattr(config, key, val)

        # 脉冲额外校验
        if stype == "pulse":
            rise = getattr(config, "pulse_rise", 0)
            fall = getattr(config, "pulse_fall", 0)
            if rise + fall >= 1.0:
                errors.append("Rise + Fall must be less than 100%")

            high = getattr(config, "pulse_high", 0)
            low = getattr(config, "pulse_low", 0)
            if high <= low:
                errors.append("High level must be greater than Low level")

        if errors:
            messagebox.showerror("Parameter Error",
                                 f"Channel {ch_num}:\n" + "\n".join(errors))
            return None

        return config

    # ── 启动/停止 ────────────────────────────────────────────

    def toggle_signals(self):
        if self.running:
            self._stop_signals()
        else:
            self._start_signals()

    def _start_signals(self):
        """验证参数 → 启动两个子进程"""
        config1 = self._read_params_from_gui(1)
        config2 = self._read_params_from_gui(2)
        if config1 is None or config2 is None:
            return

        self.ch1_config = config1
        self.ch2_config = config2

        try:
            if self.mode == "serial":
                self._launch_serial_processes()
            else:
                self._launch_tcp_processes()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start: {e}")
            self._stop_signals(quiet=True)
            return

        self.running = True
        self._update_ui_state()

    def _stop_signals(self, quiet=False):
        """终止子进程"""
        for p in self.processes:
            if p.is_alive():
                p.terminate()
        self.processes.clear()
        self.value_queues.clear()

        self.running = False

        # 冻结显示（安全：widget 可能已被销毁）
        self._safe_label_config(self.ch1_value_label, "-- mA")
        self._safe_label_config(self.ch2_value_label, "-- mA")

        if not quiet:
            self._update_ui_state()

    def _launch_serial_processes(self):
        """串口模式：传入 dup(master_fd) 给子进程"""
        master1, _, slave1_name = self.pty_resources[0]
        master2, _, slave2_name = self.pty_resources[1]

        q1 = multiprocessing.Queue()
        q2 = multiprocessing.Queue()
        self.value_queues = [q1, q2]

        dup1 = os.dup(master1)
        dup2 = os.dup(master2)

        p1 = multiprocessing.Process(target=simulator_process_serial,
                                     args=(dup1, slave1_name, self.ch1_config, q1))
        p2 = multiprocessing.Process(target=simulator_process_serial,
                                     args=(dup2, slave2_name, self.ch2_config, q2))
        p1.daemon = True
        p2.daemon = True
        p1.start()
        p2.start()
        self.processes = [p1, p2]

        print(f"   [Serial-Sim] Processes started on {slave1_name}, {slave2_name}")

    def _launch_tcp_processes(self):
        """TCP 模式：传入已绑定的 server_socket"""
        sock1, port1 = self.server_sockets[0]
        sock2, port2 = self.server_sockets[1]

        q1 = multiprocessing.Queue()
        q2 = multiprocessing.Queue()
        self.value_queues = [q1, q2]

        p1 = multiprocessing.Process(target=simulator_process_tcp,
                                     args=(sock1, port1, self.ch1_config, q1))
        p2 = multiprocessing.Process(target=simulator_process_tcp,
                                     args=(sock2, port2, self.ch2_config, q2))
        p1.daemon = True
        p2.daemon = True
        p1.start()
        p2.start()
        self.processes = [p1, p2]

        print(f"   [TCP-Sim] Processes started on ports {port1}, {port2}")

    def _update_ui_state(self):
        """根据 running 状态更新按钮和参数状态"""
        if self.running:
            self.btn_toggle.config(text="■ Stop", bg="#ffcccc")
            self.btn_back.config(state="disabled")
            self._set_all_params_enabled(False)
            self._draw_status_light(self.ch1_status_canvas, active=True)
            self._draw_status_light(self.ch2_status_canvas, active=True)
        else:
            self.btn_toggle.config(text="▶ Start", bg="#a5d6a7")
            self.btn_back.config(state="normal")
            self._set_all_params_enabled(True)
            self._draw_status_light(self.ch1_status_canvas, active=False)
            self._draw_status_light(self.ch2_status_canvas, active=False)

    def _set_all_params_enabled(self, enabled):
        """锁定/解锁所有参数输入"""
        state = "normal" if enabled else "disabled"
        for entries in (self.ch1_entries, self.ch2_entries):
            for entry in entries.values():
                try:
                    entry.config(state=state)
                except tk.TclError:
                    pass
        # 类型下拉框
        for var in (self.ch1_type_var, self.ch2_type_var):
            if var:
                try:
                    # Tk OptionMenu 状态通过关联的 Menubutton 控制
                    pass  # OptionMenu 没有直接的 state，跳过
                except:
                    pass

    # ── 状态灯 ───────────────────────────────────────────────

    def _draw_status_light(self, canvas, active):
        canvas.delete("all")
        color = "#4caf50" if active else "#bdbdbd"
        canvas.create_oval(2, 2, 14, 14, fill=color, outline="")

    def _safe_label_config(self, label, text):
        """安全更新标签文本（处理 widget 已被销毁的情况）"""
        if label is not None:
            try:
                label.config(text=text)
            except tk.TclError:
                pass

    def _clear_widget_refs(self):
        """清空控件引用，防止指向已销毁的 widget"""
        self.ch1_value_label = None
        self.ch2_value_label = None
        self.ch1_status_canvas = None
        self.ch2_status_canvas = None
        self.btn_toggle = None
        self.btn_back = None
        self.info_label = None
        self.ch1_entries = {}
        self.ch2_entries = {}
        self.ch1_dc_frame = None
        self.ch1_pulse_frame = None
        self.ch1_trig_frame = None
        self.ch2_dc_frame = None
        self.ch2_pulse_frame = None
        self.ch2_trig_frame = None
        self.ch1_type_var = None
        self.ch2_type_var = None

    # ── 实时值轮询 ────────────────────────────────────────────

    def _start_polling(self):
        if self._poll_after_id:
            self.root.after_cancel(self._poll_after_id)
        self._poll_values()

    def _poll_values(self):
        if self.running and self.value_queues:
            for i, q in enumerate(self.value_queues):
                try:
                    val = q.get_nowait()
                    label = self.ch1_value_label if i == 0 else self.ch2_value_label
                    if label:
                        label.config(text=f"{val:.2f} mA")
                except Empty:
                    pass
        self._poll_after_id = self.root.after(200, self._poll_values)

    # ── 返回 ──────────────────────────────────────────────────

    def back_to_setup(self):
        if self.running:
            return
        self._release_resources()
        if self._poll_after_id:
            self.root.after_cancel(self._poll_after_id)
            self._poll_after_id = None
        self.setup_start_screen()

    # ── 资源管理 ──────────────────────────────────────────────

    def _create_serial_resources(self):
        """创建 2 个 PTY"""
        resources = []
        for _ in range(2):
            master_fd, slave_fd = pty.openpty()
            tty.setraw(master_fd)
            tty.setraw(slave_fd)
            slave_name = os.ttyname(slave_fd)
            resources.append((master_fd, slave_fd, slave_name))
            print(f"   PTY created: {slave_name}")
        self.pty_resources = resources

    def _create_tcp_resources(self):
        """绑定 2 个 TCP 端口"""
        resources = []
        for port in (1030, 1031):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('0.0.0.0', port))
            s.listen(1)
            resources.append((s, port))
            print(f"   TCP bound: 0.0.0.0:{port}")
        self.server_sockets = resources

    def _release_resources(self):
        """释放所有 PTY 和 Socket 资源"""
        for master_fd, slave_fd, _ in self.pty_resources:
            try:
                os.close(master_fd)
            except:
                pass
            try:
                os.close(slave_fd)
            except:
                pass
        self.pty_resources.clear()

        for s, _ in self.server_sockets:
            try:
                s.close()
            except:
                pass
        self.server_sockets.clear()

        print("   Resources released.")

    def cleanup_and_exit(self):
        print("\nCleaning up environment...")
        if self.running:
            self._stop_signals(quiet=True)
        self._release_resources()
        self.processes.clear()
        print("Cleanup complete.")
        self.root.destroy()


def main():
    multiprocessing.freeze_support()
    root = tk.Tk()
    app = SimulatorApp(root)
    root.protocol("WM_DELETE_WINDOW", app.cleanup_and_exit)
    root.mainloop()


if __name__ == "__main__":
    main()
