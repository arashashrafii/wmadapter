"""Platform installer adapter contracts for the dedicated browser runtime."""

from __future__ import annotations

import platform


class UnsupportedPlatformError(RuntimeError):
    pass


class InstallerAdapter:
    platform_name = "generic"

    def browser_install_message(self) -> str:
        return "MimicGate installs and verifies its dedicated Playwright browser; it never uses a user Chrome installation."


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
    raise UnsupportedPlatformError(
        f"Unsupported installer platform for the verified local bundle: {name}; only Ubuntu 24.04 x86-64 Linux is supported"
    )
