"""
Lina — Гид по установке Linux для предустановочного режима.

Предоставляет:
  - Пошаговые инструкции установки
  - Рекомендации пакетов по профилю (разработчик, геймер, учёный и т.д.)
  - Автоматическое обновление FAQ из собранных данных
  - Рекомендации по тюнингу после установки

Все рекомендации локальные, не требуют интернета.
"""

import time
import json
from pathlib import Path
from typing import Dict, List, Optional

from lina.config import KNOWLEDGE_DIR


FAQ_FILE = KNOWLEDGE_DIR / "preinstall_faq.json"


# ── Профили пользователей и рекомендованные пакеты ──

PACKAGE_PROFILES: Dict[str, Dict] = {
    "разработчик": {
        "description": "Программирование, DevOps, контейнеры",
        "packages": [
            "build-essential", "git", "curl", "wget", "vim", "neovim",
            "python3", "python3-pip", "python3-venv",
            "nodejs", "npm", "docker.io", "docker-compose",
            "gcc", "g++", "cmake", "gdb",
            "htop", "tmux", "zsh", "fzf", "ripgrep",
            "code (VS Code)", "flatpak",
        ],
        "de_suggestion": "KDE Plasma / GNOME — полнофункциональные DE",
    },
    "геймер": {
        "description": "Игры, Steam, производительность GPU",
        "packages": [
            "steam", "lutris", "wine", "gamemode", "mangohud",
            "mesa-vulkan-drivers", "vulkan-tools",
            "lib32-mesa", "lib32-vulkan-icd-loader",
            "proton-ge-custom", "dxvk",
            "discord", "obs-studio",
            "corectrl (AMD) / nvidia-settings (NVIDIA)",
        ],
        "de_suggestion": "KDE Plasma — лучшая совместимость с играми",
        "extra_notes": [
            "Включите GameMode для повышения FPS",
            "Установите последние драйверы GPU",
            "Для NVIDIA: nvidia-driver (проприетарный)",
            "Для AMD: mesa + amdgpu (встроены)",
        ],
    },
    "учёный": {
        "description": "Наука, анализ данных, ML/AI",
        "packages": [
            "python3", "python3-pip", "jupyter-notebook",
            "r-base", "octave",
            "numpy", "scipy", "pandas", "matplotlib",
            "scikit-learn", "tensorflow", "pytorch",
            "texlive-full", "pandoc",
            "conda (Anaconda/Miniconda)",
            "gnuplot", "maxima",
        ],
        "de_suggestion": "GNOME / KDE Plasma — стабильные для работы",
    },
    "офис": {
        "description": "Документы, почта, браузер, мультимедиа",
        "packages": [
            "libreoffice", "firefox", "thunderbird",
            "vlc", "gimp", "inkscape",
            "evince (PDF)", "file-roller",
            "gparted", "timeshift",
            "flatpak", "snap",
        ],
        "de_suggestion": "GNOME (простота) / KDE Plasma (гибкость)",
    },
    "сервер": {
        "description": "Серверные задачи, без GUI",
        "packages": [
            "openssh-server", "nginx", "apache2",
            "postgresql", "mariadb-server", "redis-server",
            "docker.io", "docker-compose",
            "ufw", "fail2ban", "certbot",
            "htop", "iotop", "nethogs", "nmap",
            "tmux", "screen", "rsync",
            "logrotate", "cron",
        ],
        "de_suggestion": "Без DE (headless) — экономия ресурсов",
    },
    "минимальный": {
        "description": "Лёгкая система, старое железо",
        "packages": [
            "xorg", "lxqt", "openbox",
            "firefox-esr", "pcmanfm", "lxterminal",
            "nano", "htop", "mc",
            "pulseaudio", "pavucontrol",
            "network-manager", "network-manager-gnome",
        ],
        "de_suggestion": "LXQt / XFCE / Openbox — минимум ресурсов",
    },
}


