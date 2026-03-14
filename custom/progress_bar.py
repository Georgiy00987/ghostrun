from kivy.app import App
from kivy.uix.widget import Widget
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.graphics import Color, RoundedRectangle, Rectangle
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.utils import get_color_from_hex
from kivy.properties import NumericProperty, ListProperty


class GlowProgressBar(Widget):
	"""
	Красивый прогресс-бар с анимацией.

	Параметры:
		value       — текущее значение (0..100)
		bar_color   — цвет заполнения (RGBA)
		bg_color    — цвет фона полосы (RGBA)
		show_label  — показывать % поверх бара
		animated    — анимировать изменение value
	"""
	value     = NumericProperty(0)
	bar_color = ListProperty(get_color_from_hex('#7C4DFF'))
	bg_color  = ListProperty(get_color_from_hex('#231D3F'))

	def __init__(self, show_label=True, animated=True, **kwargs):
		super().__init__(**kwargs)
		self.show_label = show_label
		self.animated   = animated
		self.bind(size=self._redraw, pos=self._redraw, value=self._redraw)

	def _redraw(self, *args):
		self.canvas.clear()
		h      = self.height
		radius = h / 2
		pct    = max(0, min(self.value, 100)) / 100
		fill_w = max(h, self.width * pct)

		with self.canvas:
			# ── Фон ─────────────────────────────────────────────
			Color(*self.bg_color)
			RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])

			# ── Заполнение ───────────────────────────────────────
			Color(*self.bar_color)
			RoundedRectangle(
				pos=self.pos,
				size=(fill_w, h),
				radius=[radius]
			)

			# ── Блик: маленький пилл внутри верхней части fill ───
			# Не нужна маска — блик сам по себе скруглённый и не
			# вылезает за края основного fill
			glare_h = h * 0.28
			glare_w = fill_w * 0.72
			glare_x = self.x + (fill_w - glare_w) / 2
			glare_y = self.y + h * 0.62
			Color(1, 1, 1, 0.22)
			RoundedRectangle(
				pos=(glare_x, glare_y),
				size=(glare_w, glare_h),
				radius=[glare_h / 2]
			)

		# ── Текст % ──────────────────────────────────────────────
		if self.show_label:
			if not hasattr(self, '_label'):
				self._label = Label(
					bold=True,
					color=get_color_from_hex('#E8E0FF'),
				)
				self.add_widget(self._label)
			self._label.text      = f'{int(self.value)}%'
			self._label.center    = self.center
			self._label.font_size = max(10, h * 0.52)

	def set_value(self, val):
		val = max(0, min(val, 100))
		if self.animated:
			Animation(value=val, duration=0.5, t='out_cubic').start(self)
		else:
			self.value = val


# ── Демо ──────────────────────────────────────────────────────
class DemoApp(App):
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
			padding=40,
			spacing=30,
			size_hint=(0.85, None),
			height=400,
			pos_hint={'center_x': 0.5, 'center_y': 0.5}
		)

		self.bar1 = GlowProgressBar(size_hint_y=None, height=36,
			bar_color=get_color_from_hex('#7C4DFF'),
			bg_color=get_color_from_hex('#1A1530'))
		self.bar1.set_value(72)

		self.bar2 = GlowProgressBar(size_hint_y=None, height=36,
			bar_color=get_color_from_hex('#00BCD4'),
			bg_color=get_color_from_hex('#1A1530'))
		self.bar2.set_value(45)

		self.bar3 = GlowProgressBar(size_hint_y=None, height=36,
			bar_color=get_color_from_hex('#69F0AE'),
			bg_color=get_color_from_hex('#1A1530'))
		self.bar3.set_value(88)

		self.bar4 = GlowProgressBar(size_hint_y=None, height=86,
			bar_color=get_color_from_hex('#FF5252'),
			bg_color=get_color_from_hex('#1A1530'))
		self.bar4.set_value(20)

		for bar in [self.bar1, self.bar2, self.bar3, self.bar4]:
			layout.add_widget(bar)

		root.add_widget(layout)
		Clock.schedule_interval(self._animate_demo, 2.5)
		return root

	def _animate_demo(self, dt):
		import random
		for bar in [self.bar1, self.bar2, self.bar3, self.bar4]:
			bar.set_value(random.randint(5, 98))


if __name__ == '__main__':
	DemoApp().run()
