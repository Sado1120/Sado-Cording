"""경량 문법 검사.

`backend/app.py`가 런타임 이전에 문법 오류 없이 컴파일되는지 빠르게 확인한다.
테스트가 실패하면 해당 파일의 구문 오류를 즉시 알 수 있으므로, 대시보드가
로딩 단계에서 멈추는 문제를 예방할 수 있다.
"""

from pathlib import Path
import py_compile
import compileall


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_backend_app_py_syntax():
    """`backend/app.py`가 파이썬 바이트코드로 컴파일되는지 확인한다."""

    target = PROJECT_ROOT / "backend" / "app.py"
    # doraise=True 옵션으로 문법 오류 발생 시 바로 예외를 일으켜 테스트를 실패시킨다.
    py_compile.compile(target, doraise=True)


def test_backend_package_syntax():
    """백엔드 전체 모듈을 컴파일해 숨은 구문 오류를 조기에 차단한다."""

    backend_dir = PROJECT_ROOT / "backend"
    success = compileall.compile_dir(str(backend_dir), quiet=1)
    assert success, "backend 패키지 컴파일 실패: 숨은 문법 오류를 확인하세요."

