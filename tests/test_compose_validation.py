from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Tuple, Union


REPO_ROOT = Path(__file__).resolve().parents[1]


def _iter_repo_files() -> Iterable[Path]:
    ignored_dirs = {".git", "__pycache__"}
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for filename in files:
            yield Path(root) / filename


def _parse_scalar(text: str) -> str:
    if text.startswith(("'", '"')) and text.endswith(("'", '"')) and len(text) >= 2:
        return text[1:-1]
    return text


def _parse_yaml(lines: list[str], start: int, indent: int) -> Tuple[Union[dict, list], int]:
    mapping: dict[str, object] = {}
    sequence: list[object] = []
    mode: str | None = None
    index = start

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped:
            index += 1
            continue

        current_indent = len(raw_line) - len(raw_line.lstrip(" "))
        if current_indent < indent:
            break
        if current_indent > indent:
            raise AssertionError("YAML 들여쓰기가 잘못되었습니다. 탭 대신 공백 2칸을 사용했는지 확인하세요.")

        if stripped.startswith("- "):
            if mode is None:
                mode = "seq"
            elif mode != "seq":
                raise AssertionError("같은 깊이에서 리스트와 맵을 혼용할 수 없습니다.")

            rest = stripped[2:]
            if rest:
                sequence.append(_parse_scalar(rest))
                index += 1
            else:
                value, index = _parse_yaml(lines, index + 1, indent + 2)
                sequence.append(value)
        else:
            if mode is None:
                mode = "map"
            elif mode != "map":
                raise AssertionError("같은 깊이에서 리스트와 맵을 혼용할 수 없습니다.")

            if ":" not in stripped:
                raise AssertionError("키:값 형태의 구문이 필요합니다.")

            key, rest = stripped.split(":", 1)
            key = key.strip()
            rest = rest.strip()
            if rest:
                mapping[key] = _parse_scalar(rest)
                index += 1
            else:
                value, index = _parse_yaml(lines, index + 1, indent + 2)
                mapping[key] = value

    if mode == "seq":
        return sequence, index
    return mapping, index


def _load_compose_yaml(text: str) -> dict:
    lines = text.splitlines()
    parsed, index = _parse_yaml(lines, 0, 0)
    assert index == len(lines), "YAML 파싱이 파일 끝까지 진행되지 못했습니다."
    assert isinstance(parsed, dict), "루트 YAML 구조는 맵 형태여야 합니다."
    return parsed


def _normalise_env(env_value):
    if isinstance(env_value, dict):
        return env_value
    if isinstance(env_value, list):
        result = {}
        for item in env_value:
            if not isinstance(item, str) or "=" not in item:
                raise AssertionError("environment 항목이 key=value 문자열 형식이어야 합니다.")
            key, value = item.split("=", 1)
            result[key] = value
        return result
    raise AssertionError("environment 항목이 dict 또는 list[str] 형식이 아닙니다.")


def test_docker_compose_layout_is_complete():
    compose_path = REPO_ROOT / "docker-compose.yml"
    compose_text = compose_path.read_text(encoding="utf-8")
    compose_data = _load_compose_yaml(compose_text)

    services = compose_data.get("services")
    assert isinstance(services, dict), "`services` 루트 키가 존재하고 매핑이어야 합니다."

    backend = services.get("backend")
    assert isinstance(backend, dict), "backend 서비스 구성이 누락되었거나 올바르지 않습니다."
    backend_build = backend.get("build")
    assert isinstance(backend_build, dict), "backend.build 항목이 올바르지 않습니다."
    assert backend_build.get("context") == ".", "backend.build.context 는 '.' 이어야 합니다."
    assert backend_build.get("dockerfile") == "backend/Dockerfile", "backend Dockerfile 경로가 잘못되었습니다."

    backend_ports = backend.get("ports")
    assert backend_ports and "8000:8000" in backend_ports, "backend 포트 매핑 8000:8000 이 필요합니다."
    backend_env = _normalise_env(backend.get("environment", []))
    assert backend_env.get("PYTHONUNBUFFERED") == "1", "backend 환경 변수 PYTHONUNBUFFERED=1 이 필요합니다."
    assert backend.get("restart") == "unless-stopped", "backend.restart 정책은 unless-stopped 여야 합니다."

    frontend = services.get("frontend")
    assert isinstance(frontend, dict), "frontend 서비스 구성이 누락되었거나 올바르지 않습니다."
    frontend_build = frontend.get("build")
    assert isinstance(frontend_build, dict), "frontend.build 항목이 올바르지 않습니다."
    assert frontend_build.get("context") == ".", "frontend.build.context 는 '.' 이어야 합니다."
    assert frontend_build.get("dockerfile") == "frontend/Dockerfile", "frontend Dockerfile 경로가 잘못되었습니다."

    frontend_ports = frontend.get("ports")
    assert frontend_ports and "8501:8501" in frontend_ports, "frontend 포트 매핑 8501:8501 이 필요합니다."
    frontend_env = _normalise_env(frontend.get("environment", {}))
    assert (
        frontend_env.get("BACKEND_URL") == "http://backend:8000"
    ), "frontend 환경 변수 BACKEND_URL=http://backend:8000 이 필요합니다."
    depends_on = frontend.get("depends_on", [])
    assert "backend" in depends_on, "frontend 는 backend 서비스에 depends_on 설정이 필요합니다."
    assert frontend.get("restart") == "unless-stopped", "frontend.restart 정책은 unless-stopped 여야 합니다."


def test_repository_does_not_include_patch_markers():
    patch_markers = ("+", "@@", "diff --git", "+++ ", "--- ")
    offending_lines: list[str] = []
    for path in _iter_repo_files():
        if path.suffix in {".py", ".js", ".css", ".html", ".yml", ".yaml", ".md", ""}:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for idx, line in enumerate(fh, start=1):
                    if any(line.startswith(marker) for marker in patch_markers):
                        offending_lines.append(f"{path.relative_to(REPO_ROOT)}:{idx}")

    assert not offending_lines, (
        "패치 복사 흔적이 감지되었습니다: " + ", ".join(offending_lines)
    )