class InstallGuide:
    """
    Гид по установке Linux.

    Предоставляет рекомендации по пакетам, пошаговые инструкции,
    FAQ и тюнинг после установки.
    """

    def __init__(self):
        self._faq: List[Dict] = self._load_faq()

    # ── FAQ ──

    def _load_faq(self) -> List[Dict]:
        """Загружает FAQ из файла."""
        try:
            if FAQ_FILE.exists():
                with open(FAQ_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
        return self._default_faq()

    def _save_faq(self) -> None:
        """Сохраняет FAQ в файл."""
        try:
            FAQ_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(FAQ_FILE, "w", encoding="utf-8") as f:
                json.dump(self._faq, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def _default_faq(self) -> List[Dict]:
        """Стандартные вопросы FAQ для установки Linux."""
        return [
            {
                "question": "Какой раздел нужен для установки?",
                "answer": (
                    "Минимум: корневой раздел (/) — 20+ GB, ext4 или btrfs. "
                    "Рекомендуется: отдельный /home для данных и swap (2-8 GB). "
                    "Для UEFI: EFI раздел 512 MB, FAT32."
                ),
                "views": 0,
            },
            {
                "question": "UEFI или BIOS — в чём разница?",
                "answer": (
                    "UEFI — современный способ загрузки, поддерживает GPT-таблицу "
                    "разделов, Secure Boot, быструю загрузку. Большинство ПК с 2012+ "
                    "используют UEFI. BIOS (Legacy) — старый способ, MBR-таблица, "
                    "ограничение 4 основных раздела."
                ),
                "views": 0,
            },
            {
                "question": "Какую файловую систему выбрать?",
                "answer": (
                    "ext4 — проверенная, надёжная, подходит для большинства. "
                    "btrfs — снапшоты, сжатие, но сложнее. "
                    "XFS — высокая производительность для больших файлов. "
                    "Для /boot/efi — только FAT32."
                ),
                "views": 0,
            },
            {
                "question": "Нужен ли swap?",
                "answer": (
                    "Да, особенно если RAM < 8 GB. Swap = подстраховка при нехватке RAM. "
                    "Рекомендации: RAM ≤ 4 GB → swap 4 GB; RAM 8 GB → swap 4-8 GB; "
                    "RAM 16+ GB → swap 2-4 GB или swapfile. "
                    "Для гибернации: swap ≥ размер RAM."
                ),
                "views": 0,
            },
            {
                "question": "Как установить драйверы NVIDIA?",
                "answer": (
                    "1. При установке: выберите 'Установить проприетарные драйверы'. "
                    "2. После установки: sudo apt install nvidia-driver (Debian/Ubuntu) "
                    "или sudo pacman -S nvidia (Arch). "
                    "3. Перезагрузитесь и проверьте: nvidia-smi. "
                    "Если Secure Boot — настройте MOK (Machine Owner Key)."
                ),
                "views": 0,
            },
            {
                "question": "Можно ли сохранить Windows при установке Linux?",
                "answer": (
                    "Да, это dual boot. Шаги: 1) Уменьшите раздел Windows в Disk Management. "
                    "2) Установите Linux на свободное место. "
                    "3) GRUB автоматически обнаружит Windows. "
                    "Важно: сначала Windows, потом Linux. Отключите Fast Startup в Windows."
                ),
                "views": 0,
            },
        ]

    def auto_faq_update(self, question: str = "", answer: str = "") -> str:
        """
        Обновляет FAQ — добавляет новый вопрос или обновляет счётчик.

        Args:
            question: Текст вопроса.
            answer: Текст ответа.

        Returns:
            Сообщение о результате + текущий FAQ.
        """
        updated = False

        if question and answer:
            # Проверяем, есть ли похожий вопрос
            q_lower = question.lower()
            for entry in self._faq:
                if q_lower in entry["question"].lower() or entry["question"].lower() in q_lower:
                    entry["views"] = entry.get("views", 0) + 1
                    updated = True
                    break

            if not updated:
                self._faq.append({
                    "question": question,
                    "answer": answer,
                    "views": 1,
                })
                updated = True

            self._save_faq()

        # Форматируем FAQ
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║      ❓ FAQ — Установка Linux                    ║")
        lines.append("╠══════════════════════════════════════════════════╣")

        for i, entry in enumerate(self._faq, 1):
            views = entry.get("views", 0)
            lines.append(f"║")
            lines.append(f"║  {i}. {entry['question']} (👁 {views})")
            # Разбиваем ответ на строки по ~55 символов
            answer_text = entry["answer"]
            while len(answer_text) > 55:
                split_pos = answer_text[:55].rfind(" ")
                if split_pos == -1:
                    split_pos = 55
                lines.append(f"║     {answer_text[:split_pos]}")
                answer_text = answer_text[split_pos:].strip()
            if answer_text:
                lines.append(f"║     {answer_text}")

        if updated:
            lines.append("║")
            lines.append("║  ✅ FAQ обновлён")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    # ── Рекомендации пакетов ──

    def package_suggestions(self, profile: str = "") -> str:
        """
        Рекомендации пакетов по профилю пользователя.

        Args:
            profile: Профиль (разработчик, геймер, учёный, офис, сервер, минимальный).
                     Если пустой — показываются все профили.

        Returns:
            Форматированная строка с рекомендациями.
        """
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║      📦 Рекомендации пакетов                    ║")
        lines.append("╠══════════════════════════════════════════════════╣")

        profile_lower = profile.lower().strip()

        if profile_lower and profile_lower in PACKAGE_PROFILES:
            # Один конкретный профиль
            p = PACKAGE_PROFILES[profile_lower]
            lines.append(f"║  🎯 Профиль: {profile_lower.upper()}")
            lines.append(f"║  📝 {p['description']}")
            lines.append(f"║  🖥  DE: {p['de_suggestion']}")
            lines.append("║")
            lines.append("║  📦 Рекомендуемые пакеты:")
            for pkg in p["packages"]:
                lines.append(f"║    • {pkg}")
            if "extra_notes" in p:
                lines.append("║")
                lines.append("║  💡 Заметки:")
                for note in p["extra_notes"]:
                    lines.append(f"║    • {note}")
        else:
            # Список всех профилей
            lines.append("║  Доступные профили:")
            lines.append("║")
            for name, p in PACKAGE_PROFILES.items():
                n_pkgs = len(p["packages"])
                lines.append(f"║  🎯 {name} — {p['description']} ({n_pkgs} пакетов)")
            lines.append("║")
            lines.append("║  Использование: пакеты <профиль>")
            lines.append("║  Пример: пакеты разработчик")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    # ── Гид по установке ──

    def installation_guide(self) -> str:
        """
        Пошаговая инструкция по установке Linux.

        Returns:
            Форматированная строка с пошаговой инструкцией.
        """
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║      📖 Пошаговая установка Linux                ║")
        lines.append("╠══════════════════════════════════════════════════╣")

        steps = [
            (
                "Загрузка с Live USB",
                "Вставьте флешку → в BIOS/UEFI выберите загрузку "
                "с USB → запустите Live-среду."
            ),
            (
                "Подключение к интернету",
                "Подключите кабель или настройте Wi-Fi. "
                "Проверьте: ping google.com"
            ),
            (
                "Запуск установщика",
                "Найдите на рабочем столе 'Install' или запустите "
                "calamares / ubiquity / anaconda."
            ),
            (
                "Разметка диска",
                "Выберите диск → создайте разделы:\n"
                "║       • EFI: 512 MB, FAT32 (для UEFI)\n"
                "║       • /: 50+ GB, ext4 или btrfs\n"
                "║       • /home: остаток диска, ext4\n"
                "║       • swap: 2-8 GB"
            ),
            (
                "Выбор пакетов / DE",
                "Выберите окружение рабочего стола (KDE, GNOME, XFCE)\n"
                "║     и дополнительные пакеты."
            ),
            (
                "Настройка пользователя",
                "Введите имя, логин, пароль. "
                "Выберите автологин если нужно."
            ),
            (
                "Установка загрузчика",
                "GRUB устанавливается автоматически. "
                "Для UEFI — в EFI раздел, для BIOS — в MBR диска."
            ),
            (
                "Перезагрузка",
                "Извлеките флешку → перезагрузитесь → "
                "войдите в установленную систему."
            ),
            (
                "После установки",
                "Обновите систему: sudo apt update && sudo apt upgrade\n"
                "║     Установите драйверы и нужные пакеты."
            ),
        ]

        for i, (title, desc) in enumerate(steps, 1):
            lines.append(f"║")
            lines.append(f"║  {i}. 📌 {title}")
            lines.append(f"║     {desc}")

        lines.append("║")
        lines.append("║  💡 Используйте 'проверка готовности' перед началом")
        lines.append("║  💡 Используйте 'анализ разделов' для помощи с дисками")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    # ── Тюнинг после установки ──

    def post_install_tune(self) -> str:
        """
        Рекомендации по настройке после установки Linux.

        Returns:
            Форматированная строка с рекомендациями тюнинга.
        """
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║      🔧 Тюнинг после установки                  ║")
        lines.append("╠══════════════════════════════════════════════════╣")

        sections = [
            ("🔄 Обновление системы", [
                "sudo apt update && sudo apt full-upgrade  (Debian/Ubuntu)",
                "sudo pacman -Syu                          (Arch)",
                "sudo dnf upgrade                          (Fedora)",
            ]),
            ("🎮 Драйверы GPU", [
                "NVIDIA: sudo apt install nvidia-driver / nvidia-dkms",
                "AMD: уже встроены (mesa, amdgpu)",
                "Intel: уже встроены (i915)",
                "Проверка: glxinfo | grep 'direct rendering'",
            ]),
            ("⚡ Производительность", [
                "Включите TRIM для SSD: sudo systemctl enable fstrim.timer",
                "Swappiness: sudo sysctl vm.swappiness=10",
                "Preload: sudo apt install preload",
                "Ananicy: приоритизация процессов",
            ]),
            ("🔒 Безопасность", [
                "Настройте фаервол: sudo ufw enable && sudo ufw default deny",
                "Автообновления: sudo apt install unattended-upgrades",
                "Fail2ban: sudo apt install fail2ban",
                "Проверьте SSH: sudo systemctl status sshd",
            ]),
            ("🖥  Окружение рабочего стола", [
                "KDE: Настройки → Оформление, эффекты, горячие клавиши",
                "GNOME: gnome-tweaks для расширенных настроек",
                "Шрифты: sudo apt install fonts-noto fonts-firacode",
                "Тема иконок: Papirus, Numix, Tela",
            ]),
            ("📦 Полезные инструменты", [
                "Timeshift — снимки системы для отката",
                "Flatpak / Snap — универсальные пакеты",
                "Syncthing — синхронизация файлов",
                "KeePassXC — менеджер паролей",
            ]),
            ("🧹 Очистка", [
                "sudo apt autoremove && sudo apt clean  (Debian/Ubuntu)",
                "sudo journalctl --vacuum-size=100M     (логи)",
                "Удалите ненужные Snap-пакеты: snap list → snap remove",
            ]),
        ]

        for title, items in sections:
            lines.append(f"║")
            lines.append(f"║  {title}")
            for item in items:
                lines.append(f"║    • {item}")

        lines.append("║")
        lines.append("║  💡 Перезагрузитесь после установки драйверов!")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    # ── Статистика ──

    def get_stats(self) -> Dict:
        """Статистика модуля guide."""
        return {
            "faq_count": len(self._faq),
            "profiles_count": len(PACKAGE_PROFILES),
            "profiles": list(PACKAGE_PROFILES.keys()),
        }
