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

# Tianzheng versions seen in the wild. Deliberately NOT a closed list: discovery
# also scans the filesystem and enumerates the registry, because Tianzheng has
# shipped T20V10, T30V1, T30V2, ... and a hardcoded list silently stopped working
# the moment the user upgraded from T20-Arch V10.0 to T30-Arch V2.0.
KNOWN_TIANZHENG_KEYS = ("30V2", "30V1", "20V10", "20V9", "20V8", "20V7", "8.0")


def _registy_tianzheng_install_roots() -> list[str]:
    """Enumerate every Tianzheng version key and ask it for its install folder."""
    roots: list[str] = []
    keys = list(KNOWN_TIANZHENG_KEYS)

    # Discover additional version keys dynamically (e.g. a future 40V1).
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Tangent\TArch") as parent:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(parent, index)
                    except OSError:
                        break
                    index += 1
                    if name not in keys:
                        keys.append(name)
        except OSError:
            continue

    for version in keys:
        for base in (
            rf"Software\Tangent\TArch\{version}",
            rf"SOFTWARE\WOW6432Node\Tangent\TArch\{version}",
        ):
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, base) as k:
                        for value_name in ("InstallPath", "InstallFolder", "Path", "Home"):
                            try:
                                value, _ = winreg.QueryValueEx(k, value_name)
                            except OSError:
                                continue
                            if value and os.path.isdir(value):
                                roots.append(str(value))
                except OSError:
                    continue

                # `Settings\Menu` usually points at `<root>\Sys\tch`.
                try:
                    with winreg.OpenKey(hive, base + r"\Settings") as k:
                        menu, _ = winreg.QueryValueEx(k, "Menu")
                        if menu:
                            candidate = os.path.dirname(os.path.dirname(str(menu)))
                            if os.path.isdir(candidate):
                                roots.append(candidate)
                except OSError:
                    pass
    return roots


def _filesystem_tianzheng_roots() -> list[str]:
    """Scan every fixed drive for a `Tangent\\TArch*` folder."""
    roots: list[str] = []
    drives = []
    try:
        drives = [f"{chr(c)}:" for c in range(ord("A"), ord("Z") + 1) if os.path.isdir(f"{chr(c)}:\\")]
    except Exception:
        drives = ["C:", "D:"]
    for drive in drives:
        for pattern in (rf"{drive}\Tangent\TArch*", rf"{drive}\Program Files\Tangent\TArch*"):
            try:
                roots.extend(p for p in glob.glob(pattern) if os.path.isdir(p))
            except Exception:
                continue
    return roots


def read_product_info(root: str) -> dict[str, Any]:
    """Read SYS\\tch.ini to report the real product name and build.

    tch.ini is encoded in the system ANSI codepage (GBK on Chinese Windows), not
    UTF-8. Reading it as UTF-8 turned the product name into mojibake, so decode
    with the preferred codepage first and fall back progressively.
    """
    info: dict[str, Any] = {"root": root, "product": None, "version": None, "build": None, "registry": None}
    ini = os.path.join(root, "SYS", "tch.ini")
    try:
        raw = open(ini, "rb").read()
    except OSError:
        return info

    text = None
    for encoding in ("gbk", "utf-8-sig", "utf-8", "latin-1"):
        try:
            candidate = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # Reject decodings that produced replacement characters.
        if "\ufffd" not in candidate:
            text = candidate
            break
    if text is None:
        text = raw.decode("gbk", errors="replace")

    section = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "Product" or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key == "About":
            info["product"] = value
        elif key == "NewVersion":
            info["version"] = value
        elif key == "SubVer":
            info["build"] = value
        elif key == "Registry":
            info["registry"] = value
    return info


