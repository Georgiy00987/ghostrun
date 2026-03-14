from kivy.app import App
from kivy.uix.widget import Widget
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.graphics import Color, RoundedRectangle, Ellipse, Rectangle
from kivy.animation import Animation
from kivy.utils import get_color_from_hex
from kivy.properties import NumericProperty


class GlowSlider(Widget):
	"""
	Параметры:
		min_val       — минимальное значение (по умолчанию 0)
		max_val       — максимальное значение (по умолчанию 100)
		value         — начальное значение
		steps         — количество делений (None = плавный, 10 = 10 шагов)
		track_height  — толщина полосы в пикселях (по умолчанию 6)
		thumb_size    — диаметр кружка в пикселях (по умолчанию 24)
		track_color   — цвет заполненной части
		bg_color      — цвет пустой части
		thumb_color   — цвет кружка
		label         — текст над слайдером
		label_color   — цвет текста
		label_size    — размер шрифта (по умолчанию 13)
		label_bold    — жирный (True/False)
		show_value    — показывать значение над кружком (True/False)
		on_change     — колбэк fn(value) при изменении
	"""
	value = NumericProperty(0)

	def __init__(
		self,
		min_val=0,
		max_val=100,
		value=50,
		steps=None,
		track_height=None,  # None = авто (12% высоты виджета)
		thumb_size=None,    # None = авто (35% высоты виджета)
		track_color=None,
		bg_color=None,
		thumb_color=None,
		label='',
		label_color=None,
		label_size=None,  # None = авто (30% высоты виджета)
		label_bold=False,
		show_value=True,
		on_change=None,
		**kwargs
	):
		super().__init__(**kwargs)
		self.min_val      = min_val
		self.max_val      = max_val
		self.steps        = steps
		self.track_height = track_height
		self.thumb_size   = thumb_size
		self.track_color  = track_color or get_color_from_hex('#7C4DFF')
		self.bg_color     = bg_color    or get_color_from_hex('#1A1530')
		self.thumb_color  = thumb_color or get_color_from_hex('#E8E0FF')
		self._label_text  = label
		self._label_clr   = label_color or get_color_from_hex('#E8E0FF')
		self._label_size  = label_size  # None = авто
		self._label_bold  = label_bold
		self.show_value   = show_value
		self._on_change   = on_change
		self._dragging    = False

		# Устанавливаем начальное значение через _snap
		self.value = self._snap(value)

		# Метка над слайдером — создаётся один раз
		# font_size=14 — временный дефолт, обновится в _redraw
		if self._label_text:
			self._lbl = Label(
				text=self._label_text,
				color=self._label_clr,
				font_size=self._label_size or 18,
				bold=self._label_bold,
				size_hint=(None, None),
				halign='left',
				valign='middle',
			)
			self.add_widget(self._lbl)

		# Значение над кружком — создаётся один раз
		if self.show_value:
			self._val_lbl = Label(
				color=get_color_from_hex('#E8E0FF'),
				font_size=self._label_size or 18,
				bold=True,
				size_hint=(None, None),
				halign='center',
				valign='middle',
			)
			self.add_widget(self._val_lbl)

		self.bind(size=self._redraw, pos=self._redraw, value=self._redraw)

	# ── Snap к ближайшему делению ────────────────────────────────
	def _snap(self, val):
		val = max(self.min_val, min(val, self.max_val))
		if self.steps and self.steps > 1:
			step_size = (self.max_val - self.min_val) / self.steps
			val = round(val / step_size) * step_size
		return val

	# ── Размеры ──────────────────────────────────────────────────
	@property
	def _r(self):
		# Кружок — 35% высоты виджета (или фиксированный)
		if self.thumb_size is not None:
			return self.thumb_size / 2
		return self.height * 0.35

	@property
	def _tr(self):
		# Полоса — почти как кружок (80% диаметра кружка)
		if self.track_height is not None:
			return self.track_height
		return self._r * 1.6

	@property
	def _font_size(self):
		# Шрифт — 30% высоты виджета
		if self._label_size is not None:
			return self._label_size
		return self.height * 0.4

	@property
	def _ty(self):
		return self.center_y - self._tr / 2

	@property
	def _kx(self):
		pct = (self.value - self.min_val) / max(1, self.max_val - self.min_val)
		r   = self._r
		return self.x + r + pct * (self.width - r * 2)

	# ── Отрисовка ────────────────────────────────────────────────
	def _redraw(self, *args):
		self.canvas.before.clear()

		tr  = self._tr
		r   = self._r
		ty  = self._ty
		kx  = self._kx
		ky  = self.center_y

		with self.canvas.before:
			# ── Фоновая полоса ──────────────────────────────────
			Color(*self.bg_color)
			RoundedRectangle(
				pos=(self.x, ty),
				size=(self.width, tr),
				radius=[tr / 2]
			)

			# ── Заполненная полоса ───────────────────────────────
			fill_w = max(tr, kx - self.x)
			Color(*self.track_color)
			RoundedRectangle(
				pos=(self.x, ty),
				size=(fill_w, tr),
				radius=[tr / 2]
			)

			# ── Блик (пилл) ──────────────────────────────────────
			if fill_w > tr * 2:
				gh = max(1, tr * 0.4)
				gw = fill_w * 0.65
				Color(1, 1, 1, 0.18)
				RoundedRectangle(
					pos=(self.x + (fill_w - gw) / 2, ty + tr * 0.55),
					size=(gw, gh),
					radius=[gh / 2]
				)

			# ── Деления (если steps задан) ───────────────────────
			if self.steps and self.steps > 1:
				Color(1, 1, 1, 0.25)
				dot_r = max(2, tr * 0.35)
				for i in range(self.steps + 1):
					pct  = i / self.steps
					dot_x = self.x + r + pct * (self.width - r * 2)
					dot_y = ky
					Ellipse(
						pos=(dot_x - dot_r, dot_y - dot_r),
						size=(dot_r * 2, dot_r * 2)
					)

			# ── Тень кружка ──────────────────────────────────────
			Color(0, 0, 0, 0.28)
			Ellipse(pos=(kx - r + 1, ky - r - 2), size=(r * 2, r * 2))

			# ── Кружок ───────────────────────────────────────────
			Color(*self.thumb_color)
			Ellipse(pos=(kx - r, ky - r), size=(r * 2, r * 2))

			# ── Акцент внутри кружка ─────────────────────────────
			Color(*self.track_color)
			ir = r * 0.38
			Ellipse(pos=(kx - ir, ky - ir), size=(ir * 2, ir * 2))

		# ── Label над слайдером ───────────────────────────────────
		if self._label_text and hasattr(self, '_lbl'):
			self._lbl.font_size = self._font_size
			self._lbl.texture_update()
			lw = max(1, self._lbl.texture_size[0])
			lh = max(1, self._lbl.texture_size[1])
			self._lbl.size = (lw, lh)
			self._lbl.x    = self.x
			self._lbl.y    = ky + r + 4

		# ── Значение над кружком ──────────────────────────────────
		if self.show_value and hasattr(self, '_val_lbl'):
			val_str = f'{int(self.value)}' if isinstance(self.value, float) and self.value == int(self.value) else f'{self.value:.1f}'
			self._val_lbl.text = val_str
			self._val_lbl.font_size = self._font_size
			self._val_lbl.texture_update()
			vw = max(1, self._val_lbl.texture_size[0])
			vh = max(1, self._val_lbl.texture_size[1])
			self._val_lbl.size = (vw, vh)
			self._val_lbl.x    = kx - vw / 2
			self._val_lbl.y    = ky + r + 4

	# ── Касание ──────────────────────────────────────────────────
	def _pos_to_value(self, touch_x):
		r   = self._r
		rel = (touch_x - self.x - r) / max(1, self.width - r * 2)
		raw = self.min_val + max(0, min(1, rel)) * (self.max_val - self.min_val)
		return self._snap(raw)

	def on_touch_down(self, touch):
		if self.collide_point(*touch.pos):
			self._dragging = True
			self.value = self._pos_to_value(touch.x)
			if self._on_change:
				self._on_change(self.value)
			return True
		return False

	def on_touch_move(self, touch):
		if self._dragging:
			self.value = self._pos_to_value(touch.x)
			if self._on_change:
				self._on_change(self.value)
			return True
		return False

	def on_touch_up(self, touch):
		if self._dragging:
			self._dragging = False
			return True
		return False

	def set_value(self, val, animated=True):
		val = self._snap(val)
		if animated:
			Animation(value=val, duration=0.3, t='out_cubic').start(self)
		else:
			self.value = val


