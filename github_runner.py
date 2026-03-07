"""
GitHubProjectRunner — загружает GitHub-репозиторий в память и запускает его
как локальный проект через кастомную систему импортов Python.

Зависимости: pip install aiohttp

Оптимизации памяти (v2):
  • VirtualFS хранит данные в zlib-сжатии (Python-файлы: −65–75% RAM)
  • Репозиторий загружается одним tarball-запросом вместо N файл-запросов
    (нет base64-JSON оверхеда, нет N*asyncio.Task в памяти одновременно)
  • __slots__ на всех внутренних классах (нет лишнего __dict__)
  • weakref из Loader/Finder на VFS — не держим сильную ссылку
  • del source / del code / del content сразу после использования
  • gc.collect() после полной загрузки и после очистки finder-а
  • TCPConnector с limit и keepalive_timeout для контроля сокетов
"""

import asyncio
import gc
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import io
import logging
import os
import re
import sys
import tarfile
import types
import uuid
import weakref
import zlib
from pathlib import PurePosixPath
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Виртуальная файловая система (только RAM, опциональное zlib-сжатие)
# ─────────────────────────────────────────────────────────────────────────────

class VirtualFS:
	"""
	Хранит файлы репозитория в памяти.

	При compress=True (по умолчанию) данные хранятся в сжатом виде zlib.
	Python-исходники обычно сжимаются на 65–75%, что резко снижает RAM.
	Декомпрессия происходит при каждом чтении — CPU-трейдофф оправдан
	для long-running процессов, где файлы читаются редко.
	"""
	__slots__ = ("_files", "_compress", "__weakref__")

	def __init__(self, compress: bool = True):
		self._files: dict[str, bytes] = {}
		self._compress = compress

	def write(self, path: str, data: bytes) -> None:
		key = path.lstrip("/")
		self._files[key] = zlib.compress(data, 6) if self._compress else data

	def read(self, path: str) -> Optional[bytes]:
		raw = self._files.get(path.lstrip("/"))
		if raw is None:
			return None
		return zlib.decompress(raw) if self._compress else raw

	def read_text(self, path: str, encoding: str = "utf-8") -> Optional[str]:
		data = self.read(path)
		return data.decode(encoding, errors="replace") if data is not None else None

	def exists(self, path: str) -> bool:
		path = path.lstrip("/")
		return path in self._files or any(k.startswith(path + "/") for k in self._files)

	def is_file(self, path: str) -> bool:
		return path.lstrip("/") in self._files

	def listdir(self, path: str = "") -> list[str]:
		path = path.lstrip("/")
		prefix = path + "/" if path else ""
		result: set[str] = set()
		for k in self._files:
			if k.startswith(prefix):
				rest = k[len(prefix):]
				result.add(rest.split("/")[0])
		return sorted(result)

	@property
	def all_paths(self) -> list[str]:
		return sorted(self._files)

	def ram_usage(self) -> int:
		"""Объём данных в RAM (после сжатия, если включено)."""
		return sum(len(v) for v in self._files.values())

	def __len__(self) -> int:
		return len(self._files)

	def __repr__(self) -> str:
		ram = self.ram_usage()
		suffix = " zlib" if self._compress else ""
		return f"VirtualFS(files={len(self._files)}, ram={ram:,}B{suffix})"


# ─────────────────────────────────────────────────────────────────────────────
# Кастомный загрузчик модулей из VirtualFS
# ─────────────────────────────────────────────────────────────────────────────

