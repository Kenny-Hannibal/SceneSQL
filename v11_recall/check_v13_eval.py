import json

CASES = json.load(open('eval_v3_raw.json'))['cases']
V12 = json.load(open('v12_full.json'))['rows']
V13 = json.load(open('v13_full.json'))['rows']

v12_by_bag = {}
for r in V12: v12_by_bag.setdefault(r['bag_id'], []).append(r)
v13_by_bag = {}
for r in V13: v13_by_bag.setdefault(r['bag_id'], []).append(r)

print(f"v12 rows={len(V12)} bags={len(v12_by_bag)} | v13 rows={len(V13)} bags={len(v13_by_bag)}")

print("\n=== 评测集 27 例核对 ===")
ok = bad = 0
for c in CASES:
    b, verd = c['bag_id'], c['verdict']
    in12 = b in v12_by_bag
    rows13 = v13_by_bag.get(b, [])
    in13w = any(not (r['end_ts'] < c['start_ts'] or r['start_ts'] > c['end_ts']) for r in rows13)
    in13 = bool(rows13)
    hit = '✓' if (verd == 'pass') == in13w else '✗'
    if (verd == 'pass') == in13w: ok += 1
    else: bad += 1
    r13 = rows13[0] if rows13 else {}
    print(f"{hit} {verd:5} {b[:18]:18} v12={'Y' if in12 else '-'} v13={'Y' if in13 else '-'}{'(win✓)' if in13 else ''}"
          + (f" objs={r13.get('obj_id')} game={r13.get('game_theory_result')}" if in13 else ""))
print(f"\n正确 {ok}/27, 错误 {bad}")

print("\n=== v12 → v13 集合变化 ===")
dropped = sorted(set(v12_by_bag) - set(v13_by_bag))
added = sorted(set(v13_by_bag) - set(v12_by_bag))
print(f"v13 移除 ({len(dropped)}):")
evalver = {c['bag_id']: c['verdict'] for c in CASES}
for b in dropped:
    r = v12_by_bag[b][0]
    print(f"  {b[:20]:20} eval={evalver.get(b,'-'):5} v12_objs={r.get('obj_id')} game={r.get('game_theory_result')}")
print(f"v13 新增 ({len(added)}):")
for b in added:
    r = v13_by_bag[b][0]
    print(f"  {b[:20]:20} eval={evalver.get(b,'-'):5} objs={r.get('obj_id')}")
