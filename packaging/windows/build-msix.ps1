<#
.SYNOPSIS
    Builds a Store-ready MSIX package for ByteProof (PyInstaller output).
.DESCRIPTION
    Stages the app, writes AppxManifest.xml from msix-config.json, packs with
    MakeAppx.exe, and signs with a self-signed certificate whose subject
    matches the Publisher in the manifest. The Microsoft Store re-signs the
    package when you upload it, so no paid certificate is required.
#>
param(
    [string]$AppDir = "dist\ByteProof",
    [string]$ConfigPath = "packaging\windows\msix-config.json",
    [string]$AssetsDir = "packaging\windows\Assets",
    [string]$Output = "ByteProof_Installer_x64.msix"
)

$ErrorActionPreference = "Stop"

$script:BootstrapDir = $null

function Find-KitTool {
    param([string]$Name)

    # 1) Use an installed Windows SDK if one is available.
    $roots = @(
        "$env:ProgramFiles(x86)\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
    )
    $tool = $roots |
        ForEach-Object { Get-ChildItem -Path $_ -Filter "$Name.exe" -Recurse -ErrorAction SilentlyContinue } |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -ne $tool) { return $tool }

    # 2) Fall back to the official Windows SDK BuildTools NuGet package.
    #    This works on GitHub runners and dev machines without admin rights.
    if ($null -eq $script:BootstrapDir) {
        $tempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
        $script:BootstrapDir = Join-Path $tempRoot "windows-sdk-buildtools"
        $nupkg = Join-Path $tempRoot "Microsoft.Windows.SDK.BuildTools.nupkg"
        if (-not (Test-Path $nupkg)) {
            Write-Host "Windows SDK tools not installed; downloading Microsoft.Windows.SDK.BuildTools ..."
            Invoke-WebRequest -Uri "https://www.nuget.org/api/v2/package/Microsoft.Windows.SDK.BuildTools" -OutFile $nupkg
        }
        if (Test-Path $script:BootstrapDir) { Remove-Item -Recurse -Force $script:BootstrapDir }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($nupkg, $script:BootstrapDir)
    }

    $tool = Get-ChildItem -Path (Join-Path $script:BootstrapDir "bin") -Filter "$Name.exe" -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "\\x64\\" } |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    return $tool
}

if (-not (Test-Path $AppDir)) { throw "App directory not found: $AppDir" }
if (-not (Test-Path $ConfigPath)) { throw "Config not found: $ConfigPath" }
if (-not (Test-Path $AssetsDir)) { throw "Assets directory not found: $AssetsDir" }

$config = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
$identityName = $config.identity_name
$publisher = $config.publisher
$displayName = $config.display_name
$publisherDisplayName = $config.publisher_display_name
$description = $config.description
$minVersion = $config.min_version
$maxVersionTested = $config.max_version_tested
$architecture = $config.architecture

# Keep the existing ZIP release working until the Store identity is configured.
$isPlaceholder = $identityName -like "REPLACE*" -or $publisher -like "CN=REPLACE*"
if ($isPlaceholder) {
    Write-Host "MSIX skipped: set identity_name and publisher in $ConfigPath"
    Write-Host "Get them from Partner Center > your app > Product management > Product identity."
    Write-Host "The ZIP release is unaffected."
    exit 0
}

# Version comes from version_info.txt so tag and manual builds stay in sync.
$versionInfo = Get-Content -Raw -Path "version_info.txt"
$versionMatch = [regex]::Match($versionInfo, 'filevers=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)')
if (-not $versionMatch.Success) { throw "Could not read the version from version_info.txt" }
$version = "$($versionMatch.Groups[1].Value).$($versionMatch.Groups[2].Value).$($versionMatch.Groups[3].Value).$($versionMatch.Groups[4].Value)"

Write-Host "Building ByteProof MSIX $version ..."

# Stage the app folder + Assets, then write the manifest at the root.
$stage = Join-Path $PWD "build\msix-stage"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage -Force | Out-Null
Copy-Item -Path (Join-Path $AppDir "*") -Destination $stage -Recurse -Force
Copy-Item -Path $AssetsDir -Destination $stage -Recurse -Force

function Escape-Xml([string]$Value) {
    return [System.Security.SecurityElement]::Escape($Value)
}

$manifest = @"
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap rescap">
  <Identity Name="$identityName" Publisher="$publisher" Version="$version" ProcessorArchitecture="$architecture" />
  <Properties>
    <DisplayName>$displayName</DisplayName>
    <PublisherDisplayName>$publisherDisplayName</PublisherDisplayName>
    <Description>$(Escape-Xml $description)</Description>
    <Logo>Assets\StoreLogo.png</Logo>
  </Properties>
  <Resources>
    <Resource Language="en-us" />
  </Resources>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="$minVersion" MaxVersionTested="$maxVersionTested" />
  </Dependencies>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
  <Applications>
    <Application Id="ByteProof" Executable="ByteProof.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="$displayName"
                          Description="$(Escape-Xml $description)"
                          BackgroundColor="transparent"
                          Square150x150Logo="Assets\Square150x150Logo.png"
                          Square44x44Logo="Assets\Square44x44Logo.png" />
    </Application>
  </Applications>
</Package>
"@

Set-Content -Path (Join-Path $stage "AppxManifest.xml") -Value $manifest -Encoding UTF8

# Pack with MakeAppx.exe from the Windows SDK.
$makeappx = Find-KitTool "MakeAppx"
if ($null -eq $makeappx) {
    Write-Host "MSIX skipped: MakeAppx.exe not found (Windows SDK missing)."
    Write-Host "The ZIP release is unaffected."
    exit 0
}
& $makeappx.FullName pack /d $stage /p $Output /o
if ($LASTEXITCODE -ne 0) { throw "MakeAppx.exe failed with exit code $LASTEXITCODE" }

# Self-signed certificate whose subject matches the manifest Publisher.
# The Store replaces this signature during certification.
$cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject $publisher `
    -CertStoreLocation Cert:\CurrentUser\My `
    -KeyExportPolicy Exportable `
    -KeySpec Signature `
    -KeyUsage DigitalSignature `
    -TextExtension "2.5.29.37={text}1.3.6.1.5.5.7.3.3"

$signtool = Find-KitTool "signtool"
if ($null -eq $signtool) {
    Write-Host "MSIX skipped: signtool.exe not found (Windows SDK missing)."
    Write-Host "The ZIP release is unaffected."
    exit 0
}
$timestampUrls = @("http://timestamp.digicert.com", "http://timestamp.sectigo.com")
$signed = $false
foreach ($timestampUrl in $timestampUrls) {
    & $signtool.FullName sign /fd SHA256 /tr $timestampUrl /td SHA256 /sha1 $cert.Thumbprint /v $Output
    if ($LASTEXITCODE -eq 0) { $signed = $true; break }
}
if (-not $signed) {
    # Timestamp servers can be unreachable in CI; the signature is still valid.
    & $signtool.FullName sign /fd SHA256 /sha1 $cert.Thumbprint /v $Output
    if ($LASTEXITCODE -ne 0) { throw "signtool failed with exit code $LASTEXITCODE" }
}

Write-Host ""
Write-Host "MSIX created: $Output (version $version)"
Get-Item $Output | Select-Object Name, Length, LastWriteTime
