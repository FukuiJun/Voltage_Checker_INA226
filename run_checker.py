"""exe（PyInstaller）と開発時の起動用。`python run_checker.py --mock` で実機なしで試せる。"""

from battery_checker.app import main

if __name__ == "__main__":
    raise SystemExit(main())
