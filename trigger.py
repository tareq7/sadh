import sys
import socket

def trigger(cmd="toggle"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(cmd.encode("utf-8"), ("127.0.0.1", 45454))
    print(f"[WinVoice] Trigger command '{cmd}' sent successfully!")

if __name__ == "__main__":
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "toggle"
    trigger(action)
