# jasan_bills_extractor_ondevice

`jasan_bills`(전기료 고지서 자동 추출 툴, 별도 저장소)의 클라우드 Claude Vision 호출을
로컬 온디바이스 VLM(Ollama + Qwen2.5-VL 등)으로 대체할 수 있는지 검증하는 파일럿 저장소.

**설계 문서**: `../pilot_ondevice_vlm.md` (배경, 후보 모델, 성공 기준), `../spec.md`
(원본 파이프라인 설계) — 이 저장소는 그 설계를 실행하는 코드만 담는다.

**jasan_bills 저장소는 이 파일럿에서 읽기 전용으로만 참조**한다 (샘플 이미지
`bills_png/`, 기존 Claude 추출 결과 `poc_out_v2/raw/`). 어떤 스크립트도 그쪽
파일을 쓰거나 수정하지 않는다. `pilot/schema.py`, `pilot/prompts.py`,
`pilot/image_prep.py`는 이 저장소가 jasan_bills와 별도 git 저장소이기 때문에
필요한 만큼만 **벤더 복사**해온 것이다 — jasan_bills의 실제 스키마/프롬프트가
바뀌면 수동으로 다시 동기화해야 비교가 유효하다.

## 구조

```
pilot/
├── schema.py              # [벤더 복사] Claude tool-use JSON 스키마
├── prompts.py              # [벤더 복사] 시스템 프롬프트 + 유저 프롬프트 빌더
├── image_prep.py            # [벤더 복사] TIFF 프레임 분리 + 화질 보정 + PNG 인코딩
└── vision_client_local.py  # Ollama 호출 래퍼 (schema.py/prompts.py 재사용)

scripts/
├── run_pilot_local_vlm.py  # 배치 실행기 (raw JSON + timing.csv 생성)
└── score_pilot.py          # Claude 기준값과 필드별 정확도 비교
```

## 사전 준비

1. [Ollama](https://ollama.com/download) 설치
2. `ollama pull qwen2.5vl:7b` (최초 1회, 인터넷 필요)
3. `python -m venv .venv && source .venv/bin/activate` (Windows: `.venv\Scripts\activate`)
4. `pip install -r requirements.txt`

## 실행

jasan_bills가 이 저장소와 같은 부모 폴더(`work/`) 아래 형제 폴더로 clone되어
있다고 가정한 경로 예시:

```bash
# 1) jasan_bills의 poc_out_v2와 동일한 30건에 대해 로컬 모델 실행
python scripts/run_pilot_local_vlm.py \
    --input-dir ../../jasan_bills/bills_png \
    --output-dir ./pilot_local_out \
    --match-raw-dir ../../jasan_bills/jasan_bill_extractor/poc_out_v2/raw \
    --model qwen2.5vl:7b

# 2) Claude 결과와 필드별 정확도 비교
python scripts/score_pilot.py \
    --local-raw-dir ./pilot_local_out/raw \
    --baseline-raw-dir ../../jasan_bills/jasan_bill_extractor/poc_out_v2/raw \
    --timing-csv ./pilot_local_out/timing.csv \
    --output ./pilot_local_out/score_report.csv
```

경로는 실제 jasan_bills 저장소 위치에 맞게 조정할 것. `score_report.csv`와
콘솔에 출력되는 처리 시간을 `../pilot_ondevice_vlm.md` §5 성공 기준과 비교해서
Go/No-Go를 판단한다.