class VirtualLoader(importlib.abc.Loader):
	"""
	Загружает Python-модуль прямо из VirtualFS.
	Держит слабую ссылку на VFS — не мешает GC при очистке.
	"""
	__slots__ = ("_vfs_ref", "path", "runner_id")

	def __init__(self, vfs: VirtualFS, path: str, runner_id: str):
		self._vfs_ref: weakref.ref[VirtualFS] = weakref.ref(vfs)
		self.path = path
		self.runner_id = runner_id

	def _vfs(self) -> Optional[VirtualFS]:
		return self._vfs_ref()

	def create_module(self, spec):
		return None

	def exec_module(self, module: types.ModuleType) -> None:
		vfs = self._vfs()
		if vfs is None:
			raise ImportError(f"VirtualFS уже выгружен: {self.path}")
		source = vfs.read_text(self.path)
		if source is None:
			raise ImportError(f"Файл не найден в VirtualFS: {self.path}")
		code = compile(source, f"<vfs:{self.runner_id}>/{self.path}", "exec")
		del source	# освобождаем строку исходника до exec
		exec(code, module.__dict__)
		del code

	def get_source(self, fullname: str) -> str:
		vfs = self._vfs()
		return (vfs.read_text(self.path) if vfs else None) or ""

	def get_filename(self, fullname: str) -> str:
		return f"<vfs:{self.runner_id}>/{self.path}"


class VirtualFinder(importlib.abc.MetaPathFinder):
	"""
	Meta path finder — перехватывает import и ищет модуль в VirtualFS.
	Каждый экземпляр привязан к конкретному runner_id, чтобы при очистке
	sys.modules удалялись только модули этого запуска, а не чужие.
	Держит слабую ссылку на VFS.
	"""
	__slots__ = ("_vfs_ref", "root", "runner_id")

	def __init__(self, vfs: VirtualFS, root: str, runner_id: str):
		self._vfs_ref: weakref.ref[VirtualFS] = weakref.ref(vfs)
		self.root = root.strip("/")
		self.runner_id = runner_id

	def _vfs(self) -> Optional[VirtualFS]:
		return self._vfs_ref()

	def _candidates(self, fullname: str) -> list[tuple[str, bool]]:
		"""Возвращает список (путь_в_vfs, is_package) для имени модуля."""
		parts = fullname.split(".")
		rel = "/".join(parts)
		prefix = f"{self.root}/{rel}" if self.root else rel
		return [
			(f"{prefix}/__init__.py", True),
			(f"{prefix}.py", False),
		]

	def find_spec(self, fullname, path, target=None):
		vfs = self._vfs()
		if vfs is None:
			return None
		for vfs_path, is_package in self._candidates(fullname):
			if vfs.is_file(vfs_path):
				loader = VirtualLoader(vfs, vfs_path, self.runner_id)
				origin = f"<vfs:{self.runner_id}>/{vfs_path}"
				spec = importlib.machinery.ModuleSpec(
					name=fullname,
					loader=loader,
					origin=origin,
					is_package=is_package,
				)
				spec.submodule_search_locations = (
					[f"<vfs:{self.runner_id}>/{vfs_path.replace('/__init__.py', '')}"]
					if is_package else []
				)
				return spec
		return None


# ─────────────────────────────────────────────────────────────────────────────
# Загрузчик .env из VirtualFS
# ─────────────────────────────────────────────────────────────────────────────

def _load_env_from_vfs(vfs: VirtualFS, repo_label: str, path: str = ".env") -> int:
	"""
	Парсит и применяет .env-файл из VirtualFS.
	Не перезаписывает переменные, уже заданные в окружении.
	Возвращает количество применённых переменных.
	"""
	content = vfs.read_text(path)
	if content is None:
		return 0

	applied = 0
	for line in content.splitlines():
		line = line.strip()
		if not line or line.startswith("#") or "=" not in line:
			continue
		key, _, value = line.partition("=")
		key = key.strip()
		value = value.strip().strip('"').strip("'")
		if key and key not in os.environ:
			os.environ[key] = value
			applied += 1

	if applied:
		log.info(f"[{repo_label}] .env из VFS: применено {applied} переменных")
	return applied


# ─────────────────────────────────────────────────────────────────────────────
# Загрузчик репозитория с GitHub
# ─────────────────────────────────────────────────────────────────────────────

