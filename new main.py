from kivy.app import App
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.button import Button
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.animation import Animation
from kivy.graphics import Color, Rectangle, RoundedRectangle, Ellipse
from kivy.utils import get_color_from_hex
from kivy.core.window import Window
from kivy.clock import Clock
import sys, os, re, threading, asyncio, logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'custom'))
from progress_bar import GlowProgressBar
from slider import GlowSlider
from toggle import GlowToggle


# ══════════════════════════════════════════════════════════════
# ПЕРЕХВАТ ЛОГОВ GhostRun
# ══════════════════════════════════════════════════════════════
class _GhostLogHandler(logging.Handler):
	COLORS = {
		logging.DEBUG:    '#7B6FA0',
		logging.INFO:     '#E8E0FF',
		logging.WARNING:  '#FFB300',
		logging.ERROR:    '#FF5252',
		logging.CRITICAL: '#FF5252',
	}
	# Символы box-drawing — не рендерятся на Android, убираем
	_STRIP = str.maketrans('', '', '─━│┃┄┅┆┇┈┉┊┋┌┍┎┏┐┑┒┓└┕┖┗┘┙┚┛├┝┞┟┠┡┢┣┤┥┦┧┨┩┪┫┬┭┮┯┰┱┲┳┴┵┶┷┸┹┺┻┼┽┾┿╀╁╂╃╄╅╆╇╈╉╊╋')

	def emit(self, record):
		try:
			msg = self.format(record).translate(self._STRIP).strip()
			if not msg:
				return
			color = self.COLORS.get(record.levelno, '#E8E0FF')
			Clock.schedule_once(
				lambda dt, m=msg, c=color: GhostState.log_lines.append((m, c))
			)
		except Exception:
			pass

_ghost_handler = _GhostLogHandler()
_ghost_handler.setFormatter(logging.Formatter('[%(name)s] %(message)s'))
for _ln in ('__main__', 'GhostRun'):
	_l = logging.getLogger(_ln)
	_l.addHandler(_ghost_handler)
	_l.setLevel(logging.DEBUG)


# ══════════════════════════════════════════════════════════════
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# ══════════════════════════════════════════════════════════════
class GhostState:
	repos            = []
	restart_on_crash = True
	restart_delay    = 15.0
	serialize_runs   = False
	auto_cleanup     = False
	clear_cache      = False
	py_only          = True
	load_dotenv      = True
	max_file_size    = 5
	concurrency      = 10
	github_token     = 'github_pat_11BG3ZQRQ0...'
	running          = False
	uptime           = 0
	restarts         = 0
	vfs_files        = 0
	vfs_ram_kb       = 0
	branch           = '—'
	log_lines        = []
	_process_clock   = None
	_run_thread      = None


# ══════════════════════════════════════════════════════════════
# ЦВЕТА
# ══════════════════════════════════════════════════════════════
class C:
	BG          = get_color_from_hex('#0E0B1A')
	SURFACE     = get_color_from_hex('#1A1530')
	PANEL       = get_color_from_hex('#231D3F66')
	PANEL_BTN = get_color_from_hex('#231D3F00')
	ACCENT      = get_color_from_hex('#7C4DFF')
	ACCENT_SOFT = get_color_from_hex('#4A2F8A')
	TEXT        = get_color_from_hex('#E8E0FF')
	TEXT_DIM    = get_color_from_hex('#7B6FA0')
	BORDER      = get_color_from_hex('#3D2F6B')
	SUCCESS     = get_color_from_hex('#69F0AE')
	DANGER      = get_color_from_hex('#FF5252')
	WARNING     = get_color_from_hex('#FFB300')
	INFO        = get_color_from_hex('#40C4FF')


# ══════════════════════════════════════════════════════════════
# БАЗОВЫЕ ВИДЖЕТЫ
# ══════════════════════════════════════════════════════════════

class Card(BoxLayout):
	def __init__(self, radius=18, color=None, **kwargs):
		super().__init__(**kwargs)
		with self.canvas.before:
			Color(*(color or C.SURFACE))
			self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])
		self.bind(
			pos=lambda *a: setattr(self._bg, 'pos', self.pos),
			size=lambda *a: setattr(self._bg, 'size', self.size),
		)


class RoundButton(Button):
	def __init__(self, radius=18, btn_color=None, btn_color_pressed=None, **kwargs):
		super().__init__(**kwargs)
		self.background_normal = ''
		self.background_down   = ''
		self.background_color  = (0, 0, 0, 0)
		self._cn = btn_color         or [*C.ACCENT]
		self._cp = btn_color_pressed or [*C.ACCENT_SOFT]
		with self.canvas.before:
			self._fc = Color(*self._cn)
			self._rc = RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])
		self.bind(
			pos=lambda *a: setattr(self._rc, 'pos', self.pos),
			size=lambda *a: setattr(self._rc, 'size', self.size),
		)

	def on_press(self):
		self._fc.rgba = self._cp

	def on_release(self):
		self._fc.rgba = self._cn


