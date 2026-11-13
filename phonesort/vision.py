# -*- coding: utf-8 -*-
"""基于 CLIP 的零样本图片内容分类（二次元 / 梗图）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PIL import Image


def model_ready(model_dir: str | Path) -> bool:
    path = Path(model_dir)
    needed = ["config.json", "preprocessor_config.json", "tokenizer.json", "pytorch_model.bin"]
    return path.is_dir() and all((path / name).exists() for name in needed)


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        return background
    return image.convert("RGB")


class ClipClassifier:
    """把图片和一组文字提示做相似度匹配，得到每个分组的分数。"""

    def __init__(self, model_dir: str | Path, labels: dict[str, list[str]], num_threads: Optional[int] = None):
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor
        except ImportError as exc:
            raise RuntimeError(
                "内容识别需要 torch 和 transformers。请先运行：python -m pip install -r requirements-ai.txt"
            ) from exc

        self.torch = torch
        if num_threads is None:
            num_threads = max(1, (os.cpu_count() or 4) - 1)
        torch.set_num_threads(num_threads)

        model_dir = str(model_dir)
        self.model = CLIPModel.from_pretrained(model_dir, local_files_only=True).eval()
        self.processor = CLIPProcessor.from_pretrained(model_dir, local_files_only=True)

        self.labels = {name: list(prompts) for name, prompts in labels.items() if prompts}
        prompts: list[str] = []
        prompt_groups: list[str] = []
        for name, group_prompts in self.labels.items():
            for prompt in group_prompts:
                prompts.append(prompt)
                prompt_groups.append(name)
        if not prompts:
            raise ValueError("没有配置任何 AI 分组提示词")

        inputs = self.processor(text=prompts, return_tensors="pt", padding=True, truncation=True)
        with torch.no_grad():
            text_output = self.model.text_model(
                input_ids=inputs["input_ids"],
                attention_mask=inputs.get("attention_mask"),
            )
            text_features = self.model.text_projection(text_output.pooler_output)
        self.text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        self.prompt_groups = prompt_groups

    def _project(self, output, projection_name: str):
        """兼容 transformers 新旧版本：新版本返回 BaseModelOutputWithPooling。"""
        if hasattr(output, "pooler_output"):
            pooled = output.pooler_output
            projection = getattr(self.model, projection_name, None)
            return projection(pooled) if projection is not None else pooled
        return output

    def predict(self, image_path: str | Path) -> dict[str, float]:
        with Image.open(image_path) as image:
            image = _to_rgb(image)
            inputs = self.processor(images=image, return_tensors="pt")
        with self.torch.no_grad():
            image_output = self.model.vision_model(pixel_values=inputs["pixel_values"])
            image_features = self.model.visual_projection(image_output.pooler_output)
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        logits = 100.0 * image_features @ self.text_features.T
        probs = logits.softmax(dim=-1)[0]
        scores: dict[str, float] = {}
        for index, group in enumerate(self.prompt_groups):
            score = float(probs[index])
            if score > scores.get(group, 0.0):
                scores[group] = score
        return scores

    def decide(self, image_path: str | Path, threshold: float, margin: float,
               thresholds: Optional[dict[str, float]] = None) -> Optional[tuple[str, float, dict[str, float]]]:
        scores = self.predict(image_path)
        candidates = [(name, score) for name, score in scores.items() if name != "其他"]
        if not candidates:
            return None
        candidates.sort(key=lambda item: -item[1])
        best_name, best_score = candidates[0]
        # “其他”也要参与比较：如果它比候选分组还高，说明更像普通照片
        second = max(scores.get("其他", 0.0), candidates[1][1] if len(candidates) > 1 else 0.0)
        limit = (thresholds or {}).get(best_name, threshold)
        if best_score >= limit and (best_score - second) >= margin:
            return best_name, best_score, scores
        return None
