# 👻 GhostRun

**Запускай GitHub-репозитории прямо в памяти — без клонирования, без следов на диске.**

GhostRun загружает репозиторий с GitHub одним tarball-запросом, хранит его в сжатой виртуальной файловой системе и запускает как полноценный Python-проект через кастомный механизм импортов. Идеален для параллельного запуска ботов, воркеров и любых async-проектов.

---

## ✨ Возможности

- **Нет клонирования** — репозиторий никогда не касается диска
- **Один HTTP-запрос** — весь репо скачивается как tarball
- **zlib-сжатие в RAM** — Python-файлы занимают на 65–75% меньше памяти
- **SpooledTemporaryFile** — tarball не держится в RAM целиком, при превышении порога уходит во временный файл
- **Изолированные модули** — каждый запуск получает уникальный `runner_id`, модули не конфликтуют в `sys.modules`
- **Теневые импорты** — при запуске скрываются внешние модули с совпадающими именами, чтобы проект не тянул чужой код
- **Автозапуск точки входа** — ищет `main.py`, `bot.py`, `app.py` и т.д. автоматически
- **Отмена зависших задач** — после завершения `main()` все задачи, оставленные проектом в event loop, отменяются
- **Автоперезапуск при краше** — `restart_on_crash=True` поднимает проект обратно
- **Параллельный запуск** — несколько репозиториев через `asyncio.gather`
- **Поддержка `.env`** — читает `.env` прямо из VirtualFS
- **`free_run()`** — однострочный запуск списка репозиториев без лишнего кода

---

## 📦 Установка

```bash
pip install aiohttp python-dotenv
```

Скопируй `GhostRun.py` в свой проект — это единственный файл, который тебе нужен.

---

## 🚀 Быстрый старт

Самый простой способ запустить несколько репозиториев:

```python
import asyncio
from GhostRun import free_run

asyncio.run(
	free_run(
		cntnts=[
			"owner/repo1",
			"owner/repo2",
		],
		clear_cache=True,
	)
)
```

`free_run` автоматически читает `GITHUB_TOKEN` из окружения, запускает все репозитории параллельно с `restart_on_crash=True` и `restart_delay=15s`.

---

## 🛠️ Ручной запуск

Если нужен полный контроль над параметрами:

```python
import asyncio
from GhostRun import GitHubProjectRunner

async def main():
	runner = GitHubProjectRunner(
		"https://github.com/owner/repo",
		token="ghp_...",
		restart_on_crash=True,
		restart_delay=15.0,
		clear_cache=True,
	)
	await runner.load()
	await runner.run()

asyncio.run(main())
```

### Явная точка входа и аргументы CLI

```python
await runner.run(entry="src/bot.py")
await runner.run(entry="src/bot.py", args=["--config", "prod.json"])
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
	tasks = []
	for repo in REPOS:
		r = GitHubProjectRunner(
			f"https://github.com/{repo}",
			restart_on_crash=True,
			restart_delay=10.0,
		)
		await r.load()
		tasks.append(r.run())

	await asyncio.gather(*tasks)

asyncio.run(main())
```

---

## ⚙️ Параметры `GitHubProjectRunner`

| Параметр | Тип | По умолчанию | Описание |
|---|---|---|---|
| `url` | `str` | — | URL репозитория (`https://github.com/owner/repo`) |
| `token` | `str \| None` | `None` | GitHub Personal Access Token |
| `branch` | `str \| None` | `None` | Ветка (если `None` — используется ветка по умолчанию) |
| `restart_on_crash` | `bool` | `False` | Перезапускать при любом необработанном исключении |
| `restart_delay` | `float` | `5.0` | Задержка перед перезапуском (секунды) |
| `serialize_runs` | `bool` | `True` | Сериализовать запуски через глобальный `asyncio.Lock` |
| `load_dotenv` | `bool` | `True` | Загружать `.env` из репозитория |
| `py_only` | `bool` | `True` | Загружать только `.py` файлы (экономит RAM) |
| `auto_cleanup` | `bool` | `False` | Освобождать ресурсы после завершения |
| `clear_cache` | `bool` | `False` | Полная очистка: VFS, модули в `sys.modules`, finder |
| `max_file_size` | `int` | `5 МБ` | Пропускать файлы больше этого размера |
| `skip_extensions` | `tuple` | `.png, .jpg, ...` | Расширения файлов, которые не загружаются (при `py_only=False`) |

---

## 🔍 Как это работает

```
GitHub API (tarball)
        │
        ▼
  _GitHubFetcher          ← 1 HTTP-запрос на весь репозиторий
        │  SpooledTemporaryFile — RAM до 1MB, потом временный файл
        ▼
    VirtualFS              ← файлы в RAM, сжатые zlib
        │
        ▼
  _shadow_sys_modules()   ← скрывает конфликтующие внешние модули
        │
        ▼
  VirtualFinder            ← встраивается в sys.meta_path
  VirtualLoader            ← перехватывает import и читает из VirtualFS
  _SysPathProxy            ← перехватывает sys.path для динамических корней
        │
        ▼
  exec(entry_point)        ← запускает точку входа как модуль
        │
        ▼
  await main() / run()     ← вызывает первую найденную async-функцию
        │
        ▼
  cancel orphaned tasks    ← отменяет зависшие задачи после завершения
        │
        ▼
  _restore_sys_modules()  ← возвращает скрытые внешние модули обратно
```

### Автоопределение точки входа

GhostRun ищет файлы в таком порядке:

```
__main__.py → main.py → app.py → run.py → cli.py → start.py → server.py → manage.py → bot.py
```

### Изоляция модулей

Каждый `GitHubProjectRunner` получает уникальный `runner_id` вида `owner_repo_a1b2c3d4`. Все модули регистрируются с этим префиксом в `sys.modules` — при очистке удаляются только они, не затрагивая другие запуски.

---

## 🛠️ Утилиты

```python
# Список файлов в VirtualFS
runner.ls()           # корень
runner.ls("src")      # папка src/

# Прочитать файл из VirtualFS
runner.cat("config.json")

# Информация о потреблении памяти
print(runner.vfs)
# → VirtualFS(files=47, ram=128,340B zlib)

# Принудительная очистка ресурсов
runner.cleanup()
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
