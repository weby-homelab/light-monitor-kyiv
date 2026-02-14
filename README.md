# 🇺🇦 Light Monitor Kyiv (Extended Edition)

[![CI Status](https://img.shields.io/github/actions/workflow/status/weby-homelab/light-monitor-kyiv/check_outages.yml?branch=main&label=Check%20Outages&style=flat-square&logo=github)](https://github.com/weby-homelab/light-monitor-kyiv/actions/workflows/check_outages.yml)
[![GitHub release](https://img.shields.io/github/v/release/weby-homelab/light-monitor-kyiv?style=flat-square&color=blueviolet)](https://github.com/weby-homelab/light-monitor-kyiv/releases)
[![Stars](https://img.shields.io/github/stars/weby-homelab/light-monitor-kyiv?style=flat-square&logo=star)](https://github.com/weby-homelab/light-monitor-kyiv/stargazers)
[![GitHub Discussions](https://img.shields.io/github/discussions/weby-homelab/light-monitor-kyiv?style=flat-square&logo=github)](https://github.com/weby-homelab/light-monitor-kyiv/discussions)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/github/license/weby-homelab/light-monitor-kyiv?style=flat-square)](LICENSE)

> **Розумна система моніторингу енергопостачання, яка порівнює обіцянки енергетиків з реальністю.**

Це не просто бот, який каже "Світло є/немає". Це аналітичний інструмент для `homelab`, який збирає статистику, будує красиві графіки (Dark Mode) та вираховує, наскільки точно дотримуються графіків відключень у вашому регіоні.

---

## ✨ Ключові можливості

### 🧠 Розумна Аналітика "План vs Факт"
Бот не просто констатує факти, він аналізує контекст:
- **Миттєві сповіщення:** `🔴` / `🟢` з розрахунком тривалості відключення.
- **Детекція відхилень:** *"Світло увімкнули із запізненням на 15 хв"* або *"Вимкнули раніше графіку"*.
- **Відсоток виконання:** У щоденному звіті ви побачите: *"Виконання плану світла: 92%"*.

### 📊 Дизайнерські Звіти (Dark Mode)
Ми знаємо, що ви дивитесь звіти вночі. Тому всі графіки генеруються у стильній темній темі `#1E122A`.
- **Щоденний звіт:** Дві смуги на одній шкалі (Знизу: Графік, Зверху: Реальність).
- **Тижневий дайджест:** Зведена інфографіка за 7 днів + "Енергетичний вердикт" тижня.

### 🤖 Автономність
- Підтримка джерел даних: **Yasno** та **GitHub API** (outage-data-ua).
- Автоматичне кешування та історія (`json` DB).
- Працює 24/7 на будь-якому Linux сервері (VPS/Raspberry Pi).

---

## 🏗 Архітектура

Система складається з незалежних модулів, що дозволяє гнучко налаштовувати її під себе.

```mermaid
graph TD
    User((User/Router)) -->|Heartbeat HTTP GET| Server[power_monitor_server.py]
    Server -->|Alerts 🔴🟢| Telegram
    Server -->|Logs| EventLog[event_log.json]
    
    Cron[Cron Scheduler] -->|Hourly| Scraper[main.py]
    Scraper -->|Fetch Data| YasnoAPI[Yasno / GitHub]
    Scraper -->|Save| ScheduleDB[schedule_history.json]
    
    Cron -->|Daily/Weekly| Generators[generate_*.py]
    Generators -->|Read| EventLog
    Generators -->|Read| ScheduleDB
    Generators -->|Chart PNG| Telegram
```

---

## 🚀 Швидкий старт

### 1. Вимоги
*   Linux (Ubuntu/Debian/Raspbian)
*   Python 3.10+
*   "Біла" IP адреса або налаштований тунель (Cloudflare) для отримання Heartbeat-запитів.

### 2. Встановлення

```bash
# Клонування репозиторію
git clone https://github.com/weby-homelab/light-monitor-kyiv.git
cd light-monitor-kyiv

# Створення віртуального оточення
python3 -m venv venv
source venv/bin/activate

# Встановлення залежностей
pip install -r requirements.txt
```

### 3. Налаштування

Створіть файл `.env` для секретів:
```ini
TELEGRAM_BOT_TOKEN=123456:Ваш_Токен_Бота
TELEGRAM_CHANNEL_ID=-1001234567890
```

Відредагуйте `config.json` для вашої групи:
<details>
  <summary>🔍 Показати приклад config.json</summary>

```json
{
  "settings": {
    "region": "kiev",
    "groups": ["GPV36.1"],
    "show_intervals_detail": true,
    "style": "list"
  },
  "ui": {
    "icons": {
      "light_on": "🟢",
      "light_off": "🔴"
    }
  },
  "sources": {
    "yasno": { "enabled": true },
    "github": { "enabled": true }
  }
}
```
</details>

### 4. Запуск

#### Сервер моніторингу (фоновий процес)
Цей скрипт має працювати постійно. Рекомендується використовувати `systemd` або `screen/tmux`.
```bash
./venv/bin/python power_monitor_server.py
```
*Після запуску він створить файл `power_push_url.txt` з вашим унікальним посиланням для пінгів.*

#### Планувальник (Crontab)
Додайте ці рядки через `crontab -e`:

```cron
# Оновлення графіків (щогодини)
0 * * * * cd /root/geminicli/light-monitor-kyiv && ./venv/bin/python main.py >> cron.log 2>&1

# Щоденний звіт (00:05)
5 0 * * * cd /root/geminicli/light-monitor-kyiv && export $(grep -v '^#' .env | xargs) && ./venv/bin/python generate_daily_report.py >> cron.log 2>&1

# Тижневий звіт (Понеділок 00:10)
10 0 * * 1 cd /root/geminicli/light-monitor-kyiv && export $(grep -v '^#' .env | xargs) && ./venv/bin/python generate_weekly_report.py >> cron.log 2>&1
```

---

## 📱 Приклади повідомлень

| Тип | Вигляд |
| :--- | :--- |
| **Alert** | **🟢 18:41 Світло з'явилося**<br>📊 Статистика: відключення 4 год 10 хв<br>🗓 Аналіз: За графіком НЕ мало бути до 21:00<br>• Наступне вимкнення: **23:00** |
| **Daily Report** | ![Щоденний звіт](docs/img/daily_report.jpg)<br>**📈 План vs Факт:**<br>• За планом: 10 год<br>• Реально: 19.2 год<br>• Відхилення: +9.2 год (192%) |
| **Text Schedule** | **🔆 Графік групи 36.1 🔆**<br><br>📆 14.02 (Субота):<br>✖️ 19:30 - 02:00 … (6.5 год.)<br>---<br>🔆 Світло є: 10 год.<br>✖️ Світла нема: 14 год.<br>---<br>■ ■ ■ |
| **Weekly Report** | ![Тижневий звіт](docs/img/weekly_report.jpg)<br>🏆 **Найкращий день:** Понеділок<br>📝 **Аналіз:** Тиждень був відносно стабільним... |

---

## 🤝 Внесок у проект (Contributing)

Ми вітаємо будь-які ідеї та покращення! Ось як ви можете допомогти:

1.  Зробіть **Fork** цього репозиторію.
2.  Створіть гілку для вашої фічі (`git checkout -b feature/AmazingFeature`).
3.  Зафіксуйте зміни (`git commit -m 'Add some AmazingFeature'`).
4.  Відправте зміни у свій форк (`git push origin feature/AmazingFeature`).
5.  Відкрийте **Pull Request** у цей репозиторій.

## 📜 Ліцензія

Цей проект поширюється під ліцензією **MIT**. Дивіться файл `LICENSE` для детальної інформації.

---
*Розроблено з ❤️ у Києві під час блекаутів.*
