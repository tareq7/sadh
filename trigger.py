"""Send an optional command to a running local Sadh instance."""
import socket
import sys


def trigger(cmd: str = "toggle") -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(cmd.encode("utf-8"), ("127.0.0.1", 45454))
    print(f"[Sadh] Trigger command '{cmd}' sent.")


def main() -> None:
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "toggle"
    trigger(action)


if __name__ == "__main__":
    main()
