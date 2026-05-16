"""
Lina — Предустановочный помощник Linux.

Модуль для работы в Live-USB окружении: сбор информации
о железе, дисках, сети, рекомендации по установке.

Компоненты:
  - hardware: обнаружение CPU, GPU, RAM, дисков, разделов, UEFI/BIOS
  - network:  сетевые интерфейсы, подключение, DNS, VPN
  - guide:    пошаговые инструкции, рекомендации пакетов, FAQ, тюнинг
"""

from lina.preinstall.hardware import HardwareScanner
from lina.preinstall.network import NetworkScanner
from lina.preinstall.guide import InstallGuide

__all__ = ["HardwareScanner", "NetworkScanner", "InstallGuide"]
