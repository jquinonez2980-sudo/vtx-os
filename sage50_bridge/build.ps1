#Requires -Version 3.0
# build.ps1 - compiles Sage50Bridge.exe using the .NET Framework 4 C# compiler.
# No Visual Studio or dotnet SDK required.
#
# Output: Sage50Bridge.exe + Newtonsoft.Json.dll in the current directory.
# The Sage 50 SDK DLLs are resolved at runtime via AssemblyResolve (see Program.cs).

param(
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"

$csc    = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$sdkDir = "C:\Program Files (x86)\Sage 50 Accounting SDK\SDK"
$out    = "Sage50Bridge.exe"

if (-not (Test-Path $csc)) {
    throw "csc.exe not found at: $csc"
}
if (-not (Test-Path "$sdkDir\Sage_SA.SDK.dll")) {
    throw "Sage 50 SDK not found at: $sdkDir"
}

Write-Host "Building $out ($Configuration)..." -ForegroundColor Cyan

$refs = @(
    "$sdkDir\Sage_SA.SDK.dll",
    "$sdkDir\Sage_SA.Domain.dll",
    "$sdkDir\Sage_SA.Domain.Utility.dll",
    "$sdkDir\Sage_SA.Common.dll",
    "$sdkDir\Sage_SA.DataAccess.dll",
    "$sdkDir\Newtonsoft.Json.dll"
)

# Build /r: arguments. Paths with spaces must be quoted inside the argument value.
$refArgs = $refs | ForEach-Object { "/r:`"$_`"" }

$compileArgs = @(
    "/out:$out",
    "/target:exe",
    "/platform:x86",
    "/optimize+",
    "/nologo"
) + $refArgs + @("Program.cs")

& $csc @compileArgs

if ($LASTEXITCODE -ne 0) {
    throw "Compilation failed (exit $LASTEXITCODE)"
}

# Copy Newtonsoft.Json.dll so the exe can find it at runtime.
Copy-Item "$sdkDir\Newtonsoft.Json.dll" "." -Force

Write-Host "Build succeeded: $out" -ForegroundColor Green
Write-Host ""
Write-Host "Prerequisites: Sage 50 must be open with the company file loaded." -ForegroundColor Yellow
Write-Host ""
Write-Host "Run examples (supply your actual Sage 50 username/password):"
Write-Host "  List tables:    .\Sage50Bridge.exe --sai 'R:\Concetta Enterprises Inc\2026.SAI' --user sysadmin --table tables"
Write-Host "  Chart of accts: .\Sage50Bridge.exe --sai 'R:\Concetta Enterprises Inc\2026.SAI' --user sysadmin --table coa"
Write-Host "  GL (full year): .\Sage50Bridge.exe --sai 'R:\Concetta Enterprises Inc\2026.SAI' --user sysadmin --table gl --start-date 2025-01-01 --end-date 2025-12-31"
Write-Host ""
Write-Host "If you see FAIL: open Sage 50 -> Setup -> System Settings -> Security -> authorize 'SASDK'."
