"""로컬 진단 로그. 공개 라운드 아카이브와 분리하고 UI/MCP에는 배포하지 않는다."""
from datetime import datetime, timezone
import json
import logging


def record_runtime(table, kind: str, **details):
    if not table.config.runtime_log_enabled:
        return
    row = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'type': kind,
           'game_id': table.game_id, 'round_id': table.round.number if table.round else None,
           **details}
    try:
        table.archive_dir.mkdir(parents=True, exist_ok=True)
        with (table.archive_dir / 'runtime.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    except OSError:
        logging.getLogger('poker').exception('Failed to write local runtime log')
