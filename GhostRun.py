"""
Optimized GitHubProjectRunner — single-file implementation.

Ключевые улучшения по сравнению с предоставленным вариантом:
	• Спул-файл (tempfile.SpooledTemporaryFile) для tarball — не держим весь
	  архив в RAM (перекладывается на диск при превышении порога).
	• Чтение tar в streaming-режиме (итерация по TarFile) — меньше временных структур.
	• async with global lock — корректная и простая сериализация запусков.
	• исправлен сохранённый sys.path (копия) при установке прокси.
	• дополнительные del и gc.collect() в горячих местах.
	• отложенная/автоочистка ресурсов (auto_cleanup) и метод cleanup публичный.
	• табы вместо 4 пробелов (файл готов к вставке как новый модуль).
"""

import asyncio
import gc
import importlib.abc
import importlib.machinery
import io
import logging
import os
import re
import sys
import tarfile
import tempfile
import types
import uuid
import weakref
import zlib
from pathlib import PurePosixPath
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# VirtualFS (in-memory, optional zlib compression)
# ─────────────────────────────────────────────────────────────────────────────

class VirtualFS:
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

	def is_dir(self, path: str) -> bool:
		path = path.lstrip("/")
		if not path:
			return True
		return any(k.startswith(path + "/") for k in self._files)

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
		return sum(len(v) for v in self._files.values())

	def __len__(self) -> int:
		return len(self._files)

	def __repr__(self) -> str:
		ram = self.ram_usage()
		suffix = " zlib" if self._compress else ""
		return f"VirtualFS(files={len(self._files)}, ram={ram:,}B{suffix})"


# ─────────────────────────────────────────────────────────────────────────────
# Loader / Finder
# ─────────────────────────────────────────────────────────────────────────────

class VirtualLoader(importlib.abc.Loader):
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
			raise ImportError(f"VirtualFS already unloaded: {self.path}")
		source = vfs.read_text(self.path)
		if source is None:
			raise ImportError(f"File not found in VirtualFS: {self.path}")
		code = compile(source, f"<vfs:{self.runner_id}>/{self.path}", "exec")
		del source
		try:
			exec(code, module.__dict__)
		finally:
			del code

	def get_source(self, fullname: str) -> str:
		vfs = self._vfs()
		return (vfs.read_text(self.path) if vfs else None) or ""

	def get_filename(self, fullname: str) -> str:
		return f"<vfs:{self.runner_id}>/{self.path}"


