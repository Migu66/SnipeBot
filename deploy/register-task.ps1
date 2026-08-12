<#
Registra una Tarea Programada de Windows que ejecuta main.py cada
$RepeatMinutes minutos a partir de $DailyAt, usando el Python del entorno
virtual del proyecto (.venv). Equivalente al systemd timer de
deploy/snipebot.timer para Linux.

La repeticion intradia evita que un solo disparo diario a una hora fija se
pierda sin mas intentos si el PC esta apagado/dormido justo en ese instante
(StartWhenAvailable solo ayuda si el PC llega a encenderse ese dia).

Uso:
    .\deploy\register-task.ps1
    .\deploy\register-task.ps1 -DailyAt "21:00" -RepeatMinutes 60

Requiere haber creado antes el entorno virtual:
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
#>

param(
    [string]$DailyAt = "09:00",
    [int]$RepeatMinutes = 30
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$MainScript = Join-Path $ProjectRoot "main.py"

if (-not (Test-Path $PythonExe)) {
    throw "No se encuentra $PythonExe. Crea antes el entorno virtual: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$MainScript`"" -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$Trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $DailyAt `
    -RepetitionInterval (New-TimeSpan -Minutes $RepeatMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 1)).Repetition

# IgnoreNew evita que se solape con una ejecucion anterior todavia en marcha,
# ademas del lock de fichero que ya hace main.py por su cuenta.
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -DontStopOnIdleEnd

Register-ScheduledTask -TaskName "SnipeBot" `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Vigila Wallapop/Vinted y notifica por Telegram (repite cada $RepeatMinutes min)" `
    -Force | Out-Null

Write-Host "Tarea 'SnipeBot' registrada: ejecuta $MainScript desde las $DailyAt, cada $RepeatMinutes min durante el dia."
Write-Host "Consultar estado:  Get-ScheduledTask -TaskName SnipeBot | Get-ScheduledTaskInfo"
Write-Host "Quitarla:          Unregister-ScheduledTask -TaskName SnipeBot -Confirm:`$false"
