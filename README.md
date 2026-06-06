# WeChatAuto

纯视觉微信自动回复机器人。通过音频事件回调检测微信消息 → 截图 OCR → AI 决策 → 自动回复。

**原理**：`pycaw` 事件回调 + `pyautogui` 坐标点击 + `winocr` OCR + AI 对话

## 快速开始

```bash
pip install -r requirements.txt

# 启动自动回复
python agent.py

# 启动自动回复 + 调试断点模式
python agent.py --debug
```

## 发送消息（手动）

```bash
python scripts/cli.py --to "联系人名称" --content "你好"
python scripts/cli.py --to "联系人名称" --content "图片URL" --action sendpic
```

## 项目结构

```
WeChatAuto/
├── agent.py              # 主程序：自动回复机器人
├── sound_trigger.py      # 音频事件回调检测
├── test_sound.py         # 音频检测测试
├── calibrate.py          # 窗口坐标校准工具
├── ocr_debug.py          # OCR 调试工具
├── listener.py           # 备用方案：Cipher 桥接轮询
├── scripts/
│   ├── wechat_controller.py    # 核心控制器
│   └── cli.py                  # 命令行入口
├── config.json           # 基础配置
├── config_ai.json        # AI 配置（API key 等）
├── calib.json            # 校准数据（calibrate.py 生成）
├── requirements.txt
└── README.md
```

## 原理流程

```
SoundTrigger 音频回调
    ↓
process() 加载通讯录、激活微信窗口
    ↓
截图 → OCR 识别联系人 & 对话内容
    ↓
判断免回复名单 / AI 生成回复
    ↓
scripts/cli.py 发送
```

## 依赖

- Windows 10/11 + 微信 PC 客户端
- Python 3.10+
- 详见 requirements.txt
