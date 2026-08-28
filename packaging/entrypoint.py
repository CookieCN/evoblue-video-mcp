"""PyInstaller entrypoint shim for the Local Engine."""

from evoblue_video_mcp.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
