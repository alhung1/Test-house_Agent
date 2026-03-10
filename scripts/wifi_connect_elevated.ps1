param(
    [string]$SSID = "RFLabTest",
    [string]$OutputPath = "C:\Users\alhung\Test House Agent\artifacts\wifi_connect_result.txt"
)

$result = @()
$result += "=== CONNECT ==="
$connectOutput = netsh wlan connect name=$SSID 2>&1
$result += $connectOutput
$result += "EXIT_CODE=$LASTEXITCODE"
$result += ""

Start-Sleep -Seconds 5

$result += "=== INTERFACES ==="
$ifOutput = netsh wlan show interfaces 2>&1
$result += $ifOutput
$result += ""

$result += "=== IP CONFIG ==="
$ipOutput = netsh interface ip show address "Wi-Fi" 2>&1
$result += $ipOutput

$result | Out-File -FilePath $OutputPath -Encoding UTF8
