# **本项目即将更新为基于lunix虚拟屏的后台ocr以能够后台化/服务器化，只需一些时间..！**


> **本程序为 AI 辅助开发**

# WeChatAuto

纯视觉微信自动回复机器人。通过音频事件回调检测微信消息 → 截图 OCR → AI 决策 → 自动回复。

**原理**：`pycaw` 事件回调 + `pyautogui` 坐标点击 + `winocr` OCR + AI 对话

## 特点

本项目以 **图形化点击 + OCR 识别** 的方式模拟真人操作微信，**并非通过 Hook 微信 API 或注入内存**，因此不存在账号风控风险。

- ✅ **无风控风险** — 纯视觉+模拟点击
- ✅ **即装即用** — 装好依赖、校准一次坐标即可
- ✅ **API、提示词自定义** — 支持修改，支持对每个用户单独设置回复提示词
- ⏺️ **目前用法** — 挂备用电脑
> 市面上其他方案：
> - Hook 微信 WebSocket / gRPC 接口 → 速度快但容易被封
> - wechat4u / ItChat 等 Web 协议方案 → 微信已限制登录
>
> 本项目走的是最笨但最稳的纯视觉模拟路线。

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
├── calibrate.py          # 窗口坐标校准工具
├── listener.py           # 备用方案：Cipher 桥接轮询
├── install.bat           # 一键安装脚本
├── run.bat               # 启动脚本
├── scripts/
│   ├── wechat_controller.py    # 核心控制器
│   └── cli.py                  # 命令行入口
├── params/               # 参数配置
│   ├── config.json       # 基础配置
│   ├── config_ai.json    # AI 配置（本地，已屏蔽）
│   └── calib.json        # 校准数据（本地，已屏蔽）
├── profiles/             # 个人资料（本地，不上传）
│   ├── 自动回复规则.json
│   └── About uu.md
├── debug/                # 调试工具
│   ├── ocr_debug.py
│   └── test_sound.py
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

---

> 本程序由 AI 辅助开发完成，仅供学习交流使用。
