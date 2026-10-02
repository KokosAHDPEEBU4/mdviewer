"""Register the app so Windows can open ``.md`` files with it.

Everything happens in HKCU (per user, no administrator rights needed) and the
build is copied to ``%LOCALAPPDATA%\\Programs\\MDViewer`` so the registration
survives rebuilding or moving ``dist/``.

Since Windows 8 the extension -> handler mapping lives in
``...\\Explorer\\FileExts\\<ext>\\UserChoice``, a key Explorer owns: opening it
with write access fails with ``WinError 5`` no matter what the hash says, so no
program can set the default handler silently any more. This module therefore

1. registers the ProgID, icon, shell command, ``OpenWithProgids`` and the
   ``RegisteredApplications`` capability - the parts we are allowed to own, and
2. hands the last step to the shell with ``SHOpenWithDialog``, where a single
   tick ("Always use this app") makes Explorer write ``UserChoice`` for us.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import tempfile
import winreg
from pathlib import Path

from version import APP_NAME, EXTENSIONS, PROG_ID

HERE = Path(__file__).resolve().parent
CLASSES = r"Software\Classes"
FILEEXTS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"
REGISTERED = r"Software\RegisteredApplications"

APP_DIR = "MDViewer"
REGISTERED_NAME = f"{APP_NAME} (MDViewer)"
_EXE_NAME = f"{APP_DIR}.exe"

Status = tuple[int, str]

SAMPLE = """# Sample

This file was only used to show the "Open with" dialog.
"""


# --- installation ----------------------------------------------------------


def install(prompt: bool = True) -> Status:
    """Copy the build into the user's Programs folder and register it."""
    source = _built_exe()
    if source is None:
        return 1, f"{APP_NAME}: сначала собери приложение (build.bat)."

    target = install_dir() / _EXE_NAME
    if source.parent == target.parent:
        steps = [f"Приложение уже установлено: {target}"]
    else:
        _copy_build(source.parent, target.parent)
        steps = [f"Скопировано в {target.parent}"]

    _register_keys(target)
    _notify_shell()
    steps.append(f"Зарегистрирован обработчик {', '.join(EXTENSIONS)}: {PROG_ID}")

    if prompt and _promote():
        steps.append(
            "Осталось нажать «Всегда» в диалоге — после этого .md открывается "
            "этим приложением по умолчанию."
        )
    else:
        steps.append(
            "Автоматически выбрать обработчик Windows не дала. Открой "
            "Параметры → Приложения → Приложения по умолчанию и выбери "
            f"{REGISTERED_NAME} для .md (или «Открыть с помощью» → «Всегда»)."
        )
    steps.append("Откатить регистрацию: MDViewer.exe --uninstall-default")
    return 0, "\n".join(steps)


def uninstall() -> Status:
    """Drop the registration; Windows keeps its own default-handler choice."""
    for extension in EXTENSIONS:
        _delete_value(f"{CLASSES}\\{extension}\\OpenWithProgids", PROG_ID, missing_ok=True)
    _delete_key(f"{CLASSES}\\{PROG_ID}")
    _delete_key(f"{CLASSES}\\Applications\\{_EXE_NAME}")
    _delete_value(REGISTERED, REGISTERED_NAME, missing_ok=True)
    _notify_shell()
    return 0, (
        f"Регистрация {APP_NAME} удалена.\n"
        f"Папку установки ({install_dir()}) можно удалить вручную; если .md "
        "был назначен этому приложению, сбрось его в Параметры → Приложения → "
        "Приложения по умолчанию."
    )


