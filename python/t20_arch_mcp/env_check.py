"""env_check.py - diagnose the Tianzheng/AutoCAD installation.

Why this module exists
----------------------
Tianzheng's installer marks every file under `SYS` read-only. Some Tianzheng
commands then fail with a modal dialog:

    对不起，打不开DataStore.dbf文件！

While that dialog is up, AutoCAD rejects every COM call and the command that
triggered it (HZQT, for example) is aborted. The symptom looks like "the wall
command silently does nothing", which is very hard to diagnose.

This module detects the condition and EXPLAINS it. It deliberately does NOT
modify the user's Tianzheng installation: changing another machine's CAD install
without consent is not this server's call. The user is told exactly what to do.
"""
from __future__ import annotations

import glob
import os
import winreg
from typing import Any

# Version folder -> AutoCAD release this Tianzheng build targets.
VERSION_DIRS = {
    "sys19x64": "AutoCAD 2019",
    "sys20x64": "AutoCAD 2020",
    "sys21x64": "AutoCAD 2021",
    "sys22x64": "AutoCAD 2022",
    "sys23x64": "AutoCAD 2023",
    "sys24x64": "AutoCAD 2024",
    "sys25x64": "AutoCAD 2025/2026",
}

# Files Tianzheng must be able to write for object creation to work.
CRITICAL_WRITABLE = ("DataStore.dbf", "tchdata.dbf")

DEFAULT_ROOTS = (
    r"C:\Tangent\TArchT20V10",
    r"C:\Tangent\TArchT20V9",
    r"C:\Tangent\TArchT20V8",
    r"C:\Tangent\TArchT20",
    r"D:\Tangent\TArchT20V10",
)


def find_tianzheng_roots() -> list[str]:
    """Locate Tianzheng install roots: env var, registry, then common paths."""
    roots: list[str] = []

    env_root = os.environ.get("TIANZHENG_ROOT", "").strip()
    if env_root and os.path.isdir(env_root):
        roots.append(env_root)

    # Tianzheng records its install folder in the registry.
    for hive, key in (
        (winreg.HKEY_CURRENT_USER, r"Software\Tangent\TArch\20V10"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Tangent\TArch\20V10"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tangent\TArch\20V10"),
    ):
        try:
            with winreg.OpenKey(hive, key) as k:
                for value_name in ("InstallPath", "InstallFolder", "Path", "Home"):
                    try:
                        value, _ = winreg.QueryValueEx(k, value_name)
                    except OSError:
                        continue
                    if value and os.path.isdir(value):
                        roots.append(str(value))
        except OSError:
            continue

    # Registry "Settings\Menu" often points at `<root>\Sys\tch`.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tangent\TArch\20V10\Settings") as k:
            menu, _ = winreg.QueryValueEx(k, "Menu")
            if menu:
                candidate = os.path.dirname(os.path.dirname(str(menu)))
                if os.path.isdir(candidate):
                    roots.append(candidate)
    except OSError:
        pass

    for pattern in (r"C:\Tangent\TArch*", r"D:\Tangent\TArch*"):
        roots.extend(p for p in glob.glob(pattern) if os.path.isdir(p))

    roots.extend(p for p in DEFAULT_ROOTS if os.path.isdir(p))

    # De-duplicate, keep order, and require a SYS subfolder as proof.
    seen: set[str] = set()
    valid: list[str] = []
    for root in roots:
        key = os.path.normcase(os.path.abspath(root))
        if key in seen:
            continue
        seen.add(key)
        if os.path.isdir(os.path.join(root, "SYS")):
            valid.append(os.path.abspath(root))
    return valid


def detect_version_dirs(root: str) -> list[dict[str, Any]]:
    """Which sysXXx64 folders exist, and which contain tch_kernal.arx."""
    found: list[dict[str, Any]] = []
    for folder, cad_name in VERSION_DIRS.items():
        path = os.path.join(root, folder)
        if not os.path.isdir(path):
            continue
        kernal = os.path.join(path, "tch_kernal.arx")
        found.append(
            {
                "folder": folder,
                "targets": cad_name,
                "path": path,
                "tch_kernal_present": os.path.isfile(kernal),
                "template_present": os.path.isfile(os.path.join(path, "ACAD.DWT")),
            }
        )
    return found


