"""
Lina — Сетевая диагностика и управление.

Функции:
  - Информация об интерфейсах (ip, nmcli)
  - WiFi-сети и подключения
  - Проверка интернета (ping, DNS, HTTP)
  - Firewall-правила
  - Открытые порты
  - Диагностика "нет интернета"

Все операции — read-only через subprocess.
При необходимости — делегирует к preinstall/network.py.
"""

import subprocess
import re
from typing import Dict, List, Optional


def _run(cmd: str, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True,
            timeout=timeout,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _run_rc(cmd: str, timeout: int = 10) -> int:
    """Возвращает код возврата команды."""
    try:
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True,
            timeout=timeout,
        )
        return result.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return -1


def _run_lines(cmd: str, timeout: int = 10) -> List[str]:
    out = _run(cmd, timeout)
    return [l for l in out.split("\n") if l.strip()] if out else []


class NetworkDiagnostics:
    """Сетевая диагностика Linux."""

    def get_interfaces(self) -> List[Dict]:
        """
        Список сетевых интерфейсов.

        Returns:
            [{name, state, mac, ipv4, ipv6, type}, ...]
        """
        lines = _run_lines("ip -brief addr 2>/dev/null")
        interfaces = []
        for line in lines:
            parts = line.split(None, 2)
            if len(parts) >= 2:
                name = parts[0]
                state = parts[1]
                addrs = parts[2].strip() if len(parts) > 2 else ""

                # Тип интерфейса
                itype = "unknown"
                if name.startswith("wl") or name.startswith("wlan"):
                    itype = "wifi"
                elif name.startswith("en") or name.startswith("eth"):
                    itype = "ethernet"
                elif name == "lo":
                    itype = "loopback"
                elif name.startswith("br"):
                    itype = "bridge"
                elif name.startswith("veth") or name.startswith("docker"):
                    itype = "virtual"
                elif name.startswith("tun") or name.startswith("wg"):
                    itype = "vpn"

                # MAC
                mac = _run(f"cat /sys/class/net/{name}/address 2>/dev/null")

                # Разбираем адреса
                ipv4 = []
                ipv6 = []
                for addr in addrs.split():
                    addr_clean = addr.split("/")[0]
                    if ":" in addr_clean:
                        ipv6.append(addr)
                    elif re.match(r'\d+\.\d+\.\d+\.\d+', addr_clean):
                        ipv4.append(addr)

                interfaces.append({
                    "name": name,
                    "state": state,
                    "type": itype,
                    "mac": mac,
                    "ipv4": ipv4,
                    "ipv6": ipv6,
                })

        return interfaces

    def get_wifi_networks(self) -> List[Dict]:
        """
        Доступные WiFi-сети (nmcli).

        Returns:
            [{ssid, signal, security, frequency, bssid, in_use}, ...]
        """
        lines = _run_lines(
            "nmcli -t -f SSID,SIGNAL,SECURITY,FREQ,BSSID,IN-USE device wifi list 2>/dev/null"
        )
        networks = []
        seen_ssids = set()
        for line in lines:
            parts = line.split(":")
            if len(parts) >= 4:
                ssid = parts[0].strip()
                if not ssid or ssid in seen_ssids:
                    continue
                seen_ssids.add(ssid)
                networks.append({
                    "ssid": ssid,
                    "signal": int(parts[1]) if parts[1].isdigit() else 0,
                    "security": parts[2] if len(parts) > 2 else "",
                    "frequency": parts[3] if len(parts) > 3 else "",
                    "bssid": parts[4] if len(parts) > 4 else "",
                    "in_use": parts[5].strip() == "*" if len(parts) > 5 else False,
                })
        return sorted(networks, key=lambda x: x["signal"], reverse=True)

    def get_active_connections(self) -> List[Dict]:
        """
        Активные сетевые подключения (nmcli).

        Returns:
            [{name, type, device, state}, ...]
        """
        lines = _run_lines(
            "nmcli -t -f NAME,TYPE,DEVICE,STATE connection show --active 2>/dev/null"
        )
        connections = []
        for line in lines:
            parts = line.split(":")
            if len(parts) >= 3:
                connections.append({
                    "name": parts[0],
                    "type": parts[1],
                    "device": parts[2] if len(parts) > 2 else "",
                    "state": parts[3] if len(parts) > 3 else "",
                })
        return connections

    def check_internet(self) -> Dict:
        """
        Комплексная проверка интернета.

        Returns:
            {ping_ok, dns_ok, http_ok, ping_ms, dns_server, details}
        """
        result = {
            "ping_ok": False,
            "dns_ok": False,
            "http_ok": False,
            "ping_ms": "",
            "dns_server": "",
            "details": [],
        }

        # 1. Ping
        ping = _run("ping -c 1 -W 3 8.8.8.8 2>/dev/null")
        if "time=" in ping:
            result["ping_ok"] = True
            match = re.search(r'time=(\S+)', ping)
            result["ping_ms"] = match.group(1) if match else ""
            result["details"].append("✅ Ping 8.8.8.8 — OK")
        else:
            result["details"].append("❌ Ping 8.8.8.8 — Failed")

        # 2. DNS
        dns_out = _run("nslookup google.com 2>/dev/null", timeout=5)
        if "Address" in dns_out and "NXDOMAIN" not in dns_out:
            result["dns_ok"] = True
            result["details"].append("✅ DNS resolution — OK")
        else:
            result["details"].append("❌ DNS resolution — Failed")

        dns_lines = _run_lines("grep '^nameserver' /etc/resolv.conf 2>/dev/null")
        if dns_lines:
            result["dns_server"] = dns_lines[0].split()[-1] if dns_lines[0].split() else ""

        # 3. HTTP
        try:
            from lina.utils.http import http_check
            result["http_ok"] = http_check("http://google.com", timeout=5)
        except Exception:
            result["http_ok"] = False
        if result["http_ok"]:
            result["details"].append("✅ HTTP connection — OK")
        else:
            result["details"].append("❌ HTTP connection — Failed")

        return result

    def check_dns(self) -> Dict:
        """
        Проверка DNS.

        Returns:
            {working, servers, resolve_time_ms, resolv_conf}
        """
        info = {"working": False, "servers": [], "resolve_time_ms": "", "resolv_conf": ""}

        # Серверы
        dns_lines = _run_lines("grep '^nameserver' /etc/resolv.conf 2>/dev/null")
        info["servers"] = [l.split()[-1] for l in dns_lines if l.strip()]

        # resolv.conf содержимое
        info["resolv_conf"] = _run("cat /etc/resolv.conf 2>/dev/null")

        # Тест резолва
        out = _run("dig +time=3 +tries=1 google.com 2>/dev/null", timeout=5)
        if "ANSWER SECTION" in out:
            info["working"] = True
            match = re.search(r'Query time:\s+(\d+)', out)
            if match:
                info["resolve_time_ms"] = match.group(1)
        else:
            # Фолбэк
            nslookup = _run("nslookup google.com 2>/dev/null", timeout=5)
            info["working"] = "Address" in nslookup and "NXDOMAIN" not in nslookup

        return info

    def get_firewall_rules(self) -> Dict:
        """
        Правила файрвола.

        Returns:
            {backend, rules, active}
        """
        info = {"backend": "none", "rules": [], "active": False}

        # ufw?
        ufw = _run("ufw status 2>/dev/null")
        if ufw and "Status: active" in ufw:
            info["backend"] = "ufw"
            info["active"] = True
            info["rules"] = _run_lines("ufw status numbered 2>/dev/null")
            return info

        # firewalld?
        fwd = _run("firewall-cmd --state 2>/dev/null")
        if fwd == "running":
            info["backend"] = "firewalld"
            info["active"] = True
            info["rules"] = _run_lines("firewall-cmd --list-all 2>/dev/null")
            return info

        # nftables?
        nft = _run("nft list ruleset 2>/dev/null")
        if nft:
            info["backend"] = "nftables"
            info["active"] = True
            info["rules"] = nft.split("\n")[:50]
            return info

        # iptables fallback
        ipt = _run("iptables -L -n --line-numbers 2>/dev/null")
        if ipt:
            info["backend"] = "iptables"
            info["active"] = True
            info["rules"] = ipt.split("\n")[:50]

        return info

    def get_open_ports(self) -> List[Dict]:
        """
        Открытые (слушающие) порты.

        Returns:
            [{protocol, address, port, pid, process}, ...]
        """
        lines = _run_lines("ss -tlnp 2>/dev/null")
        ports = []
        for line in lines[1:]:  # skip header
            parts = line.split()
            if len(parts) >= 5:
                # Парсим local address
                local = parts[3]
                match = re.match(r'(.+):(\d+)$', local)
                if match:
                    addr = match.group(1)
                    port = match.group(2)
                else:
                    addr = local
                    port = ""

                # Process
                proc_match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', parts[-1]) if len(parts) > 5 else None

                ports.append({
                    "protocol": "tcp",
                    "address": addr,
                    "port": port,
                    "pid": proc_match.group(2) if proc_match else "",
                    "process": proc_match.group(1) if proc_match else "",
                })

        # UDP
        lines_udp = _run_lines("ss -ulnp 2>/dev/null")
        for line in lines_udp[1:]:
            parts = line.split()
            if len(parts) >= 5:
                local = parts[4]
                match = re.match(r'(.+):(\d+)$', local)
                if match:
                    ports.append({
                        "protocol": "udp",
                        "address": match.group(1),
                        "port": match.group(2),
                        "pid": "",
                        "process": "",
                    })

        return ports

    def get_wifi_signal(self) -> Dict:
        """
        Уровень сигнала текущего WiFi.

        Returns:
            {ssid, signal, frequency, bitrate, link_quality}
        """
        info = {"ssid": "", "signal": 0, "frequency": "", "bitrate": ""}

        # nmcli
        active = _run("nmcli -t -f SSID,SIGNAL,FREQ connection show --active 2>/dev/null")
        if not active:
            # iwconfig fallback
            iw = _run("iwconfig 2>/dev/null | grep -A5 'ESSID'")
            if iw:
                ssid_match = re.search(r'ESSID:"([^"]+)"', iw)
                if ssid_match:
                    info["ssid"] = ssid_match.group(1)
                signal_match = re.search(r'Signal level=(-?\d+)', iw)
                if signal_match:
                    info["signal"] = int(signal_match.group(1))
            return info

        parts = active.split(":")
        if parts:
            info["ssid"] = parts[0]
            info["signal"] = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            info["frequency"] = parts[2] if len(parts) > 2 else ""

        return info

    def diagnose_no_internet(self) -> Dict:
        """
        Пошаговая диагностика "нет интернета".

        Проверяет: интерфейс → IP → шлюз → DNS → ping → HTTP.

        Returns:
            {steps, diagnosis, suggestion, severity}
        """
        steps = []
        diagnosis = ""
        suggestion = ""
        severity = "unknown"

        # Step 1: Интерфейс есть?
        interfaces = self.get_interfaces()
        active_ifaces = [i for i in interfaces
                         if i["state"] == "UP" and i["type"] != "loopback"]
        if not active_ifaces:
            steps.append({"check": "Активные интерфейсы", "status": "FAIL", "detail": "Нет активных сетевых интерфейсов"})
            return {
                "steps": steps,
                "diagnosis": "Нет активных сетевых интерфейсов",
                "suggestion": "Проверьте: ip link show. Включите интерфейс: sudo ip link set <iface> up",
                "severity": "critical",
            }
        steps.append({"check": "Активные интерфейсы", "status": "OK",
                       "detail": f"{len(active_ifaces)} интерфейс(ов): {', '.join(i['name'] for i in active_ifaces)}"})

        # Step 2: Есть IP?
        has_ip = any(i["ipv4"] for i in active_ifaces)
        if not has_ip:
            steps.append({"check": "IP-адрес", "status": "FAIL", "detail": "Нет IPv4-адреса"})
            return {
                "steps": steps,
                "diagnosis": "Нет IP-адреса. DHCP не работает или не настроена статика.",
                "suggestion": "sudo dhclient <iface> или проверьте NetworkManager: nmcli connection show",
                "severity": "critical",
            }
        steps.append({"check": "IP-адрес", "status": "OK",
                       "detail": f"IP: {active_ifaces[0]['ipv4']}"})

        # Step 3: Шлюз?
        gw = _run("ip route | grep default | head -1")
        if not gw:
            steps.append({"check": "Шлюз (Gateway)", "status": "FAIL", "detail": "Нет default gateway"})
            return {
                "steps": steps,
                "diagnosis": "Нет шлюза по умолчанию.",
                "suggestion": "sudo ip route add default via <gateway_ip>",
                "severity": "critical",
            }
        gw_match = re.search(r'via\s+(\S+)', gw)
        gw_ip = gw_match.group(1) if gw_match else "?"
        steps.append({"check": "Шлюз", "status": "OK", "detail": f"Gateway: {gw_ip}"})

        # Step 4: Ping gateway
        gw_ping = _run_rc(f"ping -c 1 -W 2 {gw_ip} 2>/dev/null")
        if gw_ping != 0:
            steps.append({"check": "Ping шлюза", "status": "FAIL", "detail": f"Пинг {gw_ip} не прошёл"})
            return {
                "steps": steps,
                "diagnosis": f"Шлюз {gw_ip} недоступен. Проблема на уровне L2/L3.",
                "suggestion": "Проверьте кабель/WiFi подключение и настройки роутера",
                "severity": "high",
            }
        steps.append({"check": "Ping шлюза", "status": "OK", "detail": f"Шлюз {gw_ip} доступен"})

        # Step 5: Ping внешний IP
        ext_ping = _run_rc("ping -c 1 -W 3 8.8.8.8 2>/dev/null")
        if ext_ping != 0:
            steps.append({"check": "Ping 8.8.8.8", "status": "FAIL", "detail": "Нет доступа к интернету"})
            return {
                "steps": steps,
                "diagnosis": "Шлюз доступен, но интернет — нет. Проблема на роутере или у провайдера.",
                "suggestion": "Перезагрузите роутер. Проверьте: traceroute 8.8.8.8",
                "severity": "high",
            }
        steps.append({"check": "Ping 8.8.8.8", "status": "OK", "detail": "Интернет через IP работает"})

        # Step 6: DNS
        dns_out = _run("nslookup google.com 2>/dev/null", timeout=5)
        if "Address" not in dns_out or "NXDOMAIN" in dns_out:
            steps.append({"check": "DNS", "status": "FAIL", "detail": "DNS не работает"})
            return {
                "steps": steps,
                "diagnosis": "DNS не работает. IP-доступ есть.",
                "suggestion": "Временное решение: echo 'nameserver 8.8.8.8' | sudo tee /etc/resolv.conf",
                "severity": "medium",
            }
        steps.append({"check": "DNS", "status": "OK", "detail": "DNS работает"})

        # Step 7: HTTP
        try:
            from lina.utils.http import http_check
            _http_ok = http_check("http://google.com", timeout=5)
        except Exception:
            _http_ok = False
        if not _http_ok:
            steps.append({"check": "HTTP", "status": "FAIL", "detail": "HTTP-запрос не прошёл"})
            return {
                "steps": steps,
                "diagnosis": "Ping и DNS работают, но HTTP заблокирован. Возможно, прокси или firewall.",
                "suggestion": "Проверьте proxy: echo $http_proxy. Проверьте firewall: sudo iptables -L",
                "severity": "medium",
            }
        steps.append({"check": "HTTP", "status": "OK", "detail": "HTTP работает"})

        return {
            "steps": steps,
            "diagnosis": "Интернет работает нормально.",
            "suggestion": "",
            "severity": "ok",
        }

    def format_status(self) -> str:
        """Форматирует сетевой статус в текст."""
        interfaces = self.get_interfaces()
        check = self.check_internet()

        lines = ["═══ Сетевой статус ═══", ""]

        for iface in interfaces:
            if iface["type"] == "loopback":
                continue
            icon = "✅" if iface["state"] == "UP" else "❌"
            ips = ", ".join(iface["ipv4"]) if iface["ipv4"] else "no IP"
            lines.append(f"{icon} {iface['name']} ({iface['type']}): {iface['state']} — {ips}")

        lines.append("")
        inet = "✅" if check["ping_ok"] else "❌"
        dns = "✅" if check["dns_ok"] else "❌"
        http = "✅" if check["http_ok"] else "❌"
        lines.append(f"Ping: {inet} | DNS: {dns} | HTTP: {http}")
        if check["ping_ms"]:
            lines.append(f"Latency: {check['ping_ms']} ms")

        return "\n".join(lines)
