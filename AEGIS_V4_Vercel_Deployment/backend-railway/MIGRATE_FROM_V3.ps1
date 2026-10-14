$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$old = Read-Host 'Full path to previous AEGIS V3 folder (close both apps before migration)'
if (-not (Test-Path -LiteralPath $old -PathType Container)) { throw 'Previous project folder was not found.' }
$old = (Resolve-Path -LiteralPath $old).Path
if ($old.TrimEnd('\') -eq $PSScriptRoot.TrimEnd('\')) { throw 'Old and new folder cannot be the same.' }
if (-not (Test-Path -LiteralPath (Join-Path $old 'state\aegis.db'))) { throw 'No old state\aegis.db found. Check folder path.' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $PSScriptRoot "migration_backups\$stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
$relatives = @('state\aegis.db','data\policy.md','outbox.json','agent_log.jsonl')
foreach ($rel in $relatives) {
  $source = Join-Path $old $rel
  $dest = Join-Path $PSScriptRoot $rel
  if (Test-Path -LiteralPath $source -PathType Leaf) {
    if (Test-Path -LiteralPath $dest -PathType Leaf) {
      $bk = Join-Path $backup $rel
      New-Item -ItemType Directory -Force -Path (Split-Path $bk) | Out-Null
      Copy-Item -LiteralPath $dest -Destination $bk -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item -LiteralPath $source -Destination $dest -Force
    Write-Host "Migrated: $rel" -ForegroundColor Green
  }
}
$oldInbox = Join-Path $old 'data\inbox'
$newInbox = Join-Path $PSScriptRoot 'data\inbox'
if (Test-Path -LiteralPath $oldInbox -PathType Container) {
  New-Item -ItemType Directory -Force -Path $newInbox | Out-Null
  Get-ChildItem -LiteralPath $oldInbox -Filter '*.json' -File | ForEach-Object {
    $dest = Join-Path $newInbox $_.Name
    if (Test-Path -LiteralPath $dest) { Copy-Item -LiteralPath $dest -Destination (Join-Path $backup $_.Name) -Force }
    Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
  }
}
Write-Host "Migration complete. Previous destination files backed up at $backup" -ForegroundColor Cyan
Write-Host 'Gmail tokens and API keys remain in memory and are not migrated. Reconnect using Settings.'
Read-Host 'Press Enter to close'
