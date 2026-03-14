from kivy.app import App
from kivy.uix.widget import Widget
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.graphics import Color, RoundedRectangle, Ellipse, Rectangle
from kivy.animation import Animation
from kivy.utils import get_color_from_hex
from kivy.properties import BooleanProperty, NumericProperty


class GlowToggle(Widget):
	"""
	Параметры:
		active        — начальное состояние (True/False)
		color_on      — цвет трассы когда включён
		color_off     — цвет трассы когда выключен
		knob_color    — цвет кружка
		label         — текст рядом с переключателем
		label_side    — 'left' или 'right' (по умолчанию 'right')
		label_color   — цвет текста
		label_size    — размер шрифта (по умолчанию 14)
		label_bold    — жирный (True/False)
		label_italic  — курсив (True/False)
		on_toggle     — колбэк fn(active: bool)
	"""
	active    = BooleanProperty(False)
	_knob_pct = NumericProperty(0.0)

	def __init__(
		self,
		active=False,
		color_on=None,
		color_off=None,
		knob_color=None,
		label='',
		label_side='right',
		label_color=None,
		label_size=14,
		label_bold=True,
		label_italic=False,
		on_toggle=None,
		**kwargs
	):
		super().__init__(**kwargs)
		self.color_on      = color_on   or get_color_from_hex('#6B6BFF')
		self.color_off     = color_off  or get_color_from_hex('#3A3550')
		self.knob_color    = knob_color or get_color_from_hex('#1A1535')
		self._label_text   = label
		self._label_side   = label_side
		self._label_clr    = label_color or get_color_from_hex('#E8E0FF')
		self._label_size   = label_size
		self._label_bold   = label_bold
		self._label_italic = label_italic
		self._on_toggle    = on_toggle

		self._knob_pct = 1.0 if active else 0.0
		self.active    = active

		# Создаём Label ОДИН РАЗ в __init__
		if self._label_text:
			self._lbl = Label(
				text=self._label_text,
				color=self._label_clr,
				font_size=self._label_size,
				bold=self._label_bold,
				italic=self._label_italic,
				size_hint=(None, None),
				halign='left',
				valign='middle',
			)
			self.add_widget(self._lbl)

		self.bind(size=self._redraw, pos=self._redraw, _knob_pct=self._redraw)

	# ── Размеры ──────────────────────────────────────────────────
	@property
	def _th(self):
		return self.height * 0.72

	@property
	def _tw(self):
		return self._th * 1.85

	@property
	def _tx(self):
		return self.x

	@property
	def _ty(self):
		return self.center_y - self._th / 2

	@property
	def _kr(self):
		return self._th * 0.42

	@property
	def _kx(self):
		pad   = self._th * 0.10
		r     = self._kr
		x_off = self._tx + pad + r
		x_on  = self._tx + self._tw - pad - r
		return x_off + self._knob_pct * (x_on - x_off)

	def _lerp(self, c1, c2, t):
		return [c1[i] + (c2[i] - c1[i]) * t for i in range(4)]

	# ── Отрисовка ────────────────────────────────────────────────
	def _redraw(self, *args):
		# Рисуем на canvas.before — Label (дочерний виджет) рендерится поверх
		self.canvas.before.clear()

		th = self._th
		tw = self._tw
		tx = self._tx
		ty = self._ty
		r  = self._kr
		kx = self._kx
		ky = self.center_y
		t  = self._knob_pct

		track_c = self._lerp(list(self.color_off), list(self.color_on), t)

		with self.canvas.before:
			# ── Трасса ───────────────────────────────────────────
			Color(*track_c)
			RoundedRectangle(pos=(tx, ty), size=(tw, th), radius=[th / 2])

			# ── Блик ─────────────────────────────────────────────
			glare_h = th * 0.22
			glare_w = tw * 0.6
			Color(1, 1, 1, 0.12)
			RoundedRectangle(
				pos=(tx + (tw - glare_w) / 2, ty + th * 0.68),
				size=(glare_w, glare_h),
				radius=[glare_h / 2]
			)

			# ── Тень кружка ──────────────────────────────────────
			Color(0, 0, 0, 0.4)
			Ellipse(pos=(kx - r + 1, ky - r - 2), size=(r * 2, r * 2))

			# ── Кружок ────────────────────────────────────────────
			Color(*self.knob_color)
			Ellipse(pos=(kx - r, ky - r), size=(r * 2, r * 2))

			# ── Ободок ────────────────────────────────────────────
			ir = r * 0.78
			Color(0, 0, 0, 0.25)
			Ellipse(pos=(kx - ir, ky - ir), size=(ir * 2, ir * 2))

			# ── Центр кружка — меняет цвет ───────────────────────
			cr      = r * 0.55
			inner_c = self._lerp(list(self.knob_color), list(self.color_on), t)
			Color(*inner_c)
			Ellipse(pos=(kx - cr, ky - cr), size=(cr * 2, cr * 2))

		# ── Позиционирование Label ────────────────────────────────
		if self._label_text and hasattr(self, '_lbl'):
			self._lbl.texture_update()
			lbl_w = max(1, self._lbl.texture_size[0])
			lbl_h = max(1, self._lbl.texture_size[1])
			self._lbl.size = (lbl_w, lbl_h)
			gap = 12
			if self._label_side == 'right':
				self._lbl.x = tx + tw + gap
			else:
				self._lbl.x = tx - gap - lbl_w
			self._lbl.y = ky - lbl_h / 2

	# ── Касание ──────────────────────────────────────────────────
	def on_touch_down(self, touch):
		tx, ty = self._tx, self._ty
		if tx <= touch.x <= tx + self._tw and ty <= touch.y <= ty + self._th:
			self._toggle()
			return True
		return False

	def _toggle(self):
		self.active = not self.active
		target      = 1.0 if self.active else 0.0
		Animation(_knob_pct=target, duration=0.28, t='out_cubic').start(self)
		if self._on_toggle:
			self._on_toggle(self.active)

	def set_active(self, val, animated=True):
		if val == self.active:
			return
		if animated:
			self._toggle()
		else:
			self.active    = val
			self._knob_pct = 1.0 if val else 0.0


# ── Демо ──────────────────────────────────────────────────────
class ToggleDemoApp(App):
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
			padding=[60, 20],
			spacing=22,
			size_hint=(0.75, None),
			height=380,
			pos_hint={'center_x': 0.5, 'center_y': 0.5}
		)

		layout.add_widget(GlowToggle(
			active=True,
			color_on=get_color_from_hex('#6B6BFF'),
			color_off=get_color_from_hex('#2A2445'),
			label='Текст справа',
			label_side='right',
			label_size=34,
			label_bold=True,
			size_hint_y=None, height=100,
		))

		layout.add_widget(GlowToggle(
			active=False,
			color_on=get_color_from_hex('#00BCD4'),
			color_off=get_color_from_hex('#2A2445'),
			label='Текст слева',
			label_side='left',
			label_size=34,
			size_hint_y=None, height=100,
		))

		layout.add_widget(GlowToggle(
			active=True,
			color_on=get_color_from_hex('#69F0AE'),
			color_off=get_color_from_hex('#2A2445'),
			label='Большой курсив',
			label_size=40,
			label_bold=False,
			label_italic=True,
			size_hint_y=None, height=100,
		))

		layout.add_widget(GlowToggle(
			active=False,
			color_on=get_color_from_hex('#FF5252'),
			color_off=get_color_from_hex('#2A2445'),
			size_hint_y=None, height=100,
		))

		root.add_widget(layout)
		return root


if __name__ == '__main__':
	ToggleDemoApp().run()