class StatusDot(Widget):
	"""Пульсирующая точка статуса.
	   Используем Clock вместо Animation — надёжнее с обычными float-полями."""

	def __init__(self, **kwargs):
		super().__init__(**kwargs)
		self._running    = False
		self._pulse_clock = None
		self._alpha       = 1.0      # float — обновляем вручную через Clock
		self._pulse_dir   = -1       # -1 = гаснет, +1 = светлеет
		self.size_hint = (None, None)
		self.size      = (40, 40)
		self.bind(pos=self._draw, size=self._draw)

	def _draw(self, *a):
		self.canvas.clear()
		r = min(self.width, self.height) / 2
		with self.canvas:
			if self._running:
				Color(*C.SUCCESS[:3], self._alpha * 0.25)
				Ellipse(pos=self.pos, size=(r * 2, r * 2))
				Color(*C.SUCCESS[:3], self._alpha)
			else:
				Color(*C.TEXT_DIM)
			ir = r * 0.52
			Ellipse(pos=(self.x + r - ir, self.y + r - ir), size=(ir * 2, ir * 2))

	def _pulse_tick(self, dt):
		"""Вызывается каждые 16 мс — плавное мигание через синус-шаг."""
		self._alpha += self._pulse_dir * dt * 1.1
		if self._alpha <= 0.15:
			self._alpha    = 0.15
			self._pulse_dir = 1
		elif self._alpha >= 1.0:
			self._alpha    = 1.0
			self._pulse_dir = -1
		self._draw()

	def set_running(self, val):
		self._running = val
		# Останавливаем предыдущий Clock
		if self._pulse_clock:
			self._pulse_clock.cancel()
			self._pulse_clock = None
		if val:
			self._alpha    = 1.0
			self._pulse_dir = -1
			self._pulse_clock = Clock.schedule_interval(self._pulse_tick, 1 / 60)
		else:
			self._alpha = 1.0
			self._draw()


def lbl(text, size=24, color=None, bold=False, halign='left',
        height=None, width=None, markup=False, size_hint_y=None, padding=[5, 5]):
	kw = dict(
		text=text, font_size=size, markup=markup,
		color=color or C.TEXT,
		halign=halign, valign='middle',
	)
	if bold:
		kw['bold'] = True
	if height is not None:
		kw['height'] = height
		kw['size_hint_y'] = None
	else:
		kw['size_hint_y'] = size_hint_y
	if width is not None:
		kw['width'] = width
		kw['size_hint_x'] = None
	w = Label(**kw, padding=padding)
	w.bind(size=lambda s, v: setattr(s, 'text_size', v))
	return w


def section(text):
	return lbl(f'[b]{text}[/b]', size=30, color=C.TEXT_DIM,
	           markup=True, height=56)


def stat_row(left_text, right_widget, size=26, padding=[0, 0]):
	"""Строка: текст слева, виджет справа."""
	row = BoxLayout(size_hint_y=None, height=60, padding=padding)
	row.add_widget(lbl(left_text, size, C.TEXT_DIM, height=60))
	row.add_widget(right_widget)
	return row


def _update_vfs_state(n, ram, branch):
	GhostState.vfs_files  = n
	GhostState.vfs_ram_kb = ram // 1024
	GhostState.branch     = branch


