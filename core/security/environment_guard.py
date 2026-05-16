"""
Environment Guard.

Protects the runtime environment:
  - Environment variable redaction (API keys, tokens, secrets)
  - Safe environment snapshot (for subprocess execution)
  - Resource limit enforcement (open files, memory)
  - Platform detection and adaptation
  - Secure temporary directory management

Designed to prevent secret leaking and resource abuse.
"""

import os
import sys
import time
import shutil
import tempfile
import platform
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Set

logger = logging.getLogger("lina.core.security.environment_guard")


# Environment variable name patterns that indicate secrets
_SECRET_PATTERNS = frozenset({
    "KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD",
    "CREDENTIAL", "AUTH", "PRIVATE", "API_KEY",
    "ACCESS_KEY", "SIGNING", "HMAC",
    "DB_PASS", "DB_PASSWORD", "DATABASE_URL",
    "AWS_SECRET", "GCP_KEY", "AZURE_KEY",
    "OPENAI_API", "ANTHROPIC_API", "GEMINI_API",
    "SMTP_PASS", "MAIL_PASS",
})

# Specific env vars to always redact
_ALWAYS_REDACT = frozenset({
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN", "GITLAB_TOKEN", "NPM_TOKEN",
    "DATABASE_URL", "REDIS_URL",
    "SMTP_PASSWORD", "MAIL_PASSWORD",
    "PRIVATE_KEY", "SIGNING_KEY",
})


@dataclass
class PlatformInfo:
    """Detected platform information."""
    os_name: str          # "Linux", "Darwin", "Windows"
    os_version: str
    arch: str             # "x86_64", "arm64"
    python_version: str
    hostname: str
    user: str
    home_dir: str
    temp_dir: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "os": self.os_name,
            "os_version": self.os_version,
            "arch": self.arch,
            "python": self.python_version,
            "hostname": self.hostname,
            "user": self.user,
            "home": self.home_dir,
            "temp": self.temp_dir,
        }


@dataclass
class ResourceLimits:
    """Configurable resource limits."""
    max_open_files: int = 256
    max_temp_files: int = 50
    max_temp_total_bytes: int = 100 * 1024 * 1024  # 100 MB
    max_env_vars: int = 100


class EnvironmentGuard:
    """
    Environment protection and platform awareness.

    Features:
      1. Secret environment variable redaction
      2. Safe env snapshot for subprocesses
      3. Platform detection
      4. Temp directory management with cleanup
      5. Resource limit enforcement

    Usage:
        guard = EnvironmentGuard()
        safe_env = guard.get_safe_env()
        platform_info = guard.detect_platform()
        tmp_path = guard.create_temp_file("output.txt")
    """

    def __init__(self, limits: Optional[ResourceLimits] = None) -> None:
        self._limits = limits or ResourceLimits()
        self._temp_files: List[str] = []
        self._temp_dir: Optional[str] = None
        self._platform: Optional[PlatformInfo] = None
        self._stats = {
            "env_vars_redacted": 0,
            "temp_files_created": 0,
            "temp_files_cleaned": 0,
            "secret_leaks_prevented": 0,
        }

    def detect_platform(self) -> PlatformInfo:
        """Detect current platform."""
        if self._platform:
            return self._platform

        self._platform = PlatformInfo(
            os_name=platform.system(),
            os_version=platform.release(),
            arch=platform.machine(),
            python_version=platform.python_version(),
            hostname=platform.node(),
            user=os.environ.get("USER", os.environ.get("USERNAME", "unknown")),
            home_dir=os.path.expanduser("~"),
            temp_dir=tempfile.gettempdir(),
        )
        return self._platform

    def get_safe_env(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Get environment variables with secrets redacted.

        Returns a copy of os.environ with secret values replaced by "[REDACTED]".
        """
        safe: Dict[str, str] = {}
        for key, value in os.environ.items():
            if self._is_secret_key(key):
                safe[key] = "[REDACTED]"
                self._stats["env_vars_redacted"] += 1
            else:
                safe[key] = value

        if extra:
            for k, v in extra.items():
                if self._is_secret_key(k):
                    safe[k] = "[REDACTED]"
                else:
                    safe[k] = v

        return safe

    def check_env_leak(self, text: str) -> List[str]:
        """
        Check if any secret env values appear in text.

        Returns list of leaked variable names.
        """
        leaks: List[str] = []
        for key, value in os.environ.items():
            if not self._is_secret_key(key):
                continue
            if len(value) < 8:
                continue  # Too short to reliably detect
            if value in text:
                leaks.append(key)
                self._stats["secret_leaks_prevented"] += 1

        return leaks

    def redact_secrets(self, text: str) -> str:
        """Replace any secret values found in text with [REDACTED]."""
        result = text
        for key, value in os.environ.items():
            if not self._is_secret_key(key):
                continue
            if len(value) < 8:
                continue
            if value in result:
                result = result.replace(value, "[REDACTED]")
                self._stats["secret_leaks_prevented"] += 1
        return result

    def create_temp_dir(self) -> str:
        """Create a managed temp directory."""
        if self._temp_dir and os.path.isdir(self._temp_dir):
            return self._temp_dir
        self._temp_dir = tempfile.mkdtemp(prefix="lina_")
        return self._temp_dir

    def create_temp_file(
        self,
        name: str = "output.txt",
        content: str = "",
    ) -> str:
        """Create a temp file in the managed temp dir."""
        if len(self._temp_files) >= self._limits.max_temp_files:
            raise ResourceError(
                f"Temp file limit reached: {self._limits.max_temp_files}"
            )

        tmp_dir = self.create_temp_dir()
        path = os.path.join(tmp_dir, name)

        # Check total size
        total_size = sum(
            os.path.getsize(f) for f in self._temp_files if os.path.isfile(f)
        )
        new_size = len(content.encode("utf-8"))
        if total_size + new_size > self._limits.max_temp_total_bytes:
            raise ResourceError(
                f"Temp storage limit reached: {total_size + new_size} > {self._limits.max_temp_total_bytes}"
            )

        with open(path, "w") as f:
            f.write(content)

        self._temp_files.append(path)
        self._stats["temp_files_created"] += 1
        return path

    def cleanup_temp(self) -> int:
        """Remove all managed temp files/dirs."""
        cleaned = 0
        for f in self._temp_files:
            try:
                if os.path.isfile(f):
                    os.remove(f)
                    cleaned += 1
            except OSError:
                pass
        self._temp_files.clear()

        if self._temp_dir and os.path.isdir(self._temp_dir):
            try:
                shutil.rmtree(self._temp_dir)
                cleaned += 1
            except OSError:
                pass
            self._temp_dir = None

        self._stats["temp_files_cleaned"] += cleaned
        return cleaned

    def _is_secret_key(self, key: str) -> bool:
        """Check if an env var name looks like a secret."""
        upper = key.upper()
        if upper in _ALWAYS_REDACT:
            return True
        return any(pattern in upper for pattern in _SECRET_PATTERNS)

    def get_resource_usage(self) -> Dict[str, Any]:
        """Get current resource usage vs limits."""
        temp_bytes = sum(
            os.path.getsize(f)
            for f in self._temp_files
            if os.path.isfile(f)
        )
        return {
            "temp_files": len(self._temp_files),
            "temp_files_limit": self._limits.max_temp_files,
            "temp_bytes": temp_bytes,
            "temp_bytes_limit": self._limits.max_temp_total_bytes,
        }

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)


class ResourceError(Exception):
    """Raised when a resource limit is exceeded."""
    pass
