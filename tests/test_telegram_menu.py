from pathlib import Path


def test_private_admin_menu_is_persistent_and_has_core_actions():
    source = Path('app/telegram/bots.py').read_text(encoding='utf-8')

    required = [
        'ReplyKeyboardMarkup',
        'MessageHandler',
        'filters.TEXT & ~filters.COMMAND',
        '🔎 فحص فرصة',
        '📈 حالة السوق',
        '📂 الصفقات المفتوحة',
        '🩺 صحة النظام',
        '🧪 اختبار Tasilab',
        '⏸️ إيقاف الإشارات',
        '▶️ استئناف الإشارات',
        'is_persistent=True',
    ]
    for token in required:
        assert token in source