# ══════════════════════════════════════════════════════════════
# СТРАНИЦА 1 — ЗАПУСК
# ══════════════════════════════════════════════════════════════
class RunPage(BoxLayout):
	def __init__(self, **kwargs):
		super().__init__(orientation='vertical', padding=[28, 32, 28, 20], spacing=18, **kwargs)
		self._bar_dir = 1
		self._build()
		self._sync()
		# Если процесс уже идёт пока мы были на другой вкладке — подхватываем тик
		if GhostState.running and GhostState._process_clock is None:
			GhostState._process_clock = Clock.schedule_interval(self._global_tick, 1)

	def _build(self):
		scroll = ScrollView()
		inn = BoxLayout(orientation='vertical', spacing=18, size_hint_y=None)
		inn.bind(minimum_height=inn.setter('height'))

		# ── Заголовок ────────────────────────────────────────────
		hdr = BoxLayout(size_hint_y=None, height=90, spacing=14)
		self.dot = StatusDot()
		self.dot.pos_hint = {'center_y': 0.5}
		self.lbl_status = lbl('Остановлен', 32, C.TEXT_DIM,
		                      halign='right', height=90, width=300)
		hdr.add_widget(self.dot)
		hdr.add_widget(lbl('[b]Состояние процесса[/b]', 40, markup=True, height=90))
		hdr.add_widget(self.lbl_status)
		inn.add_widget(hdr)

		# ── Мониторинг ───────────────────────────────────────────
		inn.add_widget(section('Мониторинг'))
		stat = Card(orientation='vertical', padding=[22, 18], spacing=10,
		            size_hint_y=None, height=280)

		self.lbl_uptime = lbl('00:00:00', 36, C.TEXT, bold=True, height=60, width=300)
		stat.add_widget(stat_row('Время работы', self.lbl_uptime, size=32))

		self.lbl_branch = lbl(GhostState.branch, 36, C.INFO, height=60, width=300)
		stat.add_widget(stat_row('Ветка', self.lbl_branch, size=32, padding=[0, 15]))

		self.lbl_restarts = lbl('0', 36, C.WARNING, bold=True, height=60, width=300)
		stat.add_widget(stat_row('Перезапусков', self.lbl_restarts, size=32, padding=[0, 30]))

		self.bar = GlowProgressBar(
			show_label=False, animated=False,
			size_hint_y=None, height=32,
			bar_color=get_color_from_hex('#7C4DFF'),
			bg_color=get_color_from_hex('#0E0B1A'),
		)
		self.bar.value = 0
		stat.add_widget(self.bar)
		inn.add_widget(stat)

		# ── VirtualFS ────────────────────────────────────────────
		inn.add_widget(section('VirtualFS'))
		vfs = Card(orientation='vertical', padding=[22, 18], spacing=0,
		           size_hint_y=None, height=150)

		self.lbl_vfs_files = lbl('0', 36, C.ACCENT, bold=True, height=60, width=220)
		vfs.add_widget(stat_row('Файлов в памяти', self.lbl_vfs_files, size=30, padding=[0, 0]))

		self.lbl_vfs_ram = lbl('0 KB', 36, C.INFO, bold=True, height=60, width=220)
		vfs.add_widget(stat_row('RAM (zlib)', self.lbl_vfs_ram, size=30, padding=[0, 0]))
		inn.add_widget(vfs)

		# ── Главные кнопки ────────────────────────────────────────
		inn.add_widget(section('Управление'))
		main_btns = Card(orientation='horizontal', padding=[18, 16], spacing=16,
		                 size_hint_y=None, height=130)

		self.btn_start = RoundButton(
			text='[b]Запустить[/b]', markup=True, font_size=34,
			btn_color=get_color_from_hex('#1B5E20'),
			btn_color_pressed=get_color_from_hex('#0A3D12'),
		)
		self.btn_start.bind(on_press=self._start)

		self.btn_stop = RoundButton(
			text='[b]Остановить[/b]', markup=True, font_size=34,
			btn_color=get_color_from_hex('#B71C1C'),
			btn_color_pressed=get_color_from_hex('#7F0000'),
		)
		self.btn_stop.bind(on_press=self._stop)

		main_btns.add_widget(self.btn_start)
		main_btns.add_widget(self.btn_stop)
		inn.add_widget(main_btns)

		# ── Доп. кнопки ──────────────────────────────────────────
		extra_btns = Card(orientation='horizontal', padding=[18, 14], spacing=14,
		                  size_hint_y=None, height=110)

		btn_restart = RoundButton(
			text='[b]Перезапуск[/b]', markup=True, font_size=34,
			btn_color=get_color_from_hex('#E65100'),
			btn_color_pressed=get_color_from_hex('#BF360C'),
		)
		btn_restart.bind(on_press=self._restart)

		btn_clear = RoundButton(
			text='[b]Сбросить статус[/b]', markup=True, font_size=34,
			btn_color=[*C.ACCENT_SOFT],
			btn_color_pressed=[*C.ACCENT],
		)
		btn_clear.bind(on_press=self._clear)

		extra_btns.add_widget(btn_restart)
		extra_btns.add_widget(btn_clear)
		inn.add_widget(extra_btns)

		inn.add_widget(Widget(size_hint_y=None, height=24))
		scroll.add_widget(inn)
		self.add_widget(scroll)

	def _sync(self):
		r = GhostState.running
		self.dot.set_running(r)
		self.lbl_status.text  = 'Работает'  if r else 'Остановлен'
		self.lbl_status.color = C.SUCCESS   if r else C.TEXT_DIM
		self.btn_start.disabled = r
		self.btn_start.opacity  = 0.3 if r else 1.0
		self.btn_stop.disabled  = not r
		self.btn_stop.opacity   = 1.0 if r else 0.3
		h, m, s = GhostState.uptime // 3600, (GhostState.uptime % 3600) // 60, GhostState.uptime % 60
		self.lbl_uptime.text    = f'{h:02}:{m:02}:{s:02}'
		self.lbl_restarts.text  = str(GhostState.restarts)
		self.lbl_branch.text    = GhostState.branch
		self.lbl_vfs_files.text = str(GhostState.vfs_files)
		self.lbl_vfs_ram.text   = f'{GhostState.vfs_ram_kb} KB'
		# Прогресс-бар пинпонг пока работает
		if r:
			v = self.bar.value + self._bar_dir * 3
			if v >= 100: v = 100; self._bar_dir = -1
			elif v <= 0: v = 0;   self._bar_dir =  1
			self.bar.value = v

	# ── Запуск реального GhostRun ─────────────────────────────
	def _run_ghostrun_thread(self):
		"""Запускается в потоке. Поднимает asyncio + GhostRun."""
		try:
			sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
			from GhostRun import free_run, GitHubProjectRunner

			token = GhostState.github_token.strip() or None

			# Патчим __init__ чтобы передать настройки из GhostState
			_orig = GitHubProjectRunner.__init__
			def _patched(self_r, url, **kw):
				kw.setdefault('token',            token)
				kw.setdefault('restart_on_crash', GhostState.restart_on_crash)
				kw.setdefault('restart_delay',    GhostState.restart_delay)
				kw.setdefault('serialize_runs',   GhostState.serialize_runs)
				kw.setdefault('auto_cleanup',     GhostState.auto_cleanup)
				kw.setdefault('clear_cache',      GhostState.clear_cache)
				kw.setdefault('py_only',          GhostState.py_only)
				kw.setdefault('load_dotenv',      GhostState.load_dotenv)
				kw.setdefault('max_file_size',    GhostState.max_file_size * 1024 * 1024)
				kw.setdefault('concurrency',      GhostState.concurrency)
				_orig(self_r, url, **kw)
			GitHubProjectRunner.__init__ = _patched

			# Патчим load() чтобы перехватить VFS stats и branch
			_orig_load = GitHubProjectRunner.load
			async def _patched_load(self_r):
				result = await _orig_load(self_r)
				if self_r._vfs:
					n   = len(self_r._vfs)
					ram = self_r._vfs.ram_usage()
					br  = self_r.branch or 'main'
					Clock.schedule_once(lambda dt, _n=n, _r=ram, _b=br:
						_update_vfs_state(_n, _r, _b))
				return result
			GitHubProjectRunner.load = _patched_load

			asyncio.run(free_run(list(GhostState.repos), clear_cache=GhostState.clear_cache))
			Clock.schedule_once(lambda dt: GhostState.log_lines.append(
				('Все проекты завершены', '#69F0AE')))
		except Exception as e:
			Clock.schedule_once(lambda dt, err=str(e):
				GhostState.log_lines.append((f'Ошибка: {err}', '#FF5252')))
		finally:
			Clock.schedule_once(lambda dt: self._on_finished())

	def _on_finished(self):
		GhostState.running = False
		if GhostState._process_clock:
			GhostState._process_clock.cancel()
			GhostState._process_clock = None
		self._sync()

	def _start(self, *a):
		if GhostState.running: return
		if not GhostState.repos:
			GhostState.log_lines.append(('Нет репозиториев', '#FFB300'))
			return
		GhostState.running    = True
		GhostState.branch     = 'загрузка...'
		GhostState.vfs_files  = 0
		GhostState.vfs_ram_kb = 0
		GhostState.log_lines.append(('Запуск GhostRun...', '#69F0AE'))
		for rp in GhostState.repos:
			GhostState.log_lines.append((f'  Репо: {rp}', '#7B6FA0'))

		# Глобальный тик аптайма
		GhostState._process_clock = Clock.schedule_interval(self._global_tick, 1)
		# Запуск в потоке
		t = threading.Thread(target=self._run_ghostrun_thread, daemon=True)
		GhostState._run_thread = t
		t.start()
		self._sync()

	def _stop(self, *a):
		if not GhostState.running: return
		GhostState.running = False
		GhostState.log_lines.append(('Остановлен', '#FF5252'))
		if GhostState._process_clock:
			GhostState._process_clock.cancel()
			GhostState._process_clock = None
		self._sync()

	def _restart(self, *a):
		self._stop()
		GhostState.restarts += 1
		GhostState.log_lines.append((f'Перезапуск #{GhostState.restarts}', '#FFB300'))
		Clock.schedule_once(lambda dt: self._start(), 0.5)

	def _clear(self, *a):
		self._stop()
		GhostState.uptime = GhostState.restarts = GhostState.vfs_files = GhostState.vfs_ram_kb = 0
		GhostState.branch = '—'
		GhostState.log_lines.append(('Сброс статуса', '#7B6FA0'))
		self._sync()

	def _global_tick(self, dt):
		"""Тикает каждую секунду пока running. Переживает смену вкладок."""
		if not GhostState.running: return
		GhostState.uptime += 1
		self._sync()


