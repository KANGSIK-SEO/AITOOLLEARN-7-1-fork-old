"""AI 모델 사용 정책.

- solar-pro3만 허용한다. 다른 모델은 과금되므로 코드 어디서도 호출할 수 없다.
- 2027-04-01부터는 solar-pro3도 과금되므로 모든 모델을 무효화한다.
"""
import os
from datetime import date

ALLOWED_MODEL = "solar-pro3"
AI_CUTOFF_DATE = date(2027, 4, 1)  # 이 날짜 이후(포함) 모든 AI 호출 차단
UPSTAGE_BASE_URL = "https://api.upstage.ai/v1"


class AIUnavailableError(RuntimeError):
    """정책상 AI 호출이 허용되지 않을 때 발생. code는 API 오류 응답에 그대로 쓴다."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def ensure_ai_allowed(model: str, today: date | None = None) -> str:
    """호출 직전에 반드시 통과시킬 것. 허용되면 모델명을 그대로 반환한다."""
    if (today or date.today()) >= AI_CUTOFF_DATE:
        raise AIUnavailableError(
            "AI_EXPIRED", "2027-04 이후에는 모든 AI 모델이 과금되어 사용이 종료되었습니다."
        )
    if model != ALLOWED_MODEL:
        raise AIUnavailableError("AI_MODEL_NOT_ALLOWED", f"허용되지 않은 모델입니다: {model}")
    return model


def get_api_key() -> str:
    key = os.environ.get("UPSTAGE_API_KEY", "")
    if not key:
        raise AIUnavailableError("AI_KEY_MISSING", "UPSTAGE_API_KEY가 설정되지 않았습니다.")
    return key
