import unittest
from types import SimpleNamespace
from unittest.mock import patch

from devmark.hardware import collect, mac_hardware, parse_dmidecode, size_bytes, windows_hardware


def blank():
    return {"cpu": {"name": None}, "ram": {"modules": []}, "gpus": [], "os_name": "Windows", "notes": []}


class HardwareTests(unittest.TestCase):
    def test_unavailable_apple_clock_does_not_use_unreliable_psutil_frequency(self):
        with (
            patch("devmark.hardware.platform.system", return_value="Darwin"),
            patch("devmark.hardware.cpuinfo.get_cpu_info", return_value={}),
            patch("devmark.hardware.psutil.cpu_freq", return_value=SimpleNamespace(max=4)),
            patch("devmark.hardware.probe", return_value=None),
        ):
            self.assertIsNone(collect()["cpu"]["clock_mhz"])

    def test_memory_sizes_and_unknown_values(self):
        self.assertEqual(size_bytes("16 GB"), 16 * 1024 ** 3)
        self.assertEqual(size_bytes("8192 MB"), 8 * 1024 ** 3)
        self.assertIsNone(size_bytes("No Module Installed"))

    def test_unified_memory_does_not_become_fake_vram(self):
        data = blank()
        mac_hardware(data, {
            "SPHardwareDataType": [{"machine_name": "MacBook Pro", "chip_type": "Apple M4", "physical_memory": "32 GB"}],
            "SPDisplaysDataType": [{"sppci_model": "Apple M4"}],
        })
        self.assertEqual(data["gpus"][0]["name"], "Apple M4")
        self.assertIsNone(data["gpus"][0]["vram_bytes"])
        self.assertEqual(data["gpus"][0]["memory_type"], "unified")

    def test_windows_uses_64_bit_vram_instead_of_truncated_cim_value(self):
        data = blank()
        windows_hardware(data, {
            "gpus": [{"Name": "Test GPU", "AdapterRAM": 0}],
            "video_registry": [{"name": "Test GPU", "bytes": 24 * 1024 ** 3}],
            "ram": [{"PartNumber": " DIMM-123 ", "Capacity": "17179869184", "ConfiguredClockSpeed": 6000, "SMBIOSMemoryType": 34}],
        })
        self.assertEqual(data["gpus"][0]["vram_bytes"], 24 * 1024 ** 3)
        self.assertEqual(data["ram"]["modules"][0]["standard"], "DDR5")
        self.assertEqual(data["ram"]["modules"][0]["name"], "DIMM-123")

    def test_dmi_ignores_empty_slots(self):
        modules = parse_dmidecode("""
Memory Device
    Size: 16 GB
    Type: DDR5
    Manufacturer: Vendor
    Part Number: DIMM-123
    Speed: 6000 MT/s
    Configured Memory Speed: 5600 MT/s
Memory Device
    Size: No Module Installed
    Type: Unknown
""")
        self.assertEqual(len(modules), 1)
        self.assertEqual(modules[0]["size_bytes"], 16 * 1024 ** 3)
        self.assertEqual(modules[0]["speed"], "5600 MT/s")


if __name__ == "__main__":
    unittest.main()