# ══════════════════════════════════════════════════════════════
# СТРАНИЦА 2 — ПРОЕКТЫ
# ══════════════════════════════════════════════════════════════
class ProjectsPage(BoxLayout):
	def __init__(self, **kwargs):
		super().__init__(orientation='vertical', padding=[28, 32, 28, 20], spacing=18, **kwargs)
		self._build()

	def _build(self):
		self.add_widget(lbl('[b]Проекты[/b]', 40, markup=True, height=72))

		scroll = ScrollView()
		self._inn = BoxLayout(orientation='vertical', spacing=16,
		                      padding=[0, 14], size_hint_y=None)
		self._inn.bind(minimum_height=self._inn.setter('height'))
		scroll.add_widget(self._inn)
		self.add_widget(scroll)
		self._refresh()

		# Поле добавления
		add_card = Card(orientation='horizontal', padding=[14, 12], spacing=12,
		                size_hint_y=None, height=100)
		self._inp = TextInput(
			hint_text='user/repo  или  github.com/user/repo',
			multiline=False, font_size=36,
			background_color=get_color_from_hex('#0E0B1A'),
			foreground_color=C.TEXT,
			cursor_color=C.ACCENT,
			padding_y=[20],
			padding_x=[15],
		)
		btn_add = RoundButton(
			text='[b]Добавить[/b]', markup=True, font_size=36,
			size_hint_x=None, width=280,
			btn_color=[*C.ACCENT],
			btn_color_pressed=[*C.ACCENT_SOFT],
		)
		btn_add.bind(on_press=self._add)
		add_card.add_widget(self._inp)
		add_card.add_widget(btn_add)
		self.add_widget(add_card)

	def _make_card(self, rp, i):
		"""Создаёт карточку репозитория."""
		card = Card(orientation='horizontal', padding=[20, 0], spacing=14,
		            size_hint_y=None, height=110)

		num = lbl(f'[b]{i + 1}[/b]', 32, C.ACCENT, markup=True,
		          halign='center', height=110, width=54)

		info = BoxLayout(orientation='vertical', spacing=0, padding=[0, 10])
		parts = rp.split('/')
		info.add_widget(lbl(parts[-1] if len(parts) > 1 else rp,
		                    32, C.TEXT, bold=True, height=50))
		info.add_widget(lbl(rp, 26, C.TEXT_DIM, height=40))

		btn_del = RoundButton(
			text='[b]Удалить[/b]', markup=True, font_size=32,
			size_hint=(None, None), size=(190, 82),
			btn_color=get_color_from_hex('#B71C1C'),
			btn_color_pressed=get_color_from_hex('#7F0000'),
		)
		btn_del.bind(on_press=lambda btn, r=rp, c=card: self._remove_animated(r, c))

		btn_wrap = BoxLayout(orientation='vertical', size_hint_x=None, width=190)
		btn_wrap.add_widget(Widget())
		btn_wrap.add_widget(btn_del)
		btn_wrap.add_widget(Widget())

		card.add_widget(num)
		card.add_widget(info)
		card.add_widget(btn_wrap)
		return card

	def _refresh(self, skip_repos=None):
		"""Перестраивает список. skip_repos — set репо, карточки которых не трогаем."""
		self._inn.clear_widgets()
		self._inn.add_widget(section(f'Репозитории  ({len(GhostState.repos)})'))

		for i, rp in enumerate(GhostState.repos):
			card = self._make_card(rp, i)
			self._inn.add_widget(card)

		# Сводка настроек
		self._inn.add_widget(section('Текущие параметры'))
		info_card = Card(orientation='vertical', padding=[22, 22], spacing=12,
		                 size_hint_y=None, height=220)
		for k, v in [
			('Режим запуска',       'Последовательный' if GhostState.serialize_runs else 'Параллельный'),
			('Перезапуск при сбое', 'Включён'          if GhostState.restart_on_crash else 'Выключен'),
			('Задержка',            f'{GhostState.restart_delay:.0f} сек'),
			('Только .py файлы',    'Да'               if GhostState.py_only else 'Нет'),
		]:
			row = BoxLayout(size_hint_y=None, height=30, padding=[5])
			row.add_widget(lbl(k, 32, C.TEXT_DIM, height=50))
			row.add_widget(lbl(v, 32, C.TEXT, bold=True, height=30, width=300))
			info_card.add_widget(row)
		self._inn.add_widget(info_card)
		self._inn.add_widget(Widget(size_hint_y=None, height=24))

	def _add(self, *a):
		raw = self._inp.text.strip()
		if not raw: return
		m = re.search(r'github\.com[/:]([^/\s]+)/([^/\s.]+)', raw)
		repo = f'{m.group(1)}/{m.group(2)}' if m else (raw.strip('/') if '/' in raw else None)
		if not repo or repo in GhostState.repos:
			self._inp.text = ''
			return

		GhostState.repos.append(repo)
		GhostState.log_lines.append((f'Добавлен репозиторий {repo}', '#69F0AE'))
		self._inp.text = ''

		# Строим карточку с нуля без полного refresh
		i = len(GhostState.repos) - 1
		card = self._make_card(repo, i)

		# Вставляем перед блоком "Текущие параметры"
		# В _inn: [section_repos, card0, card1, ..., section_params, info_card, spacer]
		# children в BoxLayout хранятся в обратном порядке (last added = children[0])
		# Вставляем на позицию 3 с конца (перед section_params, info_card, spacer)
		insert_idx = 3
		self._inn.add_widget(card, index=insert_idx)

		# Обновляем заголовок (section — первый, children[-1] в обратном порядке)
		# Проще пересобрать только section-заголовок
		# Находим его через clear и re-add только заголовка
		# На самом деле проще сменить текст на первом Label в children[-1]
		# children[-1] = section (добавлен первым)
		head = self._inn.children[-1]
		if hasattr(head, 'text'):
			head.text = f'[b]Репозитории  ({len(GhostState.repos)})[/b]'

		# Анимация: появление сверху-вниз + fade in
		card.opacity = 0
		card.height  = 0
		anim = (
			Animation(height=110, duration=0.3, t='out_cubic') &
			Animation(opacity=1,  duration=0.3, t='out_cubic')
		)
		anim.start(card)

	def _remove_animated(self, rp, card):
		"""Анимирует исчезновение карточки, потом удаляет из списка и обновляет UI."""
		def _finish(*a):
			if rp in GhostState.repos:
				GhostState.repos.remove(rp)
				GhostState.log_lines.append((f'Удалён репозиторий {rp}', '#FF5252'))
			self._refresh()

		# fade out + collapse height
		anim = (
			Animation(opacity=0, duration=0.2, t='in_cubic') &
			Animation(height=0,  duration=0.25, t='in_cubic')
		)
		anim.bind(on_complete=_finish)
		anim.start(card)


