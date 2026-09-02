# 板检数据核验工具 - 一键打包安装包脚本
# 用法: powershell -ExecutionPolicy Bypass -File build_installer.ps1
# 前置: dist/板检数据核验工具.exe(最新 EXE)与 devices/ 目录已就绪
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host "== 板检数据核验工具 打包 ==" -ForegroundColor Cyan

# 1. 准备 staging
$stage = Join-Path $PSScriptRoot '_installer_stage'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage 'devices') -Force | Out-Null

# 2. 复制主 EXE + 设备配置
Copy-Item (Join-Path $PSScriptRoot 'dist\板检数据核验工具.exe') $stage
Copy-Item (Join-Path $PSScriptRoot 'devices\A02EG') (Join-Path $stage 'devices') -Recurse
Copy-Item (Join-Path $PSScriptRoot 'devices\A02SG') (Join-Path $stage 'devices') -Recurse
Copy-Item (Join-Path $PSScriptRoot 'devices\F16G') (Join-Path $stage 'devices') -Recurse

# 3. 生成 README.txt 与 install.cmd(若不存在)
if (-not (Test-Path (Join-Path $stage 'README.txt'))) {
    Copy-Item (Join-Path $PSScriptRoot '_installer_assets\README.txt') $stage
}
if (-not (Test-Path (Join-Path $stage 'install.cmd'))) {
    Copy-Item (Join-Path $PSScriptRoot '_installer_assets\install.cmd') $stage
}

# 4. 打 zip
$zip = Join-Path $stage 'package.zip'
Compress-Archive -Path (Join-Path $stage 'install.cmd'),(Join-Path $stage '板检数据核验工具.exe'),(Join-Path $stage 'README.txt'),(Join-Path $stage 'devices') -DestinationPath $zip -CompressionLevel Optimal -Force

# 5. 编译 C# stub(使用 .NET Framework 自带 csc,无需额外环境)
$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$stubCs = Join-Path $PSScriptRoot '_installer_assets\stub.cs'
$stubExe = Join-Path $stage 'stub.exe'
& $csc -nologo -target:winexe -out:$stubExe -r:System.IO.Compression.dll -r:System.IO.Compression.FileSystem.dll -r:System.Windows.Forms.dll $stubCs

# 6. 拼接 stub + zip = 安装包(用字节拼接,避免 cmd 中文路径问题)
$output = Join-Path $PSScriptRoot '板检数据核验工具-安装包.exe'
$stubBytes = [IO.File]::ReadAllBytes($stubExe)
$zipBytes = [IO.File]::ReadAllBytes($zip)
$outBytes = New-Object byte[] ($stubBytes.Length + $zipBytes.Length)
[Array]::Copy($stubBytes, 0, $outBytes, 0, $stubBytes.Length)
[Array]::Copy($zipBytes, 0, $outBytes, $stubBytes.Length, $zipBytes.Length)
[IO.File]::WriteAllBytes($output, $outBytes)

# 7. 清理 staging
Remove-Item $stage -Recurse -Force

$size = (Get-Item $output).Length / 1MB
Write-Host "完成: $output ($([math]::Round($size,1)) MB)" -ForegroundColor Green
