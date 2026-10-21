"""저장된 실험 로그의 집계. 근거 평가는 사람이 채울 CSV로 분리한다."""
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import statistics
import math


def read_rows(root, stream):
    path = Path(root) / f'{stream}.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def summarize(root):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    trials, timings = read_rows(root, 'trials'), read_rows(root, 'timings')
    event_rows, model_rows = read_rows(root, 'events'), read_rows(root, 'model_calls')
    groups, timing_groups, timing_status = defaultdict(list), defaultdict(list), defaultdict(Counter)
    for row in trials:
        groups[(row['condition'], row['opponent'])].append(row)
    for span in timings:
        timing_groups[(span['kind'], span['name'])].append(span['duration_s'])
        timing_status[(span['kind'], span['name'])][span['status']] += 1
    timing_summary = []
    for (kind, name), values in timing_groups.items():
        ordered = sorted(values)
        timing_summary.append({'kind': kind, 'name': name, 'count': len(values),
                               'total_s': sum(values), 'mean_s': statistics.mean(values),
                               'median_s': statistics.median(values),
                               'p95_s': ordered[max(0, math.ceil(len(ordered)*.95)-1)], 'max_s': max(values),
                               'status_counts': dict(timing_status[(kind, name)])})
    stats = []
    for (condition, opponent), rows in groups.items():
        valid = [r for r in rows if r['valid']]
        deltas = [r['chip_delta'] for r in rows if 'chip_delta' in r]
        ids = {r['trial_id'] for r in rows}
        actions = Counter(r['betting_signature']['action'] for r in valid if 'betting_signature' in r)
        for event in event_rows:
            if event['trial_id'] in ids and event['event']['type'] == 'BetPlaced' and event['event']['player_id'] == 'A':
                actions[event['event']['action']] += 1
        calls = [r for r in model_rows if r['trial_id'] in ids]
        normal = [r['chip_delta'] for r in valid if 'chip_delta' in r]
        stats.append({'condition': condition, 'opponent': opponent, 'attempted': len(rows),
                      'valid': len(valid), 'invalid': len(rows)-len(valid),
                      'completed_rounds': len(deltas), 'mean_chip_delta_with_fallbacks': statistics.mean(deltas) if deltas else None,
                      'mean_chip_delta_valid': statistics.mean(normal) if normal else None,
                      'round_wins': sum(r.get('winner') == 'A' for r in rows), 'actions': dict(actions),
                      'model_requests': sum(r['event'] == 'request' for r in calls),
                      'tokens': sum(r.get('usage', {}).get('tokens', 0) for r in calls)})
    paired = []
    by_case = defaultdict(dict)
    for row in trials:
        by_case[row['case_id']][row['condition']] = row
    for case_id, arms in by_case.items():
        base_name = 'control' if 'control' in arms else 'no_information'
        contrasts = [(base_name, c) for c in arms if c != base_name] if base_name in arms else []
        contrasts += [(base, 'both') for base in ('search_only', 'memo_only') if base in arms and 'both' in arms]
        for base, condition in contrasts:
            baseline, row = arms[base], arms[condition]
            valid_pair = baseline['valid'] and row['valid']
            before, after = baseline.get('betting_signature'), row.get('betting_signature')
            paired.append({'case_id': case_id, 'baseline': base, 'condition': condition, 'opponent': row['opponent'],
                           'valid_pair': valid_pair,
                           'bet_changed': before != after if valid_pair and after is not None else None,
                           'effective_bet_changed': (before['action'], before['actual_increment']) != (after['action'], after['actual_increment'])
                           if valid_pair and before is not None and after is not None else None,
                           'chip_delta_difference': row['chip_delta']-baseline['chip_delta']
                           if 'chip_delta' in row and 'chip_delta' in baseline else None})
    contrasts = defaultdict(list)
    for pair in paired:
        contrasts[(pair['baseline'], pair['condition'], pair['opponent'])].append(pair)
    comparisons = []
    for (baseline, condition, opponent), pairs in contrasts.items():
        bets = [p for p in pairs if p['effective_bet_changed'] is not None]
        deltas = [p['chip_delta_difference'] for p in pairs if p['chip_delta_difference'] is not None]
        valid_deltas = [p['chip_delta_difference'] for p in pairs if p['valid_pair'] and p['chip_delta_difference'] is not None]
        comparisons.append({'baseline': baseline, 'condition': condition, 'opponent': opponent,
                            'pairs': len(pairs), 'valid_pairs': sum(p['valid_pair'] for p in pairs),
                            'decision_pairs': len(bets), 'effective_bet_changes': sum(p['effective_bet_changed'] for p in bets),
                            'mean_chip_difference_with_fallbacks': statistics.mean(deltas) if deltas else None,
                            'mean_chip_difference_valid': statistics.mean(valid_deltas) if valid_deltas else None})
    summary = {'backend': manifest['backend'], 'evidence_run': manifest['evidence_run'],
               'planned': manifest['planned_trials'], 'attempted': len(trials),
               'total_duration_s': manifest.get('total_duration_s'), 'conditions': stats,
               'paired': paired, 'comparisons': comparisons, 'timings': timing_summary,
               'note': 'Inclusive wall-clock spans overlap; do not add node/model/tool totals. Manual evidence review pending.'}
    (root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    with (root/'timings.csv').open('w', newline='', encoding='utf-8') as handle:
        fields = ['trial_id', 'decision_id', 'kind', 'name', 'call_id', 'started_offset_s', 'duration_s', 'status', 'error']
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(sorted(timings, key=lambda row: row['started_offset_s']))
    requests = {row['call_id']: row for row in model_rows if row['event'] == 'request'}
    responses = {row['call_id']: row for row in model_rows if row['event'] == 'response'}
    with (root/'responses.csv').open('w', newline='', encoding='utf-8') as handle:
        fields = ['trial_id', 'decision_id', 'call_id', 'model', 'duration_s', 'status', 'tokens']
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for timing in timings:
            if timing['kind'] != 'model':
                continue
            call_id = timing['call_id']
            writer.writerow({**{key: timing.get(key) for key in fields if key not in {'model', 'tokens'}},
                             'model': requests.get(call_id, {}).get('model'),
                             'tokens': responses.get(call_id, {}).get('usage', {}).get('tokens')})
    by_trial = {r['trial_id']: r for r in trials}
    review = root / 'review.csv'
    existing = set()
    if review.exists():
        with review.open(newline='', encoding='utf-8') as handle:
            existing = {row['decision_id'] for row in csv.DictReader(handle)}
    with review.open('a', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=['trial_id', 'case_id', 'condition', 'decision_id', 'basis_event',
            'public_text', 'memo_updates', 'speech_score', 'memo_score', 'supported', 'source_ids', 'notes'])
        if not existing and review.stat().st_size == 0:
            writer.writeheader()
        for row in read_rows(root, 'decisions'):
            if row['event'] != 'proposed' or row['decision_id'] in existing:
                continue
            decision, trial = row['result']['decision'], by_trial.get(row['trial_id'], {})
            writer.writerow({'trial_id': row['trial_id'], 'case_id': trial.get('case_id'), 'condition': trial.get('condition'),
                             'decision_id': row['decision_id'], 'basis_event': decision['basis_event'],
                             'public_text': decision['text'], 'memo_updates': json.dumps(decision['memory_updates'], ensure_ascii=False)})
    lines = ['# 실험 결과', '', f"Backend: {manifest['backend']} (실제 모델 사용: {manifest['evidence_run']})",
             f"계획 {manifest['planned_trials']} / 시도 {len(trials)} / 유효 {sum(r['valid'] for r in trials)}",
             f"전체 시간: {manifest.get('total_duration_s', 0):.3f}s", '',
             '## 조건별 결과', '', '| 조건 | 상대 | 유효/시도 | 평균 칩 변화(자동 대체 포함) |', '|---|---|---|---|']
    for row in stats:
        lines.append(f"| {row['condition']} | {row['opponent']} | {row['valid']}/{row['attempted']} | {row['mean_chip_delta_with_fallbacks']} |")
    lines += ['', '## 대응 비교', '', '| 조건 − 기준 | 상대 | 유효/대응 수 | 실제 베팅 변화/판단 쌍 | 평균 칩 차이(자동 대체 포함) |', '|---|---|---|---|---|']
    for row in comparisons:
        lines.append(f"| {row['condition']} − {row['baseline']} | {row['opponent']} | {row['valid_pairs']}/{row['pairs']} | {row['effective_bet_changes']}/{row['decision_pairs']} | {row['mean_chip_difference_with_fallbacks']} |")
    lines += ['', '## 구간별 시간', '', '| 종류 | 구간 | 횟수 | 평균 s | p95 s | 합계 s |', '|---|---|---|---|---|---|']
    for row in sorted(timing_summary, key=lambda r: r['mean_s'], reverse=True):
        lines.append(f"| {row['kind']} | {row['name']} | {row['count']} | {row['mean_s']:.4f} | {row['p95_s']:.4f} | {row['total_s']:.4f} |")
    lines += ['', '시간은 하위 호출을 포함하며 서로 겹친다. 합산해서 전체 시간으로 쓰지 않는다.',
              '근거 평가는 review.csv에 사람이 기록한다. 미평가를 0점이나 성공으로 집계하지 않는다.',
              '개별 응답 시간은 responses.csv, 전체 타임라인/성공·취소·오류는 timings.csv에서 확인한다.',
              '원본 입력/출력/검색/메모/접수 결과는 각 JSONL에서 trial_id와 decision_id로 연결한다.']
    (root / 'summary.md').write_text('\n'.join(lines) + '\n')
    return summary


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('result_dir', type=Path)
    summarize(parser.parse_args().result_dir)
