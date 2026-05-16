"""
Lina — CV (Computer Vision) модуль.

Обеспечивает:
- Захват и анализ скриншотов экрана
- OCR — распознавание текста на экране
- Детекция GUI-элементов (кнопки, окна, прогресс-бары, ошибки)

Все зависимости (mss, Pillow, opencv-python, pytesseract)
импортируются опционально — модуль работает в fallback-режиме.
"""

from lina.cv.scanner import ScreenScanner
from lina.cv.ocr import OCREngine
from lina.cv.detector import GUIDetector

__all__ = ["ScreenScanner", "OCREngine", "GUIDetector"]