# ── Демо ──────────────────────────────────────────────────────
class SliderDemoApp(App):
	def build(self):
		root = FloatLayout()

		with root.canvas.before:
			Color(*get_color_from_hex('#0E0B1A'))
			self._bg = Rectangle(pos=root.pos, size=root.size)
		root.bind(
			pos=lambda *a: setattr(self._bg, 'pos', root.pos),
			size=lambda *a: setattr(self._bg, 'size', root.size)
		)

		layout = BoxLayout(
			orientation='vertical',
			padding=[50, 20],
			spacing=30,
			size_hint=(0.85, None),
			height=460,
			pos_hint={'center_x': 0.5, 'center_y': 0.5}
		)

		# Плавный слайдер
		layout.add_widget(GlowSlider(
			min_val=0, max_val=100, value=65,
					track_color=get_color_from_hex('#7C4DFF'),
			label='Громкость (плавный)',
			on_change=lambda v: print(f'Громкость: {v:.1f}'),
			size_hint_y=None, height=70,
		))

		# 10 делений
		layout.add_widget(GlowSlider(
			min_val=0, max_val=10, value=4,
			steps=10,
					track_color=get_color_from_hex('#00BCD4'),
			label='Уровень (10 шагов)',
			on_change=lambda v: print(f'Уровень: {v:.0f}'),
			size_hint_y=None, height=70,
		))

		# 5 делений, толстая трасса
		layout.add_widget(GlowSlider(
			min_val=1, max_val=5, value=3,
			steps=5,
					track_color=get_color_from_hex('#69F0AE'),
			label='Скорость (5 шагов)',
			on_change=lambda v: print(f'Скорость: {v:.0f}'),
			size_hint_y=None, height=70,
		))

		# 50 делений
		layout.add_widget(GlowSlider(
			min_val=0, max_val=100, value=50,
			steps=50,
					track_color=get_color_from_hex('#FFB300'),
			label='Точность (50 шагов)',
			on_change=lambda v: print(f'Точность: {v:.0f}'),
			size_hint_y=None, height=70,
		))

		root.add_widget(layout)
		return root


if __name__ == '__main__':
	SliderDemoApp().run()
