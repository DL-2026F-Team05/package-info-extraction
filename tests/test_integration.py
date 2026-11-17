"""AI 4 레포(AI_tts_web)와 합쳤을 때도 동작하는지 확인한다.

AI_tts_web 을 받아 두고 경로를 알려 줄 때만 돈다 (없으면 건너뜀).
    AI_TTS_WEB=../AI_tts_web python -m pytest -q tests/test_integration.py

1. packreader 와 같이 있으면 extraction 이 packreader 의 원본 형식(Fields 등)을 쓰고, 이 레포 테스트가 그대로 통과한다.
2. AI_tts_web 복사본 맨 위에 extraction/ 폴더를 넣고, AI 3 자리인 packreader/extract/ 의 두 파일만
   이 모듈을 부르게 바꾸면 AI_tts_web 의 테스트가 모두 통과한다. 다른 사람 파일은 하나도 바꾸지 않는다.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

AI4 = os.environ.get("AI_TTS_WEB")
pytestmark = pytest.mark.skipif(not (AI4 and Path(AI4, "packreader").is_dir()), reason="AI_TTS_WEB 경로가 없어서 건너뜀")

# AI_tts_web 의 packreader/extract/ 에 넣는 연결 파일 (README '합칠 때' 와 같은 내용)
SHIM_INIT = '''"""AI 3 정보 추출. 구현은 package-info-extraction 의 extraction 패키지에 있다."""
from extraction import ALLERGENS, assess_risk, canonical, canonicalize_list, extract, extract_fields, find_allergens

__all__ = ["ALLERGENS", "canonical", "canonicalize_list", "find_allergens", "extract_fields", "assess_risk", "extract"]
'''
SHIM_ALLERGENS = "from extraction.allergens import *  # noqa: F401,F403\nfrom extraction.allergens import ALLERGENS  # noqa: F401\n"


def _env(*paths: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in paths)
    return env


def test_own_tests_pass_with_packreader_types():
    r = subprocess.run(
        [sys.executable, "-c", "import extraction, sys; sys.exit(0 if extraction.INTEGRATED else 1)"],
        env=_env(Path(AI4).resolve(), ROOT), capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_spec_ai4.py", "tests/test_extraction.py"],
        cwd=ROOT, env=_env(Path(AI4).resolve(), ROOT), capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout[-3000:]


def test_ai_tts_web_suite_passes_with_this_module(tmp_path):
    repo = tmp_path / "AI_tts_web"
    shutil.copytree(AI4, repo, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "outputs"))
    shutil.copytree(ROOT / "extraction", repo / "extraction", ignore=shutil.ignore_patterns("__pycache__"))
    ext = repo / "packreader" / "extract"
    (ext / "__init__.py").write_text(SHIM_INIT, encoding="utf-8")
    (ext / "allergens.py").write_text(SHIM_ALLERGENS, encoding="utf-8")
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts="]
    try:
        import fastapi  # noqa: F401
    except ImportError:
        args += ["--ignore=tests/test_api.py"]  # 웹 서버 테스트는 fastapi 가 있을 때만
    r = subprocess.run(args, cwd=repo, env=_env(repo), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-3000:]
    assert " passed" in r.stdout
