import os
import sys
import json
import ctypes
from ctypes import wintypes
import getpass
import logging
import logging.handlers
import shutil
import socket
import platform
import psutil
import requests
import hashlib
import subprocess
import tempfile
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog
from cryptography.fernet import Fernet
import pystray
from PIL import Image, ImageDraw

logger = logging.getLogger("itam_agent")
_instance_mutex = None


def acquire_single_instance():
    global _instance_mutex
    if os.name != "nt":
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\ITAM_Agent")
    if not _instance_mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(_instance_mutex)
        _instance_mutex = None
        return False
    return True


def configure_logging():
    if os.name == "nt":
        app_data = os.environ.get("LOCALAPPDATA")
        log_directories = (
            [Path(app_data) / "ITAM Agent"] if app_data else []
        ) + [Path(tempfile.gettempdir()) / "ITAM Agent"]
    else:
        log_directories = [
            Path.home() / ".local" / "state" / "itam-agent",
            Path(tempfile.gettempdir()) / "itam-agent",
        ]

    log_errors = []
    for directory in log_directories:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                directory / "itam-agent.log",
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            break
        except OSError as exc:
            log_errors.append(f"{directory}: {exc}")
            continue
    else:
        handler = logging.StreamHandler()

    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    ))
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    for error in log_errors:
        logger.error("Could not create a log file in %s.", error)
    logger.info("ITAM Agent started. Log file: %s", getattr(handler, "baseFilename", "stderr"))


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


def get_config_path():
    if getattr(sys, "frozen", False) and os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            return Path(program_data) / "ITAM Agent" / "config.enc"
    return Path(__file__).resolve().parent / "config.enc"


def load_config():
    try:
        key = get_secret_key()
        cipher = Fernet(key)
        with get_config_path().open("rb") as f:
            decrypted = cipher.decrypt(f.read())
        return json.loads(decrypted.decode('utf-8'))
    except Exception as e:
        logger.exception("Could not load encrypted configuration: %s", e)
        return None

def save_config(config_data):
    try:
        key = get_secret_key()
        cipher = Fernet(key)
        json_bytes = json.dumps(config_data).encode('utf-8')
        encrypted = cipher.encrypt(json_bytes)
        config_path = get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open("wb") as f:
            f.write(encrypted)
        return True
    except Exception as e:
        logger.exception("Could not save encrypted configuration: %s", e)
        return False

# ----------------------------------------------------
# 2. جمع بيانات الجهاز وإرسالها
# ----------------------------------------------------

def collect_inventory():
    payload = {
        "computerName": socket.gethostname(),
        "loggedInUser": getpass.getuser(),
        "osName": platform.system(),
        "osVersion": platform.version(),
        "osBuild": platform.version(),
        "domain": None,
        "cpu": {
            "model": platform.processor() or "Unknown",
            "cores": psutil.cpu_count(logical=False) or 0,
            "threads": psutil.cpu_count(logical=True) or 0,
            "clockSpeedMhz": 0,
            "architecture": platform.machine() or "Unknown",
        },
        "motherboard": {
            "manufacturer": "Unknown",
            "model": "Unknown",
            "serialNumber": None,
        },
        "ramModules": [],
        "disks": [],
        "software": [],
        "gitConfig": None,
    }

    if os.name == "nt":
        try:
            payload.update(_collect_windows_hardware())
        except (OSError, subprocess.SubprocessError, ValueError):
            logger.exception("Could not collect detailed Windows hardware information.")
        payload["software"] = _collect_installed_software()

    if not payload["disks"]:
        payload["disks"] = _collect_logical_disks()
    payload["gitConfig"] = _collect_git_config()
    logger.info(
        "Inventory collected: %d RAM modules, %d disks, %d software entries.",
        len(payload["ramModules"]),
        len(payload["disks"]),
        len(payload["software"]),
    )
    return payload


def _collect_logical_disks():
    disks = []
    for partition in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (PermissionError, OSError) as exc:
            logger.warning(
                "Could not read disk usage for %s: %s",
                partition.mountpoint,
                exc,
            )
            continue
        disks.append({
            "drive": partition.mountpoint,
            "volumeName": partition.device or None,
            "totalSpaceGb": round(usage.total / (1024 ** 3), 2),
            "freeSpaceGb": round(usage.free / (1024 ** 3), 2),
        })
    return disks


