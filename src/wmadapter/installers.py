"""Platform installer adapter contracts for the system Google Chrome runtime."""

from __future__ import annotations

import platform


class UnsupportedPlatformError(RuntimeError):
    pass


class InstallerAdapter:
    platform_name = "generic"

    def browser_install_message(self) -> str:
        return "Install Google Chrome for your operating system, then rerun the Web Model Adapter installer."


class LinuxInstaller(InstallerAdapter):
    platform_name = "linux"


class MacOSInstaller(InstallerAdapter):
    platform_name = "darwin"


class WindowsInstaller(InstallerAdapter):
    platform_name = "windows"


def installer_adapter(system: str | None = None) -> InstallerAdapter:
    name = (system or platform.system()).lower()
    if name == "linux":
        return LinuxInstaller()
    if name in {"darwin", "macos"}:
        return MacOSInstaller()
    if name in {"windows", "win32"}:
        return WindowsInstaller()
    raise UnsupportedPlatformError(
        f"Unsupported installer platform: {name}; supported platforms are Linux, macOS, and Windows"
    )
