---
name: "wechat-auto"
description: "微信发信：通过桌面微信发送文本、图片URL或本地文件。在 Windows 上操作已登录的微信客户端。触发词：微信发消息、微信通知、微信发文件"
---

# WeChatAuto Skill

在 Windows 上通过桌面微信发送消息给指定联系人或群。

## 调用方式

项目路径: `E:\PROJECT\WeChatAuto`

```bash
cd E:\PROJECT\WeChatAuto
python scripts/cli.py --to "联系人或群名称" --content "消息内容"
python scripts/cli.py --to "联系人或群名称" --content "图片URL" --action sendpic
python scripts/cli.py --to "联系人或群名称" --content "本地文件路径" --action sendfile
```

## 注意事项

1. 微信 PC 客户端必须已登录
2. `--to` 名称需与微信备注完全一致
3. 发送本地文件使用**绝对路径**
4. 消息长度建议不超过 2000 字，过长则分段发送
5. 失败后不要自动重试，将错误代码反馈给用户
6. 使用 `--json` 获取结构化输出
