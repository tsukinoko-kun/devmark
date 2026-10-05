import csv
import io
import json
from pathlib import Path
import platform
import re

import cpuinfo
import psutil

from .process import probe


MEMORY_TYPES = {
    20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 27: "LPDDR",
    28: "LPDDR2", 29: "LPDDR3", 30: "LPDDR4", 34: "DDR5", 35: "LPDDR5",
}


def read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def size_bytes(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"([\d.]+)\s*(KiB|MiB|GiB|TiB|KB|MB|GB|TB|B)\b", value, re.I)
    if not match:
        return None
    unit = match[2].upper().replace("IB", "B")
    return int(float(match[1]) * 1024 ** {"B": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4}[unit])


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def parse_json(text: str | None):
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


def mac_hardware(data: dict, raw: dict) -> None:
    hardware = next(iter(raw.get("SPHardwareDataType", [])), {})
    data["computer_model"] = hardware.get("machine_name")
    data["computer_model_identifier"] = hardware.get("machine_model")
    data["cpu"]["name"] = hardware.get("chip_type") or hardware.get("cpu_type") or data["cpu"]["name"]
    speed = hardware.get("current_processor_speed")
    if speed:
        match = re.search(r"([\d.]+)\s*(GHz|MHz)", speed)
        if match:
            data["cpu"]["clock_mhz"] = float(match[1]) * (1000 if match[2] == "GHz" else 1)
    for gpu in raw.get("SPDisplaysDataType", []):
        vram = gpu.get("spdisplays_vram") or gpu.get("spdisplays_vram_shared")
        data["gpus"].append({
            "name": gpu.get("sppci_model") or gpu.get("_name"),
            "vram_bytes": size_bytes(vram),
            "memory_type": "unified" if hardware.get("chip_type") else "dedicated_or_shared",
            "vram_reported": vram,
        })
    for item in objects(raw.get("SPMemoryDataType", [])):
        if "dimm_size" in item:
            data["ram"]["modules"].append({
                "name": item.get("dimm_part_number") or item.get("_name"),
                "manufacturer": item.get("dimm_manufacturer"),
                "size_bytes": size_bytes(item.get("dimm_size")),
                "speed": item.get("dimm_speed"),
                "standard": item.get("dimm_type"),
            })
    memory = next(iter(raw.get("SPMemoryDataType", [])), {})
    data["ram"]["standard"] = memory.get("memory_type")
    if hardware.get("chip_type"):
        data["notes"].append("Apple unified memory has no separate GPU VRAM capacity.")


def windows_hardware(data: dict, raw: dict) -> None:
    computer = raw.get("computer") or {}
    data["computer_model"] = " ".join(str(computer.get(k) or "").strip() for k in ("Manufacturer", "Model")).strip() or None
    os_info = raw.get("os") or {}
    data["os_name"] = os_info.get("Caption") or data["os_name"]
    processors = raw.get("cpus") or []
    if processors:
        data["cpu"]["name"] = ", ".join(dict.fromkeys(p.get("Name", "").strip() for p in processors))
        data["cpu"]["physical_cores"] = sum(p.get("NumberOfCores") or 0 for p in processors) or None
        data["cpu"]["logical_cores"] = sum(p.get("NumberOfLogicalProcessors") or 0 for p in processors) or None
        data["cpu"]["clock_mhz"] = max(p.get("MaxClockSpeed") or 0 for p in processors) or None
    for gpu in raw.get("gpus") or []:
        # Win32_VideoController.AdapterRAM is uint32 and truncates large VRAM.
        data["gpus"].append({"name": gpu.get("Name"), "vram_bytes": None, "memory_type": None})
    for item in raw.get("ram") or []:
        data["ram"]["modules"].append({
            "name": (item.get("PartNumber") or "").strip() or None,
            "manufacturer": (item.get("Manufacturer") or "").strip() or None,
            "size_bytes": int(item["Capacity"]) if item.get("Capacity") else None,
            "speed": f"{item['ConfiguredClockSpeed']} MT/s" if item.get("ConfiguredClockSpeed") else None,
            "standard": MEMORY_TYPES.get(item.get("SMBIOSMemoryType")),
        })
    for adapter in raw.get("video_registry") or []:
        for gpu in data["gpus"]:
            if adapter.get("name") == gpu["name"] and adapter.get("bytes"):
                gpu["vram_bytes"] = int(adapter["bytes"])


def linux_hardware(data: dict) -> None:
    base = Path("/sys/class/dmi/id")
    data["computer_model"] = " ".join(filter(None, (read_text(base / "sys_vendor"), read_text(base / "product_name")))) or None
    if data["computer_model"] is None:
        model = read_text(Path("/sys/firmware/devicetree/base/model"))
        data["computer_model"] = model.rstrip("\x00") if model else None
    release = read_text(Path("/etc/os-release")) or ""
    match = re.search(r'^PRETTY_NAME="?(.+?)"?$', release, re.M)
    if match:
        data["os_name"] = match[1]
    lspci = probe(["lspci", "-mm"])
    if lspci:
        for line in lspci.splitlines():
            fields = re.findall(r'"([^"]+)"', line)
            if fields and any(kind in fields[0] for kind in ("VGA", "3D controller", "Display")):
                data["gpus"].append({"name": " ".join(fields[1:3]), "vram_bytes": None, "memory_type": None})
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        if "-" in card.name:
            continue
        device = card / "device"
        vram = read_text(device / "mem_info_vram_total")
        if vram and vram.isdigit():
            slot = device.resolve().name
            name = probe(["lspci", "-s", slot]) or slot
            existing = next((g for g in data["gpus"] if g["name"] in name), None)
            if existing is not None:
                existing["vram_bytes"] = int(vram)
            else:
                data["gpus"].append({"name": name, "vram_bytes": int(vram), "memory_type": "dedicated"})
    # No sudo prompt. Unprivileged SMBIOS access depends on the distribution.
    dmi = probe(["dmidecode", "--type", "17"])
    if dmi:
        data["ram"]["modules"] = parse_dmidecode(dmi)
    if not data["ram"]["modules"]:
        data["notes"].append("RAM module details require readable SMBIOS data; no privilege elevation was attempted.")


