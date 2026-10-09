"""在教室電腦上啟動拍賣網站（給「啟動拍賣網站.bat」使用）。

- 第一次執行時自動建立 .env，並產生老師密碼
- 顯示同學手機要打開的網址
- 自動打開瀏覽器
"""
import os
import secrets
import socket
import sys
import threading
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", 5000))


def ensure_env_file():
    """沒有 .env 時，從 .env.example 複製一份並填入隨機的老師密碼。"""
    env_path = HERE / ".env"
    if env_path.exists():
        return None
    password = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(8))
    text = (HERE / ".env.example").read_text(encoding="utf-8")
    text = text.replace("TEACHER_PASSWORD=請改成你的老師密碼", f"TEACHER_PASSWORD={password}")
    text = text.replace("SECRET_KEY=請改成一長串亂碼", f"SECRET_KEY={secrets.token_hex(32)}")
    env_path.write_text(text, encoding="utf-8")
    return password


def lan_addresses():
    """找出這台電腦在區域網路（Wi-Fi）上的 IP 位址。"""
    found = []
    try:
        # 不會真的送出資料，只是讓系統告訴我們「對外連線會用哪個 IP」
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.1.1", 80))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith("127.")]


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    os.chdir(HERE)
    new_password = ensure_env_file()

    if port_in_use(PORT):
        print(f"\n[錯誤] 埠號 {PORT} 已經被使用了，可能拍賣網站已經開著。")
        print("請先關掉另一個拍賣網站的視窗，再重新啟動。")
        sys.exit(1)

    from waitress import serve

    from app import create_app

    app = create_app()

    line = "=" * 52
    print(line)
    print("  班級拍賣網站已啟動")
    print(line)
    print(f"  老師（這台電腦）： http://127.0.0.1:{PORT}")
    addresses = lan_addresses()
    if addresses:
        print("  同學手機（要連同一個 Wi-Fi）：")
        for ip in addresses:
            print(f"      http://{ip}:{PORT}")
    else:
        print("  找不到這台電腦的區網 IP，請確認已連上 Wi-Fi。")
    print(line)
    if new_password:
        print(f"  已建立設定檔 .env，老師密碼是：{new_password}")
        print("  （之後可以用記事本打開 .env 修改）")
        print(line)
    print("  要關閉網站：直接關掉這個視窗，或按 Ctrl + C")
    print(line, flush=True)

    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    serve(app, host="0.0.0.0", port=PORT, threads=16, _quiet=True)


if __name__ == "__main__":
    main()