def find_tianzheng_roots() -> list[str]:
    """Locate Tianzheng install roots: env var, registry, then all fixed drives."""
    roots: list[str] = []

    env_root = os.environ.get("TIANZHENG_ROOT", "").strip()
    if env_root and os.path.isdir(env_root):
        roots.append(env_root)

    roots.extend(_registy_tianzheng_install_roots())
    roots.extend(_filesystem_tianzheng_roots())

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
    """Report unwritable Tianzheng data files in BOTH the root and SYS folders.

    On a real T20 V10 install the critical DBFs exist twice: at the install root
    and under SYS.  Checking only SYS (the old behaviour) can declare everything
    fine while the root copy of DataStore.dbf is read-only - and that is the copy
    which makes Tianzheng show the modal "打不开DataStore.dbf文件！" dialog and
    abort HZQT.
    """
    # The per-version sysXXx64 folder is what T20 actually loads at runtime,
    # so it is the most important location - a read-only copy there shows the
    # "打不开DataStore.dbf文件！" dialog even when root/SYS copies are writable.
    locations: list[tuple[str, str]] = [
        (root, "install_root"),
        (os.path.join(root, "SYS"), "SYS"),
    ]
    for folder in VERSION_DIRS:
        path = os.path.join(root, folder)
        if os.path.isdir(path):
            locations.append((path, "version_dir"))

    results: list[dict[str, Any]] = []
    problems: list[str] = []

    for name in CRITICAL_WRITABLE:
        found = False
        for location, scope in locations:
            path = os.path.join(location, name)
            if not os.path.isfile(path):
                continue
            found = True
            writable = not _is_readonly(path)
            results.append({
                "file": name,
                "path": path,
                "scope": scope,
                "exists": True,
                "writable": writable,
                "size": os.path.getsize(path),
            })
            if not writable:
                problems.append(path)
        if not found:
            results.append({"file": name, "exists": False})

    return {
        "files": results,
        "readonly_files": problems,
        "blocking_issue": bool(problems),
        "note": (
            "Tianzheng keeps DataStore.dbf / tchdata.dbf at BOTH the install root "
            "and under SYS. If any copy is read-only, commands such as HZQT show "
            "a modal '打不开DataStore.dbf文件！' dialog and abort, which looks like "
            "a silent failure. This server only reports the condition; clearing "
            "the attribute is your call."
            if problems
            else "Tianzheng data files are writable in all checked locations."
        ),
        "remedy": (
            [f"attrib -R \"{p}\"" for p in problems]
            + [
                "Then fully restart AutoCAD via TGStart.exe.",
                "To restore the original state: attrib +R \"<path>\".",
            ]
            if problems
            else []
        ),
    }


def diagnose(root: str | None = None) -> dict[str, Any]:
    """Full installation report: roots, product, version folders, writability."""
    roots = [os.path.abspath(root)] if root and os.path.isdir(root) else find_tianzheng_roots()
    if not roots:
        return {
            "found": False,
            "error": (
                "No Tianzheng installation found (looked for a folder containing "
                "SYS, via TIANZHENG_ROOT, the registry, and every fixed drive). "
                "Set TIANZHENG_ROOT to the install folder."
            ),
        }

    reports = []
    for base in roots:
        entry = read_product_info(base)
        entry["version_dirs"] = detect_version_dirs(base)
        entry["writable_check"] = check_writable_files(base)
        reports.append(entry)
    return {"found": True, "installations": reports}


# AutoCAD COM `Application.Version` -> Tianzheng per-version folder.
#
# MEASURED WARNING: the folder naming is NOT stable across Tianzheng products.
# On the T20-Arch V10.0 install, AutoCAD 2022 used `sys22x64`. On the
# T30-Arch V2.0 install, AutoCAD 2022 (`24.1s`) uses **`sys24x64`** -- probing
# every folder showed only sys24x64 loads (sys19..sys23 all fail with ARXLOAD).
# So a hardcoded year map is WRONG and silently breaks after an upgrade.
#
# Therefore the authoritative source is the running AutoCAD's own Tianzheng
# profile: its support path names the sysXXx64 folder Tianzheng itself installed
# for that CAD. We read that first, and only fall back to a static guess.
_CAD_VERSION_FALLBACK = (
    ("26.", "sys26x64"),
    ("25.", "sys25x64"),
    ("24.4", "sys24x64"),
    ("24.3", "sys24x64"),
    ("24.2", "sys24x64"),
    ("24.1", "sys24x64"),
    ("24.0", "sys24x64"),
    ("23.", "sys23x64"),
)

