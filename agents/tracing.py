"""실행 진입점의 LangSmith 설정 로딩과 비밀값 없는 시작 진단."""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from langsmith import utils


def configure_tracing():
    # 실행 cwd와 무관하게 저장소 .env를 읽는다. 명시적 프로세스 환경변수가 우선한다.
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
    utils.get_env_var.cache_clear()
    logger = logging.getLogger('poker')
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(levelname)s %(name)s: %(message)s'))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(logging.INFO)
    status = {
        'enabled': os.environ.get('LANGSMITH_TRACING', os.environ.get('LANGCHAIN_TRACING_V2', 'false')).lower() == 'true',
        'project': os.environ.get('LANGSMITH_PROJECT', os.environ.get('LANGCHAIN_PROJECT', 'default')),
        'endpoint': os.environ.get('LANGSMITH_ENDPOINT', os.environ.get('LANGCHAIN_ENDPOINT', 'https://api.smith.langchain.com')),
        'api_key_configured': bool(os.environ.get('LANGSMITH_API_KEY') or os.environ.get('LANGCHAIN_API_KEY')),
    }
    logger.info('LangSmith enabled=%s project=%s endpoint=%s api_key_configured=%s',
                status['enabled'], status['project'], status['endpoint'], status['api_key_configured'])
    if status['enabled'] and not status['api_key_configured']:
        logger.warning('LangSmith is enabled but no API key is configured')
    return status
