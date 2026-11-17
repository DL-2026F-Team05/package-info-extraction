import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 레포 맨 위에서 `python -m pytest` 로 실행
