"""Ollama 통신 클라이언트와 운영 지원 업무를 조립하는 호환 진입점.

기존 생성자와 그래프의 메서드 호출을 유지한다.
업무 변경: incident_llm.py / Ollama 통신 변경: ollama_client.py.
토큰 비용은 기존과 동일한 가상 단가이며 실제 로컬 추론 과금은 아니다.
"""
from .incident_llm import IncidentFastLLM, IncidentQualityLLM
from .ollama_client import OllamaLLM


class OllamaFastLLM(IncidentFastLLM):
    def __init__(self, model: str, usd_per_1k_tokens: float):
        super().__init__(OllamaLLM(model, usd_per_1k_tokens))


class OllamaQualityLLM(IncidentQualityLLM):
    def __init__(self, model: str, usd_per_1k_tokens: float):
        super().__init__(OllamaLLM(model, usd_per_1k_tokens))
