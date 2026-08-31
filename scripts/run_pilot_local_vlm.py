"""
scripts/run_pilot_local_vlm.py
---------------------------------
로컬 온디바이스 VLM(Ollama) 파일럿 실행기. jasan_bills 프로덕션 코드는 건드리지
않고, 이 저장소의 pilot/ 패키지(벤더 복사된 스키마/프롬프트/전처리)만 사용한다.

--input-dir / --match-raw-dir는 jasan_bills 저장소의 실제 데이터를 "읽기 전용"으로
가리키는 인자다. jasan_bills 쪽 파일은 절대 수정하지 않는다.

사용법 (jasan_bills와 이 저장소가 work/ 아래 형제 폴더로 clone되어 있다고 가정):
    # 1) Ollama 설치 및 모델 준비 (최초 1회)
    #    https://ollama.com/download 설치 후:
    #    ollama pull qwen2.5vl:7b

    # 2) jasan_bills의 poc_out_v2와 동일한 30건에 대해 로컬 모델 실행 (권장 - 직접 비교 가능)
    python run_pilot_local_vlm.py \
        --input-dir ../../jasan_bills/bills_png \
        --output-dir ../pilot_local_out \
        --match-raw-dir ../../jasan_bills/jasan_bill_extractor/poc_out_v2/raw \
        --model qwen2.5vl:7b

    # 3) 다른 모델과 비교하고 싶으면 --model만 바꿔서 재실행 (출력 폴더도 바꿀 것)
    python run_pilot_local_vlm.py \
        --input-dir ../../jasan_bills/bills_png \
        --output-dir ../pilot_local_out_minicpm \
        --match-raw-dir ../../jasan_bills/jasan_bill_extractor/poc_out_v2/raw \
        --model minicpm-v
"""

import argparse
import csv
import json
import re
import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pilot.image_prep import prep_file  # noqa: E402
from pilot.vision_client_local import (  # noqa: E402
    DEFAULT_LOCAL_MODEL,
    extract_from_image,
    LocalVisionExtractionError,
)

TIMING_COLUMNS = ["source_file", "frame_index", "frame_count", "elapsed_sec", "status", "error"]
RAW_STEM_RE = re.compile(r"^(.*)_f\d+\.json$")


def _stems_from_raw_dir(raw_dir: Path) -> set:
    """{stem}_f{n}.json 파일명에서 원본 tif stem 집합을 뽑는다."""
    stems = set()
    for p in raw_dir.glob("*_f*.json"):
        m = RAW_STEM_RE.match(p.name)
        if m:
            stems.add(m.group(1))
    return stems


def main():
    parser = argparse.ArgumentParser(description="jasan_bills 로컬 VLM 파일럿 실행기")
    parser.add_argument("--input-dir", required=True, help="TIFF 청구서가 있는 폴더 (jasan_bills/bills_png)")
    parser.add_argument("--output-dir", required=True, help="결과(raw JSON/timing.csv)를 저장할 폴더")
    parser.add_argument("--limit", type=int, default=30, help="처리할 최대 파일 수 (기본 30)")
    parser.add_argument(
        "--model", default=DEFAULT_LOCAL_MODEL, help=f"Ollama 모델명 (기본: {DEFAULT_LOCAL_MODEL})"
    )
    parser.add_argument(
        "--match-raw-dir",
        default=None,
        help="이 폴더(예: jasan_bills/jasan_bill_extractor/poc_out_v2/raw)의 파일과 동일한 "
        "원본 tif만 골라 처리 (Claude 결과와 직접 비교하려면 사용을 권장)",
    )
    parser.add_argument("--denoise", action="store_true", help="jasan_bills의 run_poc.py와 동일 옵션")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    tif_files = sorted(input_dir.glob("*.tif")) + sorted(input_dir.glob("*.tiff"))

    if args.match_raw_dir:
        wanted_stems = _stems_from_raw_dir(Path(args.match_raw_dir))
        tif_files = [p for p in tif_files if p.stem in wanted_stems]
        print(f"[매칭] {args.match_raw_dir} 기준 stem {len(wanted_stems)}개 중 원본 {len(tif_files)}개 발견")

    tif_files = tif_files[: args.limit]

    if not tif_files:
        print("[경고] 처리할 tif 파일이 없습니다. --input-dir 경로를 확인하세요.")
        return

    print(f"[시작] {len(tif_files)}개 파일 처리, model={args.model}")

    timing_rows = []
    n_ok, n_err = 0, 0

    for i, path in enumerate(tif_files, 1):
        try:
            prepped_frames = prep_file(str(path), denoise=args.denoise)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(tif_files)}] {path.name}: 전처리 실패 - {e}")
            timing_rows.append(
                {
                    "source_file": path.name,
                    "frame_index": -1,
                    "frame_count": -1,
                    "elapsed_sec": 0,
                    "status": "preprocess_error",
                    "error": str(e),
                }
            )
            n_err += 1
            continue

        for frame in prepped_frames:
            t0 = time.time()
            try:
                result = extract_from_image(
                    frame.png_bytes,
                    filename_hint=path.name,
                    frame_index=frame.frame_index,
                    frame_count=frame.frame_count,
                    model=args.model,
                )
                elapsed = time.time() - t0
                raw_path = raw_dir / f"{path.stem}_f{frame.frame_index}.json"
                raw_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                timing_rows.append(
                    {
                        "source_file": path.name,
                        "frame_index": frame.frame_index,
                        "frame_count": frame.frame_count,
                        "elapsed_sec": round(elapsed, 1),
                        "status": "ok",
                        "error": "",
                    }
                )
                n_ok += 1
                print(f"  [{i}/{len(tif_files)}] {path.name} (frame {frame.frame_index}) 완료 ({elapsed:.1f}s)")
            except LocalVisionExtractionError as e:
                elapsed = time.time() - t0
                print(f"  [{i}/{len(tif_files)}] {path.name} (frame {frame.frame_index}): {e}")
                timing_rows.append(
                    {
                        "source_file": path.name,
                        "frame_index": frame.frame_index,
                        "frame_count": frame.frame_count,
                        "elapsed_sec": round(elapsed, 1),
                        "status": "error",
                        "error": str(e),
                    }
                )
                n_err += 1
            except Exception as e:  # noqa: BLE001
                elapsed = time.time() - t0
                print(f"  [{i}/{len(tif_files)}] {path.name}: 예기치 못한 오류 - {e}")
                traceback.print_exc()
                timing_rows.append(
                    {
                        "source_file": path.name,
                        "frame_index": frame.frame_index,
                        "frame_count": frame.frame_count,
                        "elapsed_sec": round(elapsed, 1),
                        "status": "unexpected_error",
                        "error": str(e),
                    }
                )
                n_err += 1

    timing_csv = output_dir / "timing.csv"
    with open(timing_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=TIMING_COLUMNS)
        writer.writeheader()
        writer.writerows(timing_rows)

    print(f"\n[완료] 성공 {n_ok}건 / 오류 {n_err}건 -> {raw_dir}")
    print(f"[타이밍] -> {timing_csv}")


if __name__ == "__main__":
    main()
