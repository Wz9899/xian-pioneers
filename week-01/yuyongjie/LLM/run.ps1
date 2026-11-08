# 一键运行入口：统一 UTF-8 控制台，并优先用项目 venv
# 用法:  .\run.ps1 demo.py --mock
#        .\run.ps1 chat.py "你好"
param([Parameter(ValueFromRemainingArguments = $true)]$Rest)

chcp 65001 > $null
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host "[run] 没找到 .venv，回退到系统 python" -ForegroundColor Yellow
    $python = 'python'
}

# 便捷写法：.\run.ps1 demo.py 等价于 .\run.ps1 cli/demo.py
if ($Rest.Count -gt 0) {
    $first = [string]$Rest[0]
    if ($first -notmatch '[\\/]' -and (Test-Path (Join-Path $PSScriptRoot "cli\$first"))) {
        $Rest[0] = "cli\$first"
    }
}

& $python @Rest
exit $LASTEXITCODE