# ══════════════════════════════════════════════════════════════
# СТРАНИЦА 3 — ЛОГИ
# ══════════════════════════════════════════════════════════════
class LogsPage(BoxLayout):
	def __init__(self, **kwargs):
		super().__init__(orientation='vertical', padding=[28, 32, 28, 20], spacing=18, **kwargs)
		self._build()

	def _build(self):
		hdr = BoxLayout(size_hint_y=None, height=80, spacing=14)
		hdr.add_widget(lbl('[b]Логи[/b]', 40, markup=True, height=80))

		btn_upd = RoundButton(
			text='[b]Обновить[/b]', markup=True, font_size=32,
			size_hint=(None, None), size=(240, 80),
			btn_color=get_color_from_hex('#1A1530'),
			btn_color_pressed=[*C.ACCENT_SOFT],
		)
		btn_upd.bind(on_press=lambda *a: self._refresh())

		btn_clr = RoundButton(
			text='[b]Очистить[/b]', markup=True, font_size=32,
			size_hint=(None, None), size=(240, 80),
			btn_color=[*C.ACCENT_SOFT],
			btn_color_pressed=[*C.ACCENT],
		)
		btn_clr.bind(on_press=self._clear)

		hdr.add_widget(btn_upd)
		hdr.add_widget(btn_clr)
		self.add_widget(hdr)

		self.lbl_count = lbl(f'Записей: {len(GhostState.log_lines)}',
		                     32, C.TEXT_DIM, height=40)
		self.add_widget(self.lbl_count)

		log_card = Card(orientation='vertical', padding=[16, 12], spacing=0)
		scroll = ScrollView()
		self._box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=2)
		self._box.bind(minimum_height=self._box.setter('height'))
		scroll.add_widget(self._box)
		log_card.add_widget(scroll)
		self.add_widget(log_card)

		self._refresh()

	def _refresh(self):
		self._box.clear_widgets()
		self.lbl_count.text = f'Записей: {len(GhostState.log_lines)}'

		if not GhostState.log_lines:
			self._box.add_widget(lbl('Нет записей', 34, C.TEXT_DIM, height=54))
			return

		for i, (text, color) in enumerate(reversed(GhostState.log_lines)):
			row = BoxLayout(size_hint_y=None, height=54)
			with row.canvas.before:
				Color(*C.SURFACE[:3], 0.55 if i % 2 == 0 else 0.25)
				self._rbg = Rectangle(pos=row.pos, size=row.size)
			row.bind(
				pos=lambda w, p: setattr(w.canvas.before.children[-1], 'pos', p),
				size=lambda w, s: setattr(w.canvas.before.children[-1], 'size', s),
			)
			idx = lbl(str(len(GhostState.log_lines) - i), 28,
			          C.TEXT_DIM, halign='right', height=54, width=64)
			msg = lbl(text, 32,
			          get_color_from_hex(color) if color.startswith('#') else C.TEXT,
			          height=54)
			row.add_widget(idx)
			row.add_widget(msg)
			self._box.add_widget(row)

	def _clear(self, *a):
		GhostState.log_lines.clear()
		self._refresh()


