"""
CLI test for the AI parser.
Run:  python test_ai_parse.py
"""
import os, sys, json
os.environ.setdefault('AI_PROVIDER', 'openrouter')
os.environ.setdefault('AI_BASE_URL', 'https://openrouter.ai/api/v1')
os.environ.setdefault('AI_MODEL', 'openrouter/free')
os.environ.setdefault('AI_API_KEY', os.environ.get('OPENROUTER_API_KEY', ''))

from app import create_app  # adjust to your factory name
from app.core.ai_service import parse_crm_text, AIServiceError

SAMPLES = [
    {
        'label': 'Petrofanavr (your example)',
        'context': 'opportunity',
        'text': (
            "I contacted Petrofanavr company, here is their contacts from website:\n"
            "Tel: 021-88776655 (Main), 021-88776656 (Sales)\n"
            "Website: petrofanavr.ir\n"
            "Address: Tehran, Valiasr St.\n\n"
            "I called Mr. Ahladi, who is sales engineer, mobile 0912-3456789.\n"
            "Set a meeting on 2026-09-25 about the new ball valve project.\n"
            "Budget is around 200 million tomans."
        ),
    },
    {
        'label': 'Persian meeting notes',
        'context': 'activity',
        'text': (
            "امروز با آقای رضایی از شرکت مپنا جلسه داشتیم. "
            "در مورد پروژه شیرآلات صحبت کردیم. "
            "قرار شد تا هفته آینده پیش‌فاکتور بفرستیم. "
            "تاریخ جلسه بعدی: 1405/07/15"
        ),
    },
    {
        'label': 'Website scrape → customer',
        'context': 'customer',
        'text': (
            "Petrofanavr Engineering Co.\n"
            "About: Leading supplier of industrial valves in Iran.\n"
            "Tel: +98 21 8877 6655\n"
            "Fax: +98 21 8877 6656\n"
            "Email: info@petrofanavr.ir\n"
            "Website: www.petrofanavr.ir\n"
            "Address: No. 15, Valiasr St., Tehran"
        ),
    },
    {
        'label': 'Email thread → quote note',
        'context': 'quote',
        'text': (
            "From: customer@mapna.ir\n"
            "Subject: RE: Quote Q-2025-002\n\n"
            "Dear Sir,\nWe received your quote. Please apply a 5% discount on\n"
            "ball valves and send the revised version by Friday.\n"
            "Best regards, M. Jabari"
        ),
    },
    {
        'label': 'Meeting → tasks',
        'context': 'task',
        'text': (
            "Meeting with NIOC on Sunday. Action items:\n"
            "- Send revised technical specs by Tuesday (HIGH priority)\n"
            "- Call engineering next week to confirm drawings\n"
            "- Prepare the final offer, due 2026-10-15"
        ),
    },
]


def main():
    print('=' * 70)
    print('AI PARSER — CLI TEST')
    print('=' * 70)
    print(f'Model:  {os.environ.get("AI_MODEL")}')
    print(f'URL:    {os.environ.get("AI_BASE_URL")}')
    print(f'Key:    {os.environ.get("AI_API_KEY", "")[:15]}...')
    print()

    if not os.environ.get('AI_API_KEY'):
        print('ERROR: AI_API_KEY env var is empty.')
        print('Set it:  set AI_API_KEY=sk-or-v1-...   (Windows)')
        print('         export AI_API_KEY=sk-or-v1-... (bash)')
        sys.exit(1)

    # Boot the app so `current_app` is available inside ai_service
    app = create_app()
    with app.app_context():
        for i, sample in enumerate(SAMPLES, 1):
            print('-' * 70)
            print(f'[{i}/{len(SAMPLES)}] {sample["label"]}  (context={sample["context"]})')
            print('-' * 70)
            try:
                result = parse_crm_text(sample['text'], context=sample['context'])
                print(json.dumps(result, ensure_ascii=False, indent=2))
            except AIServiceError as e:
                print(f'  ✗ ERROR: {e}')
            except Exception as e:
                print(f'  ✗ UNEXPECTED: {e}')
            print()


if __name__ == '__main__':
    main()