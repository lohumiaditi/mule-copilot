<#
Portable Neo4j for machines without Docker.

  .\scripts\neo4j.ps1 setup   # download Temurin JDK + Neo4j Community into .tools\ (no admin needed)
  .\scripts\neo4j.ps1 start   # run Neo4j in a minimised console window
  .\scripts\neo4j.ps1 stop    # stop it
  .\scripts\neo4j.ps1 status

Reads NEO4J_PASSWORD from .env. With Docker, use docker-compose.yml instead.
#>
param([ValidateSet("setup", "start", "stop", "status")][string]$Action = "status")

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar on

$Neo4jVersion = "2026.09.0"
$JdkMajor = 21
$Root = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $Root ".tools"
$JdkDir = Join-Path $Tools "jdk"
$Neo4jHome = Join-Path $Tools "neo4j-community-$Neo4jVersion"

function Get-EnvValue($Name, $Default) {
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -First 1
        if ($line) { return ($line -split "=", 2)[1].Trim().Trim('"') }
    }
    return $Default
}

function Use-Jdk {
    $env:JAVA_HOME = $JdkDir
    $env:PATH = "$JdkDir\bin;$env:PATH"
}

switch ($Action) {
    "setup" {
        New-Item -ItemType Directory -Force $Tools | Out-Null

        if (-not (Test-Path "$JdkDir\bin\java.exe")) {
            Write-Host "Downloading Temurin JDK $JdkMajor..."
            $zip = Join-Path $Tools "jdk.zip"
            Invoke-WebRequest "https://api.adoptium.net/v3/binary/latest/$JdkMajor/ga/windows/x64/jdk/hotspot/normal/eclipse" -OutFile $zip
            $tmp = Join-Path $Tools "jdk-tmp"
            Expand-Archive $zip $tmp -Force
            Move-Item (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName $JdkDir
            Remove-Item $tmp, $zip -Recurse -Force
        }

        if (-not (Test-Path "$Neo4jHome\bin\neo4j.bat")) {
            Write-Host "Downloading Neo4j Community $Neo4jVersion..."
            $zip = Join-Path $Tools "neo4j.zip"
            Invoke-WebRequest "https://dist.neo4j.org/neo4j-community-$Neo4jVersion-windows.zip" -OutFile $zip
            Expand-Archive $zip $Tools -Force
            Remove-Item $zip -Force
        }

        $conf = "$Neo4jHome\conf\neo4j.conf"
        if (-not (Select-String -Path $conf -Pattern "^dbms.usage_report.enabled" -Quiet)) {
            Add-Content $conf "`ndbms.usage_report.enabled=false"
        }

        Use-Jdk
        $password = Get-EnvValue "NEO4J_PASSWORD" "mulecopilot"
        & "$Neo4jHome\bin\neo4j-admin.bat" dbms set-initial-password $password
        Write-Host "Neo4j ready at $Neo4jHome. Run: .\scripts\neo4j.ps1 start"
    }
    "start" {
        Use-Jdk
        Start-Process -FilePath "$Neo4jHome\bin\neo4j.bat" -ArgumentList "console" -WindowStyle Minimized
        Write-Host "Starting Neo4j... Browser at http://localhost:7474 (bolt://localhost:7687)"
    }
    "stop" {
        Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" |
            Where-Object { $_.CommandLine -like "*$Neo4jHome*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Stopped Neo4j (pid $($_.ProcessId))" }
    }
    "status" {
        $proc = Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" |
            Where-Object { $_.CommandLine -like "*$Neo4jHome*" }
        if ($proc) { Write-Host "Neo4j running (pid $($proc.ProcessId))" } else { Write-Host "Neo4j not running" }
    }
}