def _is_readonly(path: str) -> bool:
    try:
        return not os.access(path, os.W_OK)
    except Exception:
        return True


def check_writable_files(root: str) -> dict[str, Any]:
    """Report which critical Tianzheng data files cannot be written."""
    sys_dir = os.path.join(root, "SYS")
    results: list[dict[str, Any]] = []
    problems: list[str] = []

    for name in CRITICAL_WRITABLE:
        path = os.path.join(sys_dir, name)
        if not os.path.isfile(path):
            results.append({"file": name, "exists": False})
            continue
        writable = not _is_readonly(path)
        entry = {
            "file": name,
            "exists": True,
            "writable": writable,
            "size": os.path.getsize(path),
        }
        results.append(entry)
        if not writable:
            problems.append(path)

    return {
        "files": results,
        "readonly_files": problems,
        "blocking_issue": bool(problems),
        "note": (
            "Tianzheng's installer marks SYS files read-only. When it cannot open "
            "DataStore.dbf for writing it shows a modal dialog and ABORTS the "
            "command (HZQT included), which looks like the command silently "
            "failed. This server will not modify your Tianzheng installation; "
            "clear the read-only attribute yourself if you want those commands "
            "to work."
            if problems
            else "Tianzheng data files are writable."
        ),
        "remedy": (
            [
                f"attrib -R \"{p}\"" for p in problems
            ]
            + [
                "Then fully restart AutoCAD via TGStart.exe.",
                "To restore the original state: attrib +R \"<path>\".",
            ]
            if problems
            else []
        ),
    }


def diagnose(root: str | None = None) -> dict[str, Any]:
    """Full installation report: roots, version folders, writability, template."""
    roots = [root] if root and os.path.isdir(root) else find_tianzheng_roots()
    if not roots:
        return {
            "found": False,
            "error": (
                "No Tianzheng installation found. Set TIANZHENG_ROOT to the "
                "install folder (the one containing SYS), for example "
                "C:\\Tangent\\TArchT20V10."
            ),
        }

    reports = []
    for base in roots:
        reports.append(
            {
                "root": base,
                "version_dirs": detect_version_dirs(base),
                "writable_check": check_writable_files(base),
            }
        )
    return {"found": True, "installations": reports}


def template_for_autocad(version_string: str | None) -> str | None:
    """Pick Tianzheng's own ACAD.DWT for the given AutoCAD version string.

    AutoCAD COM reports e.g. "24.1s (LMS Tech)" for AutoCAD 2022. Tianzheng's
    per-version folder must match, otherwise the ARX build is wrong for the host.
    """
    major = None
    if version_string:
        digits = ""
        for ch in version_string:
            if ch.isdigit() or ch == ".":
                digits += ch
            elif digits:
                break
        try:
            major = int(float(digits))
        except ValueError:
            major = None

    folder_for = {
        23: "sys19x64",
        24: "sys20x64",  # 24.0 = 2020; 24.1 = 2021/2022 handled below
        25: "sys21x64",
    }
    folder = folder_for.get(major) if major else None
    if version_string and version_string.startswith("24.1"):
        folder = "sys22x64"
    if version_string and version_string.startswith("24.2"):
        folder = "sys22x64"
    if version_string and version_string.startswith("24.3"):
        folder = "sys23x64"

    for root in find_tianzheng_roots():
        candidates = []
        if folder:
            candidates.append(os.path.join(root, folder, "ACAD.DWT"))
        for name in VERSION_DIRS:
            candidates.append(os.path.join(root, name, "ACAD.DWT"))
        for path in candidates:
            if os.path.isfile(path):
                return path
    return None
