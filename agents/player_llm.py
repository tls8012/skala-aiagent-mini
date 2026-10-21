"""게임 그래프의 LLM 진입점. 매 호출에 세 개인 메모를 자동으로 첨부한다."""
import asyncio
from agent.llm_controller import create_llm
from .prompts import render_prompt


class PlayerLLM:
    def __init__(self, table, mcp, player_id: str, client=None):
        if player_id not in table.players or table.players[player_id].kind != 'ai':
            raise ValueError('player_id must be a seated AI')
        self.table = table
        self.mcp = mcp
        self.player_id = player_id
        self.client = client if client is not None else create_llm(table.config)

    @property
    def name(self) -> str:
        return self.client.name

    async def _system(self, system: str) -> str:
        current = self.table.round
        if current is None:
            raise ValueError('no round')
        number = current.number
        memories = await self.table.call_player_tool(self.mcp, self.player_id, 'read_memories')
        if self.table.round.number != number:
            raise ValueError('round changed while reading memories; rebuild the graph input')
        values = {}
        for scope in ('long', 'game', 'round'):
            text = memories[scope]
            if not isinstance(text, str):
                raise ValueError('invalid memory tool response')
            # 서버와 호출자 설정이 다르거나 기존 파일이 길어도 프롬프트 한도는 지킨다.
            text = text[:self.table.config.memo_limit(scope)]
            values[f'{scope}_memo'] = text or '(없음)'
        return render_prompt('private_memories', system=system, **values)

    async def complete(self, system: str, user: str, schema: dict | None = None) -> tuple[str, dict]:
        system = await self._system(system)
        # 기존 동기 HTTP 구현을 재사용하면서 다른 참가자의 실행을 막지 않는다.
        return await asyncio.to_thread(self.client.complete, system, user, schema=schema)

    async def stream(self, system: str, user: str, on_token=None) -> tuple[str, dict]:
        system = await self._system(system)
        return await self.client.stream(system, user, on_token=on_token)
