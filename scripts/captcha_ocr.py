"""Local PaddleOCR recognition; only the initial model download needs a network."""
import argparse
import io
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile

import requests

MODEL_NAME = "en_PP-OCRv4_mobile_rec"
MODEL_URL = (
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/"
    "official_inference_model/paddle3.0.0/en_PP-OCRv4_mobile_rec_infer.tar"
)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_DIR = PROJECT_ROOT / ".models" / MODEL_NAME


def prepare_model(model_dir=DEFAULT_MODEL_DIR):
    """Download the official model once and install it without unsafe tar paths."""
    model_dir = Path(model_dir).resolve()
    if all((model_dir / name).is_file() for name in
           ("inference.json", "inference.pdiparams", "inference.yml")):
        return model_dir
    if model_dir != DEFAULT_MODEL_DIR.resolve():
        raise RuntimeError("指定的模型目錄不完整：%s" % model_dir)
    if model_dir.exists():
        raise RuntimeError("模型目錄不完整，請檢查或移開後重試：%s" % model_dir)
    print("首次使用：下載官方 OCR 模型到 %s" % model_dir, flush=True)
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=model_dir.parent) as work:
        archive = Path(work) / "model.tar"
        with requests.get(MODEL_URL, stream=True, timeout=(15, 120)) as response:
            response.raise_for_status()
            with archive.open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    output.write(chunk)
        extracted = Path(work) / "extracted"
        extracted.mkdir()
        with tarfile.open(archive) as tar:
            for member in tar:
                target = (extracted / member.name).resolve()
                if not target.is_relative_to(extracted.resolve()):
                    raise RuntimeError("模型壓縮檔包含不安全路徑。")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                else:
                    raise RuntimeError("模型壓縮檔包含不支援的檔案類型。")
        matches = list(extracted.rglob("inference.json"))
        if len(matches) != 1:
            raise RuntimeError("官方模型檔案格式不符預期。")
        source_dir = matches[0].parent
        if not all((source_dir / name).is_file() for name in
                   ("inference.pdiparams", "inference.yml")):
            raise RuntimeError("官方模型缺少必要檔案。")
        source_dir.rename(model_dir)
    return model_dir


class CaptchaOCR:
    def __init__(self, model_dir=DEFAULT_MODEL_DIR):
        model_dir = prepare_model(model_dir)
        # The explicit local directory avoids automatic model downloads at inference.
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        cache_dir = PROJECT_ROOT / ".models" / "cache"
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(cache_dir))
        os.environ.setdefault("PADDLE_HOME", str(cache_dir / "paddle"))
        from paddleocr import TextRecognition
        print("載入本機 OCR 模型（CPU）…", flush=True)
        self.model = TextRecognition(
            model_name=MODEL_NAME, model_dir=str(model_dir), device="cpu",
            enable_mkldnn=False,
        )

    def candidates(self, image_bytes):
        """Return unique alphanumeric strings, ordered by model confidence."""
        import numpy as np
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
        foreground = ImageOps.grayscale(image).point(lambda value: 255 if value < 210 else 0)
        bbox = foreground.getbbox()
        if bbox is None:
            return []
        crop = ImageOps.expand(image.crop(bbox), border=6, fill="white")
        enlarged = crop.resize((crop.width * 3, crop.height * 3))
        gray = ImageOps.autocontrast(ImageOps.grayscale(enlarged)).convert("RGB")
        variants = [image, enlarged, gray]
        arrays = [np.array(variant)[:, :, ::-1].copy() for variant in variants]
        results = self.model.predict(input=arrays, batch_size=len(arrays))
        scores = {}
        for result in results:
            data = result.json
            if callable(data):
                data = data()
            if isinstance(data, str):
                import json
                data = json.loads(data)
            data = data.get("res", data)
            text = re.sub(r"\s+", "", data.get("rec_text", ""))
            # Never guess missing characters or brute-force confusable letters.
            # The website's validator does not require exactly four characters.
            if re.fullmatch(r"[A-Za-z0-9]{1,8}", text):
                scores[text] = max(scores.get(text, 0), float(data.get("rec_score", 0)))
        return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="下載並準備本機 PaddleOCR 模型")
    parser.add_argument("--prepare", action="store_true", required=True)
    parser.parse_args()
    print("模型已備妥：%s" % prepare_model())
