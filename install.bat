@echo off
chcp 65001 >nul
title WeChatAuto 安装向导

echo ============================================
echo   WeChatAuto - 微信自动回复机器人 安装脚本
echo ============================================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] 安装 Python 依赖...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if %errorlevel% neq 0 (
    echo [警告] 部分包安装失败，尝试直接安装...
    pip install pyautogui pyperclip uiautomation Pillow requests pywin32 psutil comtypes pycaw
)

echo.
echo [2/4] 安装 winocr（Windows OCR 引擎）...
pip install winocr -i https://pypi.tuna.tsinghua.edu.cn/simple
if %errorlevel% neq 0 (
    echo [提示] winocr 安装失败，将使用其他 OCR 引擎
)

echo.
echo [3/4] 校准屏幕坐标...
echo 请确保微信已登录并打开，且窗口不要最小化。
echo 脚本将自动打开微信并检测会话列表位置。
echo.
pause
echo 正在运行校准...
python calibrate.py

echo.
echo [4/4] 配置完成！
echo.
echo ============ 使用方法 ============
echo 1. 确保微信已登录并打开
echo 2. 运行: python agent.py
echo 3. 打开浏览器访问: http://localhost:8080
echo 4. 在管理面板开启自动回复
echo ================================
echo.
pause
