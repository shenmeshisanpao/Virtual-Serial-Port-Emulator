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
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
import socket
import webbrowser
import os
try:
    import pty
    import tty
    import select
except ImportError:  # Windows 标准库无 pty/tty
    pty = tty = select = None
from dataclasses import dataclass
from queue import Empty

# 虚拟串口模式仅支持 POSIX（macOS/Linux）；Windows 只能使用 TCP 模式
SERIAL_SUPPORTED = (pty is not None)


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


## 全局常量

VERSION = "2.1.0"
GITHUB_URL = "https://github.com/shenmeshisanpao/Virtual-Serial-Port-Emulator/releases"


def resource_path(rel: str) -> str:
    """定位资源文件，兼容 PyInstaller onefile 模式（sys._MEIPASS）"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


## 界面设计规范

# 字号分级（按钮与首页底部信息保持原大小）
FONT_SIZE_TITLE = 26
FONT_SIZE_SUBHEADING = 17
FONT_SIZE_BODY = 15
FONT_SIZE_BUTTON = 15  # 按钮字号（不随正文放大）
FONT_SIZE_FRAME = 14   # LabelFrame 分组框标题

# 亮色配色（与 Azure 亮色主题色板对应）
LIGHT_COLORS = {
    "accent": "#0078D4",
    "success": "#3CB371",
    "danger": "#E81123",
    "inactive": "#BDBDBD",
    "muted": "#6B6B6B",
}


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


## 跨进程共享配置

# 浮点字段顺序（与 ChannelConfig 字段对应）
CONFIG_FLOAT_FIELDS = [
    "dc_value", "dc_noise",
    "pulse_high", "pulse_low", "pulse_period", "pulse_duty", "pulse_rise", "pulse_fall",
    "trig_amplitude", "trig_offset", "trig_frequency", "trig_phase", "trig_noise",
]

# signal_type <-> int 映射
TYPE_TO_INT = {"dc": 0, "pulse": 1, "trig": 2}
INT_TO_TYPE = ["dc", "pulse", "trig"]


def make_shared_config():
    """创建一个通道的跨进程可写配置（浮点数组 + 类型索引）"""
    return (
        multiprocessing.Array('d', len(CONFIG_FLOAT_FIELDS)),
        multiprocessing.Value('i', 0),
    )


def apply_config_to_shared(config, shared_cfg):
    """把 ChannelConfig 写入共享内存"""
    float_arr, type_val = shared_cfg
    with float_arr.get_lock():
        for i, key in enumerate(CONFIG_FLOAT_FIELDS):
            float_arr[i] = float(getattr(config, key))
    with type_val.get_lock():
        type_val.value = TYPE_TO_INT.get(config.signal_type, 0)


def config_from_shared(shared_cfg):
    """从共享内存读取配置，构造 ChannelConfig 副本"""
    float_arr, type_val = shared_cfg
    cfg = ChannelConfig()
    cfg.signal_type = INT_TO_TYPE[type_val.value] if 0 <= type_val.value < len(INT_TO_TYPE) else "dc"
    with float_arr.get_lock():
        for i, key in enumerate(CONFIG_FLOAT_FIELDS):
            setattr(cfg, key, float_arr[i])
    return cfg


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

def simulator_process_serial(master_fd, slave_name, cfg_shared, signal_active, value_queue):
    assert pty is not None and tty is not None and select is not None, \
        "serial simulator requires POSIX pty support"
    """
    串口模拟器工作进程。
    接收 GUI 传入的 master_fd（已是 os.dup 副本）和共享配置。
    signal_active 未置位时被动应答 0 电流，置位后按共享配置应答波形值。
    """
    try:
        tty.setraw(master_fd)
        print(f"   [Serial-Sim] started on {slave_name} (idle, 0-current)")

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
                    if signal_active.is_set():
                        val = generate_wave_value(config_from_shared(cfg_shared), elapsed)
                    else:
                        val = 0.0
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

def simulator_process_tcp(server_socket, port, cfg_shared, signal_active, value_queue):
    """TCP 网络模拟器工作进程。接收 GUI 传入的已绑定 server_socket 和共享配置"""
    print(f"   [TCP-Sim] listening on 0.0.0.0:{port} (idle, 0-current)...")

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
                            if signal_active.is_set():
                                val = generate_wave_value(config_from_shared(cfg_shared), elapsed)
                            else:
                                val = 0.0
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
SIGNAL_LABELS = {
    "dc":    {"en": "DC",    "zh": "直流"},
    "pulse": {"en": "Pulse", "zh": "脉冲"},
    "trig":  {"en": "Trig",  "zh": "三角波"},
}

# 可选语言（切换按钮显示目标语言名）
LANGUAGES = {"en": "中文", "zh": "English"}

STRINGS = {
    "en": {
        # 错误弹窗
        "error_title": "Error",
        "param_error_title": "Parameter Error",
        "err_channel_prefix": "Channel {ch_num}:",
        "err_empty": "{label}: cannot be empty",
        "err_invalid": "{label}: invalid number",
        "err_less_than": "{label}: cannot be less than {pmin}",
        "err_greater_than": "{label}: cannot be greater than {pmax}",
        "err_rise_fall": "Rise + Fall must be less than 100%",
        "err_high_low": "High level must be greater than Low level",
        "err_start_failed": "Failed to start: {e}",
        "err_serial_mode": "Failed to start serial mode: {e}",
        "err_network_mode": "Failed to start network mode: {e}",
        "err_serial_unsupported": "Virtual Serial Mode is not supported on this platform.\nPlease use Network TCP Mode instead.",
        "serial_unsupported_hint": "Virtual Serial Mode requires Linux/macOS.",
        # 启动界面
        "window_setup_title": "Instrument Simulator Setup",
        "select_mode_title": "Select Simulation Mode",
        "btn_serial_mode": "Virtual Serial Mode",
        "btn_network_mode": "Network TCP Mode",
        # 运行界面
        "run_window_title": "Simulator - {mode_name}",
        "running_title": "{mode_name} Running",
        "mode_serial": "Virtual Serial Mode",
        "mode_network": "Network TCP Mode",
        "configure_hint": "Configure signal parameters below, then press Start.",
        "ch1_port": "Channel 1 Port:",
        "ch2_port": "Channel 2 Port:",
        "ch1_addr": "Channel 1 Addr:",
        "ch2_addr": "Channel 2 Addr:",
        "channel_n": "Channel {n}",
        "signal_type_label": "Signal Type:",
        "conn_info_title": "Connection Information",
        "btn_start": "▶ Start",
        "btn_stop": "■ Stop",
        "btn_back": "↩ Back",
        "btn_exit": "Exit",
    },
    "zh": {
        # 参数行标签（key = PARAM_DEFS 英文原文；英文侧靠回退机制直接显示原文）
        "DC Value (mA)": "直流值 (mA)",
        "Noise (mA)": "噪声 (mA)",
        "High (mA)": "高电平 (mA)",
        "Low (mA)": "低电平 (mA)",
        "Period (ms)": "周期 (ms)",
        "Duty (%)": "占空比 (%)",
        "Rise (%)": "上升 (%)",
        "Fall (%)": "下降 (%)",
        "Amplitude (mA)": "幅值 (mA)",
        "Offset (mA)": "偏置 (mA)",
        "Frequency (Hz)": "频率 (Hz)",
        "Phase (°)": "相位 (°)",
        # 错误弹窗
        "error_title": "错误",
        "param_error_title": "参数错误",
        "err_channel_prefix": "通道 {ch_num}:",
        "err_empty": "{label}：不能为空",
        "err_invalid": "{label}：数字格式无效",
        "err_less_than": "{label}：不能小于 {pmin}",
        "err_greater_than": "{label}：不能大于 {pmax}",
        "err_rise_fall": "上升 + 下降 之和必须小于 100%",
        "err_high_low": "高电平必须大于低电平",
        "err_start_failed": "启动失败：{e}",
        "err_serial_mode": "虚拟串口模式启动失败：{e}",
        "err_network_mode": "网络模式启动失败：{e}",
        "err_serial_unsupported": "当前平台不支持虚拟串口模式。\n请改用网络 TCP 模式。",
        "serial_unsupported_hint": "虚拟串口模式仅支持 Linux/macOS。",
        # 启动界面
        "window_setup_title": "仪器模拟器设置",
        "select_mode_title": "选择模拟模式",
        "btn_serial_mode": "虚拟串口模式",
        "btn_network_mode": "网络 TCP 模式",
        # 运行界面
        "run_window_title": "模拟器 - {mode_name}",
        "running_title": "{mode_name} 运行中",
        "mode_serial": "虚拟串口模式",
        "mode_network": "网络 TCP 模式",
        "configure_hint": "请在下方配置信号参数，然后点击「Start」。",
        "ch1_port": "通道 1 端口：",
        "ch2_port": "通道 2 端口：",
        "ch1_addr": "通道 1 地址：",
        "ch2_addr": "通道 2 地址：",
        "channel_n": "通道 {n}",
        "signal_type_label": "信号类型：",
        "conn_info_title": "连接信息",
        "btn_start": "▶ 启动",
        "btn_stop": "■ 停止",
        "btn_back": "↩ 返回",
        "btn_exit": "退出",
    },
}

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
        self.root.title(STRINGS["en"]["window_setup_title"])
        # 主题与设计规范
        self.style = None
        self.bg_color = "#ffffff"
        self.fg_color = "#1a1a1a"
        self.colors = LIGHT_COLORS
        self.font_family = tkfont.nametofont("TkDefaultFont").actual("family")
        self._load_theme()
        self._center_window(1000, 400)
        self.root.minsize(820, 420)
        self.processes = []
        self.running = False
        self.mode = None
        self.ch1_config = ChannelConfig()
        self.ch2_config = ChannelConfig()
        self.pty_resources = []
        self.server_sockets = []
        self.value_queues = []
        self.signal_active = multiprocessing.Event()
        self.ch1_shared_cfg = make_shared_config()
        self.ch2_shared_cfg = make_shared_config()

        # 语言：默认英文，会话内保留
        self.lang = "en"

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

    def t(self, key, **kw) -> str:
        """按当前语言取词：zh 缺词回退 en，再回退 key 本身；支持 {xxx} 格式化"""
        text = STRINGS.get(self.lang, {}).get(key)
        if not isinstance(text, str):
            text = STRINGS.get("en", {}).get(key)
        if not isinstance(text, str):
            text = key
        if kw:
            try:
                text = text.format(**kw)
            except (KeyError, IndexError):
                pass
        return text

    # ── 主题与窗口 ──────────────────────────────────────────

    def _load_theme(self):
        """加载 Azure 亮色主题；失败回退内置 clam"""
        self.style = ttk.Style(self.root)
        try:
            self.root.tk.call("source", resource_path("azure.tcl"))
            self.root.tk.call("set_theme", "light")
        except tk.TclError:
            self.style.theme_use("clam")
        self.bg_color = self.style.lookup("TFrame", "background") or "#ffffff"
        self.fg_color = self.style.lookup("TLabel", "foreground") or "#1a1a1a"
        self.root.config(bg=self.bg_color)
        self._apply_custom_styles()

    def _apply_custom_styles(self):
        """配置自定义 ttk 样式；主题切换后需重新调用以刷新配色"""
        s = self.style
        # 大标题
        s.configure("Title.TLabel", font=(self.font_family, FONT_SIZE_TITLE, "bold"))
        # 副标题
        s.configure("Sub.TLabel", font=(self.font_family, FONT_SIZE_SUBHEADING))
        # 运行标题（绿色强调）
        s.configure("RunTitle.TLabel", font=(self.font_family, FONT_SIZE_TITLE, "bold"),
                    foreground=self.colors["success"])
        # 弱化提示文本
        s.configure("Hint.TLabel", foreground=self.colors["muted"])
        # 可点击版本链接
        s.configure("Link.TLabel", font=(self.font_family, 10, "underline"),
                    foreground=self.colors["accent"])
        # 工具按钮（右上角）
        s.configure("Tool.TButton", padding=(14, 6))
        # 模式选择大按钮
        s.configure("Mode.TButton", padding=(42, 28),
                    font=(self.font_family, FONT_SIZE_BUTTON))
        # 强调主按钮（Start / Stop）
        s.configure("Accent.TButton", padding=(28, 14),
                    font=(self.font_family, FONT_SIZE_BUTTON, "bold"))
        # 分组框标题
        s.configure("TLabelframe.Label", font=(self.font_family, FONT_SIZE_FRAME, "bold"))
        # 参数名称标签
        s.configure("Param.TLabel", font=(self.font_family, FONT_SIZE_BODY))
        # 连接信息只读输入框
        s.configure("Info.TEntry", font=(self.font_family, FONT_SIZE_SUBHEADING))
        # 参数输入框
        s.configure("Param.TEntry", font=(self.font_family, FONT_SIZE_BODY))
        # 信号类型下拉框
        s.configure("Type.TCombobox", font=(self.font_family, FONT_SIZE_BODY))

    def _center_window(self, width, height):
        """窗口居中并设置初始尺寸"""
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max((screen_w - width) // 2, 0)
        y = max((screen_h - height) // 2, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    # ── 启动界面 ────────────────────────────────────────────

    def setup_start_screen(self):
        for widget in self.root.winfo_children():
            widget.destroy()
        self._clear_widget_refs()
        self.running = False
        self.mode = None

        self.root.protocol("WM_DELETE_WINDOW", self.cleanup_and_exit)
        self._center_window(1000, 400)
        self.root.minsize(820, 420)
        self.root.title(self.t("window_setup_title"))

        # ── 工具按钮栏：语言切换在左上角，退出在右上角 ──
        tool_bar = ttk.Frame(self.root)
        tool_bar.pack(side="top", fill="x", padx=12, pady=8)
        ttk.Button(tool_bar, text=LANGUAGES[self.lang], style="Tool.TButton",
                   command=self._toggle_language).pack(side="left", padx=(0, 6))
        ttk.Button(tool_bar, text=self.t("btn_exit"), style="Tool.TButton",
                   command=self.cleanup_and_exit).pack(side="right")

        # ── 标题区 ──
        ttk.Label(self.root, text=self.t("select_mode_title"),
                  style="Title.TLabel").pack(pady=26)

        # ── 模式选择区 ──
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(pady=18)

        btn_serial = ttk.Button(btn_frame, text=self.t("btn_serial_mode"),
                                style="Mode.TButton",
                                command=self.start_serial_mode,
                                state="normal" if SERIAL_SUPPORTED else "disabled")
        btn_serial.pack(side="left", padx=14)

        if not SERIAL_SUPPORTED:
            ttk.Label(btn_frame, text=self.t("serial_unsupported_hint"),
                      style="Hint.TLabel").pack(side="left", padx=10)

        btn_network = ttk.Button(btn_frame, text=self.t("btn_network_mode"),
                                 style="Mode.TButton",
                                 command=self.start_network_mode)
        btn_network.pack(side="left", padx=14)

        # ── 底部（side="bottom" 时先打包者在最下方）──
        # 最底：版权声明（不翻译）
        ttk.Label(self.root, text=f"Copyright (C) 2025-2026 ZhiCheng Zhang. All Rights Reserved.\n"
                                  "CIAE Nuclear Astrophysics Group, Beijing.",
                  style="Hint.TLabel").pack(side="bottom", pady=(6, 2))

        # 其上：可点击的版本链接
        version_label = ttk.Label(self.root, text=f"Version {VERSION}",
                                  style="Link.TLabel", cursor="hand2")
        version_label.pack(side="bottom", pady=2)
        version_label.bind("<Button-1>", lambda e: self._open_github())

        # 再上：模式提示语（不翻译）
        ttk.Label(self.root, text="Serial Mode creates native PTY devices (/dev/pts/N).\n"
                                  "Network Mode uses fixed TCP ports 1030/1031.",
                  style="Hint.TLabel").pack(side="bottom", pady=12)

    def _toggle_language(self):
        """切换语言并重建首页"""
        self.lang = "zh" if self.lang == "en" else "en"
        self.setup_start_screen()

    @staticmethod
    def _open_github():
        """在系统浏览器中打开项目 releases 页面"""
        try:
            webbrowser.open_new(GITHUB_URL)
        except Exception as e:
            print(f"   [Warn] Failed to open browser: {e}")

    # ── 模式入口 ────────────────────────────────────────────

    def start_serial_mode(self):
        if not SERIAL_SUPPORTED:
            messagebox.showerror(self.t("error_title"), self.t("err_serial_unsupported"))
            return
        try:
            self._release_resources()
            self._create_serial_resources()

            _, _, p1_name = self.pty_resources[0]
            _, _, p2_name = self.pty_resources[1]
            self.mode = "serial"
            self.signal_active.clear()
            self._launch_serial_processes()
            self.show_running_screen(self.t("mode_serial"), p1_name, p2_name,
                                     self.t("ch1_port"), self.t("ch2_port"))
        except Exception as e:
            messagebox.showerror(self.t("error_title"), self.t("err_serial_mode", e=e))

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
            self.signal_active.clear()
            self._launch_tcp_processes()
            self.show_running_screen(self.t("mode_network"), p1_text, p2_text,
                                     self.t("ch1_addr"), self.t("ch2_addr"))
        except Exception as e:
            messagebox.showerror(self.t("error_title"), self.t("err_network_mode", e=e))

    # ── 运行/控制界面 ───────────────────────────────────────

    def show_running_screen(self, mode_name, p1_info, p2_info, p1_label, p2_label):
        for widget in self.root.winfo_children():
            widget.destroy()

        self.root.protocol("WM_DELETE_WINDOW", self.cleanup_and_exit)
        self._center_window(1120, 740)
        self.root.minsize(920, 640)
        self.root.title(self.t("run_window_title", mode_name=mode_name))

        # 重置控件引用
        self.ch1_entries = {}
        self.ch2_entries = {}
        self._last_valid = {}

        # ── 顶部：模式标题 + 连接信息 ──
        ttk.Label(self.root, text=self.t("running_title", mode_name=mode_name),
                  style="RunTitle.TLabel").pack(pady=6)
        ttk.Label(self.root, text=self.t("configure_hint"),
                  style="Sub.TLabel").pack(pady=4)

        info_frame = ttk.LabelFrame(self.root, text=self.t("conn_info_title"),
                                    padding=(12, 8))
        info_frame.pack(pady=8, padx=24, fill="x")

        ttk.Label(info_frame, text=p1_label,
                  font=(self.font_family, FONT_SIZE_SUBHEADING, "bold")
                  ).grid(row=0, column=0, sticky="e", padx=10, pady=6)
        self.info_label = ttk.Entry(info_frame, style="Info.TEntry",
                                    width=32, justify="center")
        self.info_label.insert(0, p1_info)
        self.info_label.config(state="readonly")
        self.info_label.grid(row=0, column=1, padx=10, sticky="w")

        ttk.Label(info_frame, text=p2_label,
                  font=(self.font_family, FONT_SIZE_SUBHEADING, "bold")
                  ).grid(row=1, column=0, sticky="e", padx=10, pady=6)
        info_label2 = ttk.Entry(info_frame, style="Info.TEntry",
                                width=32, justify="center")
        info_label2.insert(0, p2_info)
        info_label2.config(state="readonly")
        info_label2.grid(row=1, column=1, padx=10, sticky="w")

        # ── 中部：左右双栏（Ch1 / Ch2）──
        main_frame = ttk.Frame(self.root)
        main_frame.pack(pady=8, padx=20, fill="both", expand=True)

        self._build_channel_panel(main_frame, 1, self.t("channel_n", n=1), side="left")
        self._build_channel_panel(main_frame, 2, self.t("channel_n", n=2), side="right")

        # ── 底部按钮 ──
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(pady=12)

        self.btn_toggle = ttk.Button(btn_frame, text=self.t("btn_start"),
                                     style="Accent.TButton",
                                     command=self.toggle_signals)
        self.btn_toggle.pack(side="left", padx=10)

        self.btn_back = ttk.Button(btn_frame, text=self.t("btn_back"),
                                   style="Tool.TButton",
                                   command=self.back_to_setup)
        self.btn_back.pack(side="left", padx=10)

        # 启动实时值轮询
        self._start_polling()

    def _build_channel_panel(self, parent, ch_num, title, side):
        """构建单个通道的控制面板"""
        frame = ttk.LabelFrame(parent, text=title, padding=(12, 10))
        frame.pack(side=side, fill="both", expand=True, padx=10)

        # 信号类型选择
        ttk.Label(frame, text=self.t("signal_type_label"),
                  style="Sub.TLabel").grid(row=0, column=0, sticky="w", padx=5, pady=5)

        type_var = tk.StringVar(value="dc")
        type_combo = ttk.Combobox(frame, textvariable=type_var, style="Type.TCombobox",
                                  values=[SIGNAL_LABELS["dc"][self.lang],
                                          SIGNAL_LABELS["pulse"][self.lang],
                                          SIGNAL_LABELS["trig"][self.lang]],
                                  state="readonly", width=18)
        type_combo.bind("<<ComboboxSelected>>",
                        lambda e, t=type_var: self._on_type_change(ch_num, t.get()))
        type_combo.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        if ch_num == 1:
            self.ch1_type_var = type_var
            self.ch1_combo = type_combo
        else:
            self.ch2_type_var = type_var
            self.ch2_combo = type_combo

        # 参数区域（三个 Frame，按需显示）
        param_container = ttk.Frame(frame)
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

        # 状态灯 + 实时值
        status_row = ttk.Frame(frame)
        status_row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=10)

        canvas = tk.Canvas(status_row, width=16, height=16, highlightthickness=0,
                           bg=self.bg_color)
        canvas.pack(side="left", padx=(0, 5))
        self._draw_status_light(canvas, active=False)

        value_label = ttk.Label(status_row, text="-- mA", style="Sub.TLabel")
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
            ttk.Label(frame, text=label_text,
                      style="Param.TLabel").grid(row=num, column=0, sticky="e", padx=5, pady=3)
            entry = ttk.Entry(frame, style="Param.TEntry", width=12, justify="center")
            entry.insert(0, default_val)
            entry.grid(row=num, column=1, sticky="w", padx=5, pady=3)
            entry.bind("<FocusOut>", lambda e, k=key, w=entry: self._validate_field(ch_num, k, w))
            entries[key] = entry

        # DC
        make_entry_row(0, dc_frame, "dc_value", self.t("DC Value (mA)") + ":",
                       DEFAULT_VALUES["dc"]["dc_value"])
        make_entry_row(1, dc_frame, "dc_noise", self.t("Noise (mA)") + ":",
                       DEFAULT_VALUES["dc"]["dc_noise"])

        # Pulse
        for i, (key, label, _, _, _) in enumerate(PARAM_DEFS["pulse"]):
            make_entry_row(i, pulse_frame, key, self.t(label) + ":",
                           DEFAULT_VALUES["pulse"][key])

        # Trig
        for i, (key, label, _, _, _) in enumerate(PARAM_DEFS["trig"]):
            make_entry_row(i, trig_frame, key, self.t(label) + ":",
                           DEFAULT_VALUES["trig"][key])

        return entries, dc_frame, pulse_frame, trig_frame

    def _on_type_change(self, ch_num, label):
        """下拉框切换信号类型（label 为当前语言的显示文本，需映射回 key）"""
        new_type = "dc"
        for k, labels in SIGNAL_LABELS.items():
            if labels[self.lang] == label:
                new_type = k
                break
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
        entry_widget.config(foreground=self.fg_color)
        self._last_valid[id(entry_widget)] = val_str

    def _revert_entry(self, entry, ch_num, key):
        """标红并回退到上次有效值"""
        entry.config(foreground="red")
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
                errors.append(self.t("err_empty", label=self.t(label)))
                continue
            try:
                val = ptype(val_str)
            except ValueError:
                errors.append(self.t("err_invalid", label=self.t(label)))
                continue

            if pmin is not None and val < pmin:
                errors.append(self.t("err_less_than", label=self.t(label), pmin=pmin))
                continue
            if pmax is not None and val > pmax:
                errors.append(self.t("err_greater_than", label=self.t(label), pmax=pmax))
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
                errors.append(self.t("err_rise_fall"))

            high = getattr(config, "pulse_high", 0)
            low = getattr(config, "pulse_low", 0)
            if high <= low:
                errors.append(self.t("err_high_low"))

        if errors:
            messagebox.showerror(self.t("param_error_title"),
                                 self.t("err_channel_prefix", ch_num=ch_num) + "\n"
                                 + "\n".join(errors))
            return None

        return config

    # ── 启动/停止 ────────────────────────────────────────────

    def toggle_signals(self):
        if self.running:
            self._stop_signals()
        else:
            self._start_signals()

    def _start_signals(self):
        """验证参数 → 写入共享配置 → 置位信号，让子进程输出波形值"""
        config1 = self._read_params_from_gui(1)
        config2 = self._read_params_from_gui(2)
        if config1 is None or config2 is None:
            return

        self.ch1_config = config1
        self.ch2_config = config2

        # 把最新参数写入共享内存，子进程每次应答时实时读取
        apply_config_to_shared(config1, self.ch1_shared_cfg)
        apply_config_to_shared(config2, self.ch2_shared_cfg)

        # 排空待机期间积压的 0 电流值，避免 GUI 显示滞后
        self._flush_value_queues()

        self.signal_active.set()
        self.running = True
        self._update_ui_state()

    def _flush_value_queues(self):
        """排空所有 value_queue 中积压的旧值"""
        for q in self.value_queues:
            try:
                while True:
                    q.get_nowait()
            except Empty:
                pass
            except Exception:
                break

    def _stop_signals(self, quiet=False):
        """清除信号，让子进程回到 0 电流应答（不终止进程）"""
        self.signal_active.clear()
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
                                     args=(dup1, slave1_name, self.ch1_shared_cfg, self.signal_active, q1))
        p2 = multiprocessing.Process(target=simulator_process_serial,
                                     args=(dup2, slave2_name, self.ch2_shared_cfg, self.signal_active, q2))
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
                                     args=(sock1, port1, self.ch1_shared_cfg, self.signal_active, q1))
        p2 = multiprocessing.Process(target=simulator_process_tcp,
                                     args=(sock2, port2, self.ch2_shared_cfg, self.signal_active, q2))
        p1.daemon = True
        p2.daemon = True
        p1.start()
        p2.start()
        self.processes = [p1, p2]

        print(f"   [TCP-Sim] Processes started on ports {port1}, {port2}")

    def _update_ui_state(self):
        """根据 running 状态更新按钮和参数状态（启动页 btn_toggle 为空时直接返回）"""
        if self.btn_toggle is None:
            return
        if self.running:
            self.btn_toggle.config(text=self.t("btn_stop"))
            self.btn_back.state(["disabled"])
            self._set_all_params_enabled(False)
            self._draw_status_light(self.ch1_status_canvas, active=True)
            self._draw_status_light(self.ch2_status_canvas, active=True)
        else:
            self.btn_toggle.config(text=self.t("btn_start"))
            self.btn_back.state(["!disabled"])
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
        # 类型下拉框（Combobox）
        combo_state = "readonly" if enabled else "disabled"
        for combo in (self.ch1_combo, self.ch2_combo):
            if combo is not None:
                try:
                    combo.config(state=combo_state)
                except tk.TclError:
                    pass

    # ── 状态灯 ───────────────────────────────────────────────

    def _draw_status_light(self, canvas, active):
        if canvas is None:
            return
        canvas.delete("all")
        color = self.colors["success"] if active else self.colors["inactive"]
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
        self.ch1_combo = None
        self.ch2_combo = None

    # ── 实时值轮询 ────────────────────────────────────────────

    def _start_polling(self):
        if self._poll_after_id:
            self.root.after_cancel(self._poll_after_id)
        self._poll_values()

    def _poll_values(self):
        if self.running and self.value_queues:
            for i, q in enumerate(self.value_queues):
                latest = None
                try:
                    # 清空队列，只保留最新值（避免显示滞后/积压旧值）
                    while True:
                        latest = q.get_nowait()
                except Empty:
                    pass
                except Exception:
                    continue
                if latest is not None:
                    label = self.ch1_value_label if i == 0 else self.ch2_value_label
                    if label:
                        label.config(text=f"{latest:.2f} mA")
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
        assert pty is not None and tty is not None, \
            "serial mode requires POSIX pty support"
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

    def _terminate_processes(self):
        """终止所有模拟器子进程并清空队列"""
        for p in self.processes:
            if p.is_alive():
                p.terminate()
        self.processes.clear()
        self.value_queues.clear()

    def _release_resources(self):
        """释放所有 PTY 和 Socket 资源"""
        self._terminate_processes()
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
