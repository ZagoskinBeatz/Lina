"""
InstallerMode — режим инсталлятора (Live-ISO помощник).

Полный цикл установки Arch-based системы:
  INIT → SCAN_HARDWARE → PARTITION → FORMAT → MOUNT
  → PACSTRAP → CONFIGURE → BOOTLOADER → NETWORK_SETUP
  → FINALIZE → REBOOT → COMPLETE

Каждый этап:
  1. Проходит через StateMachine
  2. Исполнение через ActionRegistry
  3. Ошибки через SignatureCollector
  4. Решения из KB

Phase: GOVERNANCE LAYER / Installer Mode
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class HardwareInfo:
    """Информация об оборудовании."""
    cpu: str = ""
    cpu_cores: int = 0
    ram_mb: int = 0
    gpu: str = ""
    gpu_vendor: str = ""        # nvidia, amd, intel
    disks: List[Dict[str, Any]] = field(default_factory=list)
    partitions: List[Dict[str, Any]] = field(default_factory=list)
    efi: bool = False
    network_interfaces: List[str] = field(default_factory=list)
    wifi_available: bool = False
    audio_devices: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu": self.cpu, "cpu_cores": self.cpu_cores,
            "ram_mb": self.ram_mb, "gpu": self.gpu,
            "gpu_vendor": self.gpu_vendor, "efi": self.efi,
            "disks": self.disks, "partitions": self.partitions,
            "network_interfaces": self.network_interfaces,
            "wifi_available": self.wifi_available,
        }


@dataclass
class InstallPlan:
    """План установки."""
    target_disk: str = ""
    partition_scheme: str = "auto"   # auto | manual | btrfs
    filesystem: str = "btrfs"        # btrfs | ext4
    btrfs_subvols: List[str] = field(default_factory=lambda: [
        "@", "@home", "@var", "@snapshots",
    ])
    mount_root: str = "/mnt"
    bootloader: str = "grub"          # grub | systemd-boot
    hostname: str = "archlinux"
    locale: str = "en_US.UTF-8"
    timezone: str = "UTC"
    keyboard: str = "us"
    username: str = ""
    packages_base: List[str] = field(default_factory=lambda: [
        "base", "base-devel", "linux", "linux-firmware",
        "networkmanager", "grub", "efibootmgr",
    ])
    packages_extra: List[str] = field(default_factory=list)
    enable_services: List[str] = field(default_factory=lambda: [
        "NetworkManager", "systemd-timesyncd",
    ])
    gpu_driver: str = ""              # nvidia | mesa | xf86-video-amdgpu

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_disk": self.target_disk,
            "partition_scheme": self.partition_scheme,
            "filesystem": self.filesystem,
            "bootloader": self.bootloader,
            "hostname": self.hostname,
            "locale": self.locale,
            "timezone": self.timezone,
            "packages_base": self.packages_base,
            "gpu_driver": self.gpu_driver,
        }


@dataclass
class StageResult:
    """Результат одного этапа установки."""
    stage: str
    success: bool = True
    command: str = ""
    output: str = ""
    error: str = ""
    duration: float = 0.0
    rollback_cmd: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage, "success": self.success,
            "output": self.output[:300], "error": self.error[:300],
            "duration": round(self.duration, 2),
        }


# ─── InstallerMode ────────────────────────────────────────────────────────────

class InstallerMode:
    """
    Управляет процессом установки Arch-based системы.

    Пример:
        installer = InstallerMode()
        hw = installer.scan_hardware()
        plan = installer.create_plan(hw)
        installer.execute_plan(plan)
    """

    def __init__(self) -> None:
        self._hw: Optional[HardwareInfo] = None
        self._plan: Optional[InstallPlan] = None
        self._stages: List[StageResult] = []
        self._current_stage: str = "init"
        self._callbacks: Dict[str, Callable] = {}

    # ── Hardware Scan ────────────────────────────────────

    def scan_hardware(self) -> HardwareInfo:
        """Сканирование оборудования."""
        logger.info("InstallerMode: scanning hardware...")
        hw = HardwareInfo()

        # CPU
        hw.cpu = self._cmd("lscpu | grep 'Model name' | sed 's/.*: *//'").strip()
        cores = self._cmd("nproc")
        hw.cpu_cores = int(cores) if cores.isdigit() else 0

        # RAM
        mem = self._cmd("grep MemTotal /proc/meminfo | awk '{print $2}'")
        hw.ram_mb = int(int(mem) / 1024) if mem.isdigit() else 0

        # GPU
        gpu_out = self._cmd("lspci -k | grep -A3 -i vga")
        hw.gpu = gpu_out.split("\n")[0] if gpu_out else ""
        if "nvidia" in gpu_out.lower():
            hw.gpu_vendor = "nvidia"
        elif "amd" in gpu_out.lower() or "radeon" in gpu_out.lower():
            hw.gpu_vendor = "amd"
        else:
            hw.gpu_vendor = "intel"

        # EFI
        hw.efi = Path("/sys/firmware/efi").exists()

        # Disks
        disks_json = self._cmd("lsblk -J -o NAME,SIZE,TYPE,MOUNTPOINT 2>/dev/null")
        if disks_json:
            try:
                disk_data = json.loads(disks_json)
                for dev in disk_data.get("blockdevices", []):
                    if dev.get("type") == "disk":
                        hw.disks.append({
                            "name": f"/dev/{dev['name']}",
                            "size": dev.get("size", ""),
                        })
                    if dev.get("type") == "part":
                        hw.partitions.append({
                            "name": f"/dev/{dev['name']}",
                            "size": dev.get("size", ""),
                            "mount": dev.get("mountpoint", ""),
                        })
            except json.JSONDecodeError:
                pass

        # Network
        net_out = self._cmd("ip -br link show | awk '{print $1}'")
        hw.network_interfaces = [i for i in net_out.splitlines() if i and i != "lo"]
        hw.wifi_available = any("wl" in i for i in hw.network_interfaces)

        # Audio
        audio_out = self._cmd("aplay -l 2>/dev/null | grep '^card'")
        hw.audio_devices = [l.strip() for l in audio_out.splitlines() if l.strip()]

        self._hw = hw
        logger.info("InstallerMode: hardware scan complete — "
                     "CPU=%s RAM=%dMB GPU=%s EFI=%s Disks=%d",
                     hw.cpu[:30], hw.ram_mb, hw.gpu_vendor, hw.efi, len(hw.disks))
        return hw

    # ── Plan ─────────────────────────────────────────────

    def create_plan(self, hw: HardwareInfo,
                    overrides: Optional[Dict[str, Any]] = None) -> InstallPlan:
        """Создать план установки на основе оборудования."""
        plan = InstallPlan()

        # Auto-select disk
        if hw.disks:
            plan.target_disk = hw.disks[0]["name"]

        # Bootloader
        if hw.efi:
            plan.bootloader = "grub"  # GRUB EFI

        # GPU driver
        if hw.gpu_vendor == "nvidia":
            plan.gpu_driver = "nvidia"
            plan.packages_extra.append("nvidia")
            plan.packages_extra.append("nvidia-utils")
        elif hw.gpu_vendor == "amd":
            plan.gpu_driver = "mesa"
            plan.packages_extra.append("mesa")
            plan.packages_extra.append("xf86-video-amdgpu")
        else:
            plan.gpu_driver = "mesa"
            plan.packages_extra.append("mesa")

        # WiFi
        if hw.wifi_available:
            plan.packages_extra.append("iwd")

        # Apply overrides
        if overrides:
            for key, value in overrides.items():
                if hasattr(plan, key):
                    setattr(plan, key, value)

        self._plan = plan
        logger.info("InstallerMode: plan created — disk=%s fs=%s boot=%s",
                     plan.target_disk, plan.filesystem, plan.bootloader)
        return plan

    # ── Execute ──────────────────────────────────────────

    def execute_stage(self, stage: str,
                      plan: Optional[InstallPlan] = None) -> StageResult:
        """Выполнить один этап установки."""
        plan = plan or self._plan
        if not plan:
            return StageResult(stage=stage, success=False,
                               error="No install plan")

        t0 = time.monotonic()
        self._current_stage = stage

        handlers = {
            "partition": self._stage_partition,
            "format": self._stage_format,
            "mount": self._stage_mount,
            "pacstrap": self._stage_pacstrap,
            "configure": self._stage_configure,
            "bootloader": self._stage_bootloader,
            "network_setup": self._stage_network_setup,
            "finalize": self._stage_finalize,
        }

        handler = handlers.get(stage)
        if not handler:
            return StageResult(stage=stage, success=False,
                               error=f"Unknown stage: {stage}")

        try:
            result = handler(plan)
        except Exception as e:
            result = StageResult(stage=stage, success=False, error=str(e))

        result.duration = time.monotonic() - t0
        self._stages.append(result)
        return result

    # ── Stage Implementations ────────────────────────────

    def _stage_partition(self, plan: InstallPlan) -> StageResult:
        """Этап: разметка диска."""
        disk = plan.target_disk
        if not disk:
            return StageResult(stage="partition", success=False,
                               error="No target disk specified")

        # Generate partition commands (UEFI + btrfs)
        cmds = []
        if plan.partition_scheme == "auto":
            cmds = [
                f"sgdisk -Z {disk}",
                f"sgdisk -n 1:0:+512M -t 1:ef00 -c 1:'EFI' {disk}",
                f"sgdisk -n 2:0:0 -t 2:8300 -c 2:'ROOT' {disk}",
            ]

        output_parts = []
        for cmd in cmds:
            rc, out = self._run(cmd)
            output_parts.append(out)
            if rc != 0:
                return StageResult(
                    stage="partition", success=False,
                    command=cmd, output="\n".join(output_parts), error=f"rc={rc}",
                    rollback_cmd=f"sgdisk -Z {disk}",
                )

        return StageResult(
            stage="partition", success=True,
            output="\n".join(output_parts),
        )

    def _stage_format(self, plan: InstallPlan) -> StageResult:
        """Этап: форматирование."""
        disk = plan.target_disk
        p1 = f"{disk}1" if disk.startswith("/dev/sd") else f"{disk}p1"
        p2 = f"{disk}2" if disk.startswith("/dev/sd") else f"{disk}p2"

        cmds = [f"mkfs.fat -F32 {p1}"]
        if plan.filesystem == "btrfs":
            cmds.append(f"mkfs.btrfs -f {p2}")
        else:
            cmds.append(f"mkfs.ext4 -F {p2}")

        output_parts = []
        for cmd in cmds:
            rc, out = self._run(cmd)
            output_parts.append(out)
            if rc != 0:
                return StageResult(
                    stage="format", success=False,
                    command=cmd, error=f"rc={rc}",
                )

        return StageResult(stage="format", success=True,
                           output="\n".join(output_parts))

    def _stage_mount(self, plan: InstallPlan) -> StageResult:
        """Этап: монтирование."""
        disk = plan.target_disk
        p2 = f"{disk}2" if disk.startswith("/dev/sd") else f"{disk}p2"
        p1 = f"{disk}1" if disk.startswith("/dev/sd") else f"{disk}p1"
        root = plan.mount_root

        cmds = []
        if plan.filesystem == "btrfs":
            cmds = [
                f"mount {p2} {root}",
            ]
            for sv in plan.btrfs_subvols:
                cmds.append(f"btrfs subvolume create {root}/{sv}")
            cmds.append(f"umount {root}")
            cmds.append(f"mount -o subvol=@,compress=zstd {p2} {root}")
            cmds.append(f"mkdir -p {root}/home {root}/var {root}/.snapshots {root}/boot/efi")
            cmds.append(f"mount -o subvol=@home,compress=zstd {p2} {root}/home")
            cmds.append(f"mount -o subvol=@var,compress=zstd {p2} {root}/var")
            cmds.append(f"mount -o subvol=@snapshots,compress=zstd {p2} {root}/.snapshots")
            cmds.append(f"mount {p1} {root}/boot/efi")
        else:
            cmds = [
                f"mount {p2} {root}",
                f"mkdir -p {root}/boot/efi",
                f"mount {p1} {root}/boot/efi",
            ]

        output_parts = []
        for cmd in cmds:
            rc, out = self._run(cmd)
            output_parts.append(f"$ {cmd}\n{out}")
            if rc != 0:
                return StageResult(
                    stage="mount", success=False,
                    command=cmd, error=f"rc={rc}",
                    rollback_cmd=f"umount -R {root}",
                )

        return StageResult(stage="mount", success=True,
                           output="\n".join(output_parts))

    def _stage_pacstrap(self, plan: InstallPlan) -> StageResult:
        """Этап: установка базовых пакетов."""
        pkgs = " ".join(plan.packages_base + plan.packages_extra)
        cmd = f"pacstrap {plan.mount_root} {pkgs}"
        rc, out = self._run(cmd, timeout=600)
        return StageResult(
            stage="pacstrap", success=rc == 0,
            command=cmd, output=out[-2000:],
            error="" if rc == 0 else f"rc={rc}",
        )

    def _stage_configure(self, plan: InstallPlan) -> StageResult:
        """Этап: конфигурация (fstab, hostname, locale, etc)."""
        root = plan.mount_root
        cmds = [
            f"genfstab -U {root} >> {root}/etc/fstab",
            f"arch-chroot {root} ln -sf /usr/share/zoneinfo/{plan.timezone} /etc/localtime",
            f"arch-chroot {root} hwclock --systohc",
            f"echo '{plan.locale} UTF-8' >> {root}/etc/locale.gen",
            f"arch-chroot {root} locale-gen",
            f"echo 'LANG={plan.locale}' > {root}/etc/locale.conf",
            f"echo '{plan.hostname}' > {root}/etc/hostname",
        ]

        if plan.username:
            cmds.extend([
                f"arch-chroot {root} useradd -m -G wheel {plan.username}",
                f"arch-chroot {root} sed -i 's/# %wheel ALL=(ALL:ALL) ALL/%wheel ALL=(ALL:ALL) ALL/' /etc/sudoers",
            ])

        output_parts = []
        for cmd in cmds:
            rc, out = self._run(cmd, timeout=30)
            output_parts.append(out)
            if rc != 0:
                return StageResult(
                    stage="configure", success=False,
                    command=cmd, error=f"rc={rc}",
                )

        return StageResult(stage="configure", success=True,
                           output="\n".join(output_parts))

    def _stage_bootloader(self, plan: InstallPlan) -> StageResult:
        """Этап: установка загрузчика."""
        root = plan.mount_root
        cmds = []
        if plan.bootloader == "grub":
            cmds = [
                f"arch-chroot {root} grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=GRUB",
                f"arch-chroot {root} grub-mkconfig -o /boot/grub/grub.cfg",
            ]
        elif plan.bootloader == "systemd-boot":
            cmds = [
                f"arch-chroot {root} bootctl install",
            ]

        output_parts = []
        for cmd in cmds:
            rc, out = self._run(cmd, timeout=60)
            output_parts.append(out)
            if rc != 0:
                return StageResult(
                    stage="bootloader", success=False,
                    command=cmd, error=f"rc={rc}",
                )

        return StageResult(stage="bootloader", success=True,
                           output="\n".join(output_parts))

    def _stage_network_setup(self, plan: InstallPlan) -> StageResult:
        """Этап: настройка сети."""
        root = plan.mount_root
        cmds = []
        for svc in plan.enable_services:
            cmds.append(f"arch-chroot {root} systemctl enable {svc}")

        output_parts = []
        for cmd in cmds:
            rc, out = self._run(cmd)
            output_parts.append(out)
            if rc != 0:
                return StageResult(
                    stage="network_setup", success=False,
                    command=cmd, error=f"rc={rc}",
                )

        return StageResult(stage="network_setup", success=True,
                           output="\n".join(output_parts))

    def _stage_finalize(self, plan: InstallPlan) -> StageResult:
        """Этап: финализация."""
        root = plan.mount_root
        cmds = [
            f"arch-chroot {root} mkinitcpio -P",
            f"umount -R {root}",
        ]

        output_parts = []
        for cmd in cmds:
            rc, out = self._run(cmd, timeout=120)
            output_parts.append(out)
            if rc != 0:
                logger.warning("Finalize cmd failed: %s rc=%d", cmd, rc)

        return StageResult(stage="finalize", success=True,
                           output="\n".join(output_parts))

    # ── Util ─────────────────────────────────────────────

    @staticmethod
    def _cmd(cmd: str, timeout: int = 15) -> str:
        """Выполнить команду и вернуть stdout."""
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, env={**os.environ, "LANG": "C.UTF-8"},
            )
            return r.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def _run(cmd: str, timeout: int = 60) -> Tuple[int, str]:
        """Выполнить команду, вернуть (rc, output)."""
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, env={**os.environ, "LANG": "C.UTF-8"},
            )
            return r.returncode, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            return -1, f"TIMEOUT ({timeout}s)"
        except Exception as e:
            return -2, str(e)

    # ── Accessors ────────────────────────────────────────

    @property
    def hardware(self) -> Optional[HardwareInfo]:
        return self._hw

    @property
    def plan(self) -> Optional[InstallPlan]:
        return self._plan

    def get_stages(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._stages]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "current_stage": self._current_stage,
            "stages_completed": len(self._stages),
            "all_success": all(s.success for s in self._stages),
        }


# ─── Singleton ─────────────────────────────────────────────────────────────────

_installer: Optional[InstallerMode] = None

def get_installer_mode() -> InstallerMode:
    """Получить единственный экземпляр InstallerMode."""
    global _installer
    if _installer is None:
        _installer = InstallerMode()
    return _installer