# ══════════════════════════════════════════════════════════════
# СТРАНИЦА 4 — НАСТРОЙКИ
# ══════════════════════════════════════════════════════════════
class SettingsPage(BoxLayout):
	def __init__(self, **kwargs):
		super().__init__(orientation='vertical', padding=[28, 32, 28, 20], spacing=18, **kwargs)
		self._build()

	def _build(self):
		self.add_widget(lbl('[b]Настройки[/b]', 40, markup=True, height=72))

		scroll = ScrollView()
		inn = BoxLayout(orientation='vertical', spacing=18, size_hint_y=None)
		inn.bind(minimum_height=inn.setter('height'))

		# ── Поведение ────────────────────────────────────────────
		inn.add_widget(section('Поведение при запуске'))
		beh = Card(orientation='vertical', padding=[22, 18], spacing=10,
		           size_hint_y=None, height=380)

		for active, attr, color, label_text in [
			(GhostState.restart_on_crash, 'restart_on_crash', '#6B6BFF', 'Перезапуск при сбое'),
			(GhostState.serialize_runs,   'serialize_runs',   '#00BCD4', 'Последовательный запуск'),
			(GhostState.py_only,          'py_only',          '#69F0AE', 'Только .py файлы'),
			(GhostState.load_dotenv,      'load_dotenv',      '#FFB300', 'Загружать .env из репозитория'),
		]:
			t = GlowToggle(
				active=active,
				color_on=get_color_from_hex(color),
				color_off=get_color_from_hex('#2A2445'),
				knob_color=get_color_from_hex('#1A1535'),
				label=label_text,
				label_size=30, label_bold=True,
				size_hint_y=None, height=78,
				on_toggle=lambda v, a=attr: setattr(GhostState, a, v),
			)
			beh.add_widget(t)
		inn.add_widget(beh)

		# ── Параметры загрузки — слайдеры (меньше) ───────────────
		inn.add_widget(section('Параметры загрузки'))
		sl_card = Card(orientation='vertical', padding=[22, 14], spacing=35,
		               size_hint_y=None, height=340)

		for val, mn, mx, steps, color, label_text, attr in [
			(GhostState.restart_delay, 1, 60, 59,  '#6B6BFF', 'Задержка перезапуска (сек)', 'restart_delay'),
			(GhostState.concurrency,   1, 20, 19,  '#00BCD4', 'Параллельных потоков',       'concurrency'),
			(GhostState.max_file_size, 1, 20, 19,  '#69F0AE', 'Макс. размер файла (MB)',    'max_file_size'),
		]:
			s = GlowSlider(
				min_val=mn, max_val=mx, value=val,
				steps=steps,
				track_color=get_color_from_hex(color),
				label=label_text,
				label_size=32,
				thumb_size=50,
				track_height=25,
				size_hint_y=None, height=68,
				on_change=lambda v, a=attr: setattr(GhostState, a, v),
			)
			sl_card.add_widget(s)
		inn.add_widget(sl_card)

		# ── Кэш ──────────────────────────────────────────────────
		inn.add_widget(section('Управление кэшем'))
		cache = Card(orientation='vertical', padding=[22, 18], spacing=10,
		             size_hint_y=None, height=200)

		for active, attr, label_text in [
			(GhostState.auto_cleanup, 'auto_cleanup', 'Автоочистка VFS после завершения'),
			(GhostState.clear_cache,  'clear_cache',  'Полная очистка (clear_cache)'),
		]:
			t = GlowToggle(
				active=active,
				color_on=get_color_from_hex('#FF5252'),
				color_off=get_color_from_hex('#2A2445'),
				knob_color=get_color_from_hex('#1A1535'),
				label=label_text,
				label_size=30, label_bold=True,
				size_hint_y=None, height=78,
				on_toggle=lambda v, a=attr: setattr(GhostState, a, v),
			)
			cache.add_widget(t)
		inn.add_widget(cache)

		# ── Токен ────────────────────────────────────────────────
		inn.add_widget(section('GitHub токен'))
		tok_card = Card(orientation='vertical', padding=[22, 16], spacing=12,
		                size_hint_y=None, height=150)
		tok_card.add_widget(lbl('Используется для доступа к приватным репозиториям',
		                        28, C.TEXT_DIM, height=40))
		self._token = TextInput(
			text=GhostState.github_token,
			multiline=False, font_size=30, password=True,
			background_color=get_color_from_hex('#0E0B1A'),
			foreground_color=C.TEXT,
			cursor_color=C.ACCENT,
			size_hint_y=None, height=72,
		)
		self._token.bind(text=lambda w, v: setattr(GhostState, 'github_token', v))
		tok_card.add_widget(self._token)
		inn.add_widget(tok_card)

		# ── Сохранить ────────────────────────────────────────────
		btn_save = RoundButton(
			text='[b]Сохранить настройки[/b]', markup=True, font_size=32,
			size_hint_y=None, height=100,
			btn_color=[*C.ACCENT],
			btn_color_pressed=[*C.ACCENT_SOFT],
		)
		btn_save.bind(on_press=lambda *a: GhostState.log_lines.append(
			('Настройки сохранены', '#7C4DFF')
		))
		inn.add_widget(btn_save)
		inn.add_widget(Widget(size_hint_y=None, height=24))

		scroll.add_widget(inn)
		self.add_widget(scroll)


