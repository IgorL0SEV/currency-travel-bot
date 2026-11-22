@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    echo Создаю виртуальное окружение…
    python -m venv venv
    if errorlevel 1 goto :error
)

echo Проверяю зависимости…
venv\Scripts\python.exe -m pip install -q -r requirements.txt
if errorlevel 1 goto :error

if /I "%~1"=="demo" (
    venv\Scripts\python.exe demo.py
) else (
    venv\Scripts\python.exe bot.py
)
goto :eof

:error
echo.
echo Не удалось подготовить окружение. Проверьте, что установлен Python 3.10+.
pause
exit /b 1
