"""
WeChatAuto CLI - 命令行微信发信工具
用法:
  python scripts/cli.py --to uu --content "你好"
  python scripts/cli.py --to uu --content "https://..." --action sendpic
  python scripts/cli.py --to uu --content "C:/file.zip" --action sendfile
"""
import argparse
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from wechat_controller import WeChatController
except ModuleNotFoundError as e:
    print(json.dumps({"success": False, "code": "DEP_MISS", "message": f"缺少依赖: {e.name}，请执行 pip install -r requirements.txt"}))
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="WeChatAuto - 微信发信CLI")
    parser.add_argument("--to", required=False, help="联系人或群名称（direct模式不需要）")
    parser.add_argument("--content", required=True, help="文本内容、图片URL或文件路径")
    parser.add_argument("--action", choices=["sendtext", "sendpic", "sendfile"], default="sendtext")
    parser.add_argument("--direct", action="store_true", help="直接发送到当前聊天（不搜索联系人）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")

    args = parser.parse_args()

    def out(success, code, msg):
        if args.json:
            print(json.dumps({"success": success, "code": code, "message": msg, "to": args.to, "action": args.action}, ensure_ascii=False))
        else:
            if success:
                print(msg)
            else:
                print(f"失败 [{code}]: {msg}")
        sys.exit(0 if success else 1)

    try:
        ctrl = WeChatController()
        if args.direct:
            r = ctrl.send_direct(args.content)
        elif args.action == "sendpic":
            r = ctrl.send_pic_to(args.to, args.content)
        elif args.action == "sendfile":
            r = ctrl.send_file_to(args.to, args.content)
        else:
            r = ctrl.send_to(args.to, args.content)
        out(r.success, r.code, r.message)
    except Exception as e:
        out(False, "EXCEPTION", str(e))


if __name__ == "__main__":
    main()