class VirtualFinder(importlib.abc.MetaPathFinder):
	__slots__ = ("_vfs_ref", "_roots", "runner_id")

	def __init__(self, vfs: VirtualFS, roots: list[str], runner_id: str):
		self._vfs_ref: weakref.ref[VirtualFS] = weakref.ref(vfs)
		seen: set[str] = set()
		self._roots: list[str] = []
		for r in roots:
			r = r.strip("/")
			if r not in seen:
				seen.add(r)
				self._roots.append(r)
		self.runner_id = runner_id

	@property
	def root(self) -> str:
		return self._roots[0] if self._roots else ""

	def add_root(self, root: str) -> bool:
		root = root.strip("/")
		if root not in self._roots:
			self._roots.append(root)
			log.debug(f"[{self.runner_id}] Added search root: '{root}'")
			return True
		return False

	def _vfs(self) -> Optional[VirtualFS]:
		return self._vfs_ref()

	def _vfs_dirs_from_path(self, path) -> list[str]:
		if not path:
			return []
		vfs_prefix = f"<vfs:{self.runner_id}>/"
		dirs: list[str] = []
		for p in path:
			if isinstance(p, str) and p.startswith(vfs_prefix):
				d = p[len(vfs_prefix):].strip("/")
				dirs.append(d)
		return dirs

	def _candidates(self, fullname: str, extra_roots: list[str] | None = None) -> list[tuple[str, bool]]:
		candidates: list[tuple[str, bool]] = []
		if extra_roots:
			leaf = fullname.split(".")[-1]
			for er in extra_roots:
				prefix = f"{er}/{leaf}" if er else leaf
				candidates.append((f"{prefix}/__init__.py", True))
				candidates.append((f"{prefix}.py", False))
		parts = fullname.split(".")
		rel = "/".join(parts)
		for root in self._roots:
			prefix = f"{root}/{rel}" if root else rel
			entry = (f"{prefix}/__init__.py", True)
			entry_py = (f"{prefix}.py", False)
			if entry not in candidates:
				candidates.append(entry)
			if entry_py not in candidates:
				candidates.append(entry_py)
		return candidates

	def _namespace_dirs(self, fullname: str, extra_roots: list[str] | None = None) -> list[str]:
		dirs: list[str] = []
		vfs = self._vfs()
		if vfs is None:
			return dirs
		all_roots: list[str] = list(extra_roots or [])
		parts = fullname.split(".")
		rel = "/".join(parts)
		leaf = parts[-1]
		for er in (extra_roots or []):
			prefix = f"{er}/{leaf}" if er else leaf
			if vfs.is_dir(prefix) and not vfs.is_file(f"{prefix}/__init__.py"):
				if prefix not in dirs:
					dirs.append(prefix)
		for root in self._roots:
			prefix = f"{root}/{rel}" if root else rel
			if vfs.is_dir(prefix) and not vfs.is_file(f"{prefix}/__init__.py"):
				if prefix not in dirs:
					dirs.append(prefix)
		return dirs

	def find_spec(self, fullname, path, target=None):
		vfs = self._vfs()
		if vfs is None:
			return None
		extra_roots = self._vfs_dirs_from_path(path)
		for vfs_path, is_package in self._candidates(fullname, extra_roots):
			if vfs.is_file(vfs_path):
				loader = VirtualLoader(vfs, vfs_path, self.runner_id)
				origin = f"<vfs:{self.runner_id}>/{vfs_path}"
				spec = importlib.machinery.ModuleSpec(
					name=fullname,
					loader=loader,
					origin=origin,
					is_package=is_package,
				)
				pkg_dir = vfs_path.replace("/__init__.py", "")
				spec.submodule_search_locations = (
					[f"<vfs:{self.runner_id}>/{pkg_dir}"] if is_package else None
				)
				return spec
		ns_dirs = self._namespace_dirs(fullname, extra_roots)
		if ns_dirs:
			spec = importlib.machinery.ModuleSpec(
				name=fullname,
				loader=None,
				is_package=True,
			)
			spec.submodule_search_locations = [
				f"<vfs:{self.runner_id}>/{d}" for d in ns_dirs
			]
			return spec
		return None


# ─────────────────────────────────────────────────────────────────────────────
# sys.path proxy
# ─────────────────────────────────────────────────────────────────────────────

class _SysPathProxy(list):
	__slots__ = ("_finder_ref", "_runner_id", "_vfs_prefix")

	def __new__(cls, initial, finder, runner_id):
		# ensure we make a proper list copy as base for proxy content
		obj = super().__new__(cls, initial)
		return obj

	def __init__(self, initial, finder: VirtualFinder, runner_id: str):
		super().__init__(initial)
		self._finder_ref: weakref.ref[VirtualFinder] = weakref.ref(finder)
		self._runner_id = runner_id
		self._vfs_prefix = f"<vfs:{runner_id}>/"

	def _notify(self, path: str) -> None:
		finder = self._finder_ref()
		if finder is None:
			return
		vfs = finder._vfs()
		if vfs is None:
			return
		if path.startswith(self._vfs_prefix):
			root = path[len(self._vfs_prefix):].strip("/")
			if root and vfs.is_dir(root):
				finder.add_root(root)
			return
		normalized = path.strip("/").strip(".")
		if normalized and vfs.is_dir(normalized):
			finder.add_root(normalized)

	def insert(self, index, path):
		super().insert(index, path)
		self._notify(path)

	def append(self, path):
		super().append(path)
		self._notify(path)

	def extend(self, paths):
		super().extend(paths)
		for p in paths:
			self._notify(p)

	def __setitem__(self, index, value):
		super().__setitem__(index, value)
		if isinstance(value, str):
			self._notify(value)
		elif isinstance(index, slice) and hasattr(value, "__iter__"):
			for v in value:
				if isinstance(v, str):
					self._notify(v)


