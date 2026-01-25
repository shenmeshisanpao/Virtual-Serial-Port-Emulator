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

def generate_wave_value(channel_name, elapsed_time):
    """生成模拟波形数据"""
    if "1" in channel_name:
        return 5.0 + 3.0 * math.sin(elapsed_time * 0.5) # Ch1 正弦
    else:
        return 2.0 + 1.0 * math.cos(elapsed_time * 0.8) + random.uniform(-0.1, 0.1) # Ch2 余弦

## 模拟器进程：串口模式

def simulator_process_serial(output_queue, channel_name):
    """
    串口模拟器工作进程 - 原生 PTY
    为解决 Linux 下 Buffer Overflow 和 Echo 问题。
    不再连接外部串口，而是自己生成一个 /dev/pts/XX 供外部连接。
    """
    
    # 1. 创建一对伪终端 (Master/Slave)
    # master_fd: 模拟器自己用（读写）
    # slave_fd:  给串口读取程序用
    try:
        master_fd, slave_fd = pty.openpty()
        
        # 2. 设置为 RAW 模式 (禁用回显、禁用缓冲、禁用特殊字符处理)
        tty.setraw(master_fd)
        tty.setraw(slave_fd)
        
        # 获取生成的串口名
        slave_name = os.ttyname(slave_fd)
        
        # 3. 将生成的端口名发送回 GUI 主进程
        output_queue.put(slave_name)
        
        print(f"   [Serial-Sim] {channel_name} started on {slave_name}")

        start_time = time.time()
        
        while True:
            # 使用 select 监听 master_fd 是否有数据可读 (非阻塞检查)
            # 0.01 秒超时
            r, w, e = select.select([master_fd], [], [], 0.01)
            
            if master_fd in r:
                # 4. 读取请求 (直接使用 os.read，绕过 Python serial 库的缓冲)
                try:
                    request = os.read(master_fd, 1024) # 一次读完所有积压数据
                except OSError:
                    break
                
                # 简单的协议解析：寻找 01 03 ...
                if len(request) >= 8:
                    # 校验简单的帧头 (Modbus: Slave 1, Func 3)
                    if request[0] == 1 and request[1] == 3:
                        elapsed = time.time() - start_time
                        val = generate_wave_value(channel_name, elapsed)
                        reg_h, reg_l = float_to_registers(val)
                        
                        # 构建响应
                        header = struct.pack('>B B B', 1, 3, 4)
                        data = struct.pack('>H H', reg_h, reg_l)
                        frame = header + data
                        crc = calculate_crc(frame)
                        packet = frame + struct.pack('<H', crc)
                        
                        # 5. 发送响应 (直接写入 master_fd)
                        os.write(master_fd, packet)
                        
    except Exception as e:
        print(f"   [Error] Simulator {channel_name} crashed: {e}")
        # 如果还没发回端口名就挂了，发个 None 防止 GUI 卡死
        try:
            output_queue.put(None)
        except:
            pass
    finally:
        try:
            os.close(master_fd)
            os.close(slave_fd)
        except:
            pass

## 模拟器进程：网络 TCP 模式

def simulator_process_tcp(port, channel_name):
    """TCP 网络模拟器工作进程"""
    print(f"   [TCP-Sim] {channel_name} listening on 0.0.0.0:{port}...")
    
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 允许端口复用
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        server_socket.bind(('0.0.0.0', port))
        server_socket.listen(1)
    except Exception as e:
        print(f"   [Error] Failed to bind TCP port {port}: {e}")
        return

    start_time = time.time()

    while True:
        try:
            # 等待客户端连接
            conn, addr = server_socket.accept()
            print(f"   [TCP-Sim] {channel_name} connected by {addr}")
            
            conn.settimeout(None) # 保持连接
            
            while True:
                try:
                    # 接收请求 (假设 Modbus RTU over TCP 帧长度不大，1024足够)
                    request = conn.recv(1024)
                    if not request:
                        break # 客户端断开
                    
                    if len(request) >= 8:
                        slave_id = request[0]
                        func_code = request[1]
                        
                        if slave_id == 1 and func_code == 3:
                            elapsed = time.time() - start_time
                            val = generate_wave_value(channel_name, elapsed)
                            reg_h, reg_l = float_to_registers(val)
                            
                            # 响应
                            header = struct.pack('>B B B', slave_id, func_code, 4)
                            data = struct.pack('>H H', reg_h, reg_l)
                            frame = header + data
                            crc = calculate_crc(frame)
                            packet = frame + struct.pack('<H', crc)
                            
                            conn.sendall(packet)
                        
                    # 防止极速循环的保险措施
                    time.sleep(0.001)

                except ConnectionResetError:
                    break # 连接重置
                except Exception as e:
                    print(f"   [Error] TCP Loop error: {e}")
                    break
            
            conn.close()
            print(f"   [TCP-Sim] {channel_name} disconnected. Waiting for new connection...")
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"   [Error] Server socket error: {e}")
            time.sleep(1)

    server_socket.close()

## GUI

class SimulatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Instrument Simulator Setup")
        self.root.geometry("1000x400")
        self.processes = []
        self.setup_start_screen()
        
    def setup_start_screen(self):
        """启动选择界面"""
        for widget in self.root.winfo_children():
            widget.destroy()
            
        tk.Label(self.root, text="Select Simulation Mode", font=("Arial", 18, "bold")).pack(pady=20)
        
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=20)
        
        # 串口模式按钮
        btn_serial = tk.Button(btn_frame, text="Virtual Serial Mode\n", 
                              font=("Arial", 12), width=20, height=3, bg="#e1f5fe",
                              command=self.start_serial_mode)
        btn_serial.pack(side="left", padx=10)
        
        # 网络模式按钮
        btn_network = tk.Button(btn_frame, text="Network TCP Mode\n", 
                               font=("Arial", 12), width=20, height=3, bg="#e8f5e9",
                               command=self.start_network_mode)
        btn_network.pack(side="left", padx=10)
        
        tk.Label(self.root, text="Native PTY Mode.\nNetwork Mode uses TCP ports 1030/1031.", fg="gray").pack(side="bottom", pady=20)

    def show_running_screen(self, mode_name, p1_info, p2_info, p1_label="Channel 1:", p2_label="Channel 2:"):
        """运行状态界面"""
        self.root.geometry("1100x550")
        self.root.title(f"Simulator Running - {mode_name}")
        
        for widget in self.root.winfo_children():
            widget.destroy()
            
        tk.Label(self.root, text=f"{mode_name} Running", font=("Arial", 20, "bold"), fg="forest green").pack(pady=15)
        tk.Label(self.root, text="Please enter the following configuration in the ammeter program:", font=("Arial", 12)).pack(pady=5)
        
        # 信息区域
        frame_ports = tk.Frame(self.root, relief="groove", borderwidth=2)
        frame_ports.pack(pady=15, padx=20, fill="x")
        
        # Channel 1
        tk.Label(frame_ports, text=p1_label, font=("Arial", 16, "bold")).grid(row=0, column=0, sticky="e", padx=10, pady=15)
        entry_p1 = tk.Entry(frame_ports, font=("Arial", 18), width=25, justify="center")
        entry_p1.insert(0, p1_info)
        entry_p1.grid(row=0, column=1, padx=10)
        
        # Channel 2
        tk.Label(frame_ports, text=p2_label, font=("Arial", 16, "bold")).grid(row=1, column=0, sticky="e", padx=10, pady=15)
        entry_p2 = tk.Entry(frame_ports, font=("Arial", 18), width=25, justify="center")
        entry_p2.insert(0, p2_info)
        entry_p2.grid(row=1, column=1, padx=10)
        
        # 提示与退出
        lbl_note = tk.Label(self.root, text="Keep this window open to maintain the connection.", fg="firebrick", font=("Arial", 12, "bold"))
        lbl_note.pack(pady=10)
        
        tk.Label(self.root, text="Copyright (C) 2025 ZhiCheng Zhang. All Rights Reserved.\nCIAE Nuclear Astrophysics Group, Beijing.", fg="gray").pack(side="bottom", pady=10)
        
        btn_stop = tk.Button(self.root, text="STOP and EXIT", command=self.cleanup_and_exit, bg="#ffcccc", font=("Arial", 10, "bold"))
        btn_stop.pack(side="bottom", pady=5)

    def start_serial_mode(self):
        """启动串口模式"""
        try:
            print("Initializing virtual serial ports...")
            q1 = multiprocessing.Queue()
            q2 = multiprocessing.Queue()
            
            # 启动模拟进程
            sim1 = multiprocessing.Process(target=simulator_process_serial, args=(q1, "Channel 1"))
            sim2 = multiprocessing.Process(target=simulator_process_serial, args=(q2, "Channel 2"))
            
            sim1.daemon = True
            sim2.daemon = True
            sim1.start()
            sim2.start()
            
            self.processes.extend([sim1, sim2])

            # 等待子进程返回生成的端口名 (阻塞式获取，通常是瞬间的)
            # 设置超时防止无限等待
            try:
                p1_user = q1.get(timeout=5)
                p2_user = q2.get(timeout=5)
            except Exception:
                messagebox.showerror("Error", "Timeout waiting for PTY generation.")
                self.cleanup_and_exit()
                return

            if not p1_user or not p2_user:
                messagebox.showerror("Error", "Failed to generate PTY ports.")
                self.cleanup_and_exit()
                return

            print(f"Ports generated: {p1_user}, {p2_user}")
            
            # 更新界面
            self.show_running_screen("Virtual Serial Mode", p1_user, p2_user, "Channel 1 Port:", "Channel 2 Port:")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start serial mode: {e}")
            self.cleanup_and_exit()

    def start_network_mode(self):
        """启动网络模式"""
        try:
            # 获取本机 IP
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except:
                local_ip = "127.0.0.1"

            port1 = 1030
            port2 = 1031
            
            # 启动模拟进程
            sim1 = multiprocessing.Process(target=simulator_process_tcp, args=(port1, "Channel 1"))
            sim2 = multiprocessing.Process(target=simulator_process_tcp, args=(port2, "Channel 2"))
            
            sim1.daemon = True
            sim2.daemon = True
            sim1.start()
            sim2.start()
            
            self.processes.extend([sim1, sim2])
            
            # 更新界面
            p1_text = f"{local_ip}:{port1}"
            p2_text = f"{local_ip}:{port2}"
            self.show_running_screen("Network TCP Mode", p1_text, p2_text, "Channel 1 Addr:", "Channel 2 Addr:")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start network mode: {e}")
            self.cleanup_and_exit()

    def cleanup_and_exit(self):
        """清理并退出"""
        print("\nCleaning up environment...")
        for p in self.processes:
            if p.is_alive(): p.terminate()
        print("Cleanup complete.")
        self.root.destroy()

def main():
    multiprocessing.freeze_support()
    root = tk.Tk()
    app = SimulatorApp(root)
    # 捕获窗口关闭事件
    root.protocol("WM_DELETE_WINDOW", app.cleanup_and_exit)
    root.mainloop()

if __name__ == "__main__":
    main()