def parse_dmidecode(text: str) -> list[dict]:
    modules = []
    for block in text.split("Memory Device")[1:]:
        fields = dict(re.findall(r"^\s+([^:\n]+):\s*(.+)$", block, re.M))
        capacity = size_bytes(fields.get("Size"))
        if not capacity:
            continue
        def known(key):
            value = fields.get(key)
            return None if value in (None, "Unknown", "Not Specified", "NO DIMM") else value
        modules.append({
            "name": known("Part Number"), "manufacturer": known("Manufacturer"),
            "size_bytes": capacity,
            "speed": known("Configured Memory Speed") or known("Speed"),
            "standard": known("Type"),
        })
    return modules


def collect() -> dict:
    try:
        info = cpuinfo.get_cpu_info()
    except Exception:
        info = {}
    try:
        frequency = psutil.cpu_freq()
    except (OSError, NotImplementedError):
        frequency = None
    advertised = info.get("hz_advertised_raw")
    clock = advertised[0] / 1_000_000 if advertised and advertised[0] else None
    system = platform.system()
    # Apple Silicon does not expose clock frequency through psutil reliably.
    if not clock and system != "Darwin" and frequency and frequency.max > 0:
        clock = frequency.max
    data = {
        "computer_model": None,
        "computer_model_identifier": None,
        "cpu": {
            "name": info.get("brand_raw") or platform.processor() or None,
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
            "clock_mhz": clock,
            "clock_source": "advertised_or_os_maximum",
        },
        "gpus": [],
        "ram": {"name": None, "size_bytes": psutil.virtual_memory().total, "speed": None, "standard": None, "modules": []},
        "os_name": platform.platform(),
        "kernel": {"name": {"Darwin": "Darwin", "Linux": "Linux", "Windows": "Windows NT"}.get(system, system), "version": platform.release()},
        "architecture": platform.machine(),
        "notes": [],
    }
    if system == "Darwin":
        frequency_hz = probe(["sysctl", "-n", "hw.cpufrequency"])
        if frequency_hz and frequency_hz.isdigit():
            data["cpu"]["clock_mhz"] = int(frequency_hz) / 1_000_000
        version = probe(["sw_vers", "-productVersion"])
        data["os_name"] = f"macOS {version}" if version else "macOS"
        # Only select non-identifying fields; do not retain serial numbers.
        raw = parse_json(probe(["system_profiler", "-json", "SPHardwareDataType", "SPDisplaysDataType", "SPMemoryDataType"]))
        if isinstance(raw, dict):
            mac_hardware(data, raw)
        data["kernel"]["version"] = platform.release()
    elif system == "Windows":
        script = """
$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8;
@{
 computer=Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model;
 os=Get-CimInstance Win32_OperatingSystem | Select-Object Caption;
 cpus=@(Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed);
 gpus=@(Get-CimInstance Win32_VideoController | Select-Object Name);
 ram=@(Get-CimInstance Win32_PhysicalMemory | Select-Object PartNumber,Manufacturer,Capacity,ConfiguredClockSpeed,SMBIOSMemoryType);
 video_registry=@(Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Video\\*\\0000' -ErrorAction SilentlyContinue | ForEach-Object { @{name=$_.DriverDesc; bytes=$_.'HardwareInformation.qwMemorySize'} });
} | ConvertTo-Json -Depth 5 -Compress
"""
        raw = parse_json(probe(["powershell", "-NoProfile", "-NonInteractive", "-Command", script]))
        if isinstance(raw, dict):
            windows_hardware(data, raw)
        data["kernel"]["version"] = platform.version()
    elif system == "Linux":
        linux_hardware(data)
    nvidia = probe(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if nvidia:
        for row in csv.reader(io.StringIO(nvidia)):
            if len(row) != 2:
                continue
            try:
                vram = int(row[1].strip()) * 1024 ** 2
            except ValueError:
                continue
            name = row[0].strip()
            existing = next((g for g in data["gpus"] if name == g["name"]), None)
            if existing is not None:
                existing["vram_bytes"] = vram
            else:
                data["gpus"].append({"name": name, "vram_bytes": vram, "memory_type": "dedicated"})
    for field in ("name", "speed", "standard"):
        values = list(dict.fromkeys(m[field] for m in data["ram"]["modules"] if m.get(field)))
        if values:
            data["ram"][field] = ", ".join(values)
    data["notes"].append("Unavailable hardware fields are null. Clock is advertised or OS maximum, not a measured sustained frequency.")
    return data