# ─────────────────────────────────────────────────────────────────────────────
# .env loader
# ─────────────────────────────────────────────────────────────────────────────

def _load_env_from_vfs(vfs: VirtualFS, repo_label: str, path: str = ".env") -> int:
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
		log.info(f"[{repo_label}] .env applied: {applied} vars")
	return applied


# ─────────────────────────────────────────────────────────────────────────────
# GitHub fetcher (tarball, streaming to spooled temp file)
# ─────────────────────────────────────────────────────────────────────────────

class _GitHubFetcher:
	__slots__ = ("headers", "_concurrency", "max_size", "skip_ext", "py_only")

	API = "https://api.github.com"

	def __init__(
		self,
		token: Optional[str],
		concurrency: int,
		max_size: int,
		skip_ext: tuple,
		py_only: bool = True,
	):
		self.headers: dict[str, str] = {
			"Accept": "application/vnd.github+json",
			"X-GitHub-Api-Version": "2022-11-28",
			**({} if not token else {"Authorization": f"Bearer {token}"}),
		}
		self._concurrency = concurrency
		self.max_size = max_size
		self.skip_ext = skip_ext
		self.py_only = py_only

	def _should_skip(self, path: str, size: int) -> bool:
		# Если включён режим только .py — пропускаем всё остальное
		if self.py_only and not path.endswith(".py"):
			log.debug(f"  [skip !py]  {path}")
			return True
		if size > self.max_size:
			log.debug(f"  [skip size] {path}")
			return True
		if not self.py_only and self.skip_ext and path.endswith(self.skip_ext):
			log.debug(f"  [skip ext]  {path}")
			return True
		return False

	async def default_branch(self, s: aiohttp.ClientSession, owner: str, repo: str) -> str:
		url = f"{self.API}/repos/{owner}/{repo}"
		try:
			async with s.get(url, headers=self.headers) as r:
				if r.status == 401:
					raise RuntimeError("GitHub 401 Unauthorized")
				if r.status == 404:
					raise RuntimeError(f"GitHub 404 — repo {owner}/{repo} not found")
				if r.status != 200:
					body = await r.text()
					raise RuntimeError(f"GitHub {r.status} when fetching repo: {body[:200]}")
				data = await r.json()
				return data["default_branch"]
		except aiohttp.ClientError as e:
			raise RuntimeError(f"Network error when fetching branch {owner}/{repo}: {e}") from e

	async def _load_via_tarball(
		self,
		s: aiohttp.ClientSession,
		owner: str,
		repo: str,
		branch: str,
		vfs: VirtualFS,
	) -> tuple[int, int, int]:
		url = f"{self.API}/repos/{owner}/{repo}/tarball/{branch}"
		log.debug(f"[{owner}/{repo}] tarball → {url}")

		# Используем Spool: держим в RAM до threshold (1MB), затем временно на диске.
		spool_threshold = 1 << 20  # 1 MiB
		spooled = tempfile.SpooledTemporaryFile(max_size=spool_threshold)
		ok = skip = err = 0

		try:
			async with s.get(url, headers=self.headers, allow_redirects=True) as r:
				if r.status == 401:
					raise RuntimeError("GitHub 401 Unauthorized")
				if r.status == 404:
					raise RuntimeError(f"GitHub 404 — {owner}/{repo}@{branch} not found")
				if r.status != 200:
					body = await r.text()
					raise RuntimeError(f"GitHub {r.status} when downloading tarball: {body[:200]}")

				# stream to spooled file to avoid holding whole response in memory
				async for chunk in r.content.iter_chunked(1 << 16):  # 64 KiB
					spooled.write(chunk)
				# ensure position at start
				spooled.seek(0)

				# open tar in streaming-friendly mode; spooled is seekable so 'r:gz' is fine
				with tarfile.open(fileobj=spooled, mode="r:gz") as tf:
					for member in tf:
						try:
							if not member.isfile():
								continue
							slash = member.name.find("/")
							if slash == -1:
								continue
							path = member.name[slash + 1:]
							if not path:
								continue
							if self._should_skip(path, member.size):
								skip += 1
								continue
							fobj = tf.extractfile(member)
							if fobj is None:
								skip += 1
								continue
							content = fobj.read()
							vfs.write(path, content)
							# remove temporary references
							del content
							del fobj
							ok += 1
						except Exception as exc:
							log.error(f"  [error] {member.name}: {exc}")
							err += 1
		except aiohttp.ClientError as e:
			raise RuntimeError(f"Network error when loading tarball {owner}/{repo}: {e}") from e
		finally:
			try:
				spooled.close()
			except Exception:
				pass
			# free any temporaries
			gc.collect()

		return ok, skip, err

	async def load_all(
		self, owner: str, repo: str, branch: Optional[str]
	) -> tuple[str, VirtualFS]:
		vfs = VirtualFS()
		connector = aiohttp.TCPConnector(
			limit=self._concurrency,
			enable_cleanup_closed=True,
			keepalive_timeout=15,
		)
		async with aiohttp.ClientSession(connector=connector) as s:
			if not branch:
				try:
					branch = await self.default_branch(s, owner, repo)
					log.info(f"[{owner}/{repo}] branch: {branch}")
				except RuntimeError as e:
					log.error(f"[{owner}/{repo}] Failed to get default branch: {e}")
					return "unknown", vfs
			try:
				ok, skip, err = await self._load_via_tarball(s, owner, repo, branch, vfs)
			except RuntimeError as e:
				log.error(f"[{owner}/{repo}] Load error: {e}")
				return branch, vfs
		log.info(f"[{owner}/{repo}] loaded {ok} | skipped {skip} | err {err}")
		gc.collect()
		return branch, vfs


