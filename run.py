import sys
from pathlib import Path

# Ensure current package directory is on sys.path
package_dir = Path(__file__).resolve().parent
if str(package_dir) not in sys.path:
    sys.path.insert(0, str(package_dir))

try:
    from .main import WinVoiceApp
except ImportError:
    from main import WinVoiceApp

def main():
    app = WinVoiceApp()
    app.start()

if __name__ == "__main__":
    main()
