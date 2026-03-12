# Submit-Photo.ps1
# Submits a photo to company-bg API and downloads all result images
#
# Usage:
#   .\Submit-Photo.ps1 -PhotoPath "C:\Users\mike\Pictures\photo.jpg" -Name "Mike Guimaraes" -Email "mike@example.com"

param (
    [Parameter(Mandatory = $true)]
    [string]$PhotoPath,

    [Parameter(Mandatory = $true)]
    [string]$Name,

    [Parameter(Mandatory = $true)]
    [string]$Email
)

$ApiUrl    = "http://192.168.9.113:8000"
$Token     = "ebwRvKT7JZ4YLkV0KmLRhSVaJDIdDoRKVHdFniFX-7TRRxPmCKkMeEw_ou07eLnl"
$OutputDir = "$env:USERPROFILE\Downloads\company-bg"

# ── Validate input ────────────────────────────────────────────────────────────
if (-not (Test-Path $PhotoPath)) {
    Write-Error "Photo not found: $PhotoPath"
    exit 1
}

# ── Submit job ────────────────────────────────────────────────────────────────
Write-Host "Submitting photo..." -ForegroundColor Cyan

$boundary = [System.Guid]::NewGuid().ToString()
$fileBytes = [System.IO.File]::ReadAllBytes($PhotoPath)
$fileName  = [System.IO.Path]::GetFileName($PhotoPath)

$bodyLines = @(
    "--$boundary",
    "Content-Disposition: form-data; name=`"file`"; filename=`"$fileName`"",
    "Content-Type: image/jpeg",
    "",
    [System.Text.Encoding]::GetEncoding("iso-8859-1").GetString($fileBytes),
    "--$boundary",
    "Content-Disposition: form-data; name=`"name`"",
    "",
    $Name,
    "--$boundary",
    "Content-Disposition: form-data; name=`"email`"",
    "",
    $Email,
    "--$boundary--"
)

$body = $bodyLines -join "`r`n"

try {
    $response = Invoke-RestMethod `
        -Uri "$ApiUrl/process-image/" `
        -Method POST `
        -ContentType "multipart/form-data; boundary=$boundary" `
        -Body ([System.Text.Encoding]::GetEncoding("iso-8859-1").GetBytes($body)) `
        -Headers @{ Authorization = "Bearer $Token" }
} catch {
    Write-Error "Failed to submit: $_"
    exit 1
}

$jobId = $response.job_id
Write-Host "Job submitted: $jobId" -ForegroundColor Green

# ── Poll for completion ───────────────────────────────────────────────────────
Write-Host "Processing..." -ForegroundColor Cyan

$maxAttempts = 30
$attempt     = 0

do {
    Start-Sleep -Seconds 4
    $attempt++

    $status = Invoke-RestMethod `
        -Uri "$ApiUrl/status/$jobId" `
        -Method GET `
        -Headers @{ Authorization = "Bearer $Token" }

    Write-Host "  Status: $($status.status) (attempt $attempt/$maxAttempts)"

    if ($status.status -eq "failed") {
        Write-Error "Job failed: $($status.error)"
        exit 1
    }

} while ($status.status -ne "done" -and $attempt -lt $maxAttempts)

if ($status.status -ne "done") {
    Write-Error "Timed out waiting for job to complete."
    exit 1
}

# ── Download images ───────────────────────────────────────────────────────────
$imageUrls = $status.image_urls

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "`nDownloading $($imageUrls.Count) images to $OutputDir..." -ForegroundColor Cyan

foreach ($url in $imageUrls) {
    $fileName   = [System.IO.Path]::GetFileName($url)
    $outputPath = Join-Path $OutputDir $fileName

    Invoke-WebRequest -Uri "$ApiUrl$url" -OutFile $outputPath
    Write-Host "  Downloaded: $fileName" -ForegroundColor Green
}

Write-Host "`nDone! All images saved to: $OutputDir" -ForegroundColor Green

# Open the folder
Start-Process explorer.exe $OutputDir
