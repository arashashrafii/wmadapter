"""Platform installer adapter contracts for the dedicated browser runtime."""

from __future__ import annotations

import platform


class UnsupportedPlatformError(RuntimeError):
    pass


class InstallerAdapter:
    platform_name = "generic"

    def browser_install_message(self) -> str:
        return "Web Model Adapter installs and verifies its dedicated Playwright browser; it never uses a user Chrome installation."


class LinuxInstaller(InstallerAdapter):
    platform_name = "linux"


class MacOSInstaller(InstallerAdapter):
    platform_name = "darwin"


class WindowsInstaller(InstallerAdapter):
    platform_name = "windows"


def installer_adapter(system: str | None = None) -> InstallerAdapter:
    name = (system or platform.system()).lower()
    machine = platform.machine().lower().replace("amd64", "x86_64")
    try:
        release_data = platform.freedesktop_os_release()
        distribution = release_data.get("ID", "").lower()
        release = release_data.get("VERSION_ID", "").lower()
    except (AttributeError, OSError):
        distribution = release = ""
    if name == "linux" and machine == "x86_64" and distribution == "ubuntu" and release == "24.04":
        return LinuxInstaller()
    raise UnsupportedPlatformError(
        f"Unsupported installer platform for the verified local bundle: {name}; only Ubuntu 24.04 x86-64 Linux is supported"
    )
