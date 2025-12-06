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

def simulator_process_serial(port, channel_name):
    """串口模拟器工作进程"""
    import serial # 在进程内导入
    
    print(f"   [Serial-Sim] {channel_name} is connecting to {port}...")
    try:
        ser = serial.Serial(port, 9600, timeout=0.1)
    except Exception as e:
        print(f"   [Error] Unable to open simulator port {port}: {e}")
        return

    start_time = time.time()
    
    while True:
        try:
            if ser.in_waiting < 8:
                time.sleep(0.01) # 如果没数据，休息 10ms，释放 CPU
                continue

            request = ser.read(8)
            if len(request) == 8:
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
                    
                    ser.write(packet)
        except:
            break
    ser.close()

## 模拟器进程：网络 TCP 模式

def simulator_process_tcp(port, channel_name):
    """TCP 网络模拟器工作进程"""
    print(f"   [TCP-Sim] {channel_name} listening on 0.0.0.0:{port}...")
    
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 允许端口复用，防止重启时报错 Address already in use
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

## 环境管理 (Socat)

def get_socat_ports():
    """启动 socat 并解析输出的端口对"""
    cmd = ["socat", "-d", "-d", "pty,raw,echo=0", "pty,raw,echo=0"]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True, bufsize=1)
    ports = []
    while len(ports) < 2:
        line = proc.stderr.readline()
        if not line: break
        match = re.search(r'PTY is (/dev/pts/\d+)', line)
        if match: ports.append(match.group(1))
            
    if len(ports) != 2:
        proc.kill()
        raise RuntimeError("Unable to get port pair from socat")
    return proc, ports[0], ports[1]

## GUI

class SimulatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Instrument Simulator Setup")
        self.root.geometry("1000x400")
        
        self.processes = []
        self.socat_procs = []
        
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
        
        tk.Label(self.root, text="Serial Mode requires 'socat' installed.\nNetwork Mode uses TCP ports 1030/1031.", fg="gray").pack(side="bottom", pady=20)

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
        # 检查 socat
        if subprocess.call(["which", "socat"], stdout=subprocess.DEVNULL) != 0:
            messagebox.showerror("Error", "socat not found! Please run: sudo apt-get install socat")
            return

        try:
            print("Initializing virtual serial ports...")
            # 启动 socat
            proc1, p1_sim, p1_user = get_socat_ports()
            self.socat_procs.append(proc1)
            
            proc2, p2_sim, p2_user = get_socat_ports()
            self.socat_procs.append(proc2)
            
            # 启动模拟进程
            sim1 = multiprocessing.Process(target=simulator_process_serial, args=(p1_sim, "Channel 1"))
            sim2 = multiprocessing.Process(target=simulator_process_serial, args=(p2_sim, "Channel 2"))
            
            sim1.daemon = True
            sim2.daemon = True
            sim1.start()
            sim2.start()
            
            self.processes.extend([sim1, sim2])
            
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
        for proc in self.socat_procs:
            proc.kill()
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
