import threading
import time
import re
import ctypes
from ctypes import wintypes

import pytesseract
import translators as ts
import wx
from PIL import ImageGrab, ImageEnhance, ImageOps


pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
HOTKEY_STOP = 9001
HOTKEY_MOVE = 9002
HOTKEY_LEFT = 9003
HOTKEY_RIGHT = 9004
HOTKEY_UP = 9005
HOTKEY_DOWN = 9006


def make_click_through(frame):
    hwnd = frame.GetHandle()
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ctypes.windll.user32.SetWindowLongW(
        hwnd,
        GWL_EXSTYLE,
        style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE,
    )
    ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 245, 2)


def set_click_through(frame, enabled):
    hwnd = frame.GetHandle()
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if enabled:
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
    else:
        style &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        style |= WS_EX_LAYERED
    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


class TranslationOverlay(wx.Frame):
    def __init__(self, parent, bbox):
        super().__init__(
            parent,
            title="Traduccion",
            style=wx.NO_BORDER | wx.FRAME_NO_TASKBAR,
        )
        self.bbox = bbox
        self.running = True
        self.last_text = None
        self.translation_cache = {}
        self.move_mode = False

        x1, y1, x2, y2 = bbox
        width = 520
        height = 150
        display_index = wx.Display.GetFromPoint(wx.Point(x1, y1))
        display = wx.Display(display_index)
        area = display.GetClientArea()
        capture = wx.Rect(x1, y1, x2 - x1, y2 - y1)

        candidates = [
            wx.Rect(area.x + 20, area.y + 20, width, height),
            wx.Rect(area.x + area.width - width - 20, area.y + 20, width, height),
            wx.Rect(area.x + 20, area.y + area.height - height - 20, width, height),
            wx.Rect(area.x + area.width - width - 20, area.y + area.height - height - 20, width, height),
        ]

        non_overlapping = [rect for rect in candidates if not rect.Intersects(capture)]
        if non_overlapping:
            placement = min(
                non_overlapping,
                key=lambda rect: (
                    (rect.x + rect.width // 2 - (x1 + (x2 - x1) // 2)) ** 2
                    + (rect.y + rect.height // 2 - (y1 + (y2 - y1) // 2)) ** 2
                )
                * -1,
            )
        else:
            placement = candidates[0]

        self.SetPosition(wx.Point(placement.x, placement.y))
        self.SetSize(wx.Size(width, height))
        self.SetTransparent(245)

        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.Colour(8, 28, 48))
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.output = wx.StaticText(
            panel,
            label="Esperando texto en ingles...",
            style=wx.ALIGN_LEFT,
        )
        self.output.SetBackgroundColour(wx.Colour(8, 28, 48))
        self.output.SetForegroundColour(wx.Colour(255, 255, 255))
        font = wx.Font(17, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        self.output.SetFont(font)
        sizer.Add(self.output, 1, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(sizer)
        wx.CallLater(20, lambda: self.output.Wrap(width - 20))

        self.SetWindowStyle(wx.STAY_ON_TOP)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Show()
        wx.CallLater(100, lambda: make_click_through(self))
        ctypes.windll.user32.RegisterHotKey(
            self.GetHandle(),
            HOTKEY_STOP,
            MOD_NOREPEAT,
            0x77,
        )
        ctypes.windll.user32.RegisterHotKey(
            self.GetHandle(),
            HOTKEY_MOVE,
            MOD_NOREPEAT,
            0x78,
        )
        for hotkey_id, virtual_key in (
            (HOTKEY_LEFT, 0x25),
            (HOTKEY_RIGHT, 0x27),
            (HOTKEY_UP, 0x26),
            (HOTKEY_DOWN, 0x28),
        ):
            ctypes.windll.user32.RegisterHotKey(
                self.GetHandle(),
                hotkey_id,
                MOD_NOREPEAT,
                virtual_key,
            )
        self.Bind(wx.EVT_HOTKEY, self.on_hotkey)
        self.hotkey_id = HOTKEY_STOP

        threading.Thread(target=self.translation_loop, daemon=True).start()

    def on_hotkey(self, event):
        hotkey_id = event.GetId()
        if hotkey_id == HOTKEY_STOP:
            self.running = False
            ctypes.windll.user32.UnregisterHotKey(self.GetHandle(), HOTKEY_STOP)
            self.Close()
        elif hotkey_id == HOTKEY_MOVE:
            self.move_mode = not self.move_mode
            set_click_through(self, not self.move_mode)
            if self.move_mode:
                self.SetBackgroundColour(wx.Colour(0, 70, 130))
                self.output.SetLabel("MODO MOVER: usa las flechas\nF9 fija el overlay")
            else:
                self.SetBackgroundColour(wx.Colour(8, 28, 48))
        elif self.move_mode:
            position = self.GetPosition()
            step = 20
            if hotkey_id == HOTKEY_LEFT:
                position.x -= step
            elif hotkey_id == HOTKEY_RIGHT:
                position.x += step
            elif hotkey_id == HOTKEY_UP:
                position.y -= step
            elif hotkey_id == HOTKEY_DOWN:
                position.y += step
            self.SetPosition(position)

    def on_close(self, event):
        self.running = False
        for hotkey_id in (
            HOTKEY_STOP,
            HOTKEY_MOVE,
            HOTKEY_LEFT,
            HOTKEY_RIGHT,
            HOTKEY_UP,
            HOTKEY_DOWN,
        ):
            ctypes.windll.user32.UnregisterHotKey(self.GetHandle(), hotkey_id)
        parent = self.GetParent()
        self.Destroy()
        if parent:
            wx.CallAfter(parent.Show)

    def set_output(self, text):
        if self.output and not self.output.IsBeingDeleted():
            self.output.SetLabel(text)
            self.output.Wrap(self.GetSize().width - 20)

    def clean_ocr_text(self, image):
        image = image.resize(
            (max(1, image.width * 2), max(1, image.height * 2)),
        )
        gray = ImageOps.grayscale(image)
        gray = ImageEnhance.Contrast(gray).enhance(1.8)

        variants = [
            (gray, "--psm 11"),
            (gray.point(lambda value: 255 - value), "--psm 11"),
            (gray.point(lambda value: 255 if value > 145 else 0), "--psm 11"),
        ]

        best_result = None
        best_score = -1
        for variant, config in variants:
            data = pytesseract.image_to_data(
                variant,
                lang="eng",
                config=config,
                output_type=pytesseract.Output.DICT,
            )
            word_count = sum(
                1
                for word in data["text"]
                if word.strip() and re.search(r"[A-Za-z]", word)
            )
            confidence_values = [
                int(float(confidence))
                for confidence in data["conf"]
                if str(confidence).strip() and int(float(confidence)) >= 0
            ]
            average_confidence = (
                sum(confidence_values) // len(confidence_values)
                if confidence_values
                else 0
            )
            score = word_count * 10 + average_confidence
            if score > best_score:
                best_score = score
                best_result = data

        data = best_result

        recognized_words = []
        for index, word in enumerate(data["text"]):
            word = word.strip()
            try:
                confidence = int(float(data["conf"][index]))
                left = int(data["left"][index])
                top = int(data["top"][index])
                height = int(data["height"][index])
            except (TypeError, ValueError):
                continue

            if not word or confidence < 55:
                continue
            if not re.search(r"[A-Za-z]", word):
                continue
            letters = re.sub(r"[^A-Za-z]", "", word)
            if len(letters) < 2:
                continue

            recognized_words.append((top, left, height, word))

        recognized_words.sort(key=lambda item: (item[0], item[1]))
        lines = []
        current_line = []
        current_top = None
        average_height = 20
        if recognized_words:
            average_height = max(
                10,
                sum(item[2] for item in recognized_words) // len(recognized_words),
            )

        for top, left, height, word in recognized_words:
            if current_top is None or abs(top - current_top) <= max(12, average_height * 0.7):
                current_line.append((left, word))
                if current_top is None:
                    current_top = top
                else:
                    current_top = (current_top + top) // 2
            else:
                current_line.sort(key=lambda item: item[0])
                lines.append(" ".join(word for _, word in current_line))
                current_line = [(left, word)]
                current_top = top

        if current_line:
            current_line.sort(key=lambda item: item[0])
            lines.append(" ".join(word for _, word in current_line))
        valid_lines = []
        for line in lines:
            line = re.sub(r"\s+", " ", line).strip()
            letters = sum(character.isalpha() for character in line)
            if len(line) < 3 or len(line) > 80:
                continue
            if letters / max(1, len(line)) < 0.55:
                continue
            valid_lines.append(line)
        return "\n".join(valid_lines)

    def translation_loop(self):
        stable_text = None
        stable_count = 0
        while self.running:
            try:
                image = ImageGrab.grab(self.bbox)
                recognized = self.clean_ocr_text(image)
                lines = [line.strip() for line in recognized.splitlines() if line.strip()]
                text = "\n".join(lines)

                if text:
                    if text == stable_text:
                        stable_count += 1
                    else:
                        stable_text = text
                        stable_count = 1

                    if stable_count >= 6 and text != self.last_text:
                        translated_lines = []
                        for line in lines:
                            try:
                                if line in self.translation_cache:
                                    translated = self.translation_cache[line]
                                else:
                                    translated = ts.translate_text(
                                        line,
                                        translator="google",
                                        to_language="es",
                                        timeout=8,
                                    )
                                    if translated:
                                        self.translation_cache[line] = translated
                                translated_lines.append(translated)
                            except Exception:
                                translated_lines.append("")

                        final_text = "\n".join(
                            line for line in translated_lines if line
                        )
                        if final_text:
                            self.last_text = text
                            wx.CallAfter(self.set_output, final_text)
                else:
                    stable_text = None
                    stable_count = 0
                    wx.CallAfter(self.set_output, "Esperando texto en ingles...")
            except Exception as error:
                wx.CallAfter(self.set_output, "Error: " + str(error))

            time.sleep(0.35)


class SelectionController(wx.Frame):
    def __init__(self):
        super().__init__(
            None,
            title="Traductor de juegos",
            size=wx.Size(760, 260),
            style=wx.DEFAULT_FRAME_STYLE,
        )
        self.overlay = None
        self.Centre()

        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.Colour(32, 32, 32))
        sizer = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(
            panel,
            label="1. Mueve y redimensiona esta ventana sobre el texto del juego.",
        )
        title.SetForegroundColour(wx.Colour(255, 255, 255))
        title_font = title.GetFont()
        title_font.PointSize = 12
        title_font.Weight = wx.FONTWEIGHT_BOLD
        title.SetFont(title_font)
        sizer.Add(title, 0, wx.EXPAND | wx.ALL, 15)

        instructions = wx.StaticText(
            panel,
            label="2. Pulsa Iniciar. La traduccion aparecera sobre el juego.\n"
                  "El overlay no interceptara clics. Pulsa F8 para cerrarlo.",
        )
        instructions.SetForegroundColour(wx.Colour(220, 220, 220))
        sizer.Add(instructions, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        start_button = wx.Button(panel, label="Iniciar traduccion")
        start_button.SetDefault()
        start_button.Bind(wx.EVT_BUTTON, self.start_translation)
        button_sizer.Add(start_button, 0, wx.RIGHT, 10)

        help_button = wx.Button(panel, label="Ayuda")
        help_button.Bind(wx.EVT_BUTTON, self.show_help)
        button_sizer.Add(help_button)
        sizer.Add(button_sizer, 0, wx.ALL, 15)

        panel.SetSizer(sizer)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Show()

    def start_translation(self, event):
        position = self.GetScreenPosition()
        size = self.GetSize()
        bbox = (
            position.x,
            position.y,
            position.x + size.width,
            position.y + size.height,
        )
        self.Hide()
        self.overlay = TranslationOverlay(self, bbox)

    def show_help(self, event):
        wx.MessageBox(
            "Coloca esta ventana exactamente sobre el texto que quieras traducir "
            "y redimensionala. Pulsa Iniciar traduccion. La traduccion al español "
            "se actualizara sola cada 0.25 segundos.",
            "Como usar",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    def on_close(self, event):
        if self.overlay:
            self.overlay.running = False
            self.overlay.Destroy()
        self.Destroy()


if __name__ == "__main__":
    app = wx.App()
    SelectionController()
    app.MainLoop()
