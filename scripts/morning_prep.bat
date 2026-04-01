@echo off
:: BlunderBus Morning Prep — runs at 6:00 AM daily
:: Creates today's Obsidian daily note with carried tasks and calendar schedule

cd /d C:\Users\brian\Desktop\blunderbus-claude

:: Load env vars
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
)

:: Fetch Obsidian token from Vaultwarden at runtime
for /f "delims=" %%T in ('bw unlock "%BW_MASTER_PASS%" --raw 2^>nul') do set BW_SESSION=%%T
for /f "delims=" %%T in ('bw list items --search "obsidian" --session "%BW_SESSION%" 2^>nul ^| python -c "import sys,re; data=sys.stdin.read(); m=re.search(r'\"Token\",\"value\":\"([^\"]+)\"', data); print(m.group(1) if m else '')"') do set OBSIDIAN_TOKEN=%%T

:: Run morning prep
py scripts\morning_prep.py >> logs\morning_prep.log 2>&1

exit /b 0
