# 👻 GhostRun

**Запускай GitHub-репозитории прямо в памяти — без клонирования, без следов на диске.**

GhostRun загружает репозиторий с GitHub одним tarball-запросом, хранит его в сжатой виртуальной файловой системе (VirtualFS) и запускает как полноценный Python-проект через кастомный механизм импортов. Идеален для параллельного запуска ботов, воркеров и любых async-проектов.

---

## ✨ Возможности

- **Нет клонирования** — репозиторий никогда не касается диска
- **Один HTTP-запрос** — весь репо скачивается как tarball (не N запросов на файл)
- **zlib-сжатие в RAM** — Python-файлы занимают на 65–75% меньше памяти
- **Изолированные модули** — каждый запуск получает уникальный `runner_id`, модули не конфликтуют в `sys.modules`
- **Автозапуск точки входа** — ищет `main.py`, `bot.py`, `app.py` и т.д. автоматически
- **Автоперезапуск при краше** — `restart_on_crash=True` поднимает бот обратно
- **Параллельный запуск** — несколько репозиториев через `asyncio.gather`
- **Поддержка `.env`** — читает `.env` прямо из VirtualFS

---

## 📦 Установка

```bash
pip install aiohttp python-dotenv
```

Скопируй `GhostRun.py` в свой проект — это единственный файл, который тебе нужен.

---

## 🚀 Быстрый старт

```python
import asyncio
from GhostRun import GitHubProjectRunner

async def main():
    runner = GitHubProjectRunner(
        "https://github.com/owner/repo",
        token="ghp_...",        # необязательно
        restart_on_crash=True,
        restart_delay=15.0,
    )
    await runner.load()
    await runner.run()

asyncio.run(main())
```

### Параллельный запуск нескольких ботов

```python
import asyncio
from GhostRun import GitHubProjectRunner

REPOS = [
    "owner/bot-one",
    "owner/bot-two",
    "owner/bot-three",
]

async def main():
    runners = []
    for repo in REPOS:
        r = GitHubProjectRunner(
            f"https://github.com/{repo}",
            restart_on_crash=True,
            restart_delay=10.0,
        )
        await r.load()
        runners.append(r.run())

    await asyncio.gather(*runners)

asyncio.run(main())
```

---

## ⚙️ Параметры `GitHubProjectRunner`

| Параметр | Тип | По умолчанию | Описание |
|---|---|---|---|
| `url` | `str` | — | URL репозитория (`https://github.com/owner/repo`) |
| `token` | `str \| None` | `None` | GitHub Personal Access Token (увеличивает rate limit) |
| `branch` | `str \| None` | `None` | Ветка (если `None` — используется ветка по умолчанию) |
| `restart_on_crash` | `bool` | `False` | Перезапускать при любом необработанном исключении |
| `restart_delay` | `float` | `5.0` | Задержка перед перезапуском (секунды) |
| `load_dotenv` | `bool` | `True` | Загружать `.env` из репозитория |
| `max_file_size` | `int` | `5 МБ` | Пропускать файлы больше этого размера |
| `skip_extensions` | `tuple` | `.png, .jpg, ...` | Расширения файлов, которые не загружаются |

---

## 🔍 Как это работает

```
GitHub API (tarball)
        │
        ▼
  _GitHubFetcher          ← 1 HTTP-запрос на весь репозиторий
        │
        ▼
    VirtualFS              ← файлы в RAM, сжатые zlib
        │
        ▼
  VirtualFinder            ← встраивается в sys.meta_path
  VirtualLoader            ← перехватывает import и читает из VirtualFS
        │
        ▼
  exec(entry_point)        ← запускает точку входа как модуль
        │
        ▼
  await main() / run()     ← вызывает первую найденную async-функцию
```

### Автоопределение точки входа

GhostRun ищет файлы в таком порядке:

```
__main__.py → main.py → app.py → run.py → cli.py → start.py → server.py → manage.py → bot.py
```

Или укажи явно:

```python
await runner.run(entry="src/mybot.py")
await runner.run(entry="src/mybot.py", args=["--config", "prod.json"])
```

### Изоляция модулей

Каждый `GitHubProjectRunner` получает уникальный `runner_id` вида `owner_repo_a1b2c3d4`. Все модули регистрируются в `sys.modules` с этим префиксом — при остановке очищаются только модули конкретного запуска, не затрагивая другие.

---

## 🛠️ Утилиты

```python
# Список файлов в VirtualFS
runner.ls()            # корень
runner.ls("src")       # папка src/

# Прочитать файл из VirtualFS
runner.cat("config.json")

# Информация о потреблении памяти
print(runner.vfs)
# → VirtualFS(files=47, ram=128,340B zlib)
```

---

## 🔑 GitHub Token

Без токена GitHub ограничивает до **60 запросов в час**. С токеном — **5 000 запросов в час**.

Создай токен: **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**  
Нужные права: `Contents: Read-only`.

```bash
# .env файл
GITHUB_TOKEN=ghp_ваш_токен
```

```python
import os
from dotenv import load_dotenv
load_dotenv()

runner = GitHubProjectRunner(url, token=os.getenv("GITHUB_TOKEN"))
```

---

## 📋 Требования

- Python **3.10+**
- [`aiohttp`](https://docs.aiohttp.org/) — HTTP-клиент
- [`python-dotenv`](https://github.com/theskumar/python-dotenv) *(опционально)* — для загрузки `.env`

---

## 📄 Лицензия

MIT — делай что хочешь, упомяни авторство.