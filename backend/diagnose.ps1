$ErrorActionPreference = 'SilentlyContinue'

Write-Host '=== Kylin Ops Agent 诊断脚本 ==='

$conn = Get-NetTCPConnection -LocalPort 8000
if ($conn) {
    $proc = Get-Process -Id $conn.OwningProcess
    Write-Host ('端口 8000 被占用: PID=' + $conn.OwningProcess + ', Name=' + $proc.ProcessName)
} else {
    Write-Host '端口 8000 未被占用！后端可能没有启动。'
}

Write-Host ''
Write-Host '测试 /api/health ...'
try {
    $resp = Invoke-RestMethod -Uri 'http://localhost:8000/api/health' -Method GET -TimeoutSec 3
    Write-Host ('OK - status=' + $resp.status + ', tools_count=' + $resp.tools_count)
} catch {
    Write-Host ('失败: ' + $_.Exception.Message)
}

Write-Host ''
Write-Host '测试 /api/auth/login ...'
try {
    $login = Invoke-RestMethod -Uri 'http://localhost:8000/api/auth/login' -Method POST -ContentType 'application/json' -Body '{"username":"opsadmin","password":"KylinOps@2024"}' -TimeoutSec 3
    Write-Host ('OK - success=' + $login.success)
} catch {
    Write-Host ('失败: ' + $_.Exception.Message)
}

Write-Host ''
Write-Host '诊断完成。如果显示失败，请先启动后端。'
