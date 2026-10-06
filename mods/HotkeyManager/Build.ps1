param(
    [string]$GameDir = 'C:\Program Files (x86)\Steam\steamapps\common\Graveyard Keeper 2'
)
$ErrorActionPreference = 'Stop'
$project = Join-Path $PSScriptRoot 'GK2.HotkeyManager.csproj'
$tests = Join-Path $PSScriptRoot 'Tests\Tests.csproj'
& dotnet build $project -c Release --nologo -v quiet "-p:GameDir=$GameDir"
if ($LASTEXITCODE -ne 0) { throw 'Mod build failed.' }
& dotnet run --project $tests -c Release --no-launch-profile "-p:GameDir=$GameDir"
if ($LASTEXITCODE -ne 0) { throw 'Binding checks failed.' }

$output = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\dist\GK2-Hotkey-Manager-1.3.0.zip'))
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($output)) | Out-Null
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$stream = [IO.File]::Open($output, [IO.FileMode]::Create, [IO.FileAccess]::Write)
$archive = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create)
try {
    [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,
        (Join-Path $PSScriptRoot 'bin\Release\netstandard2.1\GK2.HotkeyManager.dll'),
        'BepInEx/plugins/HotkeyManager/GK2.HotkeyManager.dll') | Out-Null
    [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,
        (Join-Path $PSScriptRoot 'README.md'), 'README.md') | Out-Null
    [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,
        (Join-Path $PSScriptRoot 'API.md'), 'API.md') | Out-Null
    [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,
        (Join-Path $PSScriptRoot 'Integration\OptionalHotkeys.cs'), 'Integration/OptionalHotkeys.cs') | Out-Null
}
finally { $archive.Dispose(); $stream.Dispose() }
Write-Output "Created $output"
