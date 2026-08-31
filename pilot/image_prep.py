"""
pilot/image_prep.py
---------------------------------
[벤더 복사본] 원본: jasan_bills/jasan_bill_extractor/preprocess/image_prep.py (2026-08-31 기준)

로컬 VLM과 Claude 양쪽에 완전히 동일한 전처리 결과를 입력해야 "모델 자체의
정확도 차이"만 비교할 수 있으므로, 전처리 로직을 그대로 복사해왔다. 원본이
바뀌면 수동 재동기화 필요.

TIFF 프레임 분리, 화질 보정(디노이즈/업스케일/대비), API 전송용 PNG 바이트 인코딩.
"""

import io
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageOps

# 참고용 상수: Claude Vision의 표준 해상도 상한(긴 변 기준).
MAX_LONG_EDGE = 1568


@dataclass
class PreppedImage:
    png_bytes: bytes
    width: int
    height: int
    frame_index: int
    frame_count: int


def load_tiff_frames(path: str) -> list[Image.Image]:
    """멀티프레임 TIFF를 프레임별 PIL 이미지 리스트로 분리."""
    frames = []
    with Image.open(path) as im:
        try:
            i = 0
            while True:
                im.seek(i)
                frames.append(im.copy())
                i += 1
        except EOFError:
            pass
    return frames


def enhance_fax_image(img: Image.Image, denoise: bool = False) -> Image.Image:
    """팩스 화질을 보정한다.

    1) 그레이스케일 변환
    2) (선택) 미디언 필터로 디더링 노이즈 제거
    3) 오토 컨트라스트로 대비 강화
    4) 원본이 작으면 업스케일(가독성 향상). 큰 이미지는 그대로 둔다.
    """
    gray = img.convert("L")
    if denoise:
        gray = gray.filter(ImageFilter.MedianFilter(size=3))
    contrasted = ImageOps.autocontrast(gray, cutoff=1)

    w, h = contrasted.size
    long_edge = max(w, h)

    if long_edge < 1200:
        scale = 1200 / long_edge
        new_size = (int(w * scale), int(h * scale))
        contrasted = contrasted.resize(new_size, Image.LANCZOS)

    return contrasted


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def prep_file(path: str, denoise: bool = False) -> list[PreppedImage]:
    """TIFF 파일 하나를 프레임 단위로 분리 + 보정 + PNG 인코딩까지 수행."""
    frames = load_tiff_frames(path)
    prepped = []
    for idx, frame in enumerate(frames):
        enhanced = enhance_fax_image(frame, denoise=denoise)
        png_bytes = to_png_bytes(enhanced)
        prepped.append(
            PreppedImage(
                png_bytes=png_bytes,
                width=enhanced.width,
                height=enhanced.height,
                frame_index=idx,
                frame_count=len(frames),
            )
        )
    return prepped