class _GitHubFetcher:
	"""
	Загружает репозиторий одним tarball-запросом вместо N файловых запросов.

	Преимущества по сравнению с /contents/{path} per-file:
	  • 1 HTTP-запрос вместо N (для репо с 200 файлами — в 200 раз меньше)
	  • Нет base64-JSON оверхеда (~33% трафика экономии)
	  • Нет N asyncio.Task в памяти одновременно
	  • Файлы обрабатываются потоково из tar — не накапливаются в RAM
	  • Значительно быстрее загрузка
	"""
	__slots__ = ("headers", "_concurrency", "max_size", "skip_ext")

	API = "https://api.github.com"

	def __init__(
		self,
		token: Optional[str],
		concurrency: int,
		max_size: int,
		skip_ext: tuple,
	):
		self.headers: dict[str, str] = {
			"Accept": "application/vnd.github+json",
			"X-GitHub-Api-Version": "2022-11-28",
			**({} if not token else {"Authorization": f"Bearer {token}"}),
		}
		self._concurrency = concurrency
		self.max_size = max_size
		self.skip_ext = skip_ext

	# ── Вспомогательные ─────────────────────────────────────────────────────

	def _should_skip(self, path: str, size: int) -> bool:
		if size > self.max_size:
			log.debug(f"  [skip size] {path}")
			return True
		if self.skip_ext and path.endswith(self.skip_ext):
			log.debug(f"  [skip ext]  {path}")
			return True
		return False

	# ── GitHub API ───────────────────────────────────────────────────────────

	async def default_branch(
		self, s: aiohttp.ClientSession, owner: str, repo: str
	) -> str:
		url = f"{self.API}/repos/{owner}/{repo}"
		try:
			async with s.get(url, headers=self.headers) as r:
				if r.status == 401:
					raise RuntimeError("GitHub 401 Unauthorized — токен неверный или истёк")
				if r.status == 404:
					raise RuntimeError(f"GitHub 404 — репозиторий {owner}/{repo} не найден")
				if r.status != 200:
					body = await r.text()
					raise RuntimeError(f"GitHub {r.status} при получении ветки: {body[:200]}")
				data = await r.json()
				return data["default_branch"]
		except aiohttp.ClientError as e:
			raise RuntimeError(
				f"Сетевая ошибка при получении ветки {owner}/{repo}: {e}"
			) from e

	async def _load_via_tarball(
		self,
		s: aiohttp.ClientSession,
		owner: str,
		repo: str,
		branch: str,
		vfs: VirtualFS,
	) -> tuple[int, int, int]:
		"""
		Скачивает весь репозиторий одним tarball-запросом и заполняет VFS.
		Файлы обрабатываются потоково (tarfile читает tar-stream блоками),
		поэтому в RAM никогда не хранится весь архив целиком в раскрытом виде.
		Возвращает (ok, skip, err).
		"""
		url = f"{self.API}/repos/{owner}/{repo}/tarball/{branch}"
		log.debug(f"[{owner}/{repo}] tarball → {url}")

		try:
			async with s.get(url, headers=self.headers, allow_redirects=True) as r:
				if r.status == 401:
					raise RuntimeError("GitHub 401 Unauthorized — токен неверный или истёк")
				if r.status == 404:
					raise RuntimeError(
						f"GitHub 404 — репозиторий {owner}/{repo}@{branch} не найден"
					)
				if r.status != 200:
					body = await r.text()
					raise RuntimeError(
						f"GitHub {r.status} при загрузке tarball: {body[:200]}"
					)
				# Читаем чанками — не держим весь ответ как одну строку в памяти
				buf = io.BytesIO()
				async for chunk in r.content.iter_chunked(1 << 16):  # 64 KiB
					buf.write(chunk)
				del chunk  # type: ignore[possibly-undefined]
		except aiohttp.ClientError as e:
			raise RuntimeError(
				f"Сетевая ошибка при загрузке tarball {owner}/{repo}: {e}"
			) from e

		buf.seek(0)
		ok = skip = err = 0
		try:
			# mode="r:gz" — потоковая распаковка, не грузит всё сразу
			with tarfile.open(fileobj=buf, mode="r:gz") as tf:
				for member in tf.getmembers():
					if not member.isfile():
						continue
					# Отрезаем ведущий каталог: "owner-repo-sha/path/to/file.py"
					slash = member.name.find("/")
					if slash == -1:
						continue
					path = member.name[slash + 1:]
					if not path:
						continue
					if self._should_skip(path, member.size):
						skip += 1
						continue
					try:
						fobj = tf.extractfile(member)
						if fobj is None:
							skip += 1
							continue
						content = fobj.read()
						vfs.write(path, content)
						del content  # сразу освобождаем — в VFS уже сжатый вариант
						ok += 1
					except Exception as exc:
						log.error(f"  [error] {path}: {exc}")
						err += 1
		except tarfile.TarError as exc:
			raise RuntimeError(f"Ошибка распаковки tarball {owner}/{repo}: {exc}") from exc
		finally:
			buf.close()

		return ok, skip, err

	# ── Основной метод ───────────────────────────────────────────────────────

	async def load_all(
		self, owner: str, repo: str, branch: Optional[str]
	) -> tuple[str, VirtualFS]:
		vfs = VirtualFS()	# compress=True по умолчанию
		connector = aiohttp.TCPConnector(
			limit=self._concurrency,
			enable_cleanup_closed=True,
			keepalive_timeout=15,
		)
		async with aiohttp.ClientSession(connector=connector) as s:
			if not branch:
				try:
					branch = await self.default_branch(s, owner, repo)
					log.info(f"[{owner}/{repo}] ветка: {branch}")
				except RuntimeError as e:
					log.error(f"[{owner}/{repo}] Не удалось получить ветку: {e}")
					return "unknown", vfs

			try:
				ok, skip, err = await self._load_via_tarball(s, owner, repo, branch, vfs)
			except RuntimeError as e:
				log.error(f"[{owner}/{repo}] Ошибка загрузки: {e}")
				return branch, vfs

		log.info(f"[{owner}/{repo}] загружено {ok} | пропущено {skip} | ошибок {err}")
		gc.collect()	# собираем мусор после загрузки (буферы, JSON и т.д.)
		return branch, vfs


