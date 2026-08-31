"""
pilot/vision_client_local.py
---------------------------------
Ollama에 올라간 로컬 VLM(예: qwen2.5vl:7b)을 호출해서, jasan_bills의
extract.vision_client.extract_from_image와 동일한 반환 계약({"documents": [...]})을
지킨다. jasan_bills 프로덕션 코드는 건드리지 않고, 이 저장소(파일럿 전용) 안에서
schema.py/prompts.py(같은 폴더, jasan_bills에서 벤더 복사)를 재사용한다.

Anthropic의 강제 tool_choice 같은 구조화 출력 기능이 Ollama에는 없으므로,
EXTRACT_TOOL_SCHEMA를 그대로 프롬프트에 박아 넣고 format="json"으로 구조화
출력을 유도한다. 스키마 100% 준수를 보장하지 않는다는 점 자체가 이 파일럿에서
측정하려는 대상 중 하나다 (scripts/score_pilot.py의 JSON 파싱 성공률 참고).

사전 준비 (Windows 사내 PC):
    1) https://ollama.com/download 에서 Ollama 설치
    2) ollama pull qwen2.5vl:7b   (최초 1회, 인터넷 필요. 이후 오프라인 동작)
    3) Ollama는 설치 시 보통 백그라운드 서비스로 상주한다 (http://localhost:11434).
       상주하지 않으면 별도 터미널에서 `ollama serve` 실행.
    4) pip install -r requirements.txt
"""

import base64
import json
import time

import requests

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .schema import EXTRACT_TOOL_SCHEMA

DEFAULT_LOCAL_MODEL = "qwen2.5vl:7b"
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
MAX_RETRIES = 2
RETRY_BACKOFF_SEC = 2
# CPU 추론은 이미지 1장에 수십 초~수 분 걸릴 수 있어 클라우드 API보다 넉넉히 잡는다.
REQUEST_TIMEOUT_SEC = 300
# Ollama 기본 num_ctx(보통 4096 근처)는 SYSTEM_PROMPT + 스키마 전체 + 이미지 토큰을
# 합치면 쉽게 넘친다(실측: "400 Bad Request ... exceeds the available context size").
# 요청마다 명시적으로 넉넉하게 잡아서 Modelfile의 num_ctx 설정과 무관하게 항상 통과하게 한다.
DEFAULT_NUM_CTX = 8192


class LocalVisionExtractionError(Exception):
    pass


def _schema_instructions() -> str:
    schema_json = json.dumps(EXTRACT_TOOL_SCHEMA["input_schema"], ensure_ascii=False, indent=2)
    return (
        "\n\n반드시 아래 JSON 스키마를 정확히 따르는 JSON 객체 하나만 반환하세요. "
        "스키마 설명 문장이나 마크다운 코드블록(```) 표시 없이, 순수 JSON 값만 출력하세요.\n\n"
        f"{schema_json}"
    )


def extract_from_image(
    png_bytes: bytes,
    filename_hint: str,
    frame_index: int = 0,
    frame_count: int = 1,
    model: str = DEFAULT_LOCAL_MODEL,
    num_ctx: int = DEFAULT_NUM_CTX,
) -> dict:
    """이미지 1장을 로컬 Ollama VLM에 보내 documents 배열을 얻는다.

    반환값: {"documents": [...]} — jasan_bills의 extract_from_image와 동일 계약.
    """
    b64 = base64.standard_b64encode(png_bytes).decode("ascii")
    user_prompt = build_user_prompt(filename_hint, frame_index, frame_count) + _schema_instructions()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt, "images": [b64]},
        ],
        "format": "json",
        "stream": False,
        "options": {"num_ctx": num_ctx},
    }

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            content = resp.json()["message"]["content"]
            parsed = json.loads(content)
            if "documents" not in parsed:
                raise LocalVisionExtractionError(
                    f"응답에 'documents' 키가 없습니다 (앞 200자): {content[:200]}"
                )
            return parsed
        except LocalVisionExtractionError:
            raise
        except requests.exceptions.ConnectionError as e:
            raise LocalVisionExtractionError(
                "Ollama 서버에 연결할 수 없습니다. 'ollama serve'가 실행 중인지, "
                "포트 11434가 맞는지 확인하세요."
            ) from e
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 400 and "context" in resp.text.lower():
                # 재시도해도 같은 페이로드로는 똑같이 실패하므로 즉시 중단하고 원인을 알려준다.
                raise LocalVisionExtractionError(
                    f"컨텍스트 초과(400): num_ctx={num_ctx}로도 부족합니다. "
                    f"extract_from_image(..., num_ctx=더큰값)으로 늘려서 재시도하세요. "
                    f"서버 응답: {resp.text[:200]}"
                ) from e
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
            continue
        except (json.JSONDecodeError, KeyError) as e:
            last_err = f"JSON 파싱 실패: {e}"
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
            continue
        except Exception as e:  # noqa: BLE001 - 파일럿 단계에서는 폭넓게 재시도
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
            continue

    raise LocalVisionExtractionError(
        f"{filename_hint} (frame {frame_index}) 로컬 추출 실패: {last_err}"
    )
