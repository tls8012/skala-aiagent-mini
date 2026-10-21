"""UTF-8 텍스트 템플릿을 읽는다. $name 자리만 치환하며 JSON 중괄호는 그대로 둔다."""
from functools import lru_cache
from pathlib import Path
from string import Template


@lru_cache(maxsize=None)
def _template(name: str) -> Template:
    if not name.isidentifier():
        raise ValueError('prompt name must be an identifier')
    return Template(Path(__file__).with_name(name + '.txt').read_text(encoding='utf-8').strip())


def render_prompt(name: str, **values) -> str:
    """필수 변수가 없으면 즉시 실패한다. 값에 포함된 $/JSON을 재해석하지 않는다."""
    return _template(name).substitute(values)
