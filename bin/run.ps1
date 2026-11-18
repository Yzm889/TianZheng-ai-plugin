# dsh-t20-arch launcher for Windows PowerShell.
#
# Responsibilities:
#   1. Find a Python interpreter that has BOTH pywin32 and mcp available.
#   2. Create/repair a private venv when no such interpreter exists.
#   3. Run the MCP server on stdio.
#
# Everything is resolved at runtime; no user-specific path is hardcoded.
#
# Failure policy: every fatal path prints a numbered, actionable next step and
# exits non-zero. The launcher never hangs silently -- pip gets a timeout and a
# mirror fallback, and venv creation has a documented fallback.

$ErrorActionPreference = 'Stop'

$StateDir = Join-Path $env:LOCALAPPDATA 'dsh-t20-arch'
$LogFile  = Join-Path $StateDir 'launcher.log'
$MarkerFile = Join-Path $StateDir 'deps.ok'
$ReqFile  = Join-Path $PSScriptRoot '..\python\requirements.txt'

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date).ToString('o'), $Message
    try { Add-Content -Path $LogFile -Value $line -Encoding utf8 } catch { }
    [Console]::Error.WriteLine($line)
}

function Fail([string]$What, [string[]]$Steps) {
    Write-Log "FATAL: $What"
    $i = 1
    foreach ($s in $Steps) {
        Write-Log ("  {0}. {1}" -f $i, $s)
        $i++
    }
    exit 1
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

function Get-PythonVersion([string]$Exe) {
    try {
        $v = & $Exe -c "import sys;print('%d.%d.%d' % sys.version_info[:3])" 2>&1
        return ($v | Out-String).Trim()
    } catch {
        return 'unknown'
    }
}

function Resolve-Python {
    # 1. Explicit override.
    if ($env:T20_ARCH_PYTHON) {
        if (Test-PythonReady $env:T20_ARCH_PYTHON) {
            Write-Log "using T20_ARCH_PYTHON=$env:T20_ARCH_PYTHON (Python $(Get-PythonVersion $env:T20_ARCH_PYTHON))"
            return $env:T20_ARCH_PYTHON
        }
        Write-Log "T20_ARCH_PYTHON is set but lacks pywin32/mcp; ignoring it"
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
            Write-Log "found interpreter with pywin32+mcp: $exe (Python $(Get-PythonVersion $exe))"
            return $exe
        }
    }
    return $null
}

function Invoke-PipInstall([string]$PipPython) {
    # requirements.txt is the single source of truth. Fall back to the official
    # index, then to a China mirror, and always bound the wait so a dead network
    # surfaces as an error instead of a silent hang.
    $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
                 '--timeout', '60', '--retries', '2', '-q')

    $sources = @(
        @{ label = 'default index'; extra = @() },
        @{ label = 'tsinghua mirror'; extra = @('-i', 'https://pypi.tuna.tsinghua.edu.cn/simple') },
        @{ label = 'aliyun mirror'; extra = @('-i', 'https://mirrors.aliyun.com/pypi/simple/') }
    )

    $target = if (Test-Path $ReqFile) { @('-r', (Resolve-Path $ReqFile).Path) } else { @('pywin32', 'mcp') }

    foreach ($src in $sources) {
        Write-Log ("installing dependencies from {0}" -f $src.label)
        $all = $pipArgs + $src.extra + $target
        try {
            & $PipPython @all 2>&1 | ForEach-Object { Write-Log $_ }
        } catch {
            Write-Log ("pip failed on {0}: {1}" -f $src.label, $_.Exception.Message)
        }
        if (Test-PythonReady $PipPython) {
            Write-Log ("dependencies satisfied via {0}" -f $src.label)
            return $true
        }
    }
    return $false
}

function New-PrivateVenv([string]$BasePython) {
    $VenvDir = Join-Path $StateDir 'venv'
    $VenvPy  = Join-Path $VenvDir 'Scripts\python.exe'

    if (Test-PythonReady $VenvPy) {
        Write-Log "reusing existing venv: $VenvPy (Python $(Get-PythonVersion $VenvPy))"
        return $VenvPy
    }

    if (-not (Test-Path $VenvPy)) {
        Write-Log "creating venv at $VenvDir using $BasePython (Python $(Get-PythonVersion $BasePython))"
        & $BasePython -m venv $VenvDir 2>&1 | ForEach-Object { Write-Log $_ }
        if (-not (Test-Path $VenvPy)) {
            Write-Log 'venv creation failed; retrying with --without-pip and ensurepip'
            & $BasePython -m venv --without-pip $VenvDir 2>&1 | ForEach-Object { Write-Log $_ }
            if (Test-Path $VenvPy) {
                & $VenvPy -m ensurepip --default-pip 2>&1 | ForEach-Object { Write-Log $_ }
            }
        }
    }

    if (-not (Test-Path $VenvPy)) { return $null }

    if (Invoke-PipInstall $VenvPy) { return $VenvPy }
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
        Fail 'no Python interpreter found on this machine.' @(
            'Install Python 3.10 or newer from https://www.python.org/downloads/',
            'During setup, tick "Add python.exe to PATH".',
            'Then reopen DSH (the launcher re-runs automatically).',
            'Alternative: set T20_ARCH_PYTHON to a python.exe that has pywin32 + mcp.'
        )
    }
    Write-Log "no ready interpreter; bootstrapping a venv from $base"
    $python = New-PrivateVenv $base
}

if (-not $python) {
    Fail 'could not obtain a Python with pywin32 + mcp.' @(
        "Open PowerShell and run: python -m pip install -r `"$ReqFile`"",
        'If pip is slow or fails, add a mirror: -i https://pypi.tuna.tsinghua.edu.cn/simple',
        'If Python is missing, install Python 3.10+ from https://www.python.org/downloads/',
        'Or set T20_ARCH_PYTHON to a working python.exe',
        "Launcher log: $LogFile"
    )
}

$PythonDir = Join-Path $StateDir 'python'
$env:PYTHONPATH = $PythonDir
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

# Record what we resolved so a later support request can see it.
"python=$python`nversion=$(Get-PythonVersion $python)`nresolved=$(Get-Date -Format o)" |
    Set-Content -Path $MarkerFile -Encoding utf8

Write-Log "starting MCP server with $python (Python $(Get-PythonVersion $python))"
& $python -m t20_arch_mcp.server
exit $LASTEXITCODE
