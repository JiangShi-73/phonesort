# -*- coding: utf-8 -*-
"""调用 DeepSeek（OpenAI 兼容）视觉接口做图片分类。"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image

DEFAULT_PROMPT = """你是图片分类器。判断这张图片属于哪一类，只输出一个 JSON 对象：
{"group": "二次元" 或 "梗图" 或 "其他", "confidence": 0 到 1 之间的小数, "reason": "一句话"}

分类标准：
- 二次元：动漫、漫画、卡通插画、coser/角色扮演照片、动漫游戏截图、手办或周边照片
- 梗图：meme、表情包、带文字的反应图、搞笑截图、网络梗图
- 其他：普通照片、风景、人像、自拍、食物、商品、文档、二维码、路牌、测量图、海报

注意：只是带文字的普通照片（路牌、尺寸标注、店铺招牌、支付二维码）不是梗图。"""


class ApiClassifier:
    """DeepSeek 视觉模型分类器（也兼容其他 OpenAI 兼容接口）。"""

    def __init__(self, api_cfg: dict, labels: dict | None = None):
        self.base_url = str(api_cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
        self.model = str(api_cfg.get("model") or "deepseek-flash")
        self.detail = str(api_cfg.get("detail") or "low")
        self.timeout = int(api_cfg.get("timeout") or 60)
        self.retries = int(api_cfg.get("retries") or 4)
        self.max_side = int(api_cfg.get("max_side") or 1024)
        self.min_confidence = float(api_cfg.get("threshold") or 0.55)
        self.prompt = str(api_cfg.get("prompt") or DEFAULT_PROMPT)
        self.max_tokens = int(api_cfg.get("max_tokens") or 2000)
        self.reasoning_effort = api_cfg.get("reasoning_effort")
        self.session_header = bool(api_cfg.get("session_header", False))
        self.user_agent = str(api_cfg.get("user_agent") or "opencode/1.0")
        self.api_key = self._resolve_key(api_cfg)
        self.last_error = ""

    @staticmethod
    def _resolve_key(api_cfg: dict) -> str:
        env_name = str(api_cfg.get("api_key_env") or "DEEPSEEK_API_KEY")
        key = api_cfg.get("api_key") or os.environ.get(env_name)
        if key:
            return key
        # 兼容本机 DSH 已经保存的 key（不写入项目文件）
        cred_path = Path.home() / ".dsh" / ".credentials.yaml"
        if cred_path.exists():
            try:
                import yaml

                data = yaml.safe_load(cred_path.read_text(encoding="utf-8")) or {}
                refs = data.get("refs") or {}
                key = refs.get(env_name) or refs.get("OPENCODE_API_KEY") or refs.get("DEEPSEEK_API_KEY")
            except Exception:
                key = None
        if not key:
            raise RuntimeError(f"没有 API Key：请设置环境变量 {env_name}")
        return str(key)

    def _encode(self, image_path: str | Path) -> str:
        with Image.open(image_path) as image:
            image = image.convert("RGB")
            image.thumbnail((self.max_side, self.max_side))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def _request(self, image_path: str | Path) -> str:
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": self.prompt},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/jpeg;base64," + self._encode(image_path),
                        "detail": self.detail,
                    }},
                ],
            }],
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
            "User-Agent": self.user_agent,
        }
        if self.session_header:
            headers["x-opencode-session"] = str(uuid.uuid4())
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        message = body["choices"][0]["message"]
        content = message.get("content") or ""
        if not content:
            reason = body["choices"][0].get("finish_reason")
            raise RuntimeError(f"模型没有返回内容（finish_reason={reason}，max_tokens 可能太小）")
        return content

    @staticmethod
    def _parse(text: str) -> dict:
        cleaned = text.strip()
        fence = chr(96) * 3
        cleaned = re.sub(r"^" + fence + r"(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*" + fence + r"$", "", cleaned)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]
        data = json.loads(cleaned)
        group = str(data.get("group") or "其他").strip()
        if group not in ("二次元", "梗图", "其他"):
            group = "其他"
        try:
            confidence = float(data.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return {"group": group, "confidence": max(0.0, min(1.0, confidence)), "reason": str(data.get("reason") or "")}

    def classify(self, image_path: str | Path) -> dict:
        last_error = None
        for attempt in range(self.retries + 1):
            delay = 1.5 * (attempt + 1)
            try:
                return self._parse(self._request(image_path))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                last_error = f"HTTP {exc.code}: {body[:200]}"
                if exc.code in (400, 401, 402, 403):
                    break
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = 2.0 * (attempt + 1)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(delay)
        self.last_error = last_error or "未知错误"
        raise RuntimeError(f"视觉接口识图失败：{self.last_error}")

    def decide(self, image_path: str | Path, threshold: float, margin: float,
               thresholds: Optional[dict[str, float]] = None):
        result = self.classify(image_path)
        group = result["group"]
        confidence = result["confidence"]
        if group == "其他" or confidence < self.min_confidence:
            return None
        return group, confidence, {group: confidence}