def _run_powershell_json(script):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise FileNotFoundError("PowerShell is not available.")
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def _collect_windows_hardware():
    if platform.release() == "7":
        logger.info(
            "Windows 7 detected; using built-in inventory fallbacks for "
            "hardware that requires newer PowerShell storage cmdlets."
        )
        return _collect_windows_7_fallback()

    script = r"""
$ErrorActionPreference = 'Stop'
$computer = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
$processor = Get-CimInstance Win32_Processor | Select-Object -First 1
$board = Get-CimInstance Win32_BaseBoard | Select-Object -First 1
$architectureMap = @{ 0='x86'; 1='MIPS'; 2='Alpha'; 3='PowerPC'; 5='ARM'; 6='ia64'; 9='x64' }
$architecture = if ($architectureMap.ContainsKey([int]$processor.Architecture)) {
    $architectureMap[[int]$processor.Architecture]
} else { 'Unknown' }
$ram = @(Get-CimInstance Win32_PhysicalMemory | ForEach-Object {
    @{
        capacityGb = [int][math]::Round(([double]$_.Capacity / 1GB), 0)
        speedMhz = [int]$_.Speed
        serialNumber = if ($_.SerialNumber) { $_.SerialNumber.ToString().Trim() } else { $null }
        slot = if ($_.BankLabel) { $_.BankLabel.ToString().Trim() } else { $null }
    }
})
$volumes = @(Get-Volume -ErrorAction SilentlyContinue | Where-Object DriveLetter)
$logicalDisks = @($volumes | ForEach-Object {
    @{
        drive = "$($_.DriveLetter):"
        volumeName = if ($_.FileSystemLabel) { $_.FileSystemLabel } else { $null }
        totalSpaceGb = [math]::Round(([double]$_.Size / 1GB), 2)
        freeSpaceGb = [math]::Round(([double]$_.SizeRemaining / 1GB), 2)
    }
})
$physicalDisks = @(Get-Disk -ErrorAction SilentlyContinue | ForEach-Object {
    $disk = $_
    $model = if ($disk.FriendlyName) { $disk.FriendlyName.ToString().Trim() } else { 'Unknown' }
    $type = if ($model -match 'NVMe') { 'NVME' } elseif ($model -match 'SSD') { 'SSD' } else { 'HDD' }
    $freeBytes = [double]0
    $partitions = @(Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue)
    foreach ($partition in $partitions) {
        if ($partition.DriveLetter) {
            $volume = Get-Volume -DriveLetter $partition.DriveLetter -ErrorAction SilentlyContinue
            if ($volume) { $freeBytes += [double]$volume.SizeRemaining }
        }
    }
    @{
        type = $type
        model = $model
        serialNumber = if ($disk.SerialNumber) { $disk.SerialNumber.ToString().Trim() } else { $null }
        totalSpaceGb = [math]::Round(([double]$disk.Size / 1GB), 2)
        freeSpaceGb = [math]::Round(($freeBytes / 1GB), 2)
    }
})
$domain = if ($computer.PartOfDomain) { $computer.Domain } else { $null }
[ordered]@{
    computerName = $computer.Name
    loggedInUser = if ($computer.UserName) { $computer.UserName } else { $env:USERNAME }
    osName = $os.Caption
    osVersion = $os.Version
    osBuild = $os.BuildNumber
    domain = $domain
    cpu = @{
        model = if ($processor.Name) { $processor.Name.ToString().Trim() } else { 'Unknown' }
        cores = [int]$processor.NumberOfCores
        threads = [int]$processor.NumberOfLogicalProcessors
        clockSpeedMhz = [int]$processor.MaxClockSpeed
        architecture = $architecture
    }
    motherboard = @{
        manufacturer = if ($board.Manufacturer) { $board.Manufacturer.ToString().Trim() } else { 'Unknown' }
        model = if ($board.Product) { $board.Product.ToString().Trim() } else { 'Unknown' }
        serialNumber = if ($board.SerialNumber) { $board.SerialNumber.ToString().Trim() } else { $null }
    }
    ramModules = $ram
    disks = $physicalDisks
    logicalDisks = $logicalDisks
} | ConvertTo-Json -Depth 8 -Compress
"""
    hardware = _run_powershell_json(script)
    logical_disks = hardware.pop("logicalDisks", [])
    hardware["disks"] = hardware.get("disks") or logical_disks
    return hardware


