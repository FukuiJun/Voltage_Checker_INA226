@echo off
rem Build INA226_BatteryChecker.exe (folder distribution + zip).
rem Usage: run build.bat in this folder. Requires Python 3.11 or later.
rem (ASCII only: cmd.exe reads .bat files in the console code page.)
setlocal
cd /d "%~dp0"

if not exist .venv (
  py -3 -m venv .venv || python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
python -m pip install -r requirements-dev.txt || goto :error

echo === Running tests ===
python -m pytest -q || goto :error

echo === Building exe ===
pyinstaller --noconfirm --clean INA226_BatteryChecker.spec || goto :error

for /f %%v in ('python -c "import battery_checker as m; print(m.__version__)"') do set VER=%%v
powershell -NoProfile -Command "Compress-Archive -Force -Path dist\INA226_BatteryChecker -DestinationPath INA226_BatteryChecker_v%VER%.zip" || goto :error

echo.
echo Done: dist\INA226_BatteryChecker\INA226_BatteryChecker.exe
echo       INA226_BatteryChecker_v%VER%.zip
exit /b 0

:error
echo Build FAILED.
exit /b 1
