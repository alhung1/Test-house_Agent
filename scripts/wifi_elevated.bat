@echo off
netsh wlan connect name=RFLabTest > C:\temp\wifi_result.txt 2>&1
timeout /t 5 /nobreak > nul
netsh wlan show interfaces >> C:\temp\wifi_result.txt 2>&1
netsh interface ip show address "Wi-Fi" >> C:\temp\wifi_result.txt 2>&1
