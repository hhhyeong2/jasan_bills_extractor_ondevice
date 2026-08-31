"""
scripts/score_pilot.py
---------------------------------
run_pilot_local_vlm.py의 결과(로컬 VLM raw JSON)를 Claude 기준값
(jasan_bills/jasan_bill_extractor/poc_out_v2/raw, 이미 실행된 실제 API 결과)과
필드별로 비교해 정확도 리포트를 만든다. jasan_bills 쪽 파일은 읽기만 하고
수정하지 않는다.

pilot_ondevice_vlm.md §4/§5의 측정 지표·성공 기준에 대응한다.

지표:
- 필드별 정확도: baseline(Claude)이 값을 채운 필드 중 로컬 모델이 맞춘 비율
- hallucination: baseline이 null인데 로컬 모델이 값을 만들어낸 건수
- 문서 개수 불일치: 페이지당 문서 분리 자체가 어긋난 건 (필드 비교 이전 단계의 실패)
- 처리 시간 (timing.csv가 있으면 요약)

사용법:
    python score_pilot.py \
        --local-raw-dir ../pilot_local_out/raw \
        --baseline-raw-dir ../../jasan_bills/jasan_bill_extractor/poc_out_v2/raw \
        --timing-csv ../pilot_local_out/timing.csv \
        --output ../pilot_local_out/score_report.csv
"""

import argparse
import csv
import json
from pathlib import Path

NUMERIC_FIELDS = [
    "supply_amount",
    "vat_amount",
    "power_fund_raw",
    "current_period_charge",
    "late_fee",
    "unpaid_amount",
    "unpaid_late_fee",
    "other_fee",
    "due_total_amount",
    "overdue_total_amount",
]
STRING_FIELDS = ["doc_type", "billing_period", "usage_start_date", "usage_end_date"]
ALL_FIELDS = NUMERIC_FIELDS + STRING_FIELDS
NUMERIC_TOLERANCE = 10  # 원 (spec.md §5 ArithmeticValidator와 동일 허용오차)


def _num_equal(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) <= NUMERIC_TOLERANCE
    except (TypeError, ValueError):
        return False


def _str_equal(a, b) -> bool:
    return (a or None) == (b or None)


def compare_doc(local_doc: dict, base_doc: dict, stats: dict):
    for field in ALL_FIELDS:
        lv = local_doc.get(field)
        bv = base_doc.get(field)
        eq = _num_equal(lv, bv) if field in NUMERIC_FIELDS else _str_equal(lv, bv)

        s = stats.setdefault(
            field,
            {"baseline_nonnull": 0, "correct": 0, "hallucination": 0, "missed": 0, "both_null": 0},
        )
        if bv is not None:
            s["baseline_nonnull"] += 1
            if eq:
                s["correct"] += 1
            elif lv is None:
                s["missed"] += 1
            # else: 값은 있지만 틀림 (correct/missed 어디에도 안 들어가는 잔여 케이스)
        else:
            if lv is not None:
                s["hallucination"] += 1
            else:
                s["both_null"] += 1


def main():
    parser = argparse.ArgumentParser(description="로컬 VLM 파일럿 vs Claude 기준값 채점기")
    parser.add_argument("--local-raw-dir", required=True)
    parser.add_argument("--baseline-raw-dir", required=True)
    parser.add_argument("--timing-csv", default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    local_dir = Path(args.local_raw_dir)
    base_dir = Path(args.baseline_raw_dir)

    base_files = sorted(base_dir.glob("*_f*.json"))
    stats: dict = {}
    doc_count_mismatches = []
    missing_local_files = []
    n_files_compared = 0

    for base_path in base_files:
        local_path = local_dir / base_path.name
        if not local_path.exists():
            missing_local_files.append(base_path.name)
            continue

        base_docs = json.loads(base_path.read_text(encoding="utf-8")).get("documents", [])
        try:
            local_docs = json.loads(local_path.read_text(encoding="utf-8")).get("documents", [])
        except json.JSONDecodeError:
            missing_local_files.append(f"{base_path.name} (파싱 실패)")
            continue

        if len(base_docs) != len(local_docs):
            doc_count_mismatches.append(
                f"{base_path.name}: baseline {len(base_docs)}건 vs local {len(local_docs)}건"
            )
            continue

        for bdoc, ldoc in zip(base_docs, local_docs):
            compare_doc(ldoc, bdoc, stats)
        n_files_compared += 1

    rows = []
    for field in ALL_FIELDS:
        s = stats.get(
            field,
            {"baseline_nonnull": 0, "correct": 0, "hallucination": 0, "missed": 0, "both_null": 0},
        )
        accuracy = (s["correct"] / s["baseline_nonnull"] * 100) if s["baseline_nonnull"] else None
        rows.append(
            {
                "field": field,
                "baseline_nonnull_count": s["baseline_nonnull"],
                "correct": s["correct"],
                "missed": s["missed"],
                "hallucination": s["hallucination"],
                "accuracy_pct": round(accuracy, 1) if accuracy is not None else "N/A",
            }
        )

    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "field",
                "baseline_nonnull_count",
                "correct",
                "missed",
                "hallucination",
                "accuracy_pct",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"[비교 완료] baseline {len(base_files)}개 파일 중 {n_files_compared}개 문서-단위 비교 수행")
    print(f"[제외] 로컬 결과 누락/파싱실패 {len(missing_local_files)}건: {missing_local_files}")
    print(f"[제외] 문서 개수 불일치 {len(doc_count_mismatches)}건 (페이지 분리 자체가 어긋난 케이스):")
    for m in doc_count_mismatches:
        print(f"    - {m}")

    print(f"\n필드별 정확도 -> {args.output}")
    for row in rows:
        print(
            f"  {row['field']:25s} {row['accuracy_pct']}%  "
            f"(기준 {row['baseline_nonnull_count']}건, hallucination {row['hallucination']}건)"
        )

    if args.timing_csv and Path(args.timing_csv).exists():
        times = []
        with open(args.timing_csv, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r["status"] == "ok":
                    try:
                        times.append(float(r["elapsed_sec"]))
                    except ValueError:
                        pass
        if times:
            print(
                f"\n처리 시간: 평균 {sum(times) / len(times):.1f}s, "
                f"최대 {max(times):.1f}s, 최소 {min(times):.1f}s (n={len(times)})"
            )


if __name__ == "__main__":
    main()
