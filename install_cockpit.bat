@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ====================================================================
echo  Установка Kwork Cockpit ^& Guard 2.0 (Десктопное приложение)
echo ====================================================================

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Ошибка: Python не найден в PATH. Установите Python 3.11+
    pause
    exit /b 1
)

echo [1/4] Проверка и установка зависимостей...
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [!] Ошибка при установке requirements.txt
    pause
    exit /b 1
)

echo [2/4] Генерация иконки приложения...
python tools\generate_icon.py

echo [3/4] Создание ярлыка на Рабочем столе...
python tools\install_shortcut.py

echo [4/4] Запуск диагностики рабочего места...
python -c "from cockpit.services.guard_runner import GuardRunnerService; GuardRunnerService().prepare_workplace()"

echo ====================================================================
echo [✓] Установка завершена успешно! Ярлык создан на Рабочем столе.
echo ====================================================================
pause
