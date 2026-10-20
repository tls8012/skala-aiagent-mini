"""그래프 없이 한 라운드를 실행: .venv/bin/python -m agents.demo"""
from uuid import uuid4
from .models import Bet, PlayerCommand
from .table import Table


def main():
    table = Table(['A', 'B', 'C'])
    table.start_round()
    for pid in table.round.order:
        observation = table.observe(pid)
        table.submit(pid, PlayerCommand(request_id=uuid4().hex, round_id=observation.round_id,
            basis_event=observation.event_id, kind='speak', text='이번 판은 끝까지 지켜보겠어.'))
    while table.round.phase == 'dialogue':
        pid = table.round.betting.turn
        observation = table.observe(pid)
        result = table.submit(pid, PlayerCommand(request_id=uuid4().hex,
            round_id=observation.round_id, basis_event=observation.event_id,
            kind='bet', bet=Bet(action='check')))
        assert result.status == 'accepted', result
    print(table.observe('A').model_dump_json(indent=2))
    print(f'공개 라운드 기록: {table.archive_dir}')


if __name__ == '__main__':
    main()
