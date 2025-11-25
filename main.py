import subprocess
import re
import time
import multiprocessing
import struct
import math
import random
import ctypes
import tkinter as tk

# ==========================================
# 模拟器核心逻辑
# ==========================================

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

def simulator_process(port, channel_name):
    """模拟器工作进程"""
    import serial # 在进程内导入
    
    print(f"   [Simulator] {channel_name} is connecting to {port}...")
    try:
        ser = serial.Serial(port, 9600, timeout=0.1)
    except Exception as e:
        print(f"   [Error] Unable to open simulator port {port}: {e}")
        return

    start_time = time.time()
    
    while True:
        try:
            if ser.in_waiting >= 8:
                request = ser.read(8)
                if len(request) == 8:
                    slave_id = request[0]
                    func_code = request[1]
                    
                    if slave_id == 1 and func_code == 3:
                        elapsed = time.time() - start_time
                        
                        # 生成波形
                        if "1" in channel_name:
                            val = 5.0 + 3.0 * math.sin(elapsed * 0.5) # Ch1 正弦
                        else:
                            val = 2.0 + 1.0 * math.cos(elapsed * 0.8) + random.uniform(-0.1, 0.1) # Ch2 余弦
                        
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

# ==========================================
# 环境管理与 GUI
# ==========================================

def get_socat_ports():
    """启动 socat 并解析输出的端口对"""
    cmd = ["socat", "-d", "-d", "pty,raw,echo=0", "pty,raw,echo=0"]
    
    # 启动进程，捕获 stderr (socat 的输出在 stderr)
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True, bufsize=1)
    
    ports = []
    # 读取输出直到找到两个端口
    while len(ports) < 2:
        line = proc.stderr.readline()
        if not line:
            break
        # 正则匹配 /dev/pts/数字
        match = re.search(r'PTY is (/dev/pts/\d+)', line)
        if match:
            ports.append(match.group(1))
            
    if len(ports) != 2:
        proc.kill()
        raise RuntimeError("Unable to get port pair from socat")
        
    return proc, ports[0], ports[1]

def main():
    # 检查 socat 是否安装
    if subprocess.call(["which", "socat"], stdout=subprocess.DEVNULL) != 0:
        print("Error: socat not found. Please run sudo apt-get install socat")
        return

    print("Initializing virtual environment, please wait...")

    try:
        # 1. 启动两组 socat
        proc1, p1_sim, p1_user = get_socat_ports()
        proc2, p2_sim, p2_user = get_socat_ports()
        
        print(f"Virtual line 1 established: {p1_sim} <---> {p1_user}")
        print(f"Virtual line 2 established: {p2_sim} <---> {p2_user}")

        # 2. 启动模拟器进程
        sim1 = multiprocessing.Process(target=simulator_process, args=(p1_sim, "Channel 1"))
        sim2 = multiprocessing.Process(target=simulator_process, args=(p2_sim, "Channel 2"))
        
        sim1.daemon = True
        sim2.daemon = True
        
        sim1.start()
        sim2.start()
        
        print("Simulator is running in the background.")

        # 3. 弹出 GUI 提示
        root = tk.Tk()
        root.title("Virtual Serial Port Emulator")
        root.geometry("750x420")
        
        lbl_title = tk.Label(root, text="Virtual serial port running", font=("Arial", 20, "bold"), fg="forest green")
        lbl_title.pack(pady=10)
        
        lbl_instruct = tk.Label(root, text="Please enter the following port in the ammeter program:", font=("Arial", 14))
        lbl_instruct.pack(pady=5)
        
        # 显示端口信息的区域
        frame_ports = tk.Frame(root, relief="groove", borderwidth=2)
        frame_ports.pack(pady=10, padx=20, fill="x")
        
        # Channel 1
        tk.Label(frame_ports, text="Channel 1 Serial Port:", font=("Arial", 16, "bold")).grid(row=0, column=0, sticky="e", padx=10, pady=10)
        entry_p1 = tk.Entry(frame_ports, font=("Arial", 18), width=15)
        entry_p1.insert(0, p1_user)
        entry_p1.grid(row=0, column=1, padx=10)
        
        # Channel 2
        tk.Label(frame_ports, text="Channel 2 Serial Port:", font=("Arial", 16, "bold")).grid(row=1, column=0, sticky="e", padx=10, pady=10)
        entry_p2 = tk.Entry(frame_ports, font=("Arial", 18), width=15)
        entry_p2.insert(0, p2_user)
        entry_p2.grid(row=1, column=1, padx=10)
        
        lbl_note = tk.Label(
            root, 
            text="Note: Please keep this window open.\nClosing this window will stop virtual serial ports!", 
            fg="firebrick",
            font=("Arial", 14, "bold")
        )
        lbl_note.pack(pady=1)
        lbl_note = tk.Label(root, text="Copyright (C) 2025 ZhiCheng Zhang. All Rights Reserved.\nCIAE Nuclear Astrophysics Group, Beijing.\nThis project is licensed under the GNU General Public License v3.0", fg="gray")
        lbl_note.pack(pady=20)
        
        btn_close = tk.Button(root, text="STOP and EXIT", command=root.destroy, bg="#ffcccc")
        btn_close.pack(pady=5)

        # 运行 GUI 循环
        root.mainloop()

    except Exception as e:
        print(f"Error occurred:  {e}")
    finally:
        print("\nCleaning up environment...")
        # 清理进程
        try:
            if 'sim1' in locals() and sim1.is_alive(): sim1.terminate()
            if 'sim2' in locals() and sim2.is_alive(): sim2.terminate()
            if 'proc1' in locals(): proc1.kill()
            if 'proc2' in locals(): proc2.kill()
        except:
            pass
        print("Cleanup complete.")

if __name__ == "__main__":
    # Windows下multiprocessing需要这个保护
    multiprocessing.freeze_support()
    main()
