"""
Lina — Сканер сети для предустановочного режима.

Обнаруживает и анализирует:
  - Сетевые интерфейсы (ip link, nmcli)
  - Подключение к интернету (ping, DNS)
  - Wi-Fi сети (iwlist, nmcli)
  - Рекомендации по сети (VPN, DNS, зеркала)

Все данные собираются через subprocess без сторонних зависимостей.
"""

import subprocess
import re
from typing import Dict, List, Optional


class NetworkScanner:
    """
    Сканер сети для Live-USB окружения.

    Определяет сетевые интерфейсы, подключение, Wi-Fi,
    и даёт рекомендации по настройке сети для установки.
    """

    def __init__(self):
        self._cache: Dict[str, object] = {}

    # ── Утилиты ──

    def _run(self, cmd: str, timeout: int = 10) -> str:
        """Выполняет shell-команду и возвращает stdout."""
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True,
                text=True, timeout=timeout,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, Exception):
            return ""

    # ── Сетевые интерфейсы ──

    def get_interfaces(self) -> List[Dict]:
        """
        Список сетевых интерфейсов.

        Returns:
            Список dict: name, state, mac, type, ip
        """
        if "interfaces" in self._cache:
            return self._cache["interfaces"]

        interfaces = []

        # ip link show
        output = self._run("ip -o link show 2>/dev/null")
        if output:
            for line in output.splitlines():
                match = re.match(r'\d+:\s+(\S+):', line)
                if not match:
                    continue

                name = match.group(1)
                if name == "lo":
                    continue

                iface = {
                    "name": name,
                    "state": "DOWN",
                    "mac": "",
                    "type": "ethernet",
                    "ip": "",
                }

                if "state UP" in line:
                    iface["state"] = "UP"
                elif "state DOWN" in line:
                    iface["state"] = "DOWN"

                mac_m = re.search(r'link/ether\s+(\S+)', line)
                if mac_m:
                    iface["mac"] = mac_m.group(1)

                # Wi-Fi?
                if name.startswith("wl") or "wifi" in name.lower():
                    iface["type"] = "wifi"
                elif name.startswith("eth") or name.startswith("en"):
                    iface["type"] = "ethernet"
                elif name.startswith("br"):
                    iface["type"] = "bridge"
                elif name.startswith("docker") or name.startswith("veth"):
                    iface["type"] = "virtual"

                # IP-адрес
                ip_out = self._run(f"ip -4 addr show {name} 2>/dev/null")
                ip_m = re.search(r'inet\s+(\S+)', ip_out)
                if ip_m:
                    iface["ip"] = ip_m.group(1)

                interfaces.append(iface)

        self._cache["interfaces"] = interfaces
        return interfaces

    # ── Подключение ──

    def check_connectivity(self) -> Dict:
        """
        Проверяет подключение к интернету.

        Returns:
            dict: internet (bool), dns (bool), latency_ms, dns_servers
        """
        result = {
            "internet": False,
            "dns": False,
            "latency_ms": None,
            "dns_servers": [],
        }

        # Ping по IP (без DNS)
        ping_out = self._run("ping -c 2 -W 3 8.8.8.8 2>/dev/null")
        if "bytes from" in ping_out:
            result["internet"] = True
            # Извлекаем задержку
            lat_m = re.search(r'avg[^/]*/(\d+(?:\.\d+)?)', ping_out)
            if lat_m:
                result["latency_ms"] = float(lat_m.group(1))

        # DNS-резолвинг
        dns_out = self._run("ping -c 1 -W 3 google.com 2>/dev/null")
        if "bytes from" in dns_out:
            result["dns"] = True

        # DNS-серверы
        resolv = self._run("cat /etc/resolv.conf 2>/dev/null")
        if resolv:
            for line in resolv.splitlines():
                if line.strip().startswith("nameserver"):
                    ns = line.split()[-1]
                    result["dns_servers"].append(ns)

        return result

    # ── Wi-Fi ──

    def scan_wifi(self) -> List[Dict]:
        """
        Сканирует доступные Wi-Fi сети.

        Returns:
            Список dict: ssid, signal, security, frequency
        """
        networks = []

        # nmcli (NetworkManager)
        output = self._run(
            "nmcli -t -f SSID,SIGNAL,SECURITY,FREQ dev wifi list 2>/dev/null"
        )

        if output:
            for line in output.splitlines():
                parts = line.split(":")
                if len(parts) >= 3 and parts[0]:
                    net = {
                        "ssid": parts[0],
                        "signal": parts[1] if len(parts) > 1 else "",
                        "security": parts[2] if len(parts) > 2 else "Open",
                        "frequency": parts[3] if len(parts) > 3 else "",
                    }
                    networks.append(net)
        else:
            # Fallback: iwlist
            iw_out = self._run(
                "sudo iwlist scan 2>/dev/null | grep -E 'ESSID|Quality|Encryption'"
            )
            if iw_out:
                current = {}
                for line in iw_out.splitlines():
                    line = line.strip()
                    if "ESSID:" in line:
                        ssid_m = re.search(r'ESSID:"([^"]*)"', line)
                        if ssid_m and current.get("ssid"):
                            networks.append(current)
                        current = {"ssid": ssid_m.group(1) if ssid_m else "", "signal": "", "security": "", "frequency": ""}
                    elif "Quality" in line:
                        qual_m = re.search(r'Quality=(\S+)', line)
                        if qual_m:
                            current["signal"] = qual_m.group(1)
                    elif "Encryption" in line:
                        current["security"] = "WPA" if "on" in line.lower() else "Open"
                if current.get("ssid"):
                    networks.append(current)

        return networks

    # ── Сводка ──

    def network_setup(self) -> str:
        """
        Полная сводка сетевой конфигурации.

        Returns:
            Форматированная строка с анализом сети.
        """
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║      🌐 Настройка сети — Предустановка          ║")
        lines.append("╠══════════════════════════════════════════════════╣")

        # Интерфейсы
        ifaces = self.get_interfaces()
        if ifaces:
            lines.append("║  📡 Сетевые интерфейсы:")
            for iface in ifaces:
                state_icon = "🟢" if iface["state"] == "UP" else "🔴"
                ip_str = f" [{iface['ip']}]" if iface["ip"] else ""
                lines.append(
                    f"║    {state_icon} {iface['name']} ({iface['type']}){ip_str}"
                )
        else:
            lines.append("║  ❌ Сетевые интерфейсы не обнаружены")

        # Подключение
        conn = self.check_connectivity()
        lines.append("║")
        lines.append("║  🔌 Подключение:")
        if conn["internet"]:
            lat = f" ({conn['latency_ms']:.0f} ms)" if conn["latency_ms"] else ""
            lines.append(f"║    ✅ Интернет: подключен{lat}")
        else:
            lines.append("║    ❌ Интернет: нет подключения")

        if conn["dns"]:
            lines.append("║    ✅ DNS: работает")
        else:
            lines.append("║    ❌ DNS: не работает")

        if conn["dns_servers"]:
            dns_str = ", ".join(conn["dns_servers"][:3])
            lines.append(f"║    📋 DNS-серверы: {dns_str}")

        # Wi-Fi сети
        wifi_ifaces = [i for i in ifaces if i["type"] == "wifi"]
        if wifi_ifaces:
            networks = self.scan_wifi()
            if networks:
                lines.append("║")
                lines.append(f"║  📶 Wi-Fi сети ({len(networks)}):")
                for net in networks[:10]:
                    sec = f" [{net['security']}]" if net["security"] else ""
                    sig = f" {net['signal']}%" if net["signal"] else ""
                    lines.append(f"║    • {net['ssid']}{sig}{sec}")

        # Рекомендации
        lines.append("║")
        lines.append("║  💡 Рекомендации:")
        if not conn["internet"]:
            lines.append("║    ⚠ Подключите кабель или настройте Wi-Fi")
            lines.append("║    ⚠ Без интернета обновления не будут загружены")
        else:
            lines.append("║    ✅ Интернет есть — установка пакетов доступна")

        # Проверяем зеркала
        if conn["internet"]:
            lines.append("║    💡 Рекомендуется выбрать ближайшее зеркало репозитория")

        # DNS рекомендации
        if conn["dns_servers"]:
            slow_dns = [s for s in conn["dns_servers"] if s.startswith("192.168")]
            if slow_dns:
                lines.append("║    💡 Рассмотрите публичные DNS: 8.8.8.8, 1.1.1.1")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    def clear_cache(self) -> None:
        """Очищает кэш сканирования."""
        self._cache.clear()
