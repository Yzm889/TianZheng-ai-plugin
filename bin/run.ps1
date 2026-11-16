# dsh-t20-arch launcher for Windows PowerShell.
#
# Responsibilities:
#   1. Find a Python interpreter that has BOTH pywin32 and mcp available.
#   2. Create/repair a private venv when no such interpreter exists.
#   3. Run the MCP server on stdio.
#
# Everything is resolved at runtime; no user-specific path is hardcoded.

$ErrorActionPreference = 'Stop'

$StateDir = Join-Path $env:LOCALAPPDATA 'dsh-t20-arch'
$LogFile  = Join-Path $StateDir 'launcher.log'

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date).ToString('o'), $Message
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    [Console]::Error.WriteLine($line)
}

function Test-PythonReady([string]$Exe) {
    if (-not $Exe -or -not (Test-Path $Exe)) { return $false }
    try {
        $out = & $Exe -c "import win32com.client, mcp; print('READY')" 2>&1
        return ($out -match 'READY')
    } catch {
        return $false
    }
}

function Resolve-Python {
    # 1. Explicit override.
    if ($env:T20_ARCH_PYTHON -and (Test-PythonReady $env:T20_ARCH_PYTHON)) {
        Write-Log "using T20_ARCH_PYTHON=$env:T20_ARCH_PYTHON"
        return $env:T20_ARCH_PYTHON
    }

    # 2. Any interpreter already registered with the py launcher.
    $candidates = @()
    try {
        $listed = & py -0p 2>$null
        foreach ($line in $listed) {
            $m = [regex]::Match($line, '([A-Za-z]:\\.*python\.exe)')
            if ($m.Success) { $candidates += $m.Groups[1].Value }
        }
    } catch { }

    # 3. Common install locations.
    foreach ($root in @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:ProgramFiles\Python",
        "C:\Python"
    )) {
        if (Test-Path $root) {
            $candidates += (Get-ChildItem $root -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
                            Select-Object -ExpandProperty FullName)
        }
    }

    # 4. Whatever is on PATH.
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += $cmd.Source }
    }

    foreach ($exe in ($candidates | Select-Object -Unique)) {
        if (Test-PythonReady $exe) {
            Write-Log "found interpreter with pywin32+mcp: $exe"
            return $exe
        }
    }
    return $null
}

function New-PrivateVenv([string]$BasePython) {
    $VenvDir = Join-Path $StateDir 'venv'
    $VenvPy  = Join-Path $VenvDir 'Scripts\python.exe'

    if (Test-PythonReady $VenvPy) { return $VenvPy }

    if (-not (Test-Path $VenvPy)) {
        Write-Log "creating venv at $VenvDir"
        & $BasePython -m venv $VenvDir 2>&1 | ForEach-Object { Write-Log $_ }
    }

    Write-Log 'installing pywin32 and mcp into venv (requires internet)'
    & $VenvPy -m pip install --disable-pip-version-check --quiet pywin32 mcp 2>&1 |
        ForEach-Object { Write-Log $_ }

    if (Test-PythonReady $VenvPy) { return $VenvPy }
    return $null
}

$python = Resolve-Python

if (-not $python) {
    # Fall back to a bare interpreter for the venv bootstrap.
    $base = $null
    foreach ($name in @('python', 'py')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $base = $cmd.Source; break }
    }
    if (-not $base) {
        Write-Log 'FATAL: no Python interpreter found on this machine.'
        Write-Log 'Install Python 3.10+ and run: python -m pip install pywin32 mcp'
        exit 1
    }
    Write-Log "no ready interpreter; bootstrapping a venv from $base"
    $python = New-PrivateVenv $base
}

if (-not $python) {
    Write-Log 'FATAL: could not obtain a Python with pywin32 + mcp.'
    Write-Log 'Install them manually: python -m pip install pywin32 mcp'
    Write-Log "Or set T20_ARCH_PYTHON to a suitable python.exe"
    exit 1
}

$PythonDir = Join-Path $StateDir 'python'
$env:PYTHONPATH = $PythonDir
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

Write-Log "starting MCP server with $python"
& $python -m t20_arch_mcp.server
exit $LASTEXITCODE