def status() -> Status:
    """Describe what Windows currently has registered for our extensions."""
    lines = [
        f"установлено в: {install_dir() if install_dir().exists() else '(нет)'}",
        f"ProgID {PROG_ID}: {'зарегистрирован' if _key_exists(f'{CLASSES}\\{PROG_ID}') else 'нет'}",
        f"в списке приложений: {'да' if _read_value(REGISTERED, REGISTERED_NAME) else 'нет'}",
    ]
    for extension in EXTENSIONS:
        choice = _read_value(_user_choice_path(extension), "ProgId")
        if not choice:
            lines.append(f"{extension}: обработчик по умолчанию не задан")
        elif choice == PROG_ID:
            lines.append(f"{extension}: {APP_NAME} (по умолчанию)")
        else:
            lines.append(f"{extension}: {choice}")
    return 0, "\n".join(lines)


def _promote() -> bool:
    """Ask the shell for the default handler. Windows only trusts the user."""
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHOpenWithDialog.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    shell32.SHOpenWithDialog.restype = ctypes.c_long
    sample = _sample_file()
    try:
        return shell32.SHOpenWithDialog(None, str(sample)) == 0
    except OSError:
        return False
    finally:
        sample.unlink(missing_ok=True)


def _sample_file() -> Path:
    extension = EXTENSIONS[0]
    for folder in (Path.home() / "Documents", Path.home(), Path.cwd()):
        for found in sorted(folder.glob(f"*{extension}"))[:1]:
            return found
    sample = Path(tempfile.gettempdir()) / f"{APP_DIR}-sample{extension}"
    sample.write_text(SAMPLE, encoding="utf-8")
    return sample


# --- paths and registry ----------------------------------------------------


def install_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / APP_DIR


def _built_exe() -> Path | None:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    candidate = HERE / "dist" / "MDViewer" / _EXE_NAME
    return candidate if candidate.is_file() else None


def _copy_build(source_dir: Path, target_dir: Path) -> None:
    shutil.copytree(
        source_dir,
        target_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pdb"),
    )


def _register_keys(exe: Path) -> None:
    command = f'"{exe}" "%1"'
    _set_value(f"{CLASSES}\\{PROG_ID}", "", f"Markdown Document ({APP_NAME})")
    _set_value(f"{CLASSES}\\{PROG_ID}\\DefaultIcon", "", f"{exe},0")
    _set_value(f"{CLASSES}\\{PROG_ID}\\shell\\open\\command", "", command)
    _set_value(f"{CLASSES}\\Applications\\{_EXE_NAME}\\shell\\open\\command", "", command)
    for extension in EXTENSIONS:
        _set_value(f"{CLASSES}\\{extension}\\OpenWithProgids", PROG_ID, "")
        _set_value(f"{CLASSES}\\Applications\\{_EXE_NAME}\\SupportedTypes", extension, "")

    capabilities = f"{CLASSES}\\{PROG_ID}\\Capabilities"
    _set_value(capabilities, "ApplicationName", APP_NAME)
    _set_value(capabilities, "Description", f"{APP_NAME}: просмотр Markdown")
    _set_value(capabilities, "FileAssociations", ";".join(EXTENSIONS))
    _set_value(REGISTERED, REGISTERED_NAME, capabilities)


def _user_choice_path(extension: str) -> str:
    return f"{FILEEXTS}\\{extension}\\UserChoice"


def _set_value(path: str, name: str, value: str) -> None:
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _read_value(path: str, name: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return ""


def _delete_value(path: str, name: str, missing_ok: bool = False) -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS
        ) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        if not missing_ok:
            raise


def _delete_key(path: str) -> None:
    _delete_key_from(winreg.HKEY_CURRENT_USER, path)


def _delete_key_from(root: int, path: str) -> None:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
            index = 0
            while True:
                try:
                    child = winreg.EnumKey(key, index)
                except OSError:
                    break
                _delete_key_from(key, child)
        winreg.DeleteKey(root, path)
    except OSError:
        pass


def _key_exists(path: str) -> bool:
    return bool(_read_value(path, ""))


def _notify_shell() -> None:
    SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0x0000
    ctypes.WinDLL("shell32").SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)


if __name__ == "__main__":
    sys.exit(status()[0])