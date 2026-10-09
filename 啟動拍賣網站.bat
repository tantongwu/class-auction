@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 班級拍賣網站

rem 找 Python：先試 python，再試 py
set "PY="
python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
  echo [錯誤] 找不到 Python，請先到 https://www.python.org 安裝 Python 3.11 以上版本，
  echo        安裝時記得勾選 "Add python.exe to PATH"。
  pause
  exit /b 1
)

rem 從別台電腦複製過來的 .venv 不能用，要重新建立
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
  if errorlevel 1 (
    echo 偵測到從別台電腦複製來的 Python 環境，重新建立中...
    rmdir /s /q ".venv"
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo 第一次執行：正在建立 Python 環境，請稍候...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)

rem 套件清單有變動（或第一次執行）時才安裝
fc /b requirements-local.txt ".venv\installed-requirements.txt" >nul 2>nul
if errorlevel 1 (
  echo 正在安裝需要的套件（需要網路，大約 1 分鐘）...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-local.txt
  if errorlevel 1 goto :fail
  copy /y requirements-local.txt ".venv\installed-requirements.txt" >nul
)

".venv\Scripts\python.exe" serve.py
pause
exit /b 0

:fail
echo.
echo [錯誤] 安裝失敗，請確認網路連線後再試一次。
pause
exit /b 1