_SYS_FOLDER_RE = None


def _autocad_registry_version(version_string: str | None) -> str | None:
    """Turn a COM version like '24.1s (LMS Tech)' into the registry key 'R24.1'."""
    if not version_string:
        return None
    digits = ""
    for ch in str(version_string).strip():
        if ch.isdigit() or ch == ".":
            digits += ch
        elif digits:
            break
    parts = digits.split(".")
    if len(parts) < 2:
        return None
    return f"R{parts[0]}.{parts[1]}"


def folder_from_autocad_profile(version_string: str | None) -> str | None:
    """Read the sysXXx64 folder that Tianzheng actually configured for this CAD.

    This is the only reliable mapping: it comes from Tianzheng's own profile,
    so it survives product upgrades that rename the version folders.
    """
    global _SYS_FOLDER_RE
    import re

    if _SYS_FOLDER_RE is None:
        _SYS_FOLDER_RE = re.compile(r"sys\d+x64", re.IGNORECASE)

    reg_version = _autocad_registry_version(version_string)
    if not reg_version:
        return None

    base = rf"Software\Autodesk\AutoCAD\{reg_version}"
    found: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
            index = 0
            products = []
            while True:
                try:
                    products.append(winreg.EnumKey(k, index))
                except OSError:
                    break
                index += 1
    except OSError:
        return None

    for product in products:
        profiles_key = f"{base}\\{product}\\Profiles"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, profiles_key) as pk:
                index = 0
                profiles = []
                while True:
                    try:
                        profiles.append(winreg.EnumKey(pk, index))
                    except OSError:
                        break
                    index += 1
        except OSError:
            continue

        for profile in profiles:
            if "tarch" not in profile.lower():
                continue
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{profiles_key}\\{profile}\\General") as gk:
                    support, _ = winreg.QueryValueEx(gk, "ACAD")
            except OSError:
                continue
            match = _SYS_FOLDER_RE.search(str(support or ""))
            if match:
                found.append(match.group(0))

    # Prefer a folder that actually exists under some Tianzheng root.
    roots = find_tianzheng_roots()
    for folder in found:
        for root in roots:
            if os.path.isdir(os.path.join(root, folder)):
                return folder
    return found[0] if found else None


def folder_for_autocad(version_string: str | None) -> str | None:
    """Map an AutoCAD COM version string to Tianzheng's sysXXx64 folder."""
    from_profile = folder_from_autocad_profile(version_string)
    if from_profile:
        return from_profile

    if not version_string:
        return None
    text = str(version_string).strip()
    for prefix, folder in _CAD_VERSION_FALLBACK:
        if text.startswith(prefix):
            return folder
    return None


def template_for_autocad(version_string: str | None) -> str | None:
    """Pick Tianzheng's own ACAD.DWT for the given AutoCAD version string.

    Prefers the folder matching the running AutoCAD; falls back to any template
    that exists, so a version mismatch degrades to "works" instead of "fails".
    """
    wanted = folder_for_autocad(version_string)
    for root in find_tianzheng_roots():
        candidates = []
        if wanted:
            candidates.append(os.path.join(root, wanted, "ACAD.DWT"))
        # Only fall back to other folders as a last resort: picking the wrong
        # sysXXx64 folder yields an ARX build that cannot load at all.
        for name in VERSION_DIRS:
            candidates.append(os.path.join(root, name, "ACAD.DWT"))
        candidates.append(os.path.join(root, "SYS", "ACAD.DWT"))
        for path in candidates:
            if os.path.isfile(path):
                return path
    return None