# ─────────────────────────────────────────────────────────────────────────────
# Главный класс
# ─────────────────────────────────────────────────────────────────────────────

class GitHubProjectRunner:
	"""
	Загружает GitHub-репозиторий в память и запускает его без сохранения на диск.

	Использование:
		runner = GitHubProjectRunner("https://github.com/owner/repo", token="ghp_...")
		await runner.load()
		await runner.run()                        # авто-поиск точки входа
		await runner.run(entry="src/app.py")      # явная точка входа
		await runner.run(entry="cli", args=["--help"])  # аргументы CLI
	"""

	_ENTRY_CANDIDATES = [
		"__main__.py", "main.py", "app.py", "run.py",
		"cli.py", "start.py", "server.py", "manage.py",
		"bot.py",
	]

	# Имена async-функций, которые будут вызваны как точка входа
	_ASYNC_ENTRY_NAMES = ["main", "run", "start", "app", "bot", "serve"]

	def __init__(
		self,
		url: str,
		*,
		token: Optional[str] = None,
		branch: Optional[str] = None,
		concurrency: int = 10,
		max_file_size: int = 5 * 1024 * 1024,
		skip_extensions: tuple[str, ...] = (
			".png", ".jpg", ".jpeg", ".gif", ".ico",
			".woff", ".woff2", ".ttf", ".eot", ".zip",
			".tar", ".gz", ".bin", ".exe", ".pdf",
		),
		load_dotenv: bool = True,
		restart_on_crash: bool = False,
		restart_delay: float = 5.0,
	):
		owner, repo = self._parse_url(url)
		self.owner = owner
		self.repo = repo
		self.branch = branch
		self.load_dotenv = load_dotenv
		self.restart_on_crash = restart_on_crash
		self.restart_delay = restart_delay
		self._fetcher = _GitHubFetcher(token, concurrency, max_file_size, skip_extensions)
		self._vfs: Optional[VirtualFS] = None
		# Уникальный ID запуска — нужен для изоляции модулей в sys.modules
		# при одновременном запуске нескольких репозиториев
		self._runner_id = f"{owner}_{repo}_{uuid.uuid4().hex[:8]}"

	# ── Парсинг URL ──────────────────────────────────────────────────────────

	@staticmethod
	def _parse_url(url: str) -> tuple[str, str]:
		m = re.search(r"github\.com[/:]([^/]+)/([^/.\s]+)", url)
		if not m:
			raise ValueError(f"Не удалось распознать GitHub URL: {url!r}")
		return m.group(1), m.group(2).removesuffix(".git")

	# ── Загрузка ─────────────────────────────────────────────────────────────

	async def load(self) -> "GitHubProjectRunner":
		"""Загрузить репозиторий в память (вызвать перед run)."""
		log.info(f"[{self._runner_id}] Загрузка {self.owner}/{self.repo}")
		try:
			self.branch, self._vfs = await self._fetcher.load_all(
				self.owner, self.repo, self.branch
			)
		except RuntimeError as e:
			log.error(f"[{self._runner_id}] Ошибка загрузки репозитория: {e}")
			raise
		log.info(f"[{self._runner_id}] VFS готов: {self._vfs}")

		if self.load_dotenv:
			_load_env_from_vfs(self._vfs, self._runner_id)

		return self

	# ── Поиск точки входа ────────────────────────────────────────────────────

	def _find_entry(self) -> str:
		vfs = self._vfs
		for name in self._ENTRY_CANDIDATES:
			if vfs.is_file(name):
				return name
		for path in vfs.all_paths:
			parts = path.split("/")
			if len(parts) == 2 and parts[1] in self._ENTRY_CANDIDATES:
				return path
		raise FileNotFoundError(
			"Точка входа не найдена. Укажи явно: runner.run(entry='path/to/main.py')\n"
			f"Доступные файлы: {vfs.listdir()}"
		)

	def _find_async_entry(
		self, module_dict: dict
	) -> tuple[Optional[str], Optional[object]]:
		"""Ищет первую async-функцию из списка _ASYNC_ENTRY_NAMES в пространстве модуля."""
		for name in self._ASYNC_ENTRY_NAMES:
			fn = module_dict.get(name)
			if asyncio.iscoroutinefunction(fn):
				return name, fn
		return None, None

	# ── Управление finder'ом ─────────────────────────────────────────────────

	def _install_finder(self, root: str) -> VirtualFinder:
		finder = VirtualFinder(self._vfs, root, self._runner_id)
		sys.meta_path.insert(0, finder)
		return finder

	def _uninstall_finder(self, finder: VirtualFinder) -> None:
		try:
			sys.meta_path.remove(finder)
		except ValueError:
			pass
		# Удаляем только модули ЭТОГО запуска по уникальному runner_id в origin
		origin_prefix = f"<vfs:{self._runner_id}>"
		to_delete = [
			name for name, mod in sys.modules.items()
			if getattr(mod, "__spec__", None)
			and getattr(mod.__spec__, "origin", "").startswith(origin_prefix)
		]
		for name in to_delete:
			del sys.modules[name]
		if to_delete:
			log.debug(f"[{self._runner_id}] Очищено {len(to_delete)} модулей из sys.modules")
		gc.collect()

	# ── Один запуск ──────────────────────────────────────────────────────────

	async def _run_once(self, entry: str, args: Optional[list[str]]) -> None:
		orig_argv = sys.argv.copy()
		sys.argv = [f"<vfs>/{entry}", *(args or [])]

		root = str(PurePosixPath(entry).parent) if "/" in entry else ""
		finder = self._install_finder(root)

		try:
			source = self._vfs.read_text(entry)
			if source is None:
				raise FileNotFoundError(f"Файл не найден в VirtualFS: {entry!r}")

			try:
				code = compile(source, f"<vfs:{self._runner_id}>/{entry}", "exec")
			except SyntaxError as e:
				log.error(f"[{self._runner_id}] SyntaxError в {entry}: {e}")
				raise
			del source	# освобождаем строку исходника до exec

			# ВАЖНО: ставим __name__ = "__vfs_main__", а НЕ "__main__"
			# Это предотвращает срабатывание блока `if __name__ == '__main__':`
			# и вложенного asyncio.run() внутри уже запущенного event loop.
			module = types.ModuleType("__vfs_main__")
			module.__file__ = f"<vfs:{self._runner_id}>/{entry}"
			module.__loader__ = None
			module.__package__ = root.replace("/", ".") if root else ""
			module.__spec__ = None

			log.info(f"[{self._runner_id}] ▶ {self.owner}/{self.repo}/{entry}")
			log.info("─" * 60)

			try:
				exec(code, module.__dict__)
			except Exception as e:
				log.error(
					f"[{self._runner_id}] Ошибка при инициализации модуля "
					f"{entry}: {type(e).__name__}: {e}"
				)
				raise
			finally:
				del code	# освобождаем объект кода после exec

			fn_name, fn = self._find_async_entry(module.__dict__)
			if fn is not None:
				log.info(f"[{self._runner_id}] Вызов async {fn_name}()")
				try:
					await fn()
				except Exception as e:
					log.error(
						f"[{self._runner_id}] Ошибка в {fn_name}(): "
						f"{type(e).__name__}: {e}"
					)
					raise
			else:
				log.warning(
					f"[{self._runner_id}] Async точка входа не найдена "
					f"(проверяются: {self._ASYNC_ENTRY_NAMES}). "
					"Укажи entry явно или добавь async def main()/run()."
				)

		finally:
			self._uninstall_finder(finder)
			sys.argv = orig_argv

	# ── Публичный run ─────────────────────────────────────────────────────────

	async def run(
		self,
		entry: Optional[str] = None,
		args: Optional[list[str]] = None,
	) -> None:
		"""
		Запустить проект из памяти.

		Args:
			entry: Путь к файлу внутри репозитория (напр. "src/main.py").
				   Если не указан — ищется автоматически.
			args:  Аргументы командной строки.
		"""
		if self._vfs is None:
			raise RuntimeError("Сначала вызови await runner.load()")

		if len(self._vfs) == 0:
			log.error(
				f"[{self._runner_id}] VFS пустой — репозиторий не загрузился "
				"(rate limit или ошибка сети). Запуск пропущен."
			)
			return

		if entry is None:
			try:
				entry = self._find_entry()
			except FileNotFoundError as e:
				log.error(f"[{self._runner_id}] {e}")
				return
			log.info(f"[{self._runner_id}] Точка входа: {entry}")

		if self.restart_on_crash:
			while True:
				try:
					await self._run_once(entry, args)
					log.info(f"[{self._runner_id}] Завершился без ошибок")
					break
				except (KeyboardInterrupt, asyncio.CancelledError):
					log.info(f"[{self._runner_id}] Остановлен")
					break
				except Exception as exc:
					log.info("─" * 60)
					log.error(
						f"[{self._runner_id}] Упал: {exc!r}. "
						f"Перезапуск через {self.restart_delay}с..."
					)
					await asyncio.sleep(self.restart_delay)
		else:
			await self._run_once(entry, args)

	# ── Утилиты ──────────────────────────────────────────────────────────────

	@property
	def vfs(self) -> VirtualFS:
		if self._vfs is None:
			raise RuntimeError("Репозиторий ещё не загружен")
		return self._vfs

	def ls(self, path: str = "") -> list[str]:
		"""Список файлов/папок в директории VirtualFS."""
		return self.vfs.listdir(path)

	def cat(self, path: str) -> str:
		"""Прочитать файл из VirtualFS как текст."""
		content = self.vfs.read_text(path)
		if content is None:
			raise FileNotFoundError(f"Файл не найден: {path!r}")
		return content

	def __repr__(self) -> str:
		status = repr(self._vfs) if self._vfs else "не загружен"
		return f"GitHubProjectRunner({self.owner}/{self.repo}@{self.branch}, {status})"