def _collect_windows_7_fallback():
    import winreg

    cpu_model = "Unknown"
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as cpu_key:
            cpu_model = str(winreg.QueryValueEx(cpu_key, "ProcessorNameString")[0]).strip()
    except OSError as exc:
        logger.warning("Could not read the Windows 7 CPU model from the registry: %s", exc)

    domain = os.environ.get("USERDOMAIN")
    if domain and domain.casefold() == socket.gethostname().casefold():
        domain = None

    return {
        "cpu": {
            "model": cpu_model or "Unknown",
            "cores": psutil.cpu_count(logical=False) or 0,
            "threads": psutil.cpu_count(logical=True) or 0,
            "clockSpeedMhz": 0,
            "architecture": platform.machine() or "Unknown",
        },
        "domain": domain,
        "disks": _collect_logical_disks(),
    }


def _collect_installed_software():
    if os.name != "nt":
        return []

    import winreg

    software = {}
    uninstall_paths = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, path in uninstall_paths:
        try:
            with winreg.OpenKey(hive, path) as root:
                subkey_count = winreg.QueryInfoKey(root)[0]
                for index in range(subkey_count):
                    try:
                        subkey_name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, subkey_name) as app_key:
                            name = _read_registry_value(winreg, app_key, "DisplayName")
                            if not name:
                                continue
                            software[name.casefold()] = {
                                "name": name,
                                "version": _read_registry_value(
                                    winreg, app_key, "DisplayVersion"
                                ) or "Unknown",
                                "publisher": _read_registry_value(
                                    winreg, app_key, "Publisher"
                                ),
                                "installDate": _read_registry_value(
                                    winreg, app_key, "InstallDate"
                                ),
                            }
                    except OSError as exc:
                        logger.warning("Could not read an installed-app registry entry: %s", exc)
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Could not read installed-app registry path %s: %s", path, exc)
    return sorted(software.values(), key=lambda app: app["name"].casefold())


def _read_registry_value(winreg, key, name):
    try:
        value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _collect_git_config():
    git = shutil.which("git")
    if not git:
        logger.info("Git is not installed; skipping Git inventory.")
        return None

    def read_git_value(key):
        try:
            result = subprocess.run(
                [git, "config", "--global", key],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Could not read Git setting %s: %s", key, exc)
            return None
        value = result.stdout.strip()
        return value or None

    try:
        version_result = subprocess.run(
            [git, "--version"],
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
        git_version = version_result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not read Git version: %s", exc)
        git_version = None

    ssh_dir = Path.home() / ".ssh"
    try:
        public_keys = sorted(
            path.stem for path in ssh_dir.glob("*.pub") if path.is_file()
        )
    except OSError as exc:
        logger.warning("Could not list SSH public-key filenames: %s", exc)
        public_keys = []

    return {
        "userName": read_git_value("user.name"),
        "userEmail": read_git_value("user.email"),
        "gitVersion": git_version,
        "sshPublicKeys": public_keys,
    }


def send_inventory():
    config = load_config()
    if not config:
        logger.error("Inventory was not sent because configuration could not be loaded.")
        return False

    try:
        url = f"http://{config['server_ip']}:{config['server_port']}{config['endpoint']}"
    except (KeyError, TypeError, ValueError):
        logger.exception("Invalid API configuration; inventory was not uploaded.")
        return False

    try:
        inventory = collect_inventory()
    except Exception:
        logger.exception("Could not collect inventory; upload was skipped.")
        return False

    logger.info("Uploading inventory to %s.", url)
    try:
        response = requests.post(
            url,
            json=inventory,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "ITAM-Inventory-Agent/1.0",
            },
            timeout=30,
        )
    except requests.exceptions.RequestException:
        logger.exception("Network error while uploading inventory.")
        return False

    logger.info("API response: HTTP %s %s", response.status_code, response.reason)
    if response.text:
        logger.info("API response body: %s", response.text[:4000])
    if 200 <= response.status_code < 300:
        logger.info("Inventory uploaded successfully.")
        return True
    logger.error("Inventory upload failed with HTTP %s.", response.status_code)
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
    configure_logging()
    try:
        if not acquire_single_instance():
            logger.info("Another ITAM Agent instance is already running.")
            return
    except OSError:
        logger.exception("Could not establish the single-instance guard.")
        return

    def on_sync(icon, item):
        if send_inventory():
            notification = "Inventory uploaded successfully."
        else:
            notification = "Upload failed. Check the ITAM Agent log."
        try:
            icon.notify(notification, "ITAM Agent")
        except (NotImplementedError, OSError):
            logger.exception("Could not display the sync notification.")

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
    