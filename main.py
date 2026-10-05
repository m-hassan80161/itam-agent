import os
import sys
import json
import socket
import platform
import psutil
import requests
import hashlib
import tkinter as tk
from tkinter import messagebox, simpledialog
from cryptography.fernet import Fernet
import pystray
from PIL import Image, ImageDraw

# ----------------------------------------------------
# 1. إدارة التشفير والإعدادات
# ----------------------------------------------------

def get_secret_key():
    """قراءة المفتاح سواء كان في البيئة العادية أو مضمّناً داخل EXE"""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)

    key_path = os.path.join(base_path, "secret.key")
    with open(key_path, "rb") as key_file:
        return key_file.read()

def load_config():
    try:
        key = get_secret_key()
        cipher = Fernet(key)
        with open("config.enc", "rb") as f:
            decrypted = cipher.decrypt(f.read())
        return json.loads(decrypted.decode('utf-8'))
    except Exception as e:
        print(f"Error loading config: {e}")
        return None

def save_config(config_data):
    try:
        key = get_secret_key()
        cipher = Fernet(key)
        json_bytes = json.dumps(config_data).encode('utf-8')
        encrypted = cipher.encrypt(json_bytes)
        with open("config.enc", "wb") as f:
            f.write(encrypted)
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        return False

# ----------------------------------------------------
# 2. جمع بيانات الجهاز وإرسالها
# ----------------------------------------------------

def collect_inventory():
    payload = {
        "computerName": socket.gethostname(),
        "osName": platform.system(),
        "osVersion": platform.version(),
        "cpuModel": platform.processor(),
        "cpuCores": psutil.cpu_count(logical=False),
        "cpuThreads": psutil.cpu_count(logical=True),
        "ramTotalGb": round(psutil.virtual_memory().total / (1024**3), 2),
        "disks": []
    }
    for partition in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            payload["disks"].append({
                "device": partition.device,
                "totalGb": round(usage.total / (1024**3), 2),
                "freeGb": round(usage.free / (1024**3), 2)
            })
        except PermissionError:
            continue
    return payload

def send_inventory():
    config = load_config()
    if not config:
        return False
    url = f"http://{config['server_ip']}:{config['server_port']}{config['endpoint']}"
    try:
        response = requests.post(url, json=collect_inventory(), timeout=10)
        return response.status_code in [200, 201]
    except Exception as e:
        print(f"Failed to send data: {e}")
        return False

# ----------------------------------------------------
# 3. واجهة الإعدادات المحمية بكلمة سر
# ----------------------------------------------------

def open_settings():
    config = load_config()
    if not config:
        return

    root = tk.Tk()
    root.title("ITAM Agent Settings")
    root.geometry("320x240")
    root.resizable(False, False)

    tk.Label(root, text="Server IP:").pack(pady=(10, 2))
    ip_entry = tk.Entry(root, width=25, justify="center")
    ip_entry.insert(0, config.get("server_ip", ""))
    ip_entry.config(state="disabled")
    ip_entry.pack()

    tk.Label(root, text="Server Port:").pack(pady=(10, 2))
    port_entry = tk.Entry(root, width=25, justify="center")
    port_entry.insert(0, config.get("server_port", ""))
    port_entry.config(state="disabled")
    port_entry.pack()

    is_unlocked = [False]

    def unlock():
        password = simpledialog.askstring("Admin Required", "Enter Admin Password:", show='*')
        if password:
            hashed = hashlib.sha256(password.encode('utf-8')).hexdigest()
            if hashed == config.get("admin_password_hash"):
                is_unlocked[0] = True
                ip_entry.config(state="normal")
                port_entry.config(state="normal")
                unlock_btn.config(state="disabled", text="Unlocked")
                save_btn.config(state="normal")
            else:
                messagebox.showerror("Error", "Incorrect Password!")

    def save():
        if is_unlocked[0]:
            config["server_ip"] = ip_entry.get().strip()
            config["server_port"] = port_entry.get().strip()
            if save_config(config):
                messagebox.showinfo("Success", "Settings saved!")
                root.destroy()

    unlock_btn = tk.Button(root, text="🔒 Unlock for Admin", command=unlock)
    unlock_btn.pack(pady=10)

    save_btn = tk.Button(root, text="Save Changes", command=save, state="disabled")
    save_btn.pack(pady=5)

    root.mainloop()

# ----------------------------------------------------
# 4. أيقونة شريط المهام (System Tray)
# ----------------------------------------------------

def create_tray_icon():
    image = Image.new('RGB', (64, 64), color=(0, 120, 215))
    d = ImageDraw.Draw(image)
    d.rectangle([16, 16, 48, 48], fill=(255, 255, 255))
    return image

def main():
    def on_sync(icon, item):
        send_inventory()

    def on_settings(icon, item):
        open_settings()

    def on_exit(icon, item):
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Sync Now", on_sync),
        pystray.MenuItem("Settings", on_settings),
        pystray.MenuItem("Exit", on_exit)
    )

    icon = pystray.Icon("ITAM_Agent", create_tray_icon(), "ITAM Agent", menu)
    
    # إرسال البيانات المبدئية عند التشغيل
    send_inventory()
    
    icon.run()

if __name__ == "__main__":
    main()
    