# ══════════════════════════════════════════════════════════════
# СТРАНИЦА 5 — ИНФОРМАЦИЯ
# ══════════════════════════════════════════════════════════════
class InfoPage(BoxLayout):
	def __init__(self, **kwargs):
		super().__init__(orientation='vertical', padding=[28, 32, 28, 20], spacing=18, **kwargs)
		self._build()

	def _build(self):
		self.add_widget(lbl('[b]Информация[/b]', 40, markup=True, height=72))

		scroll = ScrollView()
		inn = BoxLayout(orientation='vertical', spacing=18, size_hint_y=None)
		inn.bind(minimum_height=inn.setter('height'))

		# ── О GhostRun ───────────────────────────────────────────
		inn.add_widget(section('О GhostRun'))
		about = Card(orientation='vertical', padding=[22, 20], spacing=14,
		             size_hint_y=None, height=400)
		for k, v in [
			('Версия',        '1.0.0 Optimized'),
			('Описание',      'Запуск GitHub-проектов в памяти'),
			('Автор', '@forget_git'),
			('VirtualFS',     'In-memory словарь с zlib сжатием'),
			('Загрузка',      'Streaming tarball + SpooledTempFile'),
			('Изоляция',      'VirtualFinder + sys.path proxy'),
			('Параллельность','asyncio.gather + aiohttp'),
		]:
			row = BoxLayout(size_hint_y=None, height=20, padding=0)
			row.add_widget(lbl(k, 30, C.TEXT_DIM, bold=True, height=54))
			row.add_widget(lbl(v, 30, C.INFO,     height=54, width=420))
			about.add_widget(Widget())
			about.add_widget(row)
		inn.add_widget(about)

		# ── Архитектура ──────────────────────────────────────────
		inn.add_widget(section('Архитектура'))
		arch = Card(orientation='vertical', padding=[22, 20], spacing=0,
		            size_hint_y=None, height=400)
		for k, v in [
			('VirtualFS',             'Хранит файлы в dict с zlib'),
			('VirtualLoader',         'Загружает модули из VFS'),
			('VirtualFinder',         'Перехватчик sys.meta_path'),
			('_SysPathProxy',         'Прокси для sys.path'),
			('_GitHubFetcher',        'Загрузка tarball через aiohttp'),
			('GitHubProjectRunner',   'Основной класс запуска'),
			('free_run()',            'Параллельный запуск списка репо'),
		]:
			row = BoxLayout(size_hint_y=None, height=20)
			row.add_widget(lbl(f'[b]{k}[/b]', 30, C.ACCENT,
			                   markup=True, height=52, width=340))
			row.add_widget(lbl(v, 30, C.TEXT_DIM, height=52))
			arch.add_widget(Widget())
			arch.add_widget(row)
		inn.add_widget(arch)

		# ── Параметры класса ─────────────────────────────────────
		inn.add_widget(section('Параметры GitHubProjectRunner'))
		params_card = Card(orientation='vertical', padding=[22, 20], spacing=0,
		                   size_hint_y=None, height=560)
		for k, v in [
			('url',              'GitHub URL репозитория'),
			('token',            'GitHub Personal Access Token'),
			('branch',           'Ветка (None = default_branch)'),
			('concurrency',      'Макс. параллельных запросов'),
			('max_file_size',    'Макс. размер файла (байт)'),
			('restart_on_crash', 'Перезапуск при исключении'),
			('restart_delay',    'Задержка перезапуска (сек)'),
			('serialize_runs',   'Глобальная блокировка запусков'),
			('auto_cleanup',     'Очистка VFS после запуска'),
			('clear_cache',      'Полное удаление модулей'),
			('py_only',          'Загружать только .py файлы'),
		]:
			row = BoxLayout(size_hint_y=None, height=44)
			row.add_widget(lbl(k, 30, C.ACCENT, bold=True, height=44, width=300))
			row.add_widget(lbl(v, 30, C.TEXT_DIM, height=44))
			params_card.add_widget(Widget())
			params_card.add_widget(row)
		inn.add_widget(params_card)

		# ── Системная информация ─────────────────────────────────
		inn.add_widget(section('Системная информация'))
		sys_card = Card(orientation='vertical', padding=[22, 20], spacing=0,
		                size_hint_y=None, height=210)

		import sys as _sys
		py_ver = f'{_sys.version_info.major}.{_sys.version_info.minor}.{_sys.version_info.micro}'
		for k, v, color in [
			('Python',         py_ver,                    C.SUCCESS),
			('Платформа',      _sys.platform,             C.INFO),
			('Репозиториев',   str(len(GhostState.repos)), C.WARNING),
		]:
			row = BoxLayout(size_hint_y=None, height=40, padding=[0, 5])
			row.add_widget(lbl(k, 32, C.TEXT_DIM, height=46))
			row.add_widget(lbl(v, 32, color, bold=True, height=46, width=280))
			sys_card.add_widget(row)

		bar = GlowProgressBar(
			show_label=True, animated=True,
			size_hint_y=None, height=38,
			bar_color=get_color_from_hex('#7C4DFF'),
			bg_color=get_color_from_hex('#0E0B1A'),
		)
		bar.set_value(72)
		sys_card.add_widget(bar)
		inn.add_widget(sys_card)

		inn.add_widget(Widget(size_hint_y=None, height=24))
		scroll.add_widget(inn)
		self.add_widget(scroll)