# ─────────────────────────────────────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────────────────────────────────────

class GitHubProjectRunner:
	_ENTRY_CANDIDATES = [
		"__main__.py", "main.py", "app.py", "run.py",
		"cli.py", "start.py", "server.py", "manage.py",
		"bot.py",
	]
	_ASYNC_ENTRY_NAMES = ["main", "run", "start", "app", "bot", "serve"]
	_global_lock = asyncio.Lock()

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
		serialize_runs: bool = True,
		auto_cleanup: bool = False,
		clear_cache: bool = False,
		py_only: bool = True,
	):
		owner, repo = self._parse_url(url)
		self.owner = owner
		self.repo = repo
		self.branch = branch
		self.load_dotenv = load_dotenv
		self.restart_on_crash = restart_on_crash
		self.restart_delay = restart_delay
		self.serialize_runs = serialize_runs
		# clear_cache включает полную очистку всех остатков после завершения
		self.auto_cleanup = auto_cleanup or clear_cache
		self.clear_cache = clear_cache
		self.py_only = py_only
		self._fetcher = _GitHubFetcher(token, concurrency, max_file_size, skip_extensions, py_only)
		self._vfs: Optional[VirtualFS] = None
		self._runner_id = f"{owner}_{repo}_{uuid.uuid4().hex[:8]}"

	@staticmethod
	def _parse_url(url: str) -> tuple[str, str]:
		m = re.search(r"github\.com[/:]([^/]+)/([^/.\s]+)", url)
		if not m:
			raise ValueError(f"Cannot parse GitHub URL: {url!r}")
		return m.group(1), m.group(2).removesuffix(".git")

	async def load(self) -> "GitHubProjectRunner":
		log.info(f"[{self._runner_id}] Loading {self.owner}/{self.repo}")
		try:
			self.branch, self._vfs = await self._fetcher.load_all(
				self.owner, self.repo, self.branch
			)
		except RuntimeError as e:
			log.error(f"[{self._runner_id}] Repo load error: {e}")
			raise
		log.info(f"[{self._runner_id}] VFS ready: {self._vfs}")

		# free fetcher ASAP
		try:
			self._fetcher = None
		finally:
			gc.collect()

		if self.load_dotenv and self._vfs is not None:
			_load_env_from_vfs(self._vfs, self._runner_id)

		return self

	def _find_entry(self) -> str:
		vfs = self._vfs
		if vfs is None:
			raise FileNotFoundError("VFS empty")
		for name in self._ENTRY_CANDIDATES:
			if vfs.is_file(name):
				return name
		for path in vfs.all_paths:
			parts = path.split("/")
			if len(parts) == 2 and parts[1] in self._ENTRY_CANDIDATES:
				return path
		raise FileNotFoundError(
			"Entry not found. Provide entry='path/to/main.py'. "
			f"Available: {vfs.listdir()}"
		)

	def _find_async_entry(self, module_dict: dict) -> tuple[Optional[str], Optional[object]]:
		for name in self._ASYNC_ENTRY_NAMES:
			fn = module_dict.get(name)
			if asyncio.iscoroutinefunction(fn):
				return name, fn
		return None, None

	def _build_roots(self, entry_dir: str) -> list[str]:
		roots: list[str] = [entry_dir]
		parts = entry_dir.split("/") if entry_dir else []
		for i in range(len(parts) - 1, 0, -1):
			roots.append("/".join(parts[:i]))
		roots.append("")
		return roots

	def _install_finder(self, entry_dir: str) -> VirtualFinder:
		roots = self._build_roots(entry_dir)
		finder = VirtualFinder(self._vfs, roots, self._runner_id)
		sys.meta_path.insert(0, finder)
		log.debug(f"[{self._runner_id}] Finder installed, roots: {finder._roots}")
		return finder

	def _uninstall_finder(self, finder: VirtualFinder) -> None:
		try:
			sys.meta_path.remove(finder)
		except ValueError:
			pass
		origin_prefix = f"<vfs:{self._runner_id}>"
		to_delete = [
			name for name, mod in list(sys.modules.items())
			if getattr(mod, "__spec__", None)
			and getattr(mod.__spec__, "origin", "").startswith(origin_prefix)
		]
		for name in to_delete:
			try:
				del sys.modules[name]
			except KeyError:
				pass
		if to_delete:
			log.debug(f"[{self._runner_id}] Removed {len(to_delete)} modules from sys.modules")
		gc.collect()

	def _vfs_top_packages(self) -> set[str]:
		tops: set[str] = set()
		if self._vfs is None:
			return tops
		for path in self._vfs.all_paths:
			top = path.split("/")[0]
			if top.endswith(".py"):
				top = top[:-3]
			if top:
				tops.add(top)
		return tops

	def _shadow_sys_modules(self) -> dict[str, types.ModuleType]:
		tops = self._vfs_top_packages()
		shadowed: dict[str, types.ModuleType] = {}
		for name in list(sys.modules.keys()):
			if name.split(".")[0] in tops:
				shadowed[name] = sys.modules.pop(name)
		if shadowed:
			log.debug(
				f"[{self._runner_id}] Hid {len(shadowed)} external modules from sys.modules: "
				f"{sorted(shadowed)[:10]}{'...' if len(shadowed) > 10 else ''}"
			)
		return shadowed

	def _restore_sys_modules(self, shadowed: dict[str, types.ModuleType]) -> None:
		self._uninstall_finder_modules_only()
		sys.modules.update(shadowed)

	def _uninstall_finder_modules_only(self) -> None:
		origin_prefix = f"<vfs:{self._runner_id}>"
		to_delete = [
			name for name, mod in list(sys.modules.items())
			if getattr(mod, "__spec__", None)
			and getattr(mod.__spec__, "origin", "").startswith(origin_prefix)
		]
		for name in to_delete:
			try:
				del sys.modules[name]
			except KeyError:
				pass
		if to_delete:
			log.debug(f"[{self._runner_id}] Removed {len(to_delete)} own modules from sys.modules")

	def _cleanup(self) -> None:
		if hasattr(self, "_vfs") and self._vfs is not None:
			self._vfs = None
		self._fetcher = None
		gc.collect()
		log.debug(f"[{self._runner_id}] Resources cleaned up")

	def _deep_cleanup(self) -> None:
		"""Полная очистка всех остатков проекта (вызывается при clear_cache=True)."""
		# Удаляем все модули, связанные с этим runner_id
		origin_prefix = f"<vfs:{self._runner_id}>"
		to_delete = [
			name for name, mod in list(sys.modules.items())
			if getattr(mod, "__spec__", None)
			and getattr(mod.__spec__, "origin", "").startswith(origin_prefix)
		]
		for name in to_delete:
			try:
				del sys.modules[name]
			except KeyError:
				pass
		if to_delete:
			log.debug(f"[{self._runner_id}] clear_cache: removed {len(to_delete)} modules from sys.modules")

		# Удаляем finder из meta_path если вдруг остался
		sys.meta_path[:] = [
			f for f in sys.meta_path
			if not (isinstance(f, VirtualFinder) and f.runner_id == self._runner_id)
		]

		# Очищаем VFS и fetcher
		if hasattr(self, "_vfs") and self._vfs is not None:
			self._vfs._files.clear()
			self._vfs = None
		self._fetcher = None

		gc.collect()
		log.info(f"[{self._runner_id}] clear_cache: все остатки проекта удалены")

	# Публичный метод очистки (может быть вызван извне)
	def cleanup(self) -> None:
		"""Public alias to force resource cleanup. При clear_cache=True выполняет полную очистку."""
		if getattr(self, "clear_cache", False):
			self._deep_cleanup()
		else:
			self._cleanup()

	async def _run_once(self, entry: str, args: Optional[list[str]]) -> None:
		cc = self.clear_cache  # локальная копия флага для быстрого доступа

		orig_argv = sys.argv.copy()
		orig_sys_path = sys.path.copy()
		sys.argv = [f"<vfs>/{entry}", *(args or [])]

		# ── 1. entry_dir нужен только для настройки finder/module ──────────────
		entry_dir = str(PurePosixPath(entry).parent) if "/" in entry else ""
		shadowed = self._shadow_sys_modules()
		finder = self._install_finder(entry_dir)

		sys.path = _SysPathProxy(orig_sys_path.copy(), finder, self._runner_id)

		try:
			# ── 2. Читаем исходник ──────────────────────────────────────────────
			source = self._vfs.read_text(entry) if self._vfs else None
			if source is None:
				raise FileNotFoundError(f"File not found in VFS: {entry!r}")

			compile_path = f"<vfs:{self._runner_id}>/{entry}"
			try:
				code = compile(source, compile_path, "exec")
			except SyntaxError as e:
				log.error(f"[{self._runner_id}] SyntaxError in {entry}: {e}")
				raise
			finally:
				# source больше не нужен — освобождаем сразу после компиляции
				del source
				del compile_path
				gc.collect()

			# ── 3. Собираем модуль ──────────────────────────────────────────────
			module = types.ModuleType("__vfs_main__")
			module.__file__ = f"<vfs:{self._runner_id}>/{entry}"
			module.__loader__ = None
			module.__package__ = entry_dir.replace("/", ".") if entry_dir else ""
			module.__spec__ = None
			if entry_dir:
				module.__path__ = [f"<vfs:{self._runner_id}>/{entry_dir}"]

			# entry_dir больше не нужен после настройки module
				del entry_dir
				gc.collect()

			log.info(f"[{self._runner_id}] ▶ {self.owner}/{self.repo}/{entry}")
			log.info("─" * 60)

			# ── 4. exec — запуск кода модуля ────────────────────────────────────
			try:
				exec(code, module.__dict__)
			except Exception as e:
				log.error(
					f"[{self._runner_id}] Error initializing module {entry}: {type(e).__name__}: {e}"
				)
				raise
			finally:
				# code больше не нужен после exec
				del code
				gc.collect()

			# ── 5. Ищем async-точку входа ────────────────────────────────────────
			fn_name, fn = self._find_async_entry(module.__dict__)

			if fn is not None:
				log.info(f"[{self._runner_id}] Calling async {fn_name}()")

				# Снимок задач ДО запуска — чтобы после завершения отменить те,
				# что создал сам проект (aiogram polling, handlers и т.д.)
				tasks_before: set[asyncio.Task] = asyncio.all_tasks()

				try:
					await fn()
				except Exception as e:
					log.error(
						f"[{self._runner_id}] Error in {fn_name}(): {type(e).__name__}: {e}"
					)
					raise
				finally:
					# Отменяем все задачи, которые проект оставил висеть в event loop
					current = asyncio.current_task()
					orphaned = asyncio.all_tasks() - tasks_before - {current}
					if orphaned:
						log.debug(
							f"[{self._runner_id}] Cancelling {len(orphaned)} orphaned task(s)..."
						)
						for t in orphaned:
							t.cancel()
						await asyncio.gather(*orphaned, return_exceptions=True)
						log.debug(f"[{self._runner_id}] Orphaned tasks cancelled")
					del tasks_before, orphaned, current

					# fn завершилась — теперь безопасно чистить module и все его globals
					module.__dict__.clear()
					del module, fn, fn_name
					gc.collect()
			else:
				log.warning(
					f"[{self._runner_id}] Async entry not found (checked: {self._ASYNC_ENTRY_NAMES}). "
					"Provide explicit entry or add async def main()/run()."
				)
				module.__dict__.clear()
				del module, fn, fn_name
				gc.collect()
		finally:
			# ── 6. Восстанавливаем окружение ─────────────────────────────────────
			try:
				self._uninstall_finder(finder)
			finally:
				self._restore_sys_modules(shadowed)
				sys.argv = orig_argv
				sys.path = orig_sys_path
				# shadowed / orig_* больше не нужны
				del shadowed, orig_argv, orig_sys_path, finder
				gc.collect()

	async def run(
		self,
		entry: Optional[str] = None,
		args: Optional[list[str]] = None,
	) -> None:
		if self._vfs is None:
			raise RuntimeError("Call await runner.load() first")
		if len(self._vfs) == 0:
			log.error(f"[{self._runner_id}] VFS is empty — repo likely didn't load (rate limit/network). Skipping run.")
			return
		if entry is None:
			try:
				entry = self._find_entry()
			except FileNotFoundError as e:
				log.error(f"[{self._runner_id}] {e}")
				return
			log.info(f"[{self._runner_id}] Entry: {entry}")

		# helper to perform single run inside optional lock
		async def _do_run():
			await self._run_once(entry, args)

		if self.restart_on_crash:
			while True:
				try:
					if self.serialize_runs:
						async with self._global_lock:
							await _do_run()
					else:
						await _do_run()
					log.info(f"[{self._runner_id}] Finished without errors")
					break
				except (KeyboardInterrupt, asyncio.CancelledError):
					log.info(f"[{self._runner_id}] Stopped")
					break
				except Exception as exc:
					log.info("─" * 60)
					log.error(f"[{self._runner_id}] Crashed: {exc!r}. Restarting in {self.restart_delay}s...")
					await asyncio.sleep(self.restart_delay)
			if self.clear_cache:
				self._deep_cleanup()
			elif self.auto_cleanup:
				self._cleanup()
		else:
			if self.serialize_runs:
				async with self._global_lock:
					await _do_run()
			else:
				await _do_run()
			if self.clear_cache:
				self._deep_cleanup()
			elif self.auto_cleanup:
				self._cleanup()

	# utilities
	@property
	def vfs(self) -> VirtualFS:
		if self._vfs is None:
			raise RuntimeError("Repository not loaded yet")
		return self._vfs

	def ls(self, path: str = "") -> list[str]:
		return self.vfs.listdir(path)

	def cat(self, path: str) -> str:
		content = self.vfs.read_text(path)
		if content is None:
			raise FileNotFoundError(f"File not found: {path!r}")
		return content

	def __repr__(self) -> str:
		status = repr(self._vfs) if self._vfs else "not loaded"
		return f"GitHubProjectRunner({self.owner}/{self.repo}@{self.branch}, {status})"

async def _git_run(github):
	await github.load()
	await github.run()

async def free_run(cntnts: list[str], clear_cache: bool = False) -> None:
	tasks: list[_git_run] = []

	# Загружаем все репозитории
	for rp in cntnts:
		log.info("─" * 60)
		github = GitHubProjectRunner(
			f"https://github.com/{rp}",
			token=os.getenv("GITHUB_TOKEN"),
			restart_on_crash=True,
			restart_delay=15.0,
			serialize_runs=False,
			py_only=True,
			clear_cache=clear_cache
		)
		tasks.append(_git_run(github))

	await asyncio.gather(*tasks, return_exceptions=True)
	log.info("Все проекты завершены.")