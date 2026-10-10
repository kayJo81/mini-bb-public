"""wrangler `kv namespace list` 출력에서 제목이 argv[1] 인 KV 의 id 를 출력 (없으면 빈 줄).

출력에 '▲ [WARNING] …' 같은 안내 문구가 섞여 있어도 JSON 배열 부분만 찾아 읽는다.
"""
import json
import sys

text = sys.stdin.read()
dec = json.JSONDecoder()
items = []
for i, ch in enumerate(text):
    if ch != "[":
        continue
    try:
        val, _ = dec.raw_decode(text, i)
    except ValueError:
        continue
    if isinstance(val, list) and all(isinstance(x, dict) for x in val):
        items = val
        break
# wrangler 는 설정 이름이 없으면 제목 앞에 'worker-' 를 붙여 만든다
want = sys.argv[1]
print(next((n["id"] for n in items if n.get("title") in (want, f"worker-{want}")), ""))