# ══════════════════════════════════════════════════════════════
# ОВЕРЛЕЙ
# ══════════════════════════════════════════════════════════════
class Overlay(Widget):
	def __init__(self, on_close, panel, **kwargs):
		super().__init__(**kwargs)
		self.on_close = on_close
		self.panel    = panel
		self.opacity  = 0
		self.disabled = True
		with self.canvas:
			Color(0, 0, 0, 0.7)
			self.rect = Rectangle(pos=self.pos, size=self.size)
		self.bind(pos=self._upd, size=self._upd)

	def _upd(self, *a):
		self.rect.pos  = self.pos
		self.rect.size = self.size

	def on_touch_down(self, touch):
		if self.opacity == 0 or self.disabled: return False
		if self.panel.collide_point(*touch.pos): return False
		if self.collide_point(*touch.pos):
			self.on_close(); return True
		return False


# ══════════════════════════════════════════════════════════════
# БОКОВАЯ ПАНЕЛЬ
# ══════════════════════════════════════════════════════════════
class SidePanel(BoxLayout):
	def __init__(self, callbacks, **kwargs):
		super().__init__(orientation='vertical', **kwargs)
		self.size_hint = (None, 1)
		self.width     = Window.width * 0.25
		self.x         = -self.width
		self.spacing   = 12

		with self.canvas.before:
			Color(*C.PANEL)
			self._bg = Rectangle(pos=self.pos, size=self.size)
		self.bind(
			pos=lambda *a: setattr(self._bg, 'pos', self.pos),
			size=lambda *a: setattr(self._bg, 'size', self.size),
		)
		Window.bind(size=self._resize)

		title = RoundButton(
			text='[b]GhostRun[/b]', markup=True,
			size_hint_y=None, size_hint_x=0.6, height=130,
			pos_hint={'center_x': 0.5},
			btn_color=[*C.PANEL_BTN], btn_color_pressed=[*C.PANEL_BTN],
			font_size=90,
		)
		title.on_press   = lambda *a: None
		title.on_release = lambda *a: None
		self.add_widget(title)
		self.add_widget(Widget(size_hint_y=None, height=8))

		for text, cb in [
			('Запуск',     'run_p'),
			('Проекты',    'projects_p'),
			('Логи',       'log_p'),
			('Настройки',  'settings_p'),
			('Информация', 'info_p'),
		]:
			self.add_widget(self._make_btn(text, callbacks.get(cb)))

		self.add_widget(Widget())
		self.add_widget(self._make_btn(
			'Выход', callbacks.get('exit_p'),
			get_color_from_hex('#C62828'),
			get_color_from_hex('#7F0000'),
		))
		self.add_widget(Widget(size_hint_y=None, height=10))

	def _resize(self, win, size):
		self.width = size[0] * 0.25
		if self.x < 0: self.x = -self.width

	def _make_btn(self, text, cb, color=None, color_p=None):
		btn = RoundButton(
			text=f'[b]{text}[/b]', markup=True,
			size_hint_y=None, height=72, font_size=40,
			btn_color=color   or [*C.ACCENT_SOFT],
			btn_color_pressed=color_p or [*C.ACCENT],
		)
		if cb: btn.bind(on_press=cb)
		return btn

	def open(self):
		Animation(x=0, duration=0.25, t='out_cubic').start(self)

	def close(self):
		Animation(x=-self.width, duration=0.2, t='in_cubic').start(self)


# ══════════════════════════════════════════════════════════════
# ROOT
# ══════════════════════════════════════════════════════════════
class Studio(FloatLayout):
	def __init__(self, **kwargs):
		super().__init__(**kwargs)
		self._panel_open = False

		self.content = BoxLayout()
		self.add_widget(self.content)

		with self.content.canvas.before:
			Color(*C.BG)
			self._cbg = Rectangle(pos=self.content.pos, size=self.content.size)
		self.content.bind(
			pos=lambda *a: setattr(self._cbg, 'pos', self.content.pos),
			size=lambda *a: setattr(self._cbg, 'size', self.content.size),
		)

		self.hamburger = RoundButton(
			text='[b]Меню[/b]', markup=True,
			radius=30, size_hint=(None, None),
			height=110, width=370,
			pos_hint={'center_x': 0.5, 'top': 0.99}, font_size=50,
		)
		#self.hamburger.x = 18
		self.hamburger.bind(on_press=lambda _: self.toggle_panel())
		self.add_widget(self.hamburger)

		self.panel = SidePanel(callbacks={
			'run_p':      self.run_p,
			'projects_p': self.projects_p,
			'log_p':      self.log_p,
			'settings_p': self.settings_p,
			'info_p':     self.info_p,
			'exit_p':     lambda *a: App.get_running_app().stop(),
		})
		self.overlay = Overlay(on_close=self.toggle_panel, panel=self.panel)
		self.add_widget(self.overlay)
		self.add_widget(self.panel)

		self.run_p(load=False)

	def toggle_panel(self):
		if self._panel_open:
			self.panel.close()
			Animation(opacity=0, duration=0.2).start(self.overlay)
			self.overlay.disabled = True
		else:
			self.panel.open()
			self.overlay.disabled = False
			Animation(opacity=1, duration=0.2).start(self.overlay)
		self._panel_open = not self._panel_open

	def _load(self):
		self.content.clear_widgets()
		if self._panel_open:
			self.toggle_panel()

	def run_p(self, *a, load=True):
		if load: self._load()
		self.content.add_widget(RunPage())

	def projects_p(self, *a):
		self._load()
		self.content.add_widget(ProjectsPage())

	def log_p(self, *a):
		self._load()
		self.content.add_widget(LogsPage())

	def settings_p(self, *a):
		self._load()
		self.content.add_widget(SettingsPage())

	def info_p(self, *a):
		self._load()
		self.content.add_widget(InfoPage())


class StudioApp(App):
	def build(self):
		return Studio()


if __name__ == '__main__':
	StudioApp().run()
