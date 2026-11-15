# console_shot.ps1 - run a script in a real console window and screenshot it when done.
#
# Captures the actual Windows Terminal window (new consoles open as a tab), so the
# image proves the command really ran. Waits for a completion flag file instead of a
# fixed sleep, so long runs are not cut off.
#
# Windows refuses plain SetForegroundWindow from a background process ("foreground
# lock"), so we attach to the foreground thread's input queue to borrow the right to
# raise our window, then verify the switch actually happened.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 parses a BOM-less UTF-8
# script as ANSI/GBK and corrupts non-ASCII characters.
param(
    [Parameter(Mandatory = $true)][string]$Script,
    [string]$ScriptArgs = "",
    [Parameter(Mandatory = $true)][string]$Out,
    [int]$Timeout = 180
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

if (-not ('Win32Shot' -as [type])) {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Win32Shot {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint attach, uint attachTo, bool doAttach);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdc, uint flags);

    public static bool ForceForeground(IntPtr hWnd) {
        IntPtr foreground = GetForegroundWindow();
        if (foreground == hWnd) return true;

        uint pid;
        uint foregroundThread = GetWindowThreadProcessId(foreground, out pid);
        uint myThread = GetCurrentThreadId();
        bool attached = false;
        if (foregroundThread != myThread) {
            attached = AttachThreadInput(myThread, foregroundThread, true);
        }
        ShowWindow(hWnd, 9);
        BringWindowToTop(hWnd);
        SetForegroundWindow(hWnd);
        if (attached) AttachThreadInput(myThread, foregroundThread, false);
        return GetForegroundWindow() == hWnd;
    }
}
'@
}

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$flag = Join-Path $env:TEMP ('shot_done_' + [guid]::NewGuid().ToString('N'))
$runner = Join-Path $env:TEMP ('console_shot_' + [guid]::NewGuid().ToString('N') + '.ps1')

# Build the flag line separately: applying -f to the array literal below would
# stringify the whole array into a single line.
$flagLine = 'New-Item -ItemType File -Path "{0}" -Force | Out-Null' -f $flag

$lines = @(
    'chcp 65001 > $null',
    '$env:PYTHONUTF8 = "1"',
    '$env:PYTHONIOENCODING = "utf-8"',
    '$Host.UI.RawUI.WindowTitle = "LLM Demo"',
    ('& "{0}" "{1}" {2}' -f $python, (Join-Path $root $Script), $ScriptArgs),
    '""',
    $flagLine
)
Set-Content -Path $runner -Value $lines -Encoding ASCII

$proc = Start-Process powershell.exe -ArgumentList @('-NoLogo', '-NoExit', '-File', $runner) -PassThru -WindowStyle Normal

$deadline = (Get-Date).AddSeconds($Timeout)
while (-not (Test-Path $flag)) {
    if ((Get-Date) -gt $deadline) { Write-Host "WARN: timeout waiting for script"; break }
    Start-Sleep -Milliseconds 400
}
Start-Sleep -Milliseconds 800

$wt = Get-Process WindowsTerminal -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $wt) { throw "No Windows Terminal window found" }

$hwnd = $wt.MainWindowHandle
[Win32Shot]::ShowWindow($hwnd, 3) | Out-Null      # maximize, keeps the layout stable

$rect = New-Object Win32Shot+RECT
[Win32Shot]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
$w = $rect.Right - $rect.Left
$h = $rect.Bottom - $rect.Top

function New-WindowBitmap {
    param([IntPtr]$Handle, [int]$Width, [int]$Height)
    $bmp = New-Object System.Drawing.Bitmap $Width, $Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    # 2 = PW_RENDERFULLCONTENT, needed for XAML/DirectComposition windows
    $ok = [Win32Shot]::PrintWindow($Handle, $hdc, 2)
    $g.ReleaseHdc($hdc)
    $g.Dispose()
    return @{ Bitmap = $bmp; Ok = $ok }
}

function Get-NonBlackRatio {
    param([System.Drawing.Bitmap]$Bitmap)
    $hits = 0
    $samples = 0
    for ($y = 0; $y -lt $Bitmap.Height; $y += 13) {
        for ($x = 0; $x -lt $Bitmap.Width; $x += 13) {
            $samples++
            $c = $Bitmap.GetPixel($x, $y)
            if ($c.R -gt 12 -or $c.G -gt 12 -or $c.B -gt 12) { $hits++ }
        }
    }
    if ($samples -eq 0) { return 0 }
    return $hits / $samples
}

# Preferred: PrintWindow does not need focus, so an actively used desktop cannot
# steal the shot between raising the window and copying pixels.
$shot = New-WindowBitmap -Handle $hwnd -Width $w -Height $h
$ratio = Get-NonBlackRatio -Bitmap $shot.Bitmap

if (-not $shot.Ok -or $ratio -lt 0.01) {
    # Fallback: raise the window for real and grab the screen area
    Write-Host "PrintWindow empty (ratio=$ratio), falling back to screen capture"
    $shot.Bitmap.Dispose()
    for ($i = 0; $i -lt 8; $i++) {
        if ([Win32Shot]::ForceForeground($hwnd)) { break }
        Start-Sleep -Milliseconds 300
    }
    Start-Sleep -Milliseconds 500
    [Win32Shot]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
    $w = $rect.Right - $rect.Left
    $h = $rect.Bottom - $rect.Top
    $bmp = New-Object System.Drawing.Bitmap $w, $h
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($rect.Left, $rect.Top, 0, 0, (New-Object System.Drawing.Size $w, $h))
    $g.Dispose()
    $shot = @{ Bitmap = $bmp; Ok = $true }
}

$bmp = $shot.Bitmap
$outPath = Join-Path $root $Out
$dir = Split-Path -Parent $outPath
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Remove-Item $runner, $flag -ErrorAction SilentlyContinue

Write-Host ("Saved screenshot: {0}  ({1}x{2})" -f $outPath, $w, $h)
