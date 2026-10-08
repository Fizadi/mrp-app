"""
Raw OpenRouter test — v2
Run: python test_openrouter_raw.py
"""
import os, json, sys, time, traceback
import requests

API_KEY = os.environ.get('AI_API_KEY', '')
if not API_KEY:
    print('ERROR: AI_API_KEY env var is empty.')
    raise SystemExit(1)

print(f'Key: {API_KEY[:20]}...', flush=True)
print('-' * 70, flush=True)

HEADERS = {
    'Authorization': f'Bearer {API_KEY}',
    'Content-Type': 'application/json',
    'HTTP-Referer': 'http://localhost:5001',
    'X-Title': 'Taraz Tamin CRM',
}

TEST_PROMPT = (
    'Reply with exactly this JSON and nothing else, no markdown fences:\n'
    '{"company":"Petrofanavr","contact":"Ahladi","value":200000000}'
)

# Ordered by likelihood of working right now. Small/fast models first.
CANDIDATES = [
    'liquid/lfm-2.5-2.6b:free',
    'google/gemma-4-26b-a4b-it:free',
    'google/gemma-4-31b-it:free',
    'inclusionai/ling-3.0-flash-sante:free',
    'nvidia/nemotron-3.5-lightning:free',
    'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
    'qwen/qwen3.8-27b:free',
]


def test_model(model_id):
    """Returns 'ok', 'ratelimit', 'error', or 'timeout'."""
    print(f'\n=== {model_id} ===', flush=True)
    payload = {
        'model': model_id,
        'messages': [{'role': 'user', 'content': TEST_PROMPT}],
        'temperature': 0,
        'max_tokens': 200,
    }
    t0 = time.time()
    try:
        print('  → POST /chat/completions ...', flush=True)
        r = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers=HEADERS,
            json=payload,
            timeout=60,
        )
        elapsed = time.time() - t0
        print(f'  ← HTTP {r.status_code}  ({elapsed:.1f}s)', flush=True)

        if r.status_code == 200:
            try:
                body = r.json()
                content = body['choices'][0]['message']['content']
                print(f'  ✓ SUCCESS', flush=True)
                print(f'  Content: {content[:300]!r}', flush=True)
                print(f'  Usage:   {body.get("usage")}', flush=True)
                return 'ok'
            except (KeyError, ValueError) as e:
                print(f'  ✗ Bad response shape: {e}', flush=True)
                print(f'  Raw body: {r.text[:500]}', flush=True)
                return 'error'

        elif r.status_code == 429:
            print(f'  ⚠ RATE LIMIT', flush=True)
            print(f'  Body: {r.text[:400]}', flush=True)
            return 'ratelimit'

        else:
            print(f'  ✗ HTTP {r.status_code}', flush=True)
            print(f'  Body: {r.text[:400]}', flush=True)
            return 'error'

    except requests.exceptions.Timeout:
        elapsed = time.time() - t0
        print(f'  ✗ TIMEOUT after {elapsed:.1f}s', flush=True)
        return 'timeout'
    except Exception as e:
        elapsed = time.time() - t0
        print(f'  ✗ EXCEPTION after {elapsed:.1f}s: {type(e).__name__}: {e}', flush=True)
        traceback.print_exc()
        return 'error'


def main():
    results = {}
    for m in CANDIDATES:
        try:
            results[m] = test_model(m)
        except KeyboardInterrupt:
            print('\n⚠ Interrupted by user', flush=True)
            break
        # Small pause between calls to avoid hammering rate limits
        time.sleep(2)

    print('\n' + '=' * 70, flush=True)
    print('SUMMARY', flush=True)
    print('=' * 70, flush=True)
    for m, status in results.items():
        mark = '✓' if status == 'ok' else ('⚠' if status == 'ratelimit' else '✗')
        print(f'  {mark}  {m:<55} {status}', flush=True)

    ok = [m for m, s in results.items() if s == 'ok']
    if ok:
        print(f'\n👉 USE THIS MODEL:  set AI_MODEL={ok[0]}', flush=True)
    else:
        print('\n✗ No free model worked right now. Try again in a few minutes,', flush=True)
        print('  or consider topping up $1 on OpenRouter for reliable access.', flush=True)


if __name__ == '__main__':
    main()