import sys
import os
import io
import subprocess
import tempfile
import ctypes

# Register Windows AppUserModelID early for taskbar icon binding
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PC_Remote_Pro.UniversalHub.v4.0")
    except Exception:
        pass

# ----------------- 1. Absolute Base & Writable Data Directory -----------------
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_writable_app_dir():
    r"""
    Ensures config and cache are saved in %LOCALAPPDATA%\PCRemotePro to isolate credentials per PC
    and prevent account hijacking/conflicts when copying the program folder to another computer.
    """
    app_data = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "PCRemotePro")
    try:
        os.makedirs(app_data, exist_ok=True)
        return app_data
    except Exception:
        return BASE_DIR

DATA_DIR = get_writable_app_dir()
os.chdir(BASE_DIR)

# ----------------- 2. Auto-Dependency Installer -----------------
REQUIRED_LIBS = {
    "customtkinter": "customtkinter",
    "PIL": "pillow",
    "psutil": "psutil",
    "telegram": "python-telegram-bot",
    "pystray": "pystray",
    "cv2": "opencv-python"
}

missing_libs = []
for module_name, pip_name in REQUIRED_LIBS.items():
    try:
        __import__(module_name)
    except ImportError:
        missing_libs.append(pip_name)

if missing_libs:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing_libs])
    except Exception:
        pass

# ----------------- 3. Core Imports -----------------
import re
import json
import time
import socket
import asyncio
import platform
import threading
import base64
import hashlib
import ctypes
from ctypes import wintypes
import webbrowser
import urllib.parse
import urllib.request
import winreg
from datetime import datetime
import psutil
from PIL import ImageGrab, Image, ImageDraw
import tkinter as tk
import customtkinter as ctk
import tkinter.messagebox as messagebox

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

import telegram.error
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ForceReply
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
from telegram.request import HTTPXRequest

try:
    import pystray
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False

# ----------------- 64-Bit Safe Win32 API Definitions -----------------
try:
    _kernel32 = ctypes.windll.kernel32
    _user32 = ctypes.windll.user32
    _shell32 = ctypes.windll.shell32

    _kernel32.GlobalAlloc.restype = ctypes.c_void_p
    _kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    _user32.GetClipboardData.restype = ctypes.c_void_p
    _user32.GetClipboardData.argtypes = [ctypes.c_uint]
    _user32.SetClipboardData.restype = ctypes.c_void_p
    _user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
except Exception:
    pass

# ----------------- Application Info -----------------
APP_NAME = "PC Remote Pro"
APP_VERSION = "v4.0"
DEVELOPER_NAME = "Mohammed Shaheed"
MASTER_APP_INSTANCE = None
SECURITY_GUARD_SETTINGS = {
    "enabled": True,
    "alert_webcam": True,
    "alert_screen": True,
    "alert_unlock": True
}

# ----------------- Terminate Old Duplicate Instances -----------------
def kill_zombie_instances():
    """Kills previous running background instances of this script to avoid telegram polling conflicts."""
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] != current_pid and ('python' in proc.info['name'].lower() or 'pc_remote' in proc.info['name'].lower()):
                cmdline = proc.info.get('cmdline') or []
                cmd_str = " ".join(cmdline).lower()
                if 'bot_control.py' in cmd_str or 'pc_remote_pro.exe' in cmd_str:
                    try:
                        p = psutil.Process(proc.info['pid'])
                        p.terminate()
                    except Exception:
                        pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

kill_zombie_instances()

# ----------------- Global Registry & State -----------------
ACTIVE_BOT_REGISTRY = {}
CURRENT_USER = os.getlogin() if hasattr(os, 'getlogin') else os.environ.get('USERNAME', 'User')

DESKTOP_FOLDER = os.path.join(os.path.expanduser("~"), "Desktop")
DOWNLOADS_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads")
DOCUMENTS_FOLDER = os.path.join(os.path.expanduser("~"), "Documents")

PATH_STORE = {}
PATH_TO_ID = {}

def get_path_id(path_str: str) -> str:
    path_str = os.path.abspath(path_str)
    if path_str in PATH_TO_ID:
        return PATH_TO_ID[path_str]
    uid = hashlib.md5(path_str.encode('utf-8')).hexdigest()[:8]
    PATH_STORE[uid] = path_str
    PATH_TO_ID[path_str] = uid
    return uid

# ----------------- Full Auto-Clipboard Sync State -----------------
AUTO_CLIPBOARD_SUBS = set()
LAST_SEEN_CLIP_TEXT = ""
LAST_SEEN_CLIP_IMG_HASH = ""
LAST_SEEN_CLIP_FILES_HASH = ""

# ----------------- Internationalization (i18n) Engine -----------------
USER_LANGS_FILE = os.path.join(DATA_DIR, "user_languages.json")
USER_LANGUAGES = {}

def load_user_languages():
    global USER_LANGUAGES
    if os.path.exists(USER_LANGS_FILE):
        try:
            with open(USER_LANGS_FILE, 'r', encoding='utf-8') as f:
                USER_LANGUAGES = json.load(f)
        except Exception:
            USER_LANGUAGES = {}
    else:
        USER_LANGUAGES = {}

def save_user_language(user_id: int, lang_code: str):
    USER_LANGUAGES[str(user_id)] = lang_code
    try:
        with open(USER_LANGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(USER_LANGUAGES, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def get_user_language(user_id: int) -> str:
    return USER_LANGUAGES.get(str(user_id), "en")

load_user_languages()

SUPPORTED_LANGUAGES = {
    "en": "🇬🇧 English",
    "ar": "🇸🇦 العربية",
    "ru": "🇷🇺 Русский",
    "es": "🇪🇸 Español",
    "fr": "🇫🇷 Français",
    "de": "🇩🇪 Deutsch",
    "tr": "🇹🇷 Türkçe"
}

def build_language_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="setlang:en"),
         InlineKeyboardButton("🇸🇦 العربية", callback_data="setlang:ar")],
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang:ru"),
         InlineKeyboardButton("🇪🇸 Español", callback_data="setlang:es")],
        [InlineKeyboardButton("🇫🇷 Français", callback_data="setlang:fr"),
         InlineKeyboardButton("🇩🇪 Deutsch", callback_data="setlang:de")],
        [InlineKeyboardButton("🇹🇷 Türkçe", callback_data="setlang:tr")]
    ])

UI_TEXTS = {
    # Main Navigation
    "btn_files": {
        "en": "📂 Files & Storage", "ar": "📂 الملفات والتخزين", "ru": "📂 Файлы и диски",
        "es": "📂 Archivos y discos", "fr": "📂 Fichiers et stockage", "de": "📂 Dateien & Speicher", "tr": "📂 Dosyalar ve Depolama"
    },
    "btn_web": {
        "en": "🌐 Web & YouTube", "ar": "🌐 الويب ويوتيوب", "ru": "🌐 Веб и YouTube",
        "es": "🌐 Web y YouTube", "fr": "🌐 Web et YouTube", "de": "🌐 Web & YouTube", "tr": "🌐 Web ve YouTube"
    },
    "btn_power": {
        "en": "⚡ Power Controls", "ar": "⚡ إدارة الطاقة", "ru": "⚡ Управление питанием",
        "es": "⚡ Control de energía", "fr": "⚡ Gestion de l'alimentation", "de": "⚡ Energieoptionen", "tr": "⚡ Güç Yönetimi"
    },
    "btn_monitor": {
        "en": "📊 System Monitor", "ar": "📊 مراقبة النظام", "ru": "📊 Мониторинг системы",
        "es": "📊 Monitor del sistema", "fr": "📊 Moniteur système", "de": "📊 System-Monitor", "tr": "📊 Sistem Monitörü"
    },
    "btn_battery": {
        "en": "🔋 Battery Status", "ar": "🔋 حالة البطارية", "ru": "🔋 Состояние батареи",
        "es": "🔋 Estado de batería", "fr": "🔋 État de la batterie", "de": "🔋 Akkustatus", "tr": "🔋 Pil Durumu"
    },
    "btn_media": {
        "en": "🎛 Audio & Media", "ar": "🎛 الصوت والميديا", "ru": "🎛 Аудио и медиа",
        "es": "🎛 Audio y medios", "fr": "🎛 Audio et médias", "de": "🎛 Audio & Medien", "tr": "🎛 Ses ve Medya"
    },
    "btn_tools": {
        "en": "🛠️ Tools & Utility", "ar": "🛠️ أدوات وصيانة", "ru": "🛠️ Инструменты",
        "es": "🛠️ Herramientas", "fr": "🛠️ Outils et utilitaires", "de": "🛠️ Tools & Utility", "tr": "🛠️ Araçlar ve Hizmetler"
    },
    "btn_screen": {
        "en": "📸 Screen Capture", "ar": "📸 لقطة شاشة", "ru": "📸 Снимок экрана",
        "es": "📸 Captura de pantalla", "fr": "📸 Capture d'écran", "de": "📸 Bildschirmfoto", "tr": "📸 Ekran Görüntüsü"
    },
    "btn_webcam": {
        "en": "📷 Webcam Snapshot", "ar": "📷 لقطة كاميرا الويب", "ru": "📷 Фото с веб-камеры",
        "es": "📷 Foto de cámara web", "fr": "📷 Photo webcam", "de": "📷 Webcam-Schnappschuss", "tr": "📷 Web Kamerası"
    },
    "btn_mic": {
        "en": "🎙️ Voice Record (30s)", "ar": "🎙️ تسجيل صوتي (30ث)", "ru": "🎙️ Запись звука (30с)",
        "es": "🎙️ Grabar audio (30s)", "fr": "🎙️ Enregistrement vocal (30s)", "de": "🎙️ Sprachaufnahme (30s)", "tr": "🎙️ Ses Kaydı (30sn)"
    },
    "btn_update_app": {
        "en": "🔄 Check Updates", "ar": "🔄 فحص تحديثات التطبيق", "ru": "🔄 Проверить обновления",
        "es": "🔄 Buscar actualizaciones", "fr": "🔄 Vérifier les mises à jour", "de": "🔄 Nach Updates suchen", "tr": "🔄 Güncellemeleri Denetle"
    },
    "btn_specs": {
        "en": "🖥️ PC Hardware Specs", "ar": "🖥️ مواصفات الكمبيوتر", "ru": "🖥️ Характеристики ПК",
        "es": "🖥️ Especificaciones de PC", "fr": "🖥️ Spécifications du PC", "de": "🖥️ PC-Spezifikationen", "tr": "🖥️ PC Donanım Bilgisi"
    },
    "btn_wifi": {
        "en": "📶 Wi-Fi & Network", "ar": "📶 الواي فاي والشبكة", "ru": "📶 Wi-Fi и сеть",
        "es": "📶 Wi-Fi y red", "fr": "📶 Wi-Fi et réseau", "de": "📶 WLAN & Netzwerk", "tr": "📶 Wi-Fi ve Ağ"
    },
    "btn_ping": {
        "en": "🏓 Ping & Latency", "ar": "🏓 سرعة الاستجابة والبنغ", "ru": "🏓 Пинг и задержка",
        "es": "🏓 Ping y latencia", "fr": "🏓 Ping et latence", "de": "🏓 Ping & Latenz", "tr": "🏓 Ping ve Gecikme"
    },
    "btn_kill": {
        "en": "⚡ Kill Process", "ar": "⚡ إنهاء عملية معلقة", "ru": "⚡ Завершить процесс",
        "es": "⚡ Finalizar proceso", "fr": "⚡ Tuer un processus", "de": "⚡ Prozess beenden", "tr": "⚡ İşlemi Sonlandır"
    },
    "btn_security_guard": {
        "en": "🛡️ Security Guard Alert", "ar": "🛡️ درع الحماية والتنبيه", "ru": "🛡️ Охранная защита",
        "es": "🛡️ Guardia de seguridad", "fr": "🛡️ Alerte de sécurité", "de": "🛡️ Sicherheitsalarm", "tr": "🛡️ Güvenlik Kalkanı"
    },
    "btn_uptime": {
        "en": "⏱️ System Uptime", "ar": "⏱️ مدة تشغيل الجهاز", "ru": "⏱️ Время работы системы",
        "es": "⏱️ Tiempo de actividad", "fr": "⏱️ Temps de fonctionnement", "de": "⏱️ Systemlaufzeit", "tr": "⏱️ Çalışma Süresi"
    },
    "btn_wifi_passwords": {
        "en": "🔑 Saved Wi-Fi Passwords", "ar": "🔑 كلمات سر الواي فاي", "ru": "🔑 Пароли Wi-Fi",
        "es": "🔑 Contraseñas de Wi-Fi", "fr": "🔑 Mots de passe Wi-Fi", "de": "🔑 WLAN-Passwörter", "tr": "🔑 Kayıtlı Wi-Fi Şifreleri"
    },
    "btn_lang": {
        "en": "🌐 Language / اللغة", "ar": "🌐 تغيير اللغة / Language", "ru": "🌐 Язык / Language",
        "es": "🌐 Idioma / Language", "fr": "🌐 Langue / Language", "de": "🌐 Sprache / Language", "tr": "🌐 Dil / Language"
    },
    "btn_back": {
        "en": "🔙 Back to Main Menu", "ar": "🔙 العودة للقائمة الرئيسية", "ru": "🔙 Назад в главное меню",
        "es": "🔙 Volver al menú principal", "fr": "🔙 Retour au menu principal", "de": "🔙 Zurück zum Hauptmenü", "tr": "🔙 Ana Menüye Dön"
    },

    # Submenus - Power (with Wake-up and Unlock)
    "btn_wake": {
        "en": "☀️ Wake Up Screen", "ar": "☀️ إيقاظ شاشة الكمبيوتر", "ru": "☀️ Разбудить экран",
        "es": "☀️ Despertar pantalla", "fr": "☀️ Réveiller l'écran", "de": "☀️ Bildschirm aufwecken", "tr": "☀️ Ekranı Uyandır"
    },
    "btn_unlock": {
        "en": "🔓 Unlock PC Screen", "ar": "🔓 فتح قفل الكمبيوتر (PIN)", "ru": "🔓 Разблокировать ПК",
        "es": "🔓 Desbloquear PC", "fr": "🔓 Déverrouiller le PC", "de": "🔓 PC entsperren", "tr": "🔓 PC Kilidini Aç"
    },
    "btn_sleep": {
        "en": "😴 Sleep Mode", "ar": "😴 وضع السكون (Sleep)", "ru": "😴 Спящий режим",
        "es": "😴 Modo de suspensión", "fr": "😴 Mode veille", "de": "😴 Energiesparmodus", "tr": "😴 Uyku Modu"
    },
    "btn_screen_off": {
        "en": "🖥️ Turn Off Display Only", "ar": "🖥️ إطفاء الشاشة فقط", "ru": "🖥️ Выключить дисплей",
        "es": "🖥️ Apagar solo pantalla", "fr": "🖥️ Éteindre l'écran uniquement", "de": "🖥️ Nur Bildschirm aus", "tr": "🖥️ Yalnızca Ekranı Kapat"
    },
    "btn_shutdown": {
        "en": "🛑 Force Shutdown (10s)", "ar": "🛑 إيقاف التشغيل (خلال 10ث)", "ru": "🛑 Выключение (10с)",
        "es": "🛑 Apagado forzado (10s)", "fr": "🛑 Arrêt forcé (10s)", "de": "🛑 Herunterfahren erzwingen (10s)", "tr": "🛑 Kapatmayı Zorla (10sn)"
    },
    "btn_cancel_shutdown": {
        "en": "❌ Cancel Shutdown", "ar": "❌ إلغاء إيقاف التشغيل", "ru": "❌ Отмена выключения",
        "es": "❌ Cancelar apagado", "fr": "❌ Annuler l'arrêt", "de": "❌ Herunterfahren abbrechen", "tr": "❌ Kapatmayı İptal Et"
    },
    "btn_restart": {
        "en": "🔄 Restart PC", "ar": "🔄 إعادة تشغيل الكمبيوتر", "ru": "🔄 Перезагрузить ПК",
        "es": "🔄 Reiniciar PC", "fr": "🔄 Redémarrer le PC", "de": "🔄 PC neustarten", "tr": "🔄 Bilgisayarı Yeniden Başlat"
    },
    "btn_lock": {
        "en": "🔒 Lock Screen", "ar": "🔒 قفل شاشة الكمبيوتر", "ru": "🔒 Заблокировать экран",
        "es": "🔒 Bloquear pantalla", "fr": "🔒 Verrouiller l'écran", "de": "🔒 Bildschirm sperren", "tr": "🔒 Ekranı Kilitle"
    },

    # Submenus - Files
    "btn_desktop": {
        "en": "🖥️ Desktop Folder", "ar": "🖥️ مجلد سطح المكتب", "ru": "🖥️ Рабочий стол",
        "es": "🖥️ Carpeta Escritorio", "fr": "🖥️ Dossier Bureau", "de": "🖥️ Desktop", "tr": "🖥️ Masaüstü"
    },
    "btn_downloads": {
        "en": "📥 Downloads Folder", "ar": "📥 مجلد التنزيلات", "ru": "📥 Загрузки",
        "es": "📥 Descargas", "fr": "📥 Téléchargements", "de": "📥 Downloads", "tr": "📥 İndirilenler"
    },
    "btn_documents": {
        "en": "📄 Documents Folder", "ar": "📄 مجلد المستندات", "ru": "📄 Документы",
        "es": "📄 Documentos", "fr": "📄 Documents", "de": "📄 Dokumente", "tr": "📄 Belgeler"
    },
    "btn_drives": {
        "en": "💾 All Storage Drives", "ar": "💾 كافة الأقراص والتخزين", "ru": "💾 Все диски",
        "es": "💾 Todas las unidades", "fr": "💾 Tous les disques", "de": "💾 Alle Festplatten", "tr": "💾 Tüm Sürücüler"
    },
    "btn_search": {
        "en": "🔎 System-Wide Search", "ar": "🔎 بحث شامل في الملفات", "ru": "🔎 Поиск по файлам",
        "es": "🔎 Búsqueda general", "fr": "🔎 Recherche sur tout le système", "de": "🔎 Systemweite Suche", "tr": "🔎 Sistem Çapında Arama"
    },
    "btn_scan_bin": {
        "en": "🗑️ Scan Recycle Bin", "ar": "🗑️ فحص سلة المحذوفات", "ru": "🗑️ Сканировать корзину",
        "es": "🗑️ Escanear papelera", "fr": "🗑️ Analyser la corbeille", "de": "🗑️ Papierkorb scannen", "tr": "🗑️ Geri Dönüşümü Tara"
    },
    "btn_empty_bin": {
        "en": "🗑️ Empty Recycle Bin", "ar": "🗑️ إفراغ سلة المحذوفات", "ru": "🗑️ Очистить корзину",
        "es": "🗑️ Vaciar papelera", "fr": "🗑️ Vider la corbeille", "de": "🗑️ Papierkorb leeren", "tr": "🗑️ Geri Dönüşümü Boşalt"
    },

    # Submenus - Web
    "btn_chrome": {
        "en": "🌐 Open Google Chrome", "ar": "🌐 فتح متصفح جوجل كروم", "ru": "🌐 Открыть Chrome",
        "es": "🌐 Abrir Google Chrome", "fr": "🌐 Ouvrir Google Chrome", "de": "🌐 Google Chrome öffnen", "tr": "🌐 Google Chrome'u Aç"
    },
    "btn_youtube": {
        "en": "▶️ Open YouTube", "ar": "▶️ فتح موقع يوتيوب", "ru": "▶️ Открыть YouTube",
        "es": "▶️ Abrir YouTube", "fr": "▶️ Ouvrir YouTube", "de": "▶️ YouTube öffnen", "tr": "▶️ YouTube'u Aç"
    },
    "btn_play_yt": {
        "en": "🎵 Direct YouTube Play", "ar": "🔍🎵 تشغيل وبحث مباشر في يوتيوب", "ru": "🎵 Прямое воспроизведение YouTube",
        "es": "🎵 Reproducción directa de YouTube", "fr": "🎵 Lecture directe YouTube", "de": "🎵 YouTube Direktwiedergabe", "tr": "🎵 Doğrudan YouTube Oynat"
    },
    "btn_google_search": {
        "en": "🔍 Search Google", "ar": "🔍 بحث في محرك جوجل", "ru": "🔍 Поиск в Google",
        "es": "🔍 Buscar en Google", "fr": "🔍 Rechercher sur Google", "de": "🔍 In Google suchen", "tr": "🔍 Google'da Ara"
    },

    # Submenus - Monitor
    "btn_cpu_ram": {
        "en": "📈 CPU & RAM Status", "ar": "📈 استهلاك المعالج والذاكرة", "ru": "📈 Статус CPU и RAM",
        "es": "📈 Estado de CPU y RAM", "fr": "📈 État processeur et RAM", "de": "📈 CPU- & RAM-Status", "tr": "📈 CPU ve RAM Durumu"
    },
    "btn_processes": {
        "en": "📋 Top Running Processes", "ar": "📋 أكثر البرامج استهلاكاً", "ru": "📋 Список процессов",
        "es": "📋 Procesos principales", "fr": "📋 Principaux processus", "de": "📋 Top-Prozesse", "tr": "📋 Çalışan En Çok Süreçler"
    },
    "btn_network_ip": {
        "en": "🌐 Network & IP Info", "ar": "🌐 معلومات الشبكة والـ IP", "ru": "🌐 Сеть и IP",
        "es": "🌐 Información de red e IP", "fr": "🌐 Infos réseau et IP", "de": "🌐 Netzwerk- & IP-Info", "tr": "🌐 Ağ ve IP Bilgisi"
    },

    # Submenus - Media
    "btn_photos": {
        "en": "🖼 Browse Photos", "ar": "🖼 استعراض أحدث الصور", "ru": "🖼 Обзор фото",
        "es": "🖼 Explorar fotos", "fr": "🖼 Parcourir les photos", "de": "🖼 Fotos durchsuchen", "tr": "🖼 Fotoğraflara Göz At"
    },
    "btn_audio": {
        "en": "🎵 Audio Files", "ar": "🎵 استعراض ملفات الصوت", "ru": "🎵 Аудиофайлы",
        "es": "🎵 Archivos de audio", "fr": "🎵 Fichiers audio", "de": "🎵 Audiodateien", "tr": "🎵 Ses Dosyaları"
    },
    "btn_vol_up": {
        "en": "🔊 Volume Up (+10%)", "ar": "🔊 رفع الصوت (+10%)", "ru": "🔊 Громкость (+10%)",
        "es": "🔊 Subir volumen (+10%)", "fr": "🔊 Augmenter le volume (+10%)", "de": "🔊 Lauter (+10%)", "tr": "🔊 Sesi Yükselt (+10%)"
    },
    "btn_vol_down": {
        "en": "🔉 Volume Down (-10%)", "ar": "🔉 خفض الصوت (-10%)", "ru": "🔉 Громкость (-10%)",
        "es": "🔉 Bajar volumen (-10%)", "fr": "🔉 Diminuer le volume (-10%)", "de": "🔉 Leiser (-10%)", "tr": "🔉 Sesi Azalt (-10%)"
    },
    "btn_play_pause": {
        "en": "⏸️ Play / Pause", "ar": "⏸️ تشغيل / إيقاف مؤقت", "ru": "⏸️ Плей / Пауза",
        "es": "⏸️ Reproducir / Pausar", "fr": "⏸️ Lecture / Pause", "de": "⏸️ Wiedergabe / Pause", "tr": "⏸️ Oynat / Duraklat"
    },
    "btn_mute": {
        "en": "🔇 Toggle Mute", "ar": "🔇 كتم / تفعيل الصوت", "ru": "🔇 Без звука",
        "es": "🔇 Alternar silencio", "fr": "🔇 Couper le son", "de": "🔇 Stummschaltung", "tr": "🔇 Sesi Aç/Kapat"
    },

    # Submenus - Tools
    "btn_autoclip": {
        "en": "📋 Live Clipboard: ON/OFF", "ar": "📋 مزامنة الحافظة: تشغيل/إيقاف", "ru": "📋 Авто-буфер: ВКЛ/ВЫКЛ",
        "es": "📋 Portapapeles en vivo: SÍ/NO", "fr": "📋 Presse-papiers direct: ON/OFF", "de": "📋 Live-Zwischenablage: AN/AUS", "tr": "📋 Canlı Pano: AÇIK/KAPALI"
    },
    "btn_read_clip": {
        "en": "📋 Read PC Clipboard", "ar": "📋 قراءة حافظة الكمبيوتر", "ru": "📋 Прочитать буфер",
        "es": "📋 Leer portapapeles", "fr": "📋 Lire le presse-papiers", "de": "📋 Zwischenablage lesen", "tr": "📋 Panoyu Oku"
    },
    "btn_send_alert": {
        "en": "📢 Send Screen Alert", "ar": "📢 إرسال رسالة منبثقة للشاشة", "ru": "📢 Сообщение на экран",
        "es": "📢 Enviar alerta de pantalla", "fr": "📢 Alerte à l'écran", "de": "📢 Bildschirmnachricht", "tr": "📢 Ekrana Uyarı Gönder"
    },
    "btn_tts": {
        "en": "🗣 Text-to-Speech", "ar": "🗣 نطق صوتي عبر السماعات", "ru": "🗣 Озвучить текст",
        "es": "🗣 Texto a voz", "fr": "🗣 Synthèse vocale", "de": "🗣 Text-zu-Sprache", "tr": "🗣 Metni Sese Çevir"
    },
    "btn_cmd": {
        "en": "⚡ Run CMD Command", "ar": "⚡ تنفيذ أمر في موجه الأوامر", "ru": "⚡ Выполнить команду CMD",
        "es": "⚡ Ejecutar comando CMD", "fr": "⚡ Exécuter une commande CMD", "de": "⚡ CMD-Befehl ausführen", "tr": "⚡ CMD Komutu Çalıştır"
    },
    "btn_clean_temp": {
        "en": "🧹 Clean Temp Files", "ar": "🧹 تنظيف الملفات المؤقتة", "ru": "🧹 Очистка временных файлов",
        "es": "🧹 Limpiar archivos temporales", "fr": "🧹 Nettoyer fichiers temporaires", "de": "🧹 Temp-Dateien löschen", "tr": "🧹 Geçici Dosyaları Temizle"
    },
    "btn_apps": {
        "en": "🖥️ PC Applications", "ar": "🖥️ تطبيقات الكمبيوتر", "ru": "🖥️ Приложения ПК",
        "es": "🖥️ Aplicaciones de PC", "fr": "🖥️ Applications PC", "de": "🖥️ PC-Anwendungen", "tr": "🖥️ Bilgisayar Uygulamaları"
    },
    "btn_cmd_py": {
        "en": "⚡ Terminal & Python", "ar": "⚡ أوامر وموجه بايثون", "ru": "⚡ Терминаل и Python",
        "es": "⚡ Terminal y Python", "fr": "⚡ Terminal et Python", "de": "⚡ Terminal und Python", "tr": "⚡ Terminal ve Python"
    },
    "btn_smart_network": {
        "en": "🌐 Smart Network Auto-Heal", "ar": "🌐 صيانة وفحص الشبكة الذاتي", "ru": "🌐 Автовосстановление сети",
        "es": "🌐 Auto-reparación de red", "fr": "🌐 Réparation auto du réseau", "de": "🌐 Netzwerk-Selbstreparatur", "tr": "🌐 Akıllı Ağ Onarımı"
    },
    "btn_hide_menu": {
        "en": "🔽 Hide Menu (Fold)", "ar": "🔽 طي وإخفاء القائمة", "ru": "🔽 Скрыть меню",
        "es": "🔽 Plegar menú", "fr": "🔽 Réduire le menu", "de": "🔽 Menü einklappen", "tr": "🔽 Menüyü Gizle"
    },
    "btn_calc": {
        "en": "🧮 Open Calculator", "ar": "🧮 فتح الآلة الحاسبة", "ru": "🧮 Открыть калькулятор",
        "es": "🧮 Abrir calculadora", "fr": "🧮 Ouvrir la calculatrice", "de": "🧮 Rechner öffnen", "tr": "🧮 Hesap Makinesini Aç"
    },

    # Prompts & Titles
    "menu_main_title": {
        "en": "Main Dashboard", "ar": "القائمة الرئيسية للتحكم", "ru": "Главное меню управления",
        "es": "Panel de control principal", "fr": "Tableau de bord principal", "de": "Hauptmenü", "tr": "Ana Kontrol Paneli"
    },
    "pc_connected_ready": {
        "en": "PC `{node}` is Online & Ready ✅", "ar": "الكمبيوتر `{node}` متصل وجاهز للأوامر ✅", "ru": "ПК `{node}` в сети и готов ✅",
        "es": "PC `{node}` en línea y listo ✅", "fr": "Le PC `{node}` est en ligne et prêt ✅", "de": "PC `{node}` ist online & bereit ✅", "tr": "PC `{node}` Çevrimiçi ve Hazır ✅"
    },
    "select_from_menu": {
        "en": "Select an action from the keyboard below:", "ar": "اختر أي قسم أو وظيفة من القائمة أدناه:", "ru": "Выберите действие из меню ниже:",
        "es": "Seleccione una acción del menú:", "fr": "Sélectionnez une action dans le menu ci-dessous :", "de": "Wählen Sie eine Aktion aus:", "tr": "Aşağıdaki menüden bir işlem seçin:"
    },
    "lang_changed_msg": {
        "en": "Language set to English 🇬🇧", "ar": "تم ضبط اللغة إلى العربية 🇸🇦", "ru": "Язык установлен на Русский 🇷🇺",
        "es": "Idioma cambiado a Español 🇪🇸", "fr": "Langue définie sur Français 🇫🇷", "de": "Sprache auf Deutsch eingestellt 🇩🇪", "tr": "Dil Türkçe olarak ayarlandı 🇹🇷"
    }
}

def B(key: str, lang: str = "en") -> str:
    item = UI_TEXTS.get(key, {})
    return item.get(lang) or item.get("en") or key

# Reverse mapping for instant multi-language dispatch
TEXT_TO_ACTION = {}
for act_id, translations in UI_TEXTS.items():
    for l_code, text_val in translations.items():
        TEXT_TO_ACTION[text_val.lower().strip()] = act_id
        no_emoji = "".join([c for c in text_val if c.isalnum() or c.isspace()]).strip().lower()
        if no_emoji:
            TEXT_TO_ACTION[no_emoji] = act_id

# Direct aliases
ALIASES = {
    "/start": "btn_back", "/menu": "btn_back", "menu": "btn_back", "start": "btn_back", "back": "btn_back", "عودة": "btn_back", "الرئيسية": "btn_back",
    "/screen": "btn_screen", "/screenshot": "btn_screen", "screenshot": "btn_screen", "سكرين": "btn_screen", "شاشة": "btn_screen", "لقطة": "btn_screen",
    "/webcam": "btn_webcam", "/cam": "btn_webcam", "webcam": "btn_webcam", "كاميرا": "btn_webcam", "كام": "btn_webcam", "camera": "btn_webcam",
    "/mic": "btn_mic", "/record": "btn_mic", "record": "btn_mic", "voice": "btn_mic", "تسجيل": "btn_mic", "صوت": "btn_mic", "مايك": "btn_mic",
    "/specs": "btn_specs", "/hw": "btn_specs", "specs": "btn_specs", "مواصفات": "btn_specs", "عتاد": "btn_specs", "hardware": "btn_specs",
    "/wifi": "btn_wifi", "/network": "btn_wifi", "wifi": "btn_wifi", "وايفاي": "btn_wifi", "شبكة": "btn_wifi", "وايرلس": "btn_wifi",
    "/ping": "btn_ping", "ping": "btn_ping", "بنغ": "btn_ping", "latency": "btn_ping", "lag": "btn_ping",
    "/kill": "btn_kill", "kill": "btn_kill", "انهاء": "btn_kill", "إيقاف": "btn_kill", "process": "btn_kill",
    "/lang": "btn_lang", "lang": "btn_lang", "language": "btn_lang", "لغة": "btn_lang", "اللغة": "btn_lang",
    "/battery": "btn_battery", "battery": "btn_battery", "بطارية": "btn_battery", "شحن": "btn_battery",
    "/wake": "btn_wake", "wake": "btn_wake", "إيقاظ": "btn_wake", "ايقاظ": "btn_wake",
    "/guard": "btn_security_guard", "/security": "btn_security_guard", "guard": "btn_security_guard", "حماية": "btn_security_guard", "أمان": "btn_security_guard", "امان": "btn_security_guard", "درع": "btn_security_guard",
    "/uptime": "btn_uptime", "uptime": "btn_uptime", "تشغيل": "btn_uptime", "مدة": "btn_uptime", "مدة التشغيل": "btn_uptime",
    "/wifipass": "btn_wifi_passwords", "/wifi_keys": "btn_wifi_passwords", "wifipass": "btn_wifi_passwords", "باسورد": "btn_wifi_passwords", "كلمة السر": "btn_wifi_passwords", "كلمات السر": "btn_wifi_passwords",
    "/play": "btn_play_yt", "/yt": "btn_play_yt", "play": "btn_play_yt", "شغل": "btn_play_yt", "تشغيل يوتيوب": "btn_play_yt",
    "/apps": "btn_apps", "apps": "btn_apps", "برامج": "btn_apps", "تطبيقات": "btn_apps",
    "/cmd_py": "btn_cmd_py", "/py": "btn_cmd_py", "python": "btn_cmd_py", "بايثون": "btn_cmd_py", "اوامر": "btn_cmd_py",
    "/heal": "btn_smart_network", "/netheal": "btn_smart_network", "شبكة ذكية": "btn_smart_network", "اصلاح الشبكة": "btn_smart_network",
    "/hide": "btn_hide_menu", "hide": "btn_hide_menu", "طي": "btn_hide_menu", "اخفاء": "btn_hide_menu", "إخفاء": "btn_hide_menu",
    "/update": "btn_update_app", "update": "btn_update_app", "تحديث": "btn_update_app", "ترقية": "btn_update_app",
    "files": "btn_files", "ملفات": "btn_files", "الملفات": "btn_files",
    "web": "btn_web", "كروم": "btn_web", "يوتيوب": "btn_web",
    "power": "btn_power", "طاقة": "btn_power", "إدارة الطاقة": "btn_power",
    "monitor": "btn_monitor", "مراقبة": "btn_monitor",
    "media": "btn_media", "ميديا": "btn_media",
    "tools": "btn_tools", "أدوات": "btn_tools", "صيانة": "btn_tools"
}
for k, v in ALIASES.items():
    TEXT_TO_ACTION[k.lower()] = v

# ----------------- Windows Autostart Management -----------------
def set_windows_autostart(enable=True):
    try:
        startup_dir = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
        bat_path = os.path.join(startup_dir, "PCRemotePro_AutoStart.bat")
        vbs_path = os.path.join(startup_dir, "PCRemotePro_AutoStart.vbs")

        if enable:
            if getattr(sys, 'frozen', False):
                exe_path = sys.executable
                cmd_line = f'"{exe_path}" --silent'
            else:
                py_exe = sys.executable
                script_path = os.path.abspath(__file__)
                cmd_line = f'"{py_exe}" "{script_path}" --silent'

            escaped_cmd = cmd_line.replace('"', '""')
            vbs_content = f'CreateObject("Wscript.Shell").Run "{escaped_cmd}", 0, False\n'
            with open(vbs_path, "w", encoding="utf-8") as f:
                f.write(vbs_content)

            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
                winreg.SetValueEx(key, "PCRemoteProController", 0, winreg.REG_SZ, cmd_line)
                winreg.CloseKey(key)
            except Exception:
                pass
        else:
            if os.path.exists(vbs_path):
                os.remove(vbs_path)
            if os.path.exists(bat_path):
                os.remove(bat_path)
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
                winreg.DeleteValue(key, "PCRemoteProController")
                winreg.CloseKey(key)
            except Exception:
                pass
    except Exception:
        pass

# ----------------- Bulletproof Dual-Backup JSON Config Storage -----------------
CONFIG_JSON_PATH = os.path.join(DATA_DIR, "accounts_config.json")
CONFIG_BACKUP_PATH = os.path.join(DATA_DIR, "accounts_config.json.bak")
LOCAL_BACKUP_PATH = os.path.join(BASE_DIR, "accounts_config.json.bak")
LEGACY_ENC_PATH = os.path.join(DATA_DIR, "secure_config.enc")

def get_machine_key():
    try:
        raw = platform.node() + os.environ.get("COMPUTERNAME", "") + "PCRemotePro_Secret_Key_v3"
        return hashlib.sha256(raw.encode()).digest()
    except Exception:
        return b"12345678901234567890123456789012"

def xor_crypt(data: bytes, key: bytes) -> bytes:
    key_len = len(key)
    return bytes([b ^ key[i % key_len] for i, b in enumerate(data)])

def save_config(accounts_list, autostart=True, security_guard=None):
    global SECURITY_GUARD_SETTINGS
    if security_guard is not None:
        SECURITY_GUARD_SETTINGS = security_guard
    data = {
        "accounts": accounts_list,
        "autostart": autostart,
        "security_guard": SECURITY_GUARD_SETTINGS,
        "saved_at": time.time(),
        "version": APP_VERSION
    }
    raw_json = json.dumps(data, indent=2, ensure_ascii=False)

    # 1. Atomic write to primary and backup JSON files
    targets = [CONFIG_JSON_PATH, CONFIG_BACKUP_PATH, LOCAL_BACKUP_PATH]
    for target in targets:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            tmp_target = target + f".tmp_{os.getpid()}"
            with open(tmp_target, "w", encoding="utf-8") as f:
                f.write(raw_json)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            if os.path.exists(target):
                try:
                    os.remove(target)
                except Exception:
                    pass
            os.replace(tmp_target, target)
        except Exception:
            try:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(raw_json)
            except Exception:
                pass

    # 2. Encrypted fallback
    try:
        enc_data = xor_crypt(raw_json.encode('utf-8'), get_machine_key())
        with open(LEGACY_ENC_PATH, "wb") as f:
            f.write(enc_data)
    except Exception:
        pass

def load_config():
    global SECURITY_GUARD_SETTINGS
    targets = [CONFIG_JSON_PATH, CONFIG_BACKUP_PATH, LOCAL_BACKUP_PATH]
    for target in targets:
        if os.path.exists(target):
            try:
                with open(target, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    data = json.loads(content)
                    if isinstance(data, dict) and "accounts" in data:
                        if "security_guard" in data and isinstance(data["security_guard"], dict):
                            SECURITY_GUARD_SETTINGS.update(data["security_guard"])
                        return data
            except Exception:
                continue

    if os.path.exists(LEGACY_ENC_PATH):
        try:
            with open(LEGACY_ENC_PATH, "rb") as f:
                enc = f.read()
            dec = xor_crypt(enc, get_machine_key()).decode('utf-8')
            data = json.loads(dec)
            if isinstance(data, dict) and "accounts" in data:
                if "security_guard" in data and isinstance(data["security_guard"], dict):
                    SECURITY_GUARD_SETTINGS.update(data["security_guard"])
                save_config(data.get("accounts", []), autostart=data.get("autostart", True))
                return data
        except Exception:
            pass

    return {"accounts": [], "autostart": True}

# ----------------- Ultra-HD Multi-Monitor Screenshot Capture -----------------
def capture_screen_ultra_hd():
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    try:
        SM_XVIRTUALSCREEN = 76
        SM_YVIRTUALSCREEN = 77
        SM_CXVIRTUALSCREEN = 78
        SM_CYVIRTUALSCREEN = 79

        left = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        top = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

        if width <= 0 or height <= 0:
            left, top = 0, 0
            width = ctypes.windll.user32.GetSystemMetrics(0)
            height = ctypes.windll.user32.GetSystemMetrics(1)

        bbox = (left, top, left + width, top + height)
        img = ImageGrab.grab(bbox=bbox, all_screens=True)
    except Exception:
        img = ImageGrab.grab()
        width, height = img.size

    now_ts = int(time.time() * 1000)
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, f"screen_ultrahd_{now_ts}.png")
    img.save(output_path, "PNG", optimize=True)
    return output_path, width, height

# ----------------- Standout Feature 1: HD Webcam Snapshot -----------------
def capture_webcam_photo() -> tuple[bool, str]:
    """Captures an instant HD snapshot using OpenCV from the default webcam."""
    try:
        import cv2
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return False, "No active webcam detected on this PC or camera is in use."

        for _ in range(5):
            cap.read()

        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            return False, "Failed to capture image frame from webcam."

        out_path = os.path.join(tempfile.gettempdir(), f"webcam_{int(time.time())}.jpg")
        cv2.imwrite(out_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return True, out_path
    except Exception as e:
        return False, f"Webcam error: {e}"

# ----------------- Standout Feature 2: Native Microphone Audio Recording (Up to 120s) -----------------
def record_microphone_audio(seconds: int = 30) -> tuple[bool, str]:
    """Records ambient sound from the PC microphone using Windows winmm API (zero external deps)."""
    seconds = max(5, min(int(seconds), 120))
    out_wav = os.path.join(tempfile.gettempdir(), f"mic_{int(time.time())}.wav")
    try:
        winmm = ctypes.windll.winmm
        winmm.mciSendStringW("close mic_rec", None, 0, 0)
        winmm.mciSendStringW("open new type waveaudio alias mic_rec", None, 0, 0)
        winmm.mciSendStringW("set mic_rec bitspersample 16 channels 1 samplespersec 16000", None, 0, 0)
        winmm.mciSendStringW("record mic_rec", None, 0, 0)
        time.sleep(seconds)
        winmm.mciSendStringW("stop mic_rec", None, 0, 0)
        winmm.mciSendStringW(f'save mic_rec "{out_wav}"', None, 0, 0)
        winmm.mciSendStringW("close mic_rec", None, 0, 0)

        if os.path.exists(out_wav) and os.path.getsize(out_wav) > 1000:
            return True, out_wav
        return False, "Microphone recording produced empty audio or mic is muted."
    except Exception as e:
        return False, f"Microphone error: {e}"

# ----------------- Standout Feature 3: Wake-Up PC & Unlock Workstation -----------------
def wake_up_pc():
    """Wakes up the PC display from sleep/standby and keeps it awake."""
    try:
        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
        # Nudge mouse
        ctypes.windll.user32.mouse_event(0x0001, 2, 2, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.mouse_event(0x0001, -2, -2, 0, 0)
        # Tap Shift
        VK_SHIFT = 0x10
        ctypes.windll.user32.keybd_event(VK_SHIFT, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_SHIFT, 0, 2, 0)
        return True
    except Exception:
        return False

# ----------------- Standout Feature 3: Wake-Up PC & Wallpaper -----------------
def set_desktop_wallpaper(image_path: str) -> bool:
    """Sets any image as the Windows Desktop Wallpaper instantly."""
    try:
        # SPI_SETDESKWALLPAPER = 20, SPIF_UPDATEINIFILE = 1, SPIF_SENDCHANGE = 2
        abs_p = os.path.abspath(image_path)
        res = ctypes.windll.user32.SystemParametersInfoW(20, 0, abs_p, 3)
        return bool(res)
    except Exception:
        return False

# ----------------- Standout Feature 4: Full Hardware & OS Specs -----------------
def get_cpu_name():
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        winreg.CloseKey(key)
        return name.strip()
    except Exception:
        return platform.processor() or "Unknown CPU"

def get_gpu_name():
    try:
        cmd = 'powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"'
        out = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        return ", ".join(lines) if lines else "Integrated Graphics"
    except Exception:
        return "Standard Graphics Adapter"

def get_full_pc_specs(lang: str = "en") -> str:
    cpu = get_cpu_name()
    cores_phys = psutil.cpu_count(logical=False) or 0
    cores_log = psutil.cpu_count(logical=True) or 0
    ram = psutil.virtual_memory()
    total_ram_gb = round(ram.total / (1024**3), 2)
    avail_ram_gb = round(ram.available / (1024**3), 2)
    used_ram_gb = round(ram.used / (1024**3), 2)
    gpu = get_gpu_name()
    os_name = f"{platform.system()} {platform.release()} (Build {platform.version()})"
    boot_time = time.strftime('%Y-%m-%d %I:%M %p', time.localtime(psutil.boot_time()))

    c_drive = psutil.disk_usage("C:\\") if os.path.exists("C:\\") else None
    c_info = f"`{round(c_drive.free / (1024**3), 1)} GB Free / {round(c_drive.total / (1024**3), 1)} GB`" if c_drive else "N/A"

    return (
        f"🖥️ **PC Hardware & System Specs:**\n\n"
        f"💻 **Device:** `{platform.node()}`\n"
        f"🪟 **OS:** `{os_name}`\n"
        f"⚙️ **Architecture:** `{platform.machine()}`\n\n"
        f"🧠 **Processor (CPU):**\n  └ `{cpu}`\n  └ Cores: `{cores_phys} Physical` | `{cores_log} Threads`\n\n"
        f"🎮 **Graphics (GPU):**\n  └ `{gpu}`\n\n"
        f"💾 **RAM Memory:**\n  └ Total: `{total_ram_gb} GB` (Used: `{used_ram_gb} GB` | Free: `{avail_ram_gb} GB`)\n\n"
        f"💽 **Primary Disk (C:):**\n  └ {c_info}\n\n"
        f"⏱️ **System Uptime:** Since `{boot_time}`"
    )

# ----------------- Standout Feature 5: Wi-Fi & Network Diagnostics -----------------
def get_wifi_and_network_info(lang: str = "en") -> str:
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        try: local_ip = socket.gethostbyname(socket.gethostname())
        except Exception: pass

    wifi_ssid = "Ethernet / Cable"
    signal_str = "N/A"
    try:
        cmd = 'netsh wlan show interfaces'
        out = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            line_str = line.strip()
            if line_str.startswith("SSID") and not line_str.startswith("BSSID"):
                wifi_ssid = line_str.split(":", 1)[1].strip()
            elif line_str.startswith("Signal"):
                signal_str = line_str.split(":", 1)[1].strip()
    except Exception:
        pass

    try:
        public_ip = urllib.request.urlopen('https://api.ipify.org', timeout=3).read().decode('utf8')
    except Exception:
        public_ip = "Unknown / Offline"

    return (
        f"📶 **Wi-Fi & Network Diagnostics:**\n\n"
        f"📡 **Wi-Fi SSID:** `{wifi_ssid}`\n"
        f"📶 **Signal Quality:** `{signal_str}`\n"
        f"🏠 **Local IPv4:** `{local_ip}`\n"
        f"🌍 **Public IPv4:** `{public_ip}`\n"
        f"💻 **Host Name:** `{platform.node()}`"
    )

# ----------------- Standout Feature 6: Network Ping & Latency Diagnostics -----------------
def test_network_latency() -> str:
    targets = [("Google DNS", "8.8.8.8"), ("Cloudflare DNS", "1.1.1.1")]
    results = []
    for name, host in targets:
        try:
            cmd = f"ping -n 2 -w 1000 {host}"
            out = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
            avg = "N/A"
            for line in out.splitlines():
                if "Average =" in line or "المتوسط =" in line or "Moyenne =" in line:
                    avg = line.split("=")[-1].strip()
            results.append(f"🌐 **{name} (`{host}`):** `{avg}`")
        except Exception:
            results.append(f"🌐 **{name} (`{host}`):** `Timeout / Error`")
    return "🏓 **Ping & Latency Diagnostic Results:**\n\n" + "\n".join(results)

# ----------------- Standout Feature: Saved Wi-Fi Passwords & Keys -----------------
def get_saved_wifi_passwords(search_query: str = None, lang: str = "ar", show_all: bool = False) -> str:
    """Retrieves saved Wi-Fi profiles and passwords stored on this Windows PC using netsh wlan."""
    try:
        proc = subprocess.run(['netsh', 'wlan', 'show', 'profiles'], capture_output=True, timeout=5)
        raw = proc.stdout
        text = ''
        for enc in ['cp1256', 'utf-8', 'cp850', 'latin-1']:
            try:
                text = raw.decode(enc)
                break
            except Exception:
                pass

        profiles = []
        for line in text.splitlines():
            if ':' in line and any(k in line.lower() for k in ['profile', 'ملف', 'user']):
                pname = line.split(':', 1)[1].strip()
                if pname and pname not in profiles and not pname.lower().startswith('group policy'):
                    profiles.append(pname)

        if not profiles:
            return "⚠️ لم يتم العثور على أي شبكات واي فاي محفوظة على هذا الجهاز." if lang == "ar" else "⚠️ No saved Wi-Fi networks found on this PC."

        if search_query:
            sq = search_query.strip().lower()
            filtered = [p for p in profiles if sq in p.lower()]
            if not filtered:
                return (
                    f"❌ لم يتم العثور على أي شبكة واي فاي مطابقة لـ: `{search_query}`\n\n"
                    f"💡 للتأكد من أسماء الشبكات، أرسل أمر `/wifipass` لعرض الشبكات المتوفرة."
                    if lang == "ar" else
                    f"❌ No saved Wi-Fi profile matched: `{search_query}`"
                )
            profiles_to_show = filtered
        elif show_all:
            profiles_to_show = profiles[:30]
        else:
            profiles_to_show = profiles[:7]

        results = []
        for name in profiles_to_show:
            key_proc = subprocess.run(['netsh', 'wlan', 'show', 'profile', f'name={name}', 'key=clear'], capture_output=True, timeout=4)
            ktext = ''
            for enc in ['cp1256', 'utf-8', 'cp850', 'latin-1']:
                try:
                    ktext = key_proc.stdout.decode(enc)
                    break
                except Exception:
                    pass
            m = re.search(r'(?:Key Content|محتوى المفتاح)\s*:\s*(.*)', ktext)
            pwd = m.group(1).strip() if m else ("مفتوحة / بدون كلمة سر" if lang == "ar" else "Open / No Password")
            results.append((name, pwd))

        if lang == "ar":
            header = f"🔑 **شبكات الواي فاي وكلمات المرور المحفوظة ({len(profiles_to_show)} من {len(profiles)}):**\n\n"
            items = [f"📶 **الشبكة:** `{ssid}`\n🔐 **كلمة السر:** `{pwd}`" for ssid, pwd in results]
            footer = "\n\n💡 _للبحث عن كلمة سر شبكة معينة، أرسل:_\n`/wifipass اسم_الشبكة`" if not search_query and not show_all else ""
        else:
            header = f"🔑 **Saved Wi-Fi Profiles & Passwords ({len(profiles_to_show)} of {len(profiles)}):**\n\n"
            items = [f"📶 **SSID:** `{ssid}`\n🔐 **Key:** `{pwd}`" for ssid, pwd in results]
            footer = "\n\n💡 _Search specific network password:_\n`/wifipass <SSID>`" if not search_query and not show_all else ""

        return header + "\n───────────────\n".join(items) + footer
    except Exception as e:
        return f"⚠️ خطأ أثناء استخراج كلمات مرور الواي فاي: {e}"

# ----------------- Standout Feature: System Uptime Diagnostic -----------------
def get_system_uptime(lang: str = "en") -> str:
    """Calculates and returns exact system uptime, boot time, and security status."""
    boot_timestamp = psutil.boot_time()
    boot_dt = datetime.fromtimestamp(boot_timestamp)
    now = datetime.now()
    uptime_seconds = max(0, int((now - boot_dt).total_seconds()))

    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    boot_str = boot_dt.strftime("%Y-%m-%d %I:%M:%S %p")
    guard_active = SECURITY_GUARD_SETTINGS.get("enabled", True)
    guard_status = "مفعّل ومحمي ✅" if guard_active else "معطّل مؤقتًا ⚠️"

    if lang == "ar":
        parts = []
        if days > 0:
            parts.append(f"{days} يوم")
        if hours > 0:
            parts.append(f"{hours} ساعة")
        if minutes > 0:
            parts.append(f"{minutes} دقيقة")
        parts.append(f"{seconds} ثانية")
        uptime_str = " و ".join(parts) if parts else "أقل من دقيقة"
        return (
            f"⏱️ **مدة تشغيل الجهاز الحالية (System Uptime):**\n\n"
            f"🟢 **الجهاز يعمل منذ:** `{uptime_str}`\n"
            f"📅 **وقت إقلاع النظام:** `{boot_str}`\n"
            f"💻 **اسم الجهاز (Host):** `{platform.node()}`\n"
            f"🛡️ **درع الحماية والتنبيه:** `{guard_status}`"
        )
    else:
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        uptime_str = " ".join(parts) if parts else "< 1m"
        guard_en = "ACTIVE ✅" if guard_active else "PAUSED ⚠️"
        return (
            f"⏱️ **PC System Uptime:**\n\n"
            f"🟢 **Running for:** `{uptime_str}`\n"
            f"📅 **Booted at:** `{boot_str}`\n"
            f"💻 **Host Name:** `{platform.node()}`\n"
            f"🛡️ **Security Guard:** `{guard_en}`"
        )

# ----------------- Security Guard Core Engine -----------------
def is_workstation_locked() -> bool:
    """Detects whether Windows workstation is currently locked without disturbing DWM."""
    try:
        desk = ctypes.windll.user32.OpenInputDesktop(0, False, 0x0001)
        if desk:
            ctypes.windll.user32.CloseDesktop(desk)
            return False
        return True
    except Exception:
        return False

def ensure_wifi_connected() -> bool:
    """Forces reconnecting to saved Wi-Fi networks if internet access is lost."""
    try:
        s = socket.create_connection(("8.8.8.8", 53), timeout=2)
        s.close()
        return True
    except OSError:
        pass

    try:
        proc = subprocess.run(
            ['netsh', 'wlan', 'show', 'profiles'],
            capture_output=True,
            timeout=3,
            creationflags=0x08000000 if sys.platform == "win32" else 0
        )
        raw = proc.stdout
        text = ''
        for enc in ['cp1256', 'utf-8', 'cp850', 'latin-1']:
            try:
                text = raw.decode(enc)
                break
            except Exception:
                pass
        profiles = []
        for line in text.splitlines():
            if ':' in line and any(k in line.lower() for k in ['profile', 'ملف', 'user']):
                pname = line.split(':', 1)[1].strip()
                if pname and pname not in profiles and not pname.lower().startswith('group policy'):
                    profiles.append(pname)

        for p in profiles[:6]:
            try:
                subprocess.run(
                    ['netsh', 'wlan', 'connect', f'name={p}'],
                    capture_output=True,
                    timeout=3,
                    creationflags=0x08000000 if sys.platform == "win32" else 0
                )
                time.sleep(2)
                s = socket.create_connection(("8.8.8.8", 53), timeout=2)
                s.close()
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False

def broadcast_security_alert(webcam_path: str = None, screen_path: str = None, caption_text: str = ""):
    """Broadcasts immediate security alert (webcam face photo + screenshot) to all registered user accounts."""
    for token, bot_entry in list(ACTIVE_BOT_REGISTRY.items()):
        app_inst = bot_entry.get("app")
        bot_loop = bot_entry.get("loop")
        user_map = bot_entry.get("user_map", {})
        if app_inst and bot_loop and not bot_loop.is_closed():
            for chat_id in user_map.keys():
                async def _send(cid=chat_id, ai=app_inst, wp=webcam_path, sp=screen_path, cap=caption_text):
                    try:
                        if wp and os.path.exists(wp):
                            with open(wp, "rb") as wf:
                                await ai.bot.send_photo(
                                    chat_id=cid,
                                    photo=wf,
                                    caption=cap,
                                    parse_mode="Markdown"
                                )
                        else:
                            await ai.bot.send_message(
                                chat_id=cid,
                                text=cap,
                                parse_mode="Markdown"
                            )

                        if sp and os.path.exists(sp):
                            with open(sp, "rb") as sf:
                                await ai.bot.send_photo(
                                    chat_id=cid,
                                    photo=sf,
                                    caption="🖥️ **لقطة شاشة لسطح المكتب أثناء فتح الجهاز / محاولة الدخول**",
                                    parse_mode="Markdown"
                                )
                    except Exception:
                        pass
                asyncio.run_coroutine_threadsafe(_send(), bot_loop)

def get_security_guard_panel(lang: str = "ar") -> tuple[str, InlineKeyboardMarkup]:
    """Generates the interactive Telegram control panel for Security Guard."""
    enabled = SECURITY_GUARD_SETTINGS.get("enabled", True)
    webcam = SECURITY_GUARD_SETTINGS.get("alert_webcam", True)
    screen = SECURITY_GUARD_SETTINGS.get("alert_screen", True)

    status_str = "مفعّل ✅" if enabled else "معطّل مؤقتًا ❌"
    cam_str = "مفعّلة ✅" if webcam else "معطّلة ❌"
    scr_str = "مفعّلة ✅" if screen else "معطّلة ❌"

    if lang == "ar":
        text = (
            f"🛡️ **مركز التحكم بدرع الحماية والتنبيه الأمني:**\n\n"
            f"• **درع الحماية الشامل:** {status_str}\n"
            f"• **التقاط صورة للوجه (Webcam):** {cam_str}\n"
            f"• **لقطة شاشة سطح المكتب (Screen):** {scr_str}\n"
            f"• **إشعار وقت المحاولة ومدة التشغيل:** مفعّل دائمًا ✅\n\n"
            f"ℹ️ _عند فتح الجهاز أو إدخال كلمة سر خاطئة، يرسل التطبيق تلقائيًا تقريرًا أمنيًا مصورًا، مع إجبار الكمبيوتر على الاتصال بالشبكة فوراً لضمان وصول التنبيه._"
        )
        kb = [
            [InlineKeyboardButton(f"{'🛑 إيقاف الحماية مؤقتًا' if enabled else '🟢 تفعيل درع الحماية'}", callback_data="guard_toggle")],
            [
                InlineKeyboardButton(f"📷 الكاميرا: {'شغالة ✅' if webcam else 'متوقفة ❌'}", callback_data="guard_cam"),
                InlineKeyboardButton(f"📸 الشاشة: {'شغالة ✅' if screen else 'متوقفة ❌'}", callback_data="guard_screen")
            ],
            [InlineKeyboardButton("🧪 تجربة إرسال تنبيه أمني الآن", callback_data="guard_test")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
        ]
    else:
        text = (
            f"🛡️ **Security Guard Control Center:**\n\n"
            f"• **Overall Guard Status:** {'ACTIVE ✅' if enabled else 'PAUSED ❌'}\n"
            f"• **Face Photo (Webcam):** {'ON ✅' if webcam else 'OFF ❌'}\n"
            f"• **Screen Capture:** {'ON ✅' if screen else 'OFF ❌'}\n"
            f"• **Uptime & Timestamp Log:** Always ON ✅\n\n"
            f"ℹ️ _When the PC is unlocked or wrong password is typed, the bot instantly sends an alert with photos and uptime status._"
        )
        kb = [
            [InlineKeyboardButton(f"{'🛑 Pause Guard' if enabled else '🟢 Enable Guard'}", callback_data="guard_toggle")],
            [
                InlineKeyboardButton(f"📷 Webcam: {'ON' if webcam else 'OFF'}", callback_data="guard_cam"),
                InlineKeyboardButton(f"📸 Screen: {'ON' if screen else 'OFF'}", callback_data="guard_screen")
            ],
            [InlineKeyboardButton("🧪 Test Security Alert Now", callback_data="guard_test")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="goto_main")]
        ]
    return text, InlineKeyboardMarkup(kb)

def update_tray_guard_status():
    """Updates tray icon tooltip to reflect current security status."""
    global MASTER_APP_INSTANCE
    if MASTER_APP_INSTANCE and hasattr(MASTER_APP_INSTANCE, 'tray_icon') and MASTER_APP_INSTANCE.tray_icon:
        try:
            enabled = SECURITY_GUARD_SETTINGS.get("enabled", True)
            tag = "🛡️ Guard Active" if enabled else "🛡️ Guard Paused"
            MASTER_APP_INSTANCE.tray_icon.title = f"{APP_NAME} - {APP_VERSION} [{tag}]"
        except Exception:
            pass

def run_security_guard_monitor():
    """Silent, non-intrusive background guard daemon. ZERO screen flicker, ZERO console spawning."""
    lock_consecutive_count = 0
    last_trigger_time = 0

    while True:
        try:
            # 10-second gentle interval prevents CPU churn and screen flicker
            time.sleep(10)
            if not SECURITY_GUARD_SETTINGS.get("enabled", True):
                lock_consecutive_count = 0
                continue

            current_time = time.time()
            now_locked = is_workstation_locked()

            if now_locked:
                lock_consecutive_count += 1
            else:
                # Workstation must have been locked for at least 2 consecutive checks (>= 20 seconds)
                # to guarantee this is a real user unlocking/opening the PC, and not an app switching focus.
                if lock_consecutive_count >= 2:
                    lock_consecutive_count = 0
                    if current_time - last_trigger_time >= 60:
                        last_trigger_time = current_time

                        # Reconnect Wi-Fi silently if needed
                        try:
                            ensure_wifi_connected()
                        except Exception:
                            pass

                        now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
                        event_title = "🔓 **تنبيه أمني: تم فتح الكمبيوتر وإلغاء القفل!**"
                        uptime_info = get_system_uptime("ar")

                        caption_ar = (
                            f"{event_title}\n\n"
                            f"⏰ **وقت المحاولة:** `{now_str}`\n"
                            f"💻 **الجهاز:** `{platform.node()}`\n\n"
                            f"{uptime_info}\n\n"
                            f"🛡️ تم التقاط صورة للوجه عبر كاميرا الويب ولقطة للشاشة فوراً."
                        )

                        webcam_path = None
                        if SECURITY_GUARD_SETTINGS.get("alert_webcam", True):
                            try:
                                ok, wpath = capture_webcam_photo()
                                if ok and os.path.exists(wpath):
                                    webcam_path = wpath
                            except Exception:
                                pass

                        screen_path = None
                        if SECURITY_GUARD_SETTINGS.get("alert_screen", True):
                            try:
                                spath, _, _ = capture_screen_ultra_hd()
                                if os.path.exists(spath):
                                    screen_path = spath
                            except Exception:
                                pass

                        broadcast_security_alert(webcam_path, screen_path, caption_ar)
                else:
                    lock_consecutive_count = 0

        except Exception:
            time.sleep(10)

# ----------------- Extended Audio Recording Engine -----------------
async def do_record_mic(msg_obj, lang: str = "en", seconds: int = 30):
    """Handles recording audio for custom durations up to 120 seconds with quick buttons."""
    seconds = max(5, min(int(seconds), 120))
    await msg_obj.reply_text(f"🎙️ Recording {seconds} seconds from PC microphone...")
    success, result = await asyncio.to_thread(record_microphone_audio, seconds)
    if success:
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
            with open(result, "rb") as voice:
                kb = [
                    [
                        InlineKeyboardButton("🎙️ 15s", callback_data="rec_sec:15"),
                        InlineKeyboardButton("🎙️ 30s", callback_data="rec_sec:30"),
                        InlineKeyboardButton("🎙️ 60s", callback_data="rec_sec:60"),
                        InlineKeyboardButton("🎙️ 90s", callback_data="rec_sec:90")
                    ],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="goto_main")]
                ]
                await msg_obj.reply_voice(
                    voice=voice,
                    caption=f"🎙️ **PC Microphone Audio ({seconds}s)**\n💻 Host: `{platform.node()}`\n⏰ Recorded: `{now_str}`",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode="Markdown"
                )
        finally:
            if os.path.exists(result):
                try: os.remove(result)
                except Exception: pass
    else:
        await msg_obj.reply_text(f"⚠️ {result}", reply_markup=get_menu_keyboard("main", lang))

# ----------------- In-App Auto-Update System (GitHub Releases) -----------------
GITHUB_REPO = "moshraheem-sudo/PC_Remote_Pro"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_API_LATEST_RELEASE = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

def parse_app_version(ver_str: str) -> tuple:
    nums = re.findall(r'\d+', str(ver_str))
    return tuple(map(int, nums)) if nums else (0,)

def check_for_github_update() -> tuple[bool, str, dict]:
    """Queries GitHub Releases API to detect if a newer release exists."""
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST_RELEASE,
            headers={"User-Agent": "PC-Remote-Pro-AutoUpdater"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        tag = data.get("tag_name", "").strip()
        remote_ver = parse_app_version(tag)
        current_ver = parse_app_version(APP_VERSION)

        if remote_ver > current_ver:
            assets = data.get("assets", [])
            chosen_asset = None
            for a in assets:
                name = a.get("name", "").lower()
                if name.endswith(".exe"):
                    chosen_asset = a
                    break
            if not chosen_asset and assets:
                chosen_asset = assets[0]

            return True, tag, {
                "tag": tag,
                "current": APP_VERSION,
                "latest": tag,
                "name": data.get("name", tag),
                "notes": data.get("body", ""),
                "asset": chosen_asset,
                "html_url": data.get("html_url", GITHUB_RELEASES_URL)
            }
        else:
            return False, APP_VERSION, {
                "tag": APP_VERSION,
                "current": APP_VERSION,
                "latest": tag or APP_VERSION
            }
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, APP_VERSION, {
                "tag": APP_VERSION,
                "current": APP_VERSION,
                "latest": APP_VERSION
            }
        return False, "Error", {"tag": APP_VERSION, "current": APP_VERSION, "latest": "Unknown"}
    except Exception:
        return False, "Error", {"tag": APP_VERSION, "current": APP_VERSION, "latest": "Unknown"}

def download_github_release_asset(download_url: str, filename: str, progress_callback=None) -> tuple[bool, str]:
    """Downloads the release asset to temp directory."""
    try:
        temp_dir = os.path.join(tempfile.gettempdir(), "PCRemotePro_Updates")
        os.makedirs(temp_dir, exist_ok=True)
        dest_path = os.path.join(temp_dir, filename)

        req = urllib.request.Request(download_url, headers={"User-Agent": "PC-Remote-Pro-AutoUpdater"})
        with urllib.request.urlopen(req, timeout=45) as response, open(dest_path, "wb") as out_file:
            total_size = response.info().get("Content-Length")
            total_size = int(total_size) if total_size else None
            downloaded = 0
            chunk_size = 65536

            while True:
                buffer = response.read(chunk_size)
                if not buffer:
                    break
                downloaded += len(buffer)
                out_file.write(buffer)
                if progress_callback and total_size:
                    progress_callback(downloaded, total_size)

        return True, dest_path
    except Exception as e:
        return False, str(e)

# ----------------- Standout Feature 7: Interactive Process Killer -----------------
def get_top_processes_for_kill():
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'memory_percent']):
        try:
            pinfo = p.info
            if pinfo['name'] and pinfo['pid'] > 4:
                procs.append(pinfo)
        except Exception:
            pass

    top = sorted(procs, key=lambda x: x['memory_percent'] or 0, reverse=True)[:8]
    kb = []
    lines = []
    for p in top:
        pid = p['pid']
        name = p['name']
        ram_pct = p['memory_percent'] or 0
        lines.append(f"🔹 `{name}` (PID: `{pid}`) - RAM: `{ram_pct:.1f}%`")
        kb.append([InlineKeyboardButton(f"❌ Kill {name[:18]} (PID: {pid})", callback_data=f"killproc:{pid}:{name[:16]}")])

    kb.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="goto_main")])
    text = "⚡ **Running Process Manager & Killer:**\nTap any button below to terminate an unresponsive program:\n\n" + "\n".join(lines)
    return text, InlineKeyboardMarkup(kb)

def kill_process_by_target(pid: int, name: str) -> tuple[bool, str]:
    try:
        p = psutil.Process(pid)
        p_name = p.name()
        p.kill()
        return True, f"✅ Process `{p_name}` (PID: {pid}) was terminated successfully."
    except psutil.NoSuchProcess:
        return False, f"⚠️ Process (PID: {pid}) has already exited."
    except Exception as e:
        return False, f"❌ Failed to terminate process: {e}"

# ----------------- Windows Power & System Actions -----------------
def execute_sleep():
    try:
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
    except Exception:
        os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")

def execute_screen_off():
    HWND_BROADCAST = 0xFFFF
    WM_SYSCOMMAND = 0x0112
    SC_MONITORPOWER = 0xF170
    MONITOR_OFF = 2
    ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF)

def execute_shutdown(seconds=10):
    os.system(f"shutdown /s /t {seconds}")

def cancel_shutdown():
    os.system("shutdown /a")

def execute_restart(seconds=10):
    os.system(f"shutdown /r /t {seconds}")

def change_volume(up=True):
    VK_VOLUME_UP = 0xAF
    VK_VOLUME_DOWN = 0xAE
    KEYEVENTF_KEYUP = 0x0002
    vk = VK_VOLUME_UP if up else VK_VOLUME_DOWN
    for _ in range(5):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

def toggle_audio_mute():
    VK_VOLUME_MUTE = 0xAD
    KEYEVENTF_KEYUP = 0x0002
    ctypes.windll.user32.keybd_event(VK_VOLUME_MUTE, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_VOLUME_MUTE, 0, KEYEVENTF_KEYUP, 0)

def toggle_media_play_pause():
    VK_MEDIA_PLAY_PAUSE = 0xB3
    KEYEVENTF_KEYUP = 0x0002
    ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_KEYUP, 0)

def show_screen_popup(message_text: str):
    def _popup():
        try:
            root = tk.Tk()
            root.title("📢 Remote Alert - PC Remote Pro")
            root.attributes('-topmost', True)
            root.geometry("450x220+500+300")
            root.configure(bg="#1e1e1e")

            lbl_title = tk.Label(root, text="📢 Remote Message from Phone", font=("Segoe UI", 12, "bold"), fg="#00bcd4", bg="#1e1e1e")
            lbl_title.pack(pady=(15, 8))

            lbl_msg = tk.Label(root, text=message_text, font=("Segoe UI", 11), fg="#ffffff", bg="#1e1e1e", wraplength=400, justify="center")
            lbl_msg.pack(pady=10, fill="both", expand=True)

            btn = tk.Button(root, text="OK / Close", font=("Segoe UI", 10, "bold"), bg="#00bcd4", fg="#000000", width=15, command=root.destroy)
            btn.pack(pady=(0, 15))

            root.focus_force()
            root.mainloop()
        except Exception:
            pass
    threading.Thread(target=_popup, daemon=True).start()

def speak_text(text_to_speak: str):
    def _speak():
        try:
            safe_text = text_to_speak.replace('"', ' ').replace("'", " ")
            cmd = f'powershell -NoProfile -Command "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak(\'{safe_text}\')"'
            subprocess.run(cmd, shell=True, creationflags=0x08000000 if sys.platform == "win32" else 0)
        except Exception:
            pass
    threading.Thread(target=_speak, daemon=True).start()

def get_battery_info() -> str:
    battery = psutil.sensors_battery()
    if not battery:
        return "🔌 **Power State:** Desktop PC connected to Direct A/C Power (No Battery)."

    percent = battery.percent
    is_plugged = battery.power_plugged
    filled_blocks = int(round(percent / 10))
    bar = "█" * filled_blocks + "░" * (10 - filled_blocks)
    status_text = "Charging ⚡" if is_plugged and percent < 100 else ("Fully Charged 🔌" if is_plugged else "Running on Battery 🔋")

    time_info = ""
    if battery.secsleft not in [psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN] and battery.secsleft > 0:
        hrs = battery.secsleft // 3600
        mins = (battery.secsleft % 3600) // 60
        time_info = f"\n⏱️ **Estimated Time Left:** `{hrs}h {mins}m`"

    warning = "\n\n⚠️ **Low Battery Alert!** Please plug in your charger." if not is_plugged and percent <= 20 else ""

    return (
        f"🔋 **Computer Battery Status:**\n\n"
        f"📊 **Charge Level:** `{percent}%`\n"
        f"`[{bar}]`\n"
        f"🔌 **Power State:** `{status_text}`"
        f"{time_info}"
        f"{warning}"
    )

# ----------------- Full Windows Clipboard Engine (Text, Images, Files) -----------------
def get_native_clipboard_text() -> str:
    try:
        if not ctypes.windll.user32.OpenClipboard(None):
            return ""
        try:
            h = ctypes.windll.user32.GetClipboardData(13)
            if not h:
                return ""
            p = _kernel32.GlobalLock(h)
            if not p:
                return ""
            try:
                val = ctypes.c_wchar_p(p).value
                return val or ""
            finally:
                _kernel32.GlobalUnlock(h)
        finally:
            ctypes.windll.user32.CloseClipboard()
    except Exception:
        return ""

def set_native_clipboard_text(text: str) -> bool:
    try:
        if not ctypes.windll.user32.OpenClipboard(None):
            return False
        try:
            ctypes.windll.user32.EmptyClipboard()
            data = text.encode('utf-16-le') + b'\x00\x00'
            h = _kernel32.GlobalAlloc(2, len(data))
            if not h:
                return False
            p = _kernel32.GlobalLock(h)
            if not p:
                return False
            ctypes.memmove(p, data, len(data))
            _kernel32.GlobalUnlock(h)
            _user32.SetClipboardData(13, h)
            return True
        finally:
            ctypes.windll.user32.CloseClipboard()
    except Exception:
        return False

def set_image_to_clipboard(image_path: str) -> bool:
    """Sets any image file directly to the Windows Clipboard (CF_DIB) for instant Ctrl+V."""
    try:
        img = Image.open(image_path)
        output = io.BytesIO()
        img.convert("RGB").save(output, "BMP")
        data = output.getvalue()[14:] # Strip 14-byte BMP header to obtain DIB
        output.close()

        if not ctypes.windll.user32.OpenClipboard(None):
            return False
        try:
            ctypes.windll.user32.EmptyClipboard()
            CF_DIB = 8
            h = _kernel32.GlobalAlloc(2, len(data))
            if not h:
                return False
            p = _kernel32.GlobalLock(h)
            if not p:
                return False
            ctypes.memmove(p, data, len(data))
            _kernel32.GlobalUnlock(h)
            _user32.SetClipboardData(CF_DIB, h)
            return True
        finally:
            ctypes.windll.user32.CloseClipboard()
    except Exception:
        return False

def get_clipboard_text():
    clip = get_native_clipboard_text()
    if clip:
        return clip
    try:
        cmd = 'powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Clipboard"'
        res = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        return res if res else "Clipboard is currently empty."
    except Exception as e:
        return f"Failed to read clipboard: {e}"

def empty_recycle_bin() -> tuple[bool, str]:
    try:
        SHERB_NOCONFIRMATION = 0x00000001
        SHERB_NOPROGRESSUI = 0x00000002
        SHERB_NOSOUND = 0x00000004
        flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        if res == 0:
            return True, "🗑️ **Recycle Bin emptied successfully!** All drives cleared."
        else:
            return True, "🗑️ **Recycle Bin is already empty.**"
    except Exception as e:
        return False, f"❌ Failed to empty Recycle Bin: {e}"

def get_recycle_bin_report() -> str:
    try:
        cmd = 'powershell -NoProfile -Command "(New-Object -ComObject Shell.Application).Namespace(0x0a).Items() | Measure-Object -Property Size -Sum | Select-Object -Property Count, Sum | ConvertTo-Json"'
        res = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        data = json.loads(res)
        count = data.get("Count", 0)
        size_bytes = data.get("Sum", 0) or 0
        size_mb = round(size_bytes / (1024 * 1024), 2)
        if count == 0:
            return "🗑️ **Recycle Bin Status:** Currently 100% empty (0 items)."
        return f"🗑️ **Recycle Bin Status:**\n📦 Items: `{count}`\n💾 Size: `{size_mb} MB`\n\nTap below to empty it."
    except Exception:
        return "🗑️ Recycle Bin scanned."

def search_youtube_videos(query_text: str, limit: int = 5):
    try:
        encoded = urllib.parse.quote(query_text)
        url = f"https://www.youtube.com/results?search_query={encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode('utf-8', errors='ignore')
        matches = re.findall(r'\"videoId\":\"([a-zA-Z0-9_-]{11})\".*?\"title\":\{\"runs\":\[\{\"text\":\"(.*?)\"\}\]', html)
        seen = set()
        results = []
        for vid, title in matches:
            if vid not in seen:
                seen.add(vid)
                results.append((vid, title))
                if len(results) >= limit:
                    break
        return results
    except Exception:
        return []

# ----------------- File Browser & Explorer -----------------
def build_advanced_explorer(current_dir: str, page: int = 0, items_per_page: int = 8):
    current_dir = os.path.abspath(current_dir)
    items = []
    try:
        with os.scandir(current_dir) as entries:
            for entry in entries:
                try:
                    if entry.name.startswith('$') or entry.name.lower() in ['system volume information', 'pagefile.sys', 'swapfile.sys', 'dumpstack.log']:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        items.append(('dir', entry.name, entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        items.append(('file', entry.name, entry.path))
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        return f"❌ Access denied to directory: `{current_dir}`", InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Main Menu", callback_data="goto_main")]])

    dirs = sorted([x for x in items if x[0] == 'dir'], key=lambda x: x[1].lower())
    files = sorted([x for x in items if x[0] == 'file'], key=lambda x: x[1].lower())
    all_sorted = dirs + files

    total_pages = max(1, (len(all_sorted) + items_per_page - 1) // items_per_page)
    page = max(0, min(page, total_pages - 1))
    current_slice = all_sorted[page * items_per_page : (page + 1) * items_per_page]

    keyboard = []
    for itype, iname, ipath in current_slice:
        uid = get_path_id(ipath)
        if itype == "dir":
            keyboard.append([InlineKeyboardButton(f"📁 {iname}", callback_data=f"nav:{uid}:0")])
        else:
            ext = os.path.splitext(iname)[1].lower()
            icon = "🖼" if ext in ['.jpg', '.png', '.jpeg', '.webp'] else ("🎵" if ext in ['.mp3', '.wav', '.m4a'] else "📄")
            keyboard.append([InlineKeyboardButton(f"{icon} {iname}", callback_data=f"ask:{uid}")])

    cur_uid = get_path_id(current_dir)
    nav_pagination = []
    if page > 0:
        nav_pagination.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"nav:{cur_uid}:{page-1}"))
    nav_pagination.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_pagination.append(InlineKeyboardButton("Next ➡️", callback_data=f"nav:{cur_uid}:{page+1}"))
    keyboard.append(nav_pagination)

    bottom_row = []
    parent_dir = os.path.dirname(os.path.abspath(current_dir))
    if parent_dir and parent_dir != os.path.abspath(current_dir):
        p_uid = get_path_id(parent_dir)
        bottom_row.append(InlineKeyboardButton("🔙 Back", callback_data=f"nav:{p_uid}:0"))
    else:
        bottom_row.append(InlineKeyboardButton("💾 All Drives", callback_data="drives"))

    bottom_row.append(InlineKeyboardButton("🔍 Search Here", callback_data=f"search_in:{cur_uid}"))
    keyboard.append(bottom_row)
    keyboard.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="goto_main")])

    text_msg = (
        f"📂 **Current Path:**\n`{current_dir}`\n\n"
        f"📊 **Contents:** `{len(dirs)}` Folders | `{len(files)}` Files\n"
        f"Tap any folder to explore or any file to manage:"
    )
    return text_msg, InlineKeyboardMarkup(keyboard)

def build_drives_markup():
    keyboard = []
    partitions = psutil.disk_partitions(all=False)
    for p in partitions:
        drive_path = p.mountpoint
        try:
            usage = psutil.disk_usage(drive_path)
            free_gb = round(usage.free / (1024**3), 1)
            total_gb = round(usage.total / (1024**3), 1)
            label = f"💾 {drive_path} ({free_gb}GB Free / {total_gb}GB)"
            uid = get_path_id(drive_path)
            keyboard.append([InlineKeyboardButton(label, callback_data=f"nav:{uid}:0")])
        except (PermissionError, OSError):
            continue

    keyboard.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="goto_main")])
    return InlineKeyboardMarkup(keyboard)


# ----------------- v4.0 Feature: Advanced PC Applications Scanner & Launcher -----------------
CACHED_PC_APPS = []
CACHED_PC_APPS_TIME = 0

def get_installed_pc_apps(force_refresh=False):
    global CACHED_PC_APPS, CACHED_PC_APPS_TIME
    now = time.time()
    if not force_refresh and CACHED_PC_APPS and (now - CACHED_PC_APPS_TIME) < 300:
        return CACHED_PC_APPS

    apps_dict = {}

    # 1. Start Menu Shortcuts (.lnk)
    start_dirs = [
        os.path.join(os.environ.get('ProgramData', ''), r'Microsoft\Windows\Start Menu\Programs'),
        os.path.join(os.environ.get('APPDATA', ''), r'Microsoft\Windows\Start Menu\Programs')
    ]
    for sdir in start_dirs:
        if os.path.exists(sdir):
            for root, _, files in os.walk(sdir):
                for f in files:
                    if f.lower().endswith(('.lnk', '.exe')):
                        name = os.path.splitext(f)[0].strip()
                        low = name.lower()
                        if any(x in low for x in ['uninstall', 'remove', 'help', 'readme', 'license', 'website', 'documentation', 'manual']):
                            continue
                        full_p = os.path.join(root, f)
                        if name not in apps_dict:
                            apps_dict[name] = {"name": name, "path": full_p, "type": "shortcut"}

    # 2. Windows Registry
    reg_roots = [
        (winreg.HKEY_LOCAL_MACHINE, r'Software\Microsoft\Windows\CurrentVersion\Uninstall'),
        (winreg.HKEY_LOCAL_MACHINE, r'Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall'),
        (winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Uninstall')
    ]
    for r_root, r_sub in reg_roots:
        try:
            with winreg.OpenKey(r_root, r_sub) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, subkey_name) as app_key:
                            disp_name, _ = winreg.QueryValueEx(app_key, "DisplayName")
                            disp_name = disp_name.strip()
                            if not disp_name:
                                continue
                            try:
                                is_sys, _ = winreg.QueryValueEx(app_key, "SystemComponent")
                                if is_sys:
                                    continue
                            except Exception:
                                pass
                            
                            install_loc = ""
                            try:
                                install_loc, _ = winreg.QueryValueEx(app_key, "InstallLocation")
                            except Exception:
                                pass

                            disp_icon = ""
                            try:
                                disp_icon, _ = winreg.QueryValueEx(app_key, "DisplayIcon")
                                if disp_icon and "," in disp_icon:
                                    disp_icon = disp_icon.split(",")[0].strip('"')
                            except Exception:
                                pass

                            target_exec = ""
                            if disp_icon and disp_icon.lower().endswith(".exe") and os.path.exists(disp_icon):
                                target_exec = disp_icon
                            elif install_loc and os.path.exists(install_loc):
                                for f in os.listdir(install_loc):
                                    if f.lower().endswith(".exe") and not any(x in f.lower() for x in ['unins', 'setup', 'update']):
                                        target_exec = os.path.join(install_loc, f)
                                        break

                            if disp_name not in apps_dict:
                                apps_dict[disp_name] = {
                                    "name": disp_name,
                                    "path": target_exec or install_loc or "",
                                    "type": "registry"
                                }
                    except Exception:
                        pass
        except Exception:
            pass

    sorted_apps = sorted(apps_dict.values(), key=lambda x: x["name"].lower())
    CACHED_PC_APPS = sorted_apps
    CACHED_PC_APPS_TIME = now
    return sorted_apps

def launch_pc_application(target: str) -> tuple[bool, str]:
    target = target.strip().strip('"')
    try:
        if os.path.exists(target):
            os.startfile(target)
            return True, f"Application launched successfully: {os.path.basename(target)}"
        else:
            subprocess.Popen(target, shell=True)
            return True, f"Command executed: {target}"
    except Exception as e:
        return False, f"Failed to launch application: {e}"

# ----------------- v4.0 Feature: Smart Network Watchdog & Auto-Repair Engine -----------------
class SmartNetworkWatchdog:
    def __init__(self):
        self.is_running = True
        self.was_connected = True
        self.heartbeat_enabled = False
        self.last_check_str = "Never"
        self.consecutive_fails = 0

    def check_internet(self) -> bool:
        # Fast multi-endpoint check using port 443
        endpoints = [
            ("api.telegram.org", 443),
            ("1.1.1.1", 443),
            ("8.8.8.8", 443)
        ]
        for host, port in endpoints:
            try:
                s = socket.create_connection((host, port), timeout=3)
                s.close()
                return True
            except OSError:
                continue
        return False

    def get_current_wifi_ssid(self) -> str:
        try:
            p = subprocess.run("netsh wlan show interfaces", shell=True, capture_output=True, text=True, timeout=8)
            m = re.search(r"^\s*SSID\s*:\s*(.+)$", p.stdout, re.MULTILINE)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
        return "Not Connected / Disconnected"

    def auto_repair(self) -> tuple[bool, list[str]]:
        actions = []
        # 1. Flush DNS
        try:
            subprocess.run("ipconfig /flushdns", shell=True, capture_output=True, text=True, timeout=8)
            actions.append("Flushed Windows DNS resolver cache")
        except Exception:
            pass

        # 2. Check Wi-Fi state
        ssid = self.get_current_wifi_ssid()
        if "Disconnected" in ssid or "Not Connected" in ssid:
            actions.append("Wi-Fi interface was disconnected. Scanning saved profiles...")
            # Query saved Wi-Fi profiles
            profiles = []
            try:
                p_prof = subprocess.run("netsh wlan show profiles", shell=True, capture_output=True, text=True, timeout=8)
                for line in p_prof.stdout.splitlines():
                    if ":" in line and ("Profile" in line or "مستخدمين" in line):
                        pr = line.split(":", 1)[1].strip()
                        if pr:
                            profiles.append(pr)
            except Exception:
                pass

            reconnected = False
            for prof in profiles:
                try:
                    subprocess.run(f'netsh wlan connect name="{prof}"', shell=True, capture_output=True, text=True, timeout=8)
                    actions.append(f"Auto-connecting to saved network: {prof}")
                    time.sleep(3)
                    if self.check_internet():
                        actions.append(f"Successfully reconnected to: {prof}")
                        reconnected = True
                        break
                except Exception:
                    pass

            # If still disconnected, re-enable Wi-Fi interface and renew DHCP
            if not reconnected:
                try:
                    subprocess.run('netsh interface set interface "Wi-Fi" admin=enable', shell=True, capture_output=True, text=True, timeout=8)
                    actions.append("Re-enabled Wi-Fi network interface")
                except Exception:
                    pass
                try:
                    subprocess.run("ipconfig /renew", shell=True, capture_output=True, text=True, timeout=15)
                    actions.append("Renewed DHCP IP configuration")
                except Exception:
                    pass

        # 3. Final verification
        time.sleep(2)
        success = self.check_internet()
        return success, actions

    def broadcast_message(self, text: str):
        for token, entry in list(ACTIVE_BOT_REGISTRY.items()):
            app = entry.get("app")
            loop = entry.get("loop")
            user_map = entry.get("user_map", {})
            if app and loop and loop.is_running():
                for cid in user_map.keys():
                    try:
                        asyncio.run_coroutine_threadsafe(
                            app.bot.send_message(chat_id=cid, text=text, parse_mode="Markdown"),
                            loop
                        )
                    except Exception:
                        pass

    def run_loop(self):
        time.sleep(12)
        while self.is_running:
            try:
                now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
                self.last_check_str = now_str
                has_net = self.check_internet()

                if not has_net:
                    self.consecutive_fails += 1
                    self.was_connected = False
                    ok, steps = self.auto_repair()
                    if ok:
                        self.was_connected = True
                        self.consecutive_fails = 0
                        steps_text = "\n- ".join(steps) if steps else "Auto-reconnected"
                        self.broadcast_message(
                            f"🌐 **تنبيه ذكي: استعادة اتصال الإنترنت تلقائياً!**\n\n"
                            f"✅ نجح نظام الفحص الذكي في حل مشكلة انقطاع الشبكة وإعادة ربط الحاسبة بالإنترنت مباشرة.\n"
                            f"💻 Host: `{platform.node()}`\n"
                            f"⏰ التوقيت: `{now_str}`\n"
                            f"📶 الشبكة: `{self.get_current_wifi_ssid()}`\n\n"
                            f"🛠️ الإجراءات المنفذة:\n- {steps_text}"
                        )
                else:
                    if not self.was_connected:
                        self.was_connected = True
                        self.consecutive_fails = 0
                        self.broadcast_message(
                            f"🌐 **استعادة اتصال الإنترنت!**\n"
                            f"🟢 الكمبيوتر متصل بالإنترنت الآن بنجاح.\n"
                            f"💻 Host: `{platform.node()}`\n"
                            f"⏰ التوقيت: `{now_str}`"
                        )
                    elif self.heartbeat_enabled:
                        # 1-minute heartbeat
                        self.broadcast_message(
                            f"💓 **نبض اتصال الشبكة الذكي (كل دقيقة):**\n"
                            f"🟢 الكمبيوتر متصل بالإنترنت بشكل سليم وجاهز لاستقبال الأوامر.\n"
                            f"📶 الشبكة: `{self.get_current_wifi_ssid()}`\n"
                            f"💻 Host: `{platform.node()}`\n"
                            f"⏰ التوقيت: `{now_str}`"
                        )
            except Exception:
                pass
            time.sleep(60)

SMART_NETWORK_WATCHDOG = SmartNetworkWatchdog()
BOOT_SNAPSHOTS_SENT = False

async def send_startup_snapshots(app: Application, target_chat_ids: list):
    now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    hostname = platform.node()

    # 1. Capture Ultra-HD Screen
    temp_screen = None
    width, height = 1920, 1080
    try:
        temp_screen, width, height = await asyncio.to_thread(capture_screen_ultra_hd)
    except Exception:
        temp_screen = None

    # 2. Capture Webcam Photo
    webcam_ok, temp_cam = await asyncio.to_thread(capture_webcam_photo)

    for cid in target_chat_ids:
        lang = get_user_language(cid)
        # Send Screen Photo
        if temp_screen and os.path.exists(temp_screen):
            try:
                caption = (
                    f"📸 **لقطة الشاشة التلقائية عند تشغيل الكمبيوتر**\n"
                    f"💻 Host: `{hostname}`\n"
                    f"📐 الدقة: `{width} x {height}` px\n"
                    f"⏰ التوقيت: `{now_str}`"
                    if lang == "ar" else
                    f"📸 **Automatic Startup Screen Capture**\n"
                    f"💻 Host: `{hostname}`\n"
                    f"📐 Resolution: `{width} x {height}` px\n"
                    f"⏰ Timestamp: `{now_str}`"
                )
                with open(temp_screen, "rb") as photo:
                    await app.bot.send_photo(chat_id=cid, photo=photo, caption=caption, parse_mode="Markdown")
            except Exception:
                pass

        # Send Webcam Photo
        if webcam_ok and temp_cam and os.path.exists(temp_cam):
            try:
                caption = (
                    f"📷 **لقطة كاميرا الويب التلقائية عند تشغيل الكمبيوتر**\n"
                    f"💻 Host: `{hostname}`\n"
                    f"⏰ التوقيت: `{now_str}`"
                    if lang == "ar" else
                    f"📷 **Automatic Startup Webcam Photo**\n"
                    f"💻 Host: `{hostname}`\n"
                    f"⏰ Timestamp: `{now_str}`"
                )
                with open(temp_cam, "rb") as photo:
                    await app.bot.send_photo(chat_id=cid, photo=photo, caption=caption, parse_mode="Markdown")
            except Exception:
                pass

    if temp_screen and os.path.exists(temp_screen):
        try: os.remove(temp_screen)
        except Exception: pass
    if webcam_ok and temp_cam and os.path.exists(temp_cam):
        try: os.remove(temp_cam)
        except Exception: pass

# ----------------- Universal Multilingual Keyboards -----------------
def get_menu_keyboard(menu_type="main", lang="en"):
    common_options = {
        "resize_keyboard": True,
        "is_persistent": False,
        "one_time_keyboard": False,
        "input_field_placeholder": "Choose from menu / اختر للتحكم..."
    }

    if menu_type == "main":
        return ReplyKeyboardMarkup([
            [B("btn_files", lang), B("btn_web", lang)],
            [B("btn_apps", lang), B("btn_cmd_py", lang)],
            [B("btn_smart_network", lang), B("btn_uptime", lang)],
            [B("btn_power", lang), B("btn_monitor", lang)],
            [B("btn_battery", lang), B("btn_media", lang)],
            [B("btn_security_guard", lang), B("btn_tools", lang)],
            [B("btn_screen", lang), B("btn_webcam", lang)],
            [B("btn_mic", lang), B("btn_specs", lang)],
            [B("btn_ping", lang), B("btn_kill", lang)],
            [B("btn_lang", lang), B("btn_hide_menu", lang)]
        ], **common_options)

    elif menu_type == "files":
        return ReplyKeyboardMarkup([
            [B("btn_desktop", lang), B("btn_downloads", lang)],
            [B("btn_documents", lang), B("btn_drives", lang)],
            [B("btn_search", lang), B("btn_scan_bin", lang)],
            [B("btn_empty_bin", lang), B("btn_screen", lang)],
            [B("btn_back", lang)]
        ], **common_options)

    elif menu_type == "web":
        return ReplyKeyboardMarkup([
            [B("btn_play_yt", lang)],
            [B("btn_chrome", lang), B("btn_youtube", lang)],
            [B("btn_screen", lang), B("btn_back", lang)]
        ], **common_options)

    elif menu_type == "power":
        return ReplyKeyboardMarkup([
            [B("btn_wake", lang), B("btn_lock", lang)],
            [B("btn_sleep", lang), B("btn_screen_off", lang)],
            [B("btn_battery", lang), B("btn_shutdown", lang)],
            [B("btn_cancel_shutdown", lang), B("btn_restart", lang)],
            [B("btn_screen", lang), B("btn_back", lang)]
        ], **common_options)

    elif menu_type == "monitor":
        return ReplyKeyboardMarkup([
            [B("btn_cpu_ram", lang), B("btn_uptime", lang)],
            [B("btn_battery", lang), B("btn_processes", lang)],
            [B("btn_wifi", lang), B("btn_wifi_passwords", lang)],
            [B("btn_specs", lang), B("btn_ping", lang)],
            [B("btn_screen", lang), B("btn_back", lang)]
        ], **common_options)

    elif menu_type == "media":
        return ReplyKeyboardMarkup([
            [B("btn_photos", lang), B("btn_audio", lang)],
            [B("btn_vol_up", lang), B("btn_vol_down", lang)],
            [B("btn_play_pause", lang), B("btn_mute", lang)],
            [B("btn_mic", lang), B("btn_screen", lang)],
            [B("btn_back", lang)]
        ], **common_options)

    elif menu_type == "tools":
        return ReplyKeyboardMarkup([
            [B("btn_security_guard", lang), B("btn_uptime", lang)],
            [B("btn_wifi_passwords", lang), B("btn_update_app", lang)],
            [B("btn_read_clip", lang), B("btn_autoclip", lang)],
            [B("btn_tts", lang), B("btn_send_alert", lang)],
            [B("btn_empty_bin", lang), B("btn_clean_temp", lang)],
            [B("btn_cmd", lang), B("btn_webcam", lang)],
            [B("btn_screen", lang), B("btn_lang", lang)],
            [B("btn_back", lang)]
        ], **common_options)

    else:
        return ReplyKeyboardMarkup([[B("btn_screen", lang), B("btn_back", lang)]], **common_options)

# ----------------- Real-Time Diagnostic Test Function -----------------
def test_telegram_connection(token: str, chat_id_str: str) -> tuple[bool, str]:
    token = token.strip()
    chat_id_str = chat_id_str.strip()

    if not token:
        return False, "❌ Bot token field is empty! Please paste your token from @BotFather."

    try:
        url_getme = f"https://api.telegram.org/bot{token}/getMe"
        req = urllib.request.Request(url_getme, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            if not data.get("ok"):
                return False, "❌ Bot token is invalid or revoked by BotFather!"
            bot_username = data.get("result", {}).get("username", "Bot")
    except urllib.error.HTTPError as e:
        if e.code in [401, 404]:
            return False, "❌ Bot token is unauthorized! Verify the token from @BotFather."
        return False, f"❌ Telegram HTTP error ({e.code}): {e.reason}"
    except Exception as e:
        return False, f"❌ Failed to reach Telegram API. Verify internet connection: {e}"

    chat_id = None
    if chat_id_str:
        try:
            chat_id = int(chat_id_str)
        except ValueError:
            return False, "❌ Invalid Chat ID! Must consist of digits only (obtain from @userinfobot)."

    if chat_id:
        try:
            test_msg = (
                f"🟢 **Connection Test Successful!**\n\n"
                f"💻 Host PC: `{platform.node()}`\n"
                f"👤 User: `{CURRENT_USER}`\n"
                f"🤖 Bot: @{bot_username}\n\n"
                f"Control keyboard registered below. Tap any button to interact with your PC:"
            )
            send_data = json.dumps({
                "chat_id": chat_id,
                "text": test_msg,
                "parse_mode": "Markdown",
                "reply_markup": {
                    "keyboard": [
                        ["📂 Files & Storage", "🌐 Web & YouTube"],
                        ["⚡ Power Controls", "📊 System Monitor"],
                        ["🔋 Battery Status", "🎛 Audio & Media"],
                        ["🛠️ Tools & Utility", "📸 Screen Capture"],
                        ["📷 Webcam Snapshot", "🎙️ Voice Record (10s)"],
                        ["🖥️ PC Hardware Specs", "📶 Wi-Fi & Network"],
                        ["🏓 Ping & Latency", "⚡ Kill Process"],
                        ["🌐 Language / اللغة", "🔙 Back to Main Menu"]
                    ],
                    "resize_keyboard": True,
                    "is_persistent": True,
                    "one_time_keyboard": False
                }
            }).encode('utf-8')

            url_send = f"https://api.telegram.org/bot{token}/sendMessage"
            req_send = urllib.request.Request(
                url_send,
                data=send_data,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req_send, timeout=8) as resp:
                data_send = json.loads(resp.read().decode())
                if data_send.get("ok"):
                    return True, f"✅ Connection verified 100%!\nLinked with @{bot_username} and test message delivered to your Telegram."
        except urllib.error.HTTPError as e:
            if e.code in [400, 403]:
                return True, (
                    f"✅ Bot Token & Connection Verified Successfully (@{bot_username})!\n\n"
                    f"💡 Application is fully connected to Telegram servers and active in the background.\n"
                    f"📌 Note: To start controlling your PC from your phone, open Telegram, search for @{bot_username}, and tap START."
                )
            return False, f"❌ Telegram HTTP error ({e.code}): {e.reason}"
        except Exception as e:
            return True, f"✅ Token is valid and linked with @{bot_username}.\nApplication is active in the background."

    return True, f"✅ Bot (@{bot_username}) verified successfully!\nApplication is ready for remote control."

# ----------------- Multi-Account Concurrent Bot Engine -----------------
def start_bot_thread(token: str, user_dict_ref: dict):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    def is_authorized(update: Update) -> bool:
        return bool(update.effective_user and update.effective_user.id in user_dict_ref)

    async def unauthorized_feedback(update: Update):
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        uname = update.effective_user.full_name or "User"
        host = platform.node()
        msg = (
            f"⚠️ **Security Alert - PC ({host}):**\n\n"
            f"Hello {uname}, your account is not authorized to control this computer.\n\n"
            f"🆔 **Your Chat ID:** `{uid}`\n\n"
            f"📌 **To Authorize:**\n"
            f"1. Open **PC Remote Pro** on your computer.\n"
            f"2. Add or edit your account credentials.\n"
            f"3. Paste `{uid}` into the **Chat ID** field and click Save."
        )
        try:
            await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception:
            pass

    async def post_init(app: Application):
        for _ in range(15):
            try:
                socket.create_connection(("api.telegram.org", 443), timeout=3)
                break
            except OSError:
                try:
                    socket.create_connection(("1.1.1.1", 443), timeout=3)
                    break
                except OSError:
                    await asyncio.sleep(2)

        hostname = platform.node()
        now_time = datetime.now().strftime("%Y-%m-%d %I:%M %p")

        for chat_id, acc_info in list(user_dict_ref.items()):
            acc_name = acc_info.get("name", "User")
            lang = get_user_language(chat_id)
            msg = (
                f"🟢 **PC is Online & Connected!**\n"
                f"👤 Account: `{acc_name}` ({CURRENT_USER})\n"
                f"💻 Host: `{hostname}`\n"
                f"⏰ Time: `{now_time}`\n"
                f"👨‍💻 Developer: `{DEVELOPER_NAME}` • `{APP_VERSION}`\n\n"
                f"Remote dashboard is active and ready for your commands:"
            )
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode="Markdown",
                    reply_markup=get_menu_keyboard("main", lang)
                )
            except Exception:
                pass

        # Automatic Boot Snapshots: Screenshot and Webcam photo sent to user on PC startup!
        global BOOT_SNAPSHOTS_SENT
        if not BOOT_SNAPSHOTS_SENT:
            BOOT_SNAPSHOTS_SENT = True
            try:
                await send_startup_snapshots(app, list(user_dict_ref.keys()))
            except Exception:
                pass

    # Ultra-HD Lossless Multi-Monitor Screenshot Delivery
    async def send_screenshot_message(target_msg_obj, lang="en"):
        temp_img = None
        try:
            temp_img, width, height = capture_screen_ultra_hd()
            now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
            doc_name = f"Screen_{platform.node()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            caption_text = (
                f"📸 **Ultra-HD Screen Capture:**\n"
                f"💻 Host: `{platform.node()}`\n"
                f"📐 Resolution: `{width} x {height}` px\n"
                f"⏰ Captured: `{now_str}`"
            )

            with open(temp_img, "rb") as photo:
                await target_msg_obj.reply_photo(
                    photo=photo,
                    caption=caption_text,
                    reply_markup=get_menu_keyboard("main", lang),
                    parse_mode="Markdown"
                )

            with open(temp_img, "rb") as doc:
                await target_msg_obj.reply_document(
                    document=doc,
                    filename=doc_name,
                    caption="💎 **Uncompressed Lossless Document** (100% Pixel Perfect Quality)."
                )
        except Exception as e:
            await target_msg_obj.reply_text(f"❌ Screen capture error: {e}", reply_markup=get_menu_keyboard("main", lang))
        finally:
            if temp_img and os.path.exists(temp_img):
                try:
                    os.remove(temp_img)
                except Exception:
                    pass

    async def inline_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        uid = update.effective_user.id
        lang = get_user_language(uid)

        if data == "noop":
            await query.answer()
            return

        # Language selection callback
        if data.startswith("setlang:"):
            selected_lang = data.split(":")[1]
            save_user_language(uid, selected_lang)
            lang_label = SUPPORTED_LANGUAGES.get(selected_lang, selected_lang)
            await query.answer(f"Language set to {lang_label}")
            acc_name = user_dict_ref.get(uid, {}).get("name", "User")
            welcome = (
                f"✅ **{B('lang_changed_msg', selected_lang)}**\n\n"
                f"🏠 **{B('menu_main_title', selected_lang)} ({acc_name}):**\n"
                f"💻 {B('pc_connected_ready', selected_lang).format(node=platform.node())}\n\n"
                f"{B('select_from_menu', selected_lang)}"
            )
            await query.message.reply_text(
                welcome,
                reply_markup=get_menu_keyboard("main", selected_lang),
                parse_mode="Markdown"
            )
            return

        # Wallpaper setter callback
        if data.startswith("set_wallpaper:"):
            await query.answer("Setting wallpaper...")
            file_uid = data.split(":")[1]
            target_path = PATH_STORE.get(file_uid)
            if target_path and os.path.exists(target_path):
                ok = set_desktop_wallpaper(target_path)
                if ok:
                    await query.message.reply_text("🖼️ **Desktop Wallpaper updated successfully!**", reply_markup=get_menu_keyboard("main", lang))
                else:
                    await query.message.reply_text("❌ Failed to update wallpaper.")
            return

        # Process killer callback
        if data.startswith("killproc:"):
            parts = data.split(":")
            proc_pid = int(parts[1])
            proc_name = parts[2]
            ok, msg = kill_process_by_target(proc_pid, proc_name)
            await query.answer(msg[:100], show_alert=True)
            txt_procs, markup_procs = get_top_processes_for_kill()
            try:
                await query.edit_message_text(txt_procs, reply_markup=markup_procs, parse_mode="Markdown")
            except Exception:
                pass
            return

        if data == "cancel_input":
            await query.answer()
            context.user_data["awaiting_input"] = None
            await query.message.reply_text("✅ Operation cancelled.", reply_markup=get_menu_keyboard("main", lang))
            return
        elif data == "show_menu":
            await query.answer("📱 تم إظهار القائمة!" if lang == "ar" else "📱 Menu restored!")
            acc_name = user_dict_ref.get(uid, {}).get("name", "User")
            welcome = (
                f"🏠 **{B('menu_main_title', lang)} ({acc_name}):**\n"
                f"💻 {B('pc_connected_ready', lang).format(node=platform.node())}\n\n"
                f"{B('select_from_menu', lang)}"
            )
            await query.message.reply_text(
                welcome,
                reply_markup=get_menu_keyboard("main", lang),
                parse_mode="Markdown"
            )
            return

        elif data == "btn_apps_panel":
            await query.answer()
            apps = get_installed_pc_apps()
            total_apps = len(apps)
            kb = [
                [InlineKeyboardButton("📋 تصفح التطبيقات (صفحة 1)", callback_data="apps_page:0"), InlineKeyboardButton("🔍 بحث عن برنامج", callback_data="apps_search_prompt")],
                [InlineKeyboardButton("⚡ تشغيل ملف بالمسار", callback_data="apps_path_prompt"), InlineKeyboardButton("📸 لقطة شاشة سريعة", callback_data="take_screenshot")],
                [InlineKeyboardButton("🔄 إعادة فحص التطبيقات", callback_data="apps_rescan"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
            ]
            msg = (
                f"🖥️ **مركز التحكم بتطبيقات وبرامج الكمبيوتر:**\n\n"
                f"📊 تم فحص النظام واكتشاف `{total_apps}` تطبيق وبرنامج مثبت على الحاسبة.\n\n"
                f"اختر تصفح التطبيقات للفتح المباشر وأخذ لقطة شاشة، أو ابحث عن أي برنامج بالاسم:"
                if lang == "ar" else
                f"🖥️ **PC Applications & Programs Center:**\n\n"
                f"📊 System scan identified `{total_apps}` installed applications.\n\n"
                f"Browse apps to launch and capture screenshot, or search by name:"
            )
            try:
                await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        elif data == "apps_rescan":
            await query.answer("🔄 جاري إعادة فحص تطبيقات النظام..." if lang == "ar" else "🔄 Rescanning apps...", show_alert=False)
            apps = get_installed_pc_apps(force_refresh=True)
            kb = [
                [InlineKeyboardButton("📋 تصفح التطبيقات (صفحة 1)", callback_data="apps_page:0"), InlineKeyboardButton("🔍 بحث عن برنامج", callback_data="apps_search_prompt")],
                [InlineKeyboardButton("⚡ تشغيل ملف بالمسار", callback_data="apps_path_prompt"), InlineKeyboardButton("📸 لقطة شاشة سريعة", callback_data="take_screenshot")],
                [InlineKeyboardButton("🔄 إعادة فحص التطبيقات", callback_data="apps_rescan"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
            ]
            msg = (
                f"✅ **تم تحديث قائمة التطبيقات بنجاح!**\n\n"
                f"📊 عدد البرامج المكتشفة الآن: `{len(apps)}` تطبيق.\n"
                f"يمكنك الآن تصفحها أو تشغيل أي برنامج مباشرة:"
                if lang == "ar" else
                f"✅ **Applications List Refreshed!**\n\n"
                f"📊 Discovered: `{len(apps)}` apps.\n"
                f"Select an action below:"
            )
            try:
                await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        elif data.startswith("apps_page:"):
            page = int(data.split(":")[1])
            apps = get_installed_pc_apps()
            total_apps = len(apps)
            per_page = 8
            total_pages = max(1, (total_apps + per_page - 1) // per_page)
            page = max(0, min(page, total_pages - 1))

            start_i = page * per_page
            end_i = min(start_i + per_page, total_apps)
            page_apps = apps[start_i:end_i]

            kb = []
            row = []
            for idx, a in enumerate(page_apps, start=start_i):
                btn_name = a["name"]
                if len(btn_name) > 22:
                    btn_name = btn_name[:20] + ".."
                row.append(InlineKeyboardButton(f"🚀 {btn_name}", callback_data=f"launch_app:{idx}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row:
                kb.append(row)

            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"apps_page:{page-1}"))
            nav.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"apps_page:{page+1}"))
            kb.append(nav)

            kb.append([
                InlineKeyboardButton("🔍 بحث عن برنامج", callback_data="apps_search_prompt"),
                InlineKeyboardButton("🔙 لوحة التطبيقات", callback_data="btn_apps_panel"),
                InlineKeyboardButton("🏠 الرئيسية", callback_data="goto_main")
            ])

            text = (
                f"🖥️ **قائمة تطبيقات الكمبيوتر (صفحة {page+1} من {total_pages}):**\n"
                f"اضغط على أي برنامج لتشغيله مباشرة واستلام لقطة شاشة للتأكد:"
                if lang == "ar" else
                f"🖥️ **PC Applications (Page {page+1} of {total_pages}):**\n"
                f"Tap any app to launch on PC and receive a verification screenshot:"
            )
            await query.answer()
            try:
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        elif data.startswith("launch_app:"):
            idx = int(data.split(":")[1])
            apps = get_installed_pc_apps()
            if 0 <= idx < len(apps):
                target_app = apps[idx]
                app_name = target_app["name"]
                app_path = target_app["path"]
                await query.answer(f"🚀 جاري تشغيل {app_name}...", show_alert=False)
                await query.message.reply_text(f"⏳ **جاري فتح التطبيق:** `{app_name}` ...", parse_mode="Markdown")

                success, msg = launch_pc_application(app_path or app_name)
                await asyncio.sleep(2.5)
                temp_img, width, height = await asyncio.to_thread(capture_screen_ultra_hd)
                now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
                caption_text = (
                    f"🚀 **تم تشغيل البرنامج بنجاح!**\n"
                    f"📱 التطبيق: **{app_name}**\n"
                    f"📂 المسار: `{app_path}`\n"
                    f"⏰ التوقيت: `{now_str}`\n"
                    f"📸 لقطة شاشة للتحقق من الشاشة:"
                    if lang == "ar" else
                    f"🚀 **Application Launched Successfully!**\n"
                    f"📱 App: **{app_name}**\n"
                    f"📂 Path: `{app_path}`\n"
                    f"⏰ Time: `{now_str}`\n"
                    f"📸 Verification screenshot:"
                )
                kb = [
                    [InlineKeyboardButton("📸 أخذ لقطة شاشة أخرى", callback_data="take_screenshot")],
                    [InlineKeyboardButton("📋 عودة للتطبيقات", callback_data="apps_page:0"), InlineKeyboardButton("🏠 الرئيسية", callback_data="goto_main")]
                ]
                if temp_img and os.path.exists(temp_img):
                    try:
                        with open(temp_img, "rb") as photo:
                            await query.message.reply_photo(photo=photo, caption=caption_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                    finally:
                        try: os.remove(temp_img)
                        except Exception: pass
                else:
                    await query.message.reply_text(f"✅ {msg}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        elif data == "apps_search_prompt":
            await query.answer()
            context.user_data["awaiting_input"] = ("apps_search", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء / Cancel", callback_data="cancel_input")]])
            msg = (
                "🔍 **البحث عن تطبيق وتشغيله:**\n\n"
                "أرسل الآن اسم التطبيق أو جزء من اسمه (مثال: chrome, word, excel, revit, calc, telegram):"
                if lang == "ar" else
                "🔍 **Search & Launch Application:**\nPlease send the app name or keyword (e.g. chrome, word, calc, telegram):"
            )
            await query.message.reply_text(msg, reply_markup=cancel_kb, parse_mode="Markdown")
            return

        elif data == "apps_path_prompt":
            await query.answer()
            context.user_data["awaiting_input"] = ("apps_path", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء / Cancel", callback_data="cancel_input")]])
            msg = (
                "⚡ **تشغيل ملف أو برنامج بالمسار:**\n\n"
                "أرسل المسار الكامل للملف أو البرنامج المراد فتحه على الكمبيوتر\n"
                "(مثال: `C:\\Program Files\\App\\app.exe` أو `D:\\Documents\\file.pdf`):"
                if lang == "ar" else
                "⚡ **Launch by Full Path:**\nPlease send the full file or executable path to open on PC:"
            )
            await query.message.reply_text(msg, reply_markup=cancel_kb, parse_mode="Markdown")
            return

        elif data == "prompt_cmd_exec":
            await query.answer()
            context.user_data["awaiting_input"] = ("run_cmd", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء / Cancel", callback_data="cancel_input")]])
            await query.message.reply_text(
                "⚡ **تنفيذ أمر CMD / موجه الأوامر:**\nأرسل الأمر المراد تنفيذه على الكمبيوتر (مثال: `ipconfig`, `dir`, `ping 8.8.8.8`):"
                if lang == "ar" else
                "⚡ **Execute CMD Command:**\nEnter terminal command to run on PC:",
                reply_markup=cancel_kb,
                parse_mode="Markdown"
            )
            return

        elif data == "open_cmd_desktop":
            await query.answer("🖥️ جاري فتح نافذة CMD...", show_alert=False)
            subprocess.Popen("start cmd.exe", shell=True)
            await asyncio.sleep(1.5)
            temp_img, width, height = await asyncio.to_thread(capture_screen_ultra_hd)
            now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
            kb = [
                [InlineKeyboardButton("📸 أخذ لقطة شاشة أخرى", callback_data="take_screenshot")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
            ]
            caption = (
                f"🖥️ **تم فتح نافذة موجه الأوامر (CMD) على شاشة الكمبيوتر!**\n"
                f"💻 Host: `{platform.node()}`\n"
                f"⏰ التوقيت: `{now_str}`"
                if lang == "ar" else
                f"🖥️ **CMD Terminal window opened on PC screen!**\n💻 Host: `{platform.node()}`\n⏰ Time: `{now_str}`"
            )
            if temp_img and os.path.exists(temp_img):
                try:
                    with open(temp_img, "rb") as photo:
                        await query.message.reply_photo(photo=photo, caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                finally:
                    try: os.remove(temp_img)
                    except Exception: pass
            else:
                await query.message.reply_text(caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        elif data == "prompt_py_file":
            await query.answer()
            context.user_data["awaiting_input"] = ("run_py_file", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء / Cancel", callback_data="cancel_input")]])
            msg = (
                "🐍 **تشغيل ملف بايثون على الكمبيوتر:**\n\n"
                "أرسل مسار ملف البايثون (مثال: `C:\\scripts\\main.py` أو `myscript.py`):\n\n"
                "💡 _ملاحظة: يمكنك أيضاً إرسال أي ملف .py مباشرة في المحادثة وسيقوم البوت بتشغيله فوراً!_"
                if lang == "ar" else
                "🐍 **Run Python Script (.py):**\nEnter path to Python file (or send the .py file directly into chat):"
            )
            await query.message.reply_text(msg, reply_markup=cancel_kb, parse_mode="Markdown")
            return

        elif data == "prompt_py_code":
            await query.answer()
            context.user_data["awaiting_input"] = ("run_py_code", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء / Cancel", callback_data="cancel_input")]])
            msg = (
                "🐍 **كتابة وبناء كود بايثون مباشر:**\n\n"
                "أرسل الآن كود بايثون لتنفيذه مباشرة واستلام النتيجة:\n\n"
                "💡 _مثال:_\n"
                "```python\n"
                "import os, math\n"
                "print('CPU Cores:', os.cpu_count())\n"
                "print('Result:', math.factorial(6))\n"
                "```"
                if lang == "ar" else
                "🐍 **Direct Python Code Execution:**\nEnter Python code to execute on PC and receive output:"
            )
            await query.message.reply_text(msg, reply_markup=cancel_kb, parse_mode="Markdown")
            return

        elif data == "net_heal_now":
            await query.answer("🛠️ جاري تشخيص وإصلاح الشبكة تلقائياً...", show_alert=False)
            await query.message.reply_text("⏳ **جاري فحص اتصال الشبكة وتشغيل أدوات الإصلاح التلقائي...**", parse_mode="Markdown")
            ok, steps = await asyncio.to_thread(SMART_NETWORK_WATCHDOG.auto_repair)
            now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
            steps_text = "\n- ".join(steps) if steps else "All network adapters checked"
            status_emoji = "🟢" if ok else "🔴"
            status_word = "متصل وسليم (Online)" if ok else "غير متصل (Offline)"
            kb = [
                [InlineKeyboardButton("🔄 إعادة الفحص الآن", callback_data="net_heal_now")],
                [InlineKeyboardButton("🔔 تفعيل/إيقاف إشعار كل دقيقة", callback_data="toggle_net_heartbeat")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
            ]
            msg = (
                f"🌐 **تقرير صيانة وفحص الشبكة الذاتي:**\n\n"
                f"📡 **حالة الاتصال:** {status_emoji} `{status_word}`\n"
                f"📶 **الشبكة:** `{SMART_NETWORK_WATCHDOG.get_current_wifi_ssid()}`\n"
                f"⏰ **التوقيت:** `{now_str}`\n\n"
                f"🛠️ **الإجراءات المنفذة:**\n- {steps_text}"
            )
            await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        elif data == "toggle_net_heartbeat":
            SMART_NETWORK_WATCHDOG.heartbeat_enabled = not SMART_NETWORK_WATCHDOG.heartbeat_enabled
            state_str = "مفعل ✅" if SMART_NETWORK_WATCHDOG.heartbeat_enabled else "معطل ❌"
            await query.answer(f"إشعار نبض الشبكة كل دقيقة أصبح الآن: {state_str}", show_alert=True)
            return

        elif data == "goto_main":
            await query.answer()
            acc_name = user_dict_ref.get(uid, {}).get("name", "User")
            welcome = (
                f"🏠 **{B('menu_main_title', lang)} ({acc_name}):**\n"
                f"💻 {B('pc_connected_ready', lang).format(node=platform.node())}\n\n"
                f"{B('select_from_menu', lang)}"
            )
            await query.message.reply_text(
                welcome,
                reply_markup=get_menu_keyboard("main", lang),
                parse_mode="Markdown"
            )
            return
        elif data == "drives":
            await query.answer()
            await query.edit_message_text(
                "💾 **Available Storage Drives:**\nSelect any drive to explore its contents:",
                reply_markup=build_drives_markup(),
                parse_mode="Markdown"
            )
            return

        if data == "vol_up":
            change_volume(up=True)
            await query.answer("🔊 Volume: +10%", show_alert=False)
            return
        elif data == "vol_down":
            change_volume(up=False)
            await query.answer("🔉 Volume: -10%", show_alert=False)
            return
        elif data == "media_pause":
            toggle_media_play_pause()
            await query.answer("⏯️ Play / Pause Toggled", show_alert=False)
            return
        elif data == "vol_mute":
            toggle_audio_mute()
            await query.answer("🔇 Mute Toggled", show_alert=False)
            return
        elif data == "take_screenshot":
            await query.answer("📸 Capturing screen...")
            await send_screenshot_message(query.message, lang)
            return

        elif data == "guard_toggle":
            SECURITY_GUARD_SETTINGS["enabled"] = not SECURITY_GUARD_SETTINGS.get("enabled", True)
            save_config(MASTER_APP_INSTANCE.accounts if MASTER_APP_INSTANCE else [], security_guard=SECURITY_GUARD_SETTINGS)
            update_tray_guard_status()
            text, kb = get_security_guard_panel(lang)
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
            await query.answer("تم تغيير حالة الحماية" if lang == "ar" else "Guard status updated")
            return
        elif data == "guard_cam":
            SECURITY_GUARD_SETTINGS["alert_webcam"] = not SECURITY_GUARD_SETTINGS.get("alert_webcam", True)
            save_config(MASTER_APP_INSTANCE.accounts if MASTER_APP_INSTANCE else [], security_guard=SECURITY_GUARD_SETTINGS)
            text, kb = get_security_guard_panel(lang)
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
            await query.answer("تم تحديث إعداد الكاميرا" if lang == "ar" else "Webcam setting updated")
            return
        elif data == "guard_screen":
            SECURITY_GUARD_SETTINGS["alert_screen"] = not SECURITY_GUARD_SETTINGS.get("alert_screen", True)
            save_config(MASTER_APP_INSTANCE.accounts if MASTER_APP_INSTANCE else [], security_guard=SECURITY_GUARD_SETTINGS)
            text, kb = get_security_guard_panel(lang)
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
            await query.answer("تم تحديث إعداد لقطة الشاشة" if lang == "ar" else "Screenshot setting updated")
            return
        elif data == "guard_test":
            await query.answer("🧪 جاري إرسال تنبيه تجريبي..." if lang == "ar" else "🧪 Sending test alert...")
            now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
            uptime_text = get_system_uptime(lang)
            caption = (
                f"🧪 **تنبيه أمني تجريبي (Test Alert):**\n\n"
                f"⏰ **الوقت:** `{now_str}`\n"
                f"💻 **الجهاز:** `{platform.node()}`\n\n"
                f"{uptime_text}\n\n"
                f"✅ تم اختبار وصول إشعار درع الحماية بنجاح."
                if lang == "ar" else
                f"🧪 **Security Test Alert:**\n\n⏰ `{now_str}`\n💻 `{platform.node()}`\n\n{uptime_text}"
            )
            wpath = None
            if SECURITY_GUARD_SETTINGS.get("alert_webcam", True):
                ok, wp = capture_webcam_photo()
                if ok: wpath = wp
            spath = None
            if SECURITY_GUARD_SETTINGS.get("alert_screen", True):
                sp, _, _ = capture_screen_ultra_hd()
                spath = sp
            broadcast_security_alert(wpath, spath, caption)
            return
        elif data == "wifi_show_all":
            await query.answer()
            report = get_saved_wifi_passwords(None, lang=lang, show_all=True)
            await query.message.reply_text(report, parse_mode="Markdown")
            return
        elif data == "wifi_search_prompt":
            await query.answer()
            context.user_data["awaiting_input"] = ("wifi_search", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_input")]])
            await query.message.reply_text(
                "🔍 أرسل الآن اسم شبكة الواي فاي للبحث عن كلمة سرها المحفوظة:" if lang == "ar" else "🔍 Send Wi-Fi network name to search its password:",
                reply_markup=cancel_kb
            )
            return

        if data.startswith("play_yt:"):
            await query.answer()
            vid = data.split("play_yt:")[1]
            yt_url = f"https://www.youtube.com/watch?v={vid}"
            webbrowser.open(yt_url)
            controls = [
                [
                    InlineKeyboardButton("🔉 -10%", callback_data="vol_down"),
                    InlineKeyboardButton("⏸️ Play/Pause", callback_data="media_pause"),
                    InlineKeyboardButton("🔊 +10%", callback_data="vol_up"),
                    InlineKeyboardButton("🔇 Mute", callback_data="vol_mute")
                ],
                [
                    InlineKeyboardButton("📸 Take Screenshot", callback_data="take_screenshot"),
                    InlineKeyboardButton("🏠 Back to Main Menu", callback_data="goto_main")
                ]
            ]
            play_msg = (
                f"▶️ **تم التشغيل مباشرة على شاشة الكمبيوتر في نافذة واحدة:**\n`{yt_url}`\n\nأزرار التحكم الفوري:"
                if lang == "ar" else
                f"▶️ **Now Playing on your PC (Single Window):**\n`{yt_url}`\n\nInstant controls:"
            )
            await query.message.reply_text(
                play_msg,
                reply_markup=InlineKeyboardMarkup(controls),
                parse_mode="Markdown"
            )
            return

        if data.startswith("rec_sec:"):
            try:
                sec = int(data.split(":")[1])
            except Exception:
                sec = 30
            await query.answer(f"🎙️ جاري تسجيل {sec} ثانية..." if lang == "ar" else f"🎙️ Recording {sec}s...")
            await do_record_mic(query.message, lang, sec)
            return

        if data.startswith("install_update:"):
            tag = data.split(":", 1)[1]
            await query.answer("⏳ جاري تنزيل التحديث..." if lang == "ar" else "⏳ Downloading update...", show_alert=False)
            has_up, msg, info = check_for_github_update()
            if not has_up:
                await query.message.reply_text(f"ℹ️ {msg}")
                return
            asset = info.get("asset")
            if not asset or not asset.get("browser_download_url"):
                await query.message.reply_text("❌ لم يتم العثور على ملف التثبيت في الإصدار." if lang == "ar" else "❌ Installer asset not found in release.")
                return
            down_url = asset["browser_download_url"]
            fname = asset.get("name", "PC_Remote_Pro_Setup.exe")
            await query.message.reply_text(
                f"📥 **جاري تحميل التحديث الجديد ({fname})...**\nيرجى الانتظار لحين اكتمال التنزيل والبدء بالتثبيت."
                if lang == "ar" else
                f"📥 **Downloading update ({fname})...** Please wait.",
                parse_mode="Markdown"
            )
            ok_down, out_file = await asyncio.to_thread(download_github_release_asset, down_url, fname)
            if ok_down and os.path.exists(out_file):
                try:
                    try:
                        ctypes.windll.shell32.ShellExecuteW(None, "runas", out_file, None, None, 1)
                    except Exception:
                        os.startfile(out_file)
                    await query.message.reply_text(
                        f"✅ **تم تحميل التحديث بنجاح!**\n🚀 تم تشغيل برنامج التثبيت `{fname}` على الكمبيوتر الآن للتحديث التلقائي.\n\nيمكنك إتمام خطوات التثبيت على شاشة الكمبيوتر."
                        if lang == "ar" else
                        f"✅ **Update downloaded successfully!**\n🚀 Running installer `{fname}` on PC now.",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    await query.message.reply_text(f"⚠️ تم تحميل الملف ولكن تعذر تشغيله تلقائياً: {e}\nالمسار: `{out_file}`")
            else:
                await query.message.reply_text(f"❌ فشل تحميل التحديث: {out_file}")
            return

        parts = data.split(":")
        action = parts[0]

        if action == "nav":
            await query.answer()
            path_uid = parts[1]
            page = int(parts[2]) if len(parts) > 2 else 0
            selected_path = PATH_STORE.get(path_uid)
            if not selected_path or not os.path.exists(selected_path):
                await query.edit_message_text("❌ Path not found or removed.")
                return

            text_msg, markup = build_advanced_explorer(selected_path, page)
            try:
                await query.edit_message_text(text_msg, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                pass

        elif action == "ask":
            await query.answer()
            file_uid = parts[1]
            selected_file = PATH_STORE.get(file_uid)
            if not selected_file or not os.path.isfile(selected_file):
                await query.message.reply_text("❌ File no longer exists.")
                return
            fname = os.path.basename(selected_file)
            fsize_mb = os.path.getsize(selected_file) / (1024 * 1024)
            kb = [
                [InlineKeyboardButton("🚀 Open on PC Screen", callback_data=f"open_pc:{file_uid}")],
                [InlineKeyboardButton(f"📥 Download to Phone ({fsize_mb:.1f} MB)", callback_data=f"get:{file_uid}")],
                [InlineKeyboardButton("🔙 Cancel", callback_data=f"nav:{get_path_id(os.path.dirname(selected_file))}:0")]
            ]
            await query.message.reply_text(
                f"📄 **Selected File:** `{fname}`\n"
                f"💾 Size: `{fsize_mb:.2f} MB`\n\n"
                f"Choose action:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="Markdown"
            )

        elif action == "open_pc":
            await query.answer("Opening on PC...")
            file_uid = parts[1]
            target_path = PATH_STORE.get(file_uid)
            if target_path and os.path.exists(target_path):
                try:
                    os.startfile(target_path)
                    await query.message.reply_text(f"🚀 **File opened on PC screen:**\n`{os.path.basename(target_path)}`", parse_mode="Markdown")
                except Exception as e:
                    await query.message.reply_text(f"❌ Failed to open: {e}")

        elif action == "get":
            await query.answer()
            file_uid = parts[1]
            target_path = PATH_STORE.get(file_uid)
            if not target_path or not os.path.exists(target_path):
                await query.message.reply_text("❌ File not found.")
                return

            size_mb = os.path.getsize(target_path) / (1024 * 1024)
            if size_mb > 48:
                await query.message.reply_text(f"⚠️ Telegram bot file transfer limit is 50MB. (File is `{size_mb:.1f} MB`)")
                return

            await query.message.reply_text(f"⏳ Uploading `{os.path.basename(target_path)}` ({size_mb:.1f} MB)...", parse_mode="Markdown")
            try:
                with open(target_path, "rb") as f:
                    await query.message.reply_document(document=f, caption=f"📥 `{os.path.basename(target_path)}`")
            except Exception as e:
                await query.message.reply_text(f"❌ Upload failed: {e}")

        elif action == "search_in":
            await query.answer()
            folder_uid = parts[1]
            target_folder = PATH_STORE.get(folder_uid)
            context.user_data["awaiting_input"] = ("search_in", target_folder)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]])
            await query.message.reply_text(
                f"🔎 **Search in Folder:**\n`{target_folder}`\n\nPlease send the filename or keyword to find:",
                reply_markup=cancel_kb,
                parse_mode="Markdown"
            )

    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return

        uid = update.effective_user.id
        acc_name = user_dict_ref.get(uid, {}).get("name", "User")

        if str(uid) not in USER_LANGUAGES:
            welcome_prompt = (
                "🌐 **Welcome to PC Remote Pro!**\n"
                "Please select your preferred language to customize all menus and alerts:\n\n"
                "يرجى اختيار لغتك المفضلة لتخصيص القوائم والتنبيهات:"
            )
            await update.message.reply_text(welcome_prompt, reply_markup=build_language_markup(), parse_mode="Markdown")
            return

        lang = get_user_language(uid)
        welcome = (
            f"🏠 **{B('menu_main_title', lang)} ({acc_name}):**\n"
            f"💻 {B('pc_connected_ready', lang).format(node=platform.node())}\n\n"
            f"{B('select_from_menu', lang)}"
        )
        await update.message.reply_text(
            welcome,
            reply_markup=get_menu_keyboard("main", lang),
            parse_mode="Markdown"
        )

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        if not update.message or not update.message.text:
            return

        uid = update.effective_user.id
        lang = get_user_language(uid)
        txt = update.message.text.strip()
        lowered = txt.lower()

        # Check for awaiting interactive prompts
        awaiting = context.user_data.get("awaiting_input")
        action_from_text = TEXT_TO_ACTION.get(lowered) or TEXT_TO_ACTION.get(txt)
        is_menu_button = action_from_text is not None

        if awaiting and not is_menu_button:
            action_type, extra_data = awaiting
            context.user_data["awaiting_input"] = None

            if action_type == "yt_search":
                await update.message.reply_text(f"🔍 Searching YouTube for: `{txt}` ...", parse_mode="Markdown")
                results = search_youtube_videos(txt, limit=5)
                if results:
                    top_vid, top_title = results[0]
                    buttons = [
                        [InlineKeyboardButton(f"⚡ تشغيل النتيجة الأولى فوراً (نافذة واحدة) ⚡", callback_data=f"play_yt:{top_vid}")]
                    ]
                    for idx, (vid, title) in enumerate(results, 1):
                        display_title = (title[:36] + "...") if len(title) > 36 else title
                        buttons.append([InlineKeyboardButton(f"▶️ {idx}. {display_title}", callback_data=f"play_yt:{vid}")])
                    controls_row = [
                        InlineKeyboardButton("🔉 -10%", callback_data="vol_down"),
                        InlineKeyboardButton("⏸️ Pause", callback_data="media_pause"),
                        InlineKeyboardButton("🔊 +10%", callback_data="vol_up"),
                        InlineKeyboardButton("🔇 Mute", callback_data="vol_mute")
                    ]
                    buttons.append(controls_row)
                    buttons.append([
                        InlineKeyboardButton("📸 Take Screenshot", callback_data="take_screenshot"),
                        InlineKeyboardButton("🏠 Back to Main Menu", callback_data="goto_main")
                    ])
                    await update.message.reply_text(
                        f"🎵 **نتائج البحث في يوتيوب:**\nاضغط على أي مقطع لتشغيله مباشرة في نافذة واحدة دون نوافذ إضافية:"
                        if lang == "ar" else
                        f"🎵 **YouTube Results:**\nTap any item below to play it directly in a single window:",
                        reply_markup=InlineKeyboardMarkup(buttons),
                        parse_mode="Markdown"
                    )
                else:
                    encoded = urllib.parse.quote(txt)
                    webbrowser.open(f"https://www.youtube.com/results?search_query={encoded}")
                    await update.message.reply_text(f"▶️ **Opened in YouTube:** `{txt}`", reply_markup=get_menu_keyboard("web", lang))
                return

            elif action_type == "wifi_search":
                res = get_saved_wifi_passwords(search_query=txt, lang=lang)
                await update.message.reply_text(res, reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")
                return

            elif action_type == "system_search":
                await update.message.reply_text(f"🔎 Scanning PC drives for: `{txt}` ...", parse_mode="Markdown")
                matches = []
                drives_to_search = [p.mountpoint for p in psutil.disk_partitions(all=False) if os.path.exists(p.mountpoint)]
                for d in drives_to_search:
                    for root, dirs, files in os.walk(d):
                        if any(x in root.lower() for x in ['windows', '$recycle.bin', 'programdata', 'appdata']):
                            continue
                        for item in dirs + files:
                            if txt.lower() in item.lower():
                                matches.append(os.path.join(root, item))
                                if len(matches) >= 12: break
                        if len(matches) >= 12: break
                    if len(matches) >= 12: break

                if not matches:
                    await update.message.reply_text("❌ No files matching your search term.", reply_markup=get_menu_keyboard("files", lang))
                    return

                kb = []
                for p in matches:
                    fname = os.path.basename(p)
                    uid_p = get_path_id(p)
                    if os.path.isdir(p):
                        kb.append([InlineKeyboardButton(f"📁 {fname}", callback_data=f"nav:{uid_p}:0")])
                    else:
                        kb.append([InlineKeyboardButton(f"📄 {fname}", callback_data=f"ask:{uid_p}")])
                kb.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="goto_main")])
                await update.message.reply_text("🔍 **Search Results (Tap to Open or Download):**", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                return

            elif action_type == "search_in":
                folder_to_search = extra_data
                await update.message.reply_text(f"⏳ Searching for `{txt}` inside `{folder_to_search}`...", parse_mode="Markdown")
                matches = []
                for root, dirs, files in os.walk(folder_to_search):
                    for item in dirs + files:
                        if txt.lower() in item.lower():
                            matches.append(os.path.join(root, item))
                            if len(matches) >= 12: break
                    if len(matches) >= 12: break

                if not matches:
                    await update.message.reply_text(f"❌ No files found matching `{txt}`.", reply_markup=get_menu_keyboard("files", lang))
                    return

                kb = []
                for p in matches:
                    fname = os.path.basename(p)
                    uid_p = get_path_id(p)
                    if os.path.isdir(p):
                        kb.append([InlineKeyboardButton(f"📁 {fname}", callback_data=f"nav:{uid_p}:0")])
                    else:
                        kb.append([InlineKeyboardButton(f"📄 {fname}", callback_data=f"ask:{uid_p}")])
                kb.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="goto_main")])
                await update.message.reply_text("🔍 **Folder Search Results:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                return

            elif action_type == "screen_msg":
                show_screen_popup(txt)
                await update.message.reply_text(
                    f"📢 **Popup Alert Displayed on PC:**\n\"{txt}\"",
                    reply_markup=get_menu_keyboard("tools", lang),
                    parse_mode="Markdown"
                )
                return

            elif action_type == "text_speech":
                speak_text(txt)
                await update.message.reply_text(
                    f"🗣 **Speaking text on PC speakers:**\n\"{txt}\"",
                    reply_markup=get_menu_keyboard("tools", lang),
                    parse_mode="Markdown"
                )
                return

            elif action_type == "run_py_code":
                await update.message.reply_text("⏳ جاري تنفيذ كود بايثون على الكمبيوتر..." if lang == "ar" else "⏳ Running Python code on PC...", parse_mode="Markdown")
                t_start = time.time()
                try:
                    p = subprocess.run(
                        [sys.executable, "-c", txt],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        creationflags=0x08000000 if sys.platform == "win32" else 0
                    )
                    t_dur = time.time() - t_start
                    out = (p.stdout or "") + ("\n[Error]:\n" + p.stderr if p.stderr else "")
                    out = out.strip() or "(Execution finished with zero output)"
                    if len(out) > 3500:
                        out = out[:3500] + "\n... [Truncated]"
                    await update.message.reply_text(
                        f"🐍 **نتيجة كود بايثون (استغرق {t_dur:.2f} ثانية):**\n```\n{out}\n```"
                        if lang == "ar" else
                        f"🐍 **Python Output (Duration: {t_dur:.2f}s):**\n```\n{out}\n```",
                        reply_markup=get_menu_keyboard("main", lang),
                        parse_mode="Markdown"
                    )
                except subprocess.TimeoutExpired:
                    await update.message.reply_text("⏱️ انتهت المهلة المحددة للكود (تجاوزت 30 ثانية).", reply_markup=get_menu_keyboard("main", lang))
                except Exception as e:
                    await update.message.reply_text(f"❌ خطأ أثناء تنفيذ الكود: {e}", reply_markup=get_menu_keyboard("main", lang))
                return

            elif action_type == "run_py_file":
                file_target = txt.strip().strip('"')
                if not os.path.exists(file_target):
                    # Try relative to desktop or downloads
                    for base in [DESKTOP_FOLDER, DOWNLOADS_FOLDER]:
                        cand = os.path.join(base, file_target)
                        if os.path.exists(cand):
                            file_target = cand
                            break
                if not os.path.exists(file_target):
                    await update.message.reply_text(f"❌ لم يتم العثور على الملف: `{file_target}`", reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")
                    return
                await update.message.reply_text(f"⏳ جاري تشغيل ملف بايثون: `{os.path.basename(file_target)}` ...", parse_mode="Markdown")
                t_start = time.time()
                try:
                    p = subprocess.run(
                        [sys.executable, file_target],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        creationflags=0x08000000 if sys.platform == "win32" else 0
                    )
                    t_dur = time.time() - t_start
                    out = (p.stdout or "") + ("\n[Error]:\n" + p.stderr if p.stderr else "")
                    out = out.strip() or "(Script finished with zero output)"
                    if len(out) > 3500:
                        out = out[:3500] + "\n... [Truncated]"
                    await update.message.reply_text(
                        f"🐍 **مخرجات تشغيل الملف (استغرق {t_dur:.2f} ثانية):**\n```\n{out}\n```"
                        if lang == "ar" else
                        f"🐍 **Script Output (Duration: {t_dur:.2f}s):**\n```\n{out}\n```",
                        reply_markup=get_menu_keyboard("main", lang),
                        parse_mode="Markdown"
                    )
                except subprocess.TimeoutExpired:
                    await update.message.reply_text("⏱️ انتهت المهلة المحددة (تجاوزت 60 ثانية).", reply_markup=get_menu_keyboard("main", lang))
                except Exception as e:
                    await update.message.reply_text(f"❌ خطأ أثناء تشغيل الملف: {e}", reply_markup=get_menu_keyboard("main", lang))
                return

            elif action_type == "apps_search":
                query_str = txt.lower().strip()
                apps = get_installed_pc_apps()
                matched = [a for a in apps if query_str in a["name"].lower()]
                if matched:
                    matched = matched[:10]
                    kb = []
                    for a in matched:
                        idx_found = apps.index(a)
                        kb.append([InlineKeyboardButton(f"🚀 {a['name'][:30]}", callback_data=f"launch_app:{idx_found}")])
                    kb.append([
                        InlineKeyboardButton("📋 كل التطبيقات", callback_data="apps_page:0"),
                        InlineKeyboardButton("🏠 الرئيسية", callback_data="goto_main")
                    ])
                    await update.message.reply_text(
                        f"🔍 **نتائج البحث عن: \"{txt}\":**\nاضغط على أي برنامج لتشغيله واستلام لقطة شاشة:"
                        if lang == "ar" else
                        f"🔍 **Results for \"{txt}\":**\nTap below to launch on PC and verify:",
                        reply_markup=InlineKeyboardMarkup(kb),
                        parse_mode="Markdown"
                    )
                else:
                    kb = [
                        [InlineKeyboardButton(f'⚡ محاولة تشغيل "{txt}" كأمر', callback_data="prompt_cmd_exec")],
                        [InlineKeyboardButton("📋 تصفح التطبيقات", callback_data="apps_page:0"), InlineKeyboardButton("🏠 الرئيسية", callback_data="goto_main")]
                    ]
                    await update.message.reply_text(
                        f"⚠️ لم يتم العثور على برنامج يطابق: `{txt}`\nيمكنك تصفح القائمة الكاملة أو محاولة تشغيله كأمر مباشر:"
                        if lang == "ar" else
                        f"⚠️ No matching application found for `{txt}`.",
                        reply_markup=InlineKeyboardMarkup(kb),
                        parse_mode="Markdown"
                    )
                return

            elif action_type == "apps_path":
                raw_path = txt.strip().strip('"')
                await update.message.reply_text(f"⏳ **جاري فتح المسار:** `{raw_path}` ...", parse_mode="Markdown")
                success, msg = launch_pc_application(raw_path)
                await asyncio.sleep(2.5)
                temp_img, width, height = await asyncio.to_thread(capture_screen_ultra_hd)
                now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
                caption_text = (
                    f"🚀 **تم فتح الملف/البرنامج بنجاح!**\n"
                    f"📂 المسار: `{raw_path}`\n"
                    f"⏰ التوقيت: `{now_str}`\n"
                    f"📸 مرفق لقطة للشاشة للتأكد:"
                    if lang == "ar" else
                    f"🚀 **Launched successfully:** `{raw_path}`\n⏰ Time: `{now_str}`"
                )
                kb = [
                    [InlineKeyboardButton("📸 أخذ لقطة شاشة أخرى", callback_data="take_screenshot")],
                    [InlineKeyboardButton("📋 عودة للتطبيقات", callback_data="apps_page:0"), InlineKeyboardButton("🏠 الرئيسية", callback_data="goto_main")]
                ]
                if temp_img and os.path.exists(temp_img):
                    try:
                        with open(temp_img, "rb") as photo:
                            await update.message.reply_photo(photo=photo, caption=caption_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                    finally:
                        try: os.remove(temp_img)
                        except Exception: pass
                else:
                    await update.message.reply_text(f"✅ {msg}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                return
            elif action_type == "run_cmd":
                await update.message.reply_text(f"⚡ Executing command on PC: `{txt}` ...", parse_mode="Markdown")
                try:
                    p = subprocess.run(
                        txt,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=15,
                        creationflags=0x08000000 if sys.platform == "win32" else 0
                    )
                    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
                    out = out.strip() or "(Command completed with zero output)"
                    if len(out) > 3500:
                        out = out[:3500] + "\n... [Truncated]"
                    await update.message.reply_text(
                        f"⚡ **Command Output:**\n```\n{out}\n```",
                        reply_markup=get_menu_keyboard("tools", lang),
                        parse_mode="Markdown"
                    )
                except subprocess.TimeoutExpired:
                    await update.message.reply_text("⏱️ Timeout (exceeded 15s). Command continues in background.", reply_markup=get_menu_keyboard("tools", lang))
                except Exception as e:
                    await update.message.reply_text(f"❌ Command execution error: {e}", reply_markup=get_menu_keyboard("tools", lang))
                return

            elif action_type == "set_clip":
                ok = set_native_clipboard_text(txt)
                if ok:
                    await update.message.reply_text(
                        f"📋 **Copied to PC Clipboard:**\n`{txt}`",
                        reply_markup=get_menu_keyboard("tools", lang),
                        parse_mode="Markdown"
                    )
                else:
                    await update.message.reply_text("❌ Failed to write to PC clipboard.", reply_markup=get_menu_keyboard("tools", lang))
                return

        elif awaiting and is_menu_button:
            context.user_data["awaiting_input"] = None

        # Resolve Action from Universal Multi-Language Mapping
        act = action_from_text

        if act == "btn_hide_menu":
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("📱 إظهار لوحة التحكم / Show Menu", callback_data="show_menu")]])
            msg = (
                "🔽 **تم طي وإخفاء قائمة التحكم لتوفير مساحة شاشة الموبايل.**\n\n"
                "💡 يمكنك الكتابة والتحكم بحرية دون حجب الشاشة.\n"
                "لإعادة إظهار القائمة في أي وقت، اضغط على الزر أدناه أو أرسل `/menu` أو `/start`."
                if lang == "ar" else
                "🔽 **Menu keyboard is now folded to maximize mobile screen space.**\n\n"
                "💡 Tap the button below or send `/menu` anytime to restore the keyboard."
            )
            await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
            await update.message.reply_text(
                "📱 **اضغط هنا لفتح القائمة عند الحاجة:**" if lang == "ar" else "📱 **Tap here to open menu:**",
                reply_markup=kb,
                parse_mode="Markdown"
            )
            return

        elif act == "btn_apps":
            apps = get_installed_pc_apps()
            total_apps = len(apps)
            kb = [
                [InlineKeyboardButton("📋 تصفح التطبيقات (صفحة 1)", callback_data="apps_page:0"), InlineKeyboardButton("🔍 بحث عن برنامج", callback_data="apps_search_prompt")],
                [InlineKeyboardButton("⚡ تشغيل ملف بالمسار", callback_data="apps_path_prompt"), InlineKeyboardButton("📸 لقطة شاشة سريعة", callback_data="take_screenshot")],
                [InlineKeyboardButton("🔄 إعادة فحص التطبيقات", callback_data="apps_rescan"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
            ]
            msg = (
                f"🖥️ **مركز التحكم بتطبيقات وبرامج الكمبيوتر:**\n\n"
                f"📊 تم فحص النظام واكتشاف `{total_apps}` تطبيق وبرنامج مثبت على الحاسبة.\n\n"
                f"اختر تصفح التطبيقات للفتح المباشر وأخذ لقطة شاشة للتأكد، أو ابحث عن أي برنامج بالاسم:"
                if lang == "ar" else
                f"🖥️ **PC Applications & Programs Center:**\n\n"
                f"📊 Discovered `{total_apps}` installed applications.\n\n"
                f"Browse apps to launch and verify with a screenshot, or search by name:"
            )
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        elif act == "btn_cmd_py":
            kb = [
                [InlineKeyboardButton("⚡ تنفيذ أمر CMD واستلام النتيجة", callback_data="prompt_cmd_exec"), InlineKeyboardButton("🖥️ فتح نافذة CMD على الشاشة", callback_data="open_cmd_desktop")],
                [InlineKeyboardButton("🐍 تشغيل ملف بايثون (.py)", callback_data="prompt_py_file"), InlineKeyboardButton("🐍 كتابة وتنفيذ كود بايثون مباشر", callback_data="prompt_py_code")],
                [InlineKeyboardButton("📸 أخذ لقطة شاشة", callback_data="take_screenshot"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
            ]
            msg = (
                "⚡ **أوامر النظام وموجه بايثون (CMD & Python Terminal):**\n\n"
                "تحكم مباشر بنظام Windows وبناء وتنفيذ أكواد بايثون عن بُعد:\n"
                "1. تنفيذ أي أمر في موجه الأوامر (CMD/PowerShell) ورؤية النتيجة فوراً.\n"
                "2. فتح نافذة CMD مرئية على شاشة الكمبيوتر.\n"
                "3. تشغيل أي ملف بايثون (.py) على الحاسبة.\n"
                "4. كتابة كود بايثون مباشرة في المحادثة وتنفيذه مع النتيجة."
                if lang == "ar" else
                "⚡ **System Terminal & Python Engine:**\n\n"
                "Execute terminal commands and run Python code remotely:"
            )
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        elif act == "btn_smart_network":
            ok = SMART_NETWORK_WATCHDOG.check_internet()
            status_emoji = "🟢" if ok else "🔴"
            status_word = "متصل وسليم (Online)" if ok else "غير متصل (Offline)"
            now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
            heartbeat_str = "مفعل ✅" if SMART_NETWORK_WATCHDOG.heartbeat_enabled else "معطل ❌"
            kb = [
                [InlineKeyboardButton("🛠️ فحص وصيانة وإصلاح الشبكة فوراً", callback_data="net_heal_now")],
                [InlineKeyboardButton("🔔 تفعيل/إيقاف إشعار كل دقيقة", callback_data="toggle_net_heartbeat")],
                [InlineKeyboardButton("📶 الشبكات المحفوظة", callback_data="wifi_show_all")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
            ]
            msg = (
                f"🌐 **نظام فحص وصيانة الشبكة الذاتي (Smart Network Auto-Heal):**\n\n"
                f"💻 **الجهاز:** `{platform.node()}`\n"
                f"📡 **حالة الاتصال بالإنترنت:** {status_emoji} `{status_word}`\n"
                f"📶 **الوايفاي الحالي:** `{SMART_NETWORK_WATCHDOG.get_current_wifi_ssid()}`\n"
                f"⏰ **آخر فحص:** `{now_str}`\n"
                f"💓 **إشعار نبض الشبكة كل دقيقة:** `{heartbeat_str}`\n\n"
                f"💡 _يقوم النظام الذكي بمراقبة الشبكة في الخلفية كل 60 ثانية تلقائياً، وفي حال انقطاع النت يعيد ربط الوايفاي وتنظيف الـ DNS وإصلاح المشكلة مباشرة دون الحاجة لتدخل المستخدم._"
                if lang == "ar" else
                f"🌐 **Smart Network Auto-Heal System:**\n\n"
                f"Status: {status_emoji} `{status_word}`\n"
                f"Wi-Fi: `{SMART_NETWORK_WATCHDOG.get_current_wifi_ssid()}`\n"
                f"Heartbeat every 1-min: `{heartbeat_str}`"
            )
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        # 1. Screen Capture
        elif act == "btn_screen":
            await send_screenshot_message(update.message, lang)
            return

        # 2. HD Webcam Snapshot
        elif act == "btn_webcam":
            await update.message.reply_text("📷 Capturing snapshot from PC webcam...")
            success, result = await asyncio.to_thread(capture_webcam_photo)
            if success:
                try:
                    now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
                    with open(result, "rb") as photo:
                        await update.message.reply_photo(
                            photo=photo,
                            caption=f"📷 **PC Webcam Snapshot**\n💻 Host: `{platform.node()}`\n⏰ Captured: `{now_str}`",
                            reply_markup=get_menu_keyboard("main", lang),
                            parse_mode="Markdown"
                        )
                finally:
                    if os.path.exists(result):
                        try: os.remove(result)
                        except Exception: pass
            else:
                await update.message.reply_text(f"⚠️ {result}", reply_markup=get_menu_keyboard("main", lang))
            return

        # 3. Microphone Audio Recording (Supports 30s+)
        elif act == "btn_mic":
            await do_record_mic(update.message, lang, 30)
            return

        # In-App Auto Update Action
        elif act == "btn_update_app":
            await update.message.reply_text("🔄 جاري التحقق من وجود تحديث جديد للتطبيق..." if lang == "ar" else "🔄 Checking for application updates...")
            has_up, msg_or_tag, rel_info = await asyncio.to_thread(check_for_github_update)
            if has_up:
                tag = rel_info.get("tag", msg_or_tag)
                kb = [
                    [InlineKeyboardButton(f"🚀 تنزيل وتثبيت التحديث ({tag})", callback_data=f"install_update:{tag}")],
                    [InlineKeyboardButton("🌐 فتح صفحة الإصدارات في المتصفح", url=GITHUB_RELEASES_URL)],
                    [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
                ]
                rep_msg = (
                    f"🎉 **يوجد تحديث جديد متاح!**\n\n"
                    f"📌 **الإصدار الجديد:** `{tag}`\n"
                    f"📝 **ملاحظات الإصدار:**\n{rel_info.get('notes', 'تحديث أمان وتحسينات شاملة')[:300]}\n\n"
                    f"اضغط على زر التنزيل والتثبيت أدناه ليقوم الكمبيوتر بتحميله وتشغيل التثبيت تلقائياً:"
                    if lang == "ar" else
                    f"🎉 **New Update Available!**\n\n📌 **Version:** `{tag}`\n\nTap below to download and install automatically:"
                )
                await update.message.reply_text(rep_msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            else:
                kb = [
                    [InlineKeyboardButton("🌐 فتح GitHub Releases", url=GITHUB_RELEASES_URL)],
                    [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
                ]
                await update.message.reply_text(
                    f"ℹ️ **الإصدار الحالي:** `{APP_VERSION}` (أنت تستخدم أحدث إصدار بالفعل)"
                    if lang == "ar" else
                    f"ℹ️ **Current Version:** `{APP_VERSION}` (Up to date)",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode="Markdown"
                )
            return

        # 4. PC Hardware Specs
        elif act == "btn_specs":
            specs_report = get_full_pc_specs(lang)
            await update.message.reply_text(specs_report, reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")
            return

        # 5. Wi-Fi & Network Diagnostics
        elif act == "btn_wifi":
            wifi_report = get_wifi_and_network_info(lang)
            await update.message.reply_text(wifi_report, reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")
            return

        elif act == "btn_wifi_passwords":
            quick_report = get_saved_wifi_passwords(search_query=None, lang=lang)
            kb = [
                [InlineKeyboardButton("🔍 بحث عن شبكة معينة", callback_data="wifi_search_prompt")],
                [InlineKeyboardButton("📋 عرض كل الشبكات المحفوظة", callback_data="wifi_show_all")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
            ]
            await update.message.reply_text(quick_report, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        elif act == "btn_uptime":
            uptime_report = get_system_uptime(lang)
            await update.message.reply_text(uptime_report, reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")
            return

        elif act == "btn_security_guard":
            panel_text, panel_kb = get_security_guard_panel(lang)
            await update.message.reply_text(panel_text, reply_markup=panel_kb, parse_mode="Markdown")
            return

        # 6. Ping & Latency Diagnostics
        elif act == "btn_ping":
            await update.message.reply_text("🏓 Running ping and latency test to global servers...")
            ping_res = await asyncio.to_thread(test_network_latency)
            await update.message.reply_text(ping_res, reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")
            return

        # 7. Interactive Process Killer
        elif act == "btn_kill":
            txt_procs, markup_procs = get_top_processes_for_kill()
            await update.message.reply_text(txt_procs, reply_markup=markup_procs, parse_mode="Markdown")
            return

        # 8. Language Selector
        elif act == "btn_lang":
            await update.message.reply_text(
                "🌐 **Select Your Language / اختر لغتك:**",
                reply_markup=build_language_markup(),
                parse_mode="Markdown"
            )
            return

        # 9. Direct Battery Check
        elif act == "btn_battery":
            await update.message.reply_text(get_battery_info(), reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")
            return

        # 10. Navigation & Submenus
        elif act == "btn_back":
            await start_cmd(update, context)
            return
        elif act == "btn_files":
            await update.message.reply_text(f"📂 **{B('btn_files', lang)}:**", reply_markup=get_menu_keyboard("files", lang), parse_mode="Markdown")
            return
        elif act == "btn_web":
            await update.message.reply_text(f"🌐 **{B('btn_web', lang)}:**", reply_markup=get_menu_keyboard("web", lang), parse_mode="Markdown")
            return
        elif act == "btn_power":
            await update.message.reply_text(f"⚡ **{B('btn_power', lang)}:**", reply_markup=get_menu_keyboard("power", lang), parse_mode="Markdown")
            return
        elif act == "btn_monitor":
            await update.message.reply_text(f"📊 **{B('btn_monitor', lang)}:**", reply_markup=get_menu_keyboard("monitor", lang), parse_mode="Markdown")
            return
        elif act == "btn_media":
            await update.message.reply_text(f"🎛 **{B('btn_media', lang)}:**", reply_markup=get_menu_keyboard("media", lang), parse_mode="Markdown")
            return
        elif act == "btn_tools":
            await update.message.reply_text(f"🛠️ **{B('btn_tools', lang)}:**", reply_markup=get_menu_keyboard("tools", lang), parse_mode="Markdown")
            return

        # 11. Files Actions
        elif act == "btn_desktop":
            t, m = build_advanced_explorer(DESKTOP_FOLDER, 0)
            await update.message.reply_text(t, reply_markup=m, parse_mode="Markdown")
            return
        elif act == "btn_downloads":
            t, m = build_advanced_explorer(DOWNLOADS_FOLDER, 0)
            await update.message.reply_text(t, reply_markup=m, parse_mode="Markdown")
            return
        elif act == "btn_documents":
            t, m = build_advanced_explorer(DOCUMENTS_FOLDER, 0)
            await update.message.reply_text(t, reply_markup=m, parse_mode="Markdown")
            return
        elif act == "btn_drives":
            await update.message.reply_text("💾 **Storage Drives:**", reply_markup=build_drives_markup(), parse_mode="Markdown")
            return
        elif act == "btn_search":
            context.user_data["awaiting_input"] = ("system_search", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]])
            await update.message.reply_text(
                "🔎 **System-Wide Search:**\nEnter filename or keyword to find across all drives:",
                reply_markup=cancel_kb,
                parse_mode="Markdown"
            )
            return
        elif act == "btn_scan_bin":
            await update.message.reply_text("⏳ Scanning Recycle Bin...")
            rep = get_recycle_bin_report()
            await update.message.reply_text(rep, reply_markup=get_menu_keyboard("files", lang), parse_mode="Markdown")
            return
        elif act == "btn_empty_bin":
            ok, msg = empty_recycle_bin()
            await update.message.reply_text(msg, reply_markup=get_menu_keyboard("files", lang), parse_mode="Markdown")
            return

        # 12. Web Actions
        elif act == "btn_play_yt":
            context.user_data["awaiting_input"] = ("yt_search", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel / إلغاء", callback_data="cancel_input")]])
            msg = (
                "🎵 **مشغل وباحث يوتيوب المباشر (Direct YouTube Player):**\n\n"
                "أرسل الآن اسم المقطع أو الموضوع الذي تريد تشغيله على شاشة وسماعات الكمبيوتر.\n\n"
                "💡 _ملاحظة: لست بحاجة لفتح جوجل أو يوتيوب مسبقاً! سيتم البحث وفتح الفيديو مباشرة في نافذة واحدة._"
                if lang == "ar" else
                "🎵 **Direct YouTube Player:**\n\nPlease send the topic or video name you want to play on your PC:\n(No need to open Chrome or YouTube homepage first; opens in a single window!)"
            )
            await update.message.reply_text(
                msg,
                reply_markup=cancel_kb,
                parse_mode="Markdown"
            )
            return
        elif act == "btn_chrome":
            webbrowser.open("https://www.google.com")
            await update.message.reply_text("🌐 Google Chrome opened on PC.", reply_markup=get_menu_keyboard("web", lang))
            return
        elif act == "btn_youtube":
            webbrowser.open("https://www.youtube.com")
            await update.message.reply_text("▶️ YouTube homepage opened on PC.", reply_markup=get_menu_keyboard("web", lang))
            return

        # 13. Power Actions (with Wake Up)
        elif act == "btn_wake":
            wake_up_pc()
            await asyncio.sleep(0.5)
            await send_screenshot_message(update.message, lang)
            await update.message.reply_text("☀️ **PC display woken up & activity signaled.**", reply_markup=get_menu_keyboard("power", lang), parse_mode="Markdown")
            return
        elif act == "btn_sleep":
            await update.message.reply_text("😴 Putting PC into Sleep Mode now...", reply_markup=get_menu_keyboard("power", lang))
            await asyncio.sleep(1)
            execute_sleep()
            return
        elif act == "btn_screen_off":
            execute_screen_off()
            await update.message.reply_text("🖥️ **Display turned off.** PC is still active in the background.", reply_markup=get_menu_keyboard("power", lang), parse_mode="Markdown")
            return
        elif act == "btn_shutdown":
            execute_shutdown(seconds=10)
            await update.message.reply_text("🛑 Force shutdown initiated in 10 seconds!\nTap (Cancel Shutdown) to abort.", reply_markup=get_menu_keyboard("power", lang))
            return
        elif act == "btn_cancel_shutdown":
            cancel_shutdown()
            await update.message.reply_text("✅ Shutdown successfully cancelled.", reply_markup=get_menu_keyboard("power", lang))
            return
        elif act == "btn_restart":
            await update.message.reply_text("🔄 Restarting computer in 10 seconds...", reply_markup=get_menu_keyboard("power", lang))
            execute_restart(seconds=10)
            return
        elif act == "btn_lock":
            os.system("rundll32.exe user32.dll,LockWorkStation")
            await update.message.reply_text("🔒 Workstation locked.", reply_markup=get_menu_keyboard("power", lang))
            return

        # 14. Media & Sound Actions
        elif act == "btn_vol_up":
            change_volume(up=True)
            await update.message.reply_text("🔊 Volume increased (+10%).", reply_markup=get_menu_keyboard("media", lang))
            return
        elif act == "btn_vol_down":
            change_volume(up=False)
            await update.message.reply_text("🔉 Volume decreased (-10%).", reply_markup=get_menu_keyboard("media", lang))
            return
        elif act == "btn_mute":
            toggle_audio_mute()
            await update.message.reply_text("🔇 Sound mute state toggled.", reply_markup=get_menu_keyboard("media", lang))
            return
        elif act == "btn_play_pause":
            toggle_media_play_pause()
            await update.message.reply_text("⏯️ Play/Pause media key sent to PC.", reply_markup=get_menu_keyboard("media", lang))
            return
        elif act == "btn_photos":
            img_exts = (".png", ".jpg", ".jpeg", ".webp")
            found = []
            for search_dir in [DESKTOP_FOLDER, DOWNLOADS_FOLDER]:
                for root, _, files in os.walk(search_dir):
                    for f in files:
                        if f.lower().endswith(img_exts):
                            found.append(os.path.join(root, f))
                            if len(found) >= 5: break
                    if len(found) >= 5: break
                if len(found) >= 5: break

            if not found:
                await update.message.reply_text("🖼 No photos found in Desktop/Downloads.", reply_markup=get_menu_keyboard("media", lang))
                return

            await update.message.reply_text(f"⏳ Sending {len(found)} recent photos...", reply_markup=get_menu_keyboard("media", lang))
            for img_path in found:
                try:
                    with open(img_path, "rb") as p:
                        await update.message.reply_photo(photo=p, caption=os.path.basename(img_path))
                except Exception: pass
            return
        elif act == "btn_audio":
            audio_exts = (".mp3", ".wav", ".m4a", ".aac")
            found = []
            for search_dir in [DOWNLOADS_FOLDER, DESKTOP_FOLDER]:
                for root, _, files in os.walk(search_dir):
                    for f in files:
                        if f.lower().endswith(audio_exts):
                            found.append(os.path.join(root, f))
                            if len(found) >= 8: break
                    if len(found) >= 8: break
                if len(found) >= 8: break
            if not found:
                await update.message.reply_text("🎵 No audio files found.", reply_markup=get_menu_keyboard("media", lang))
                return
            kb = []
            for ap in found:
                kb.append([InlineKeyboardButton(f"🎵 {os.path.basename(ap)}", callback_data=f"ask:{get_path_id(ap)}")])
            kb.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="goto_main")])
            await update.message.reply_text("🎵 **Available Audio Files:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return

        # 15. Monitor Actions
        elif act == "btn_cpu_ram":
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory().percent
            uptime = datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %I:%M %p")
            await update.message.reply_text(f"📊 **System Resource Status:**\n🖥 CPU Usage: `{cpu}%`\n🧠 RAM Usage: `{ram}%`\n⏰ Uptime: `{uptime}`", reply_markup=get_menu_keyboard("monitor", lang), parse_mode="Markdown")
            return
        elif act == "btn_processes":
            procs = []
            for p in psutil.process_iter(['name', 'memory_percent']):
                try: procs.append(p.info)
                except Exception: pass
            top_procs = sorted(procs, key=lambda x: x['memory_percent'] or 0, reverse=True)[:8]
            lines = [f"🔹 `{p['name']}`: RAM `{p['memory_percent']:.1f}%`" for p in top_procs]
            await update.message.reply_text("📋 **Top Memory-Consuming Processes:**\n\n" + "\n".join(lines), reply_markup=get_menu_keyboard("monitor", lang), parse_mode="Markdown")
            return
        elif act == "btn_network_ip":
            wifi_rep = get_wifi_and_network_info(lang)
            await update.message.reply_text(wifi_rep, reply_markup=get_menu_keyboard("monitor", lang), parse_mode="Markdown")
            return

        # 16. Tools & Utility Actions
        elif act == "btn_autoclip":
            sub_key = (token, uid)
            if sub_key in AUTO_CLIPBOARD_SUBS:
                AUTO_CLIPBOARD_SUBS.remove(sub_key)
                await update.message.reply_text(
                    "🔴 **Live Clipboard Sync: OFF.**\nCopied text, snippets, and files will not be forwarded automatically.",
                    reply_markup=get_menu_keyboard("tools", lang),
                    parse_mode="Markdown"
                )
            else:
                AUTO_CLIPBOARD_SUBS.add(sub_key)
                await update.message.reply_text(
                    "🟢 **Live Full Clipboard Sync: ON!**\n\n"
                    "⚡ Any text, snipping tool screenshot, image, or copied file on your PC will automatically be delivered to this chat.\n"
                    "💡 Tap the same button again to turn it off.",
                    reply_markup=get_menu_keyboard("tools", lang),
                    parse_mode="Markdown"
                )
            return
        elif act == "btn_read_clip":
            clip = get_clipboard_text()
            await update.message.reply_text(f"📋 **Current PC Clipboard Text:**\n\n`{clip}`", reply_markup=get_menu_keyboard("tools", lang), parse_mode="Markdown")
            return
        elif act == "btn_send_alert":
            context.user_data["awaiting_input"] = ("screen_msg", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]])
            await update.message.reply_text(
                "📢 **Send Screen Popup Alert:**\nEnter the message you want to appear on the PC screen:",
                reply_markup=cancel_kb,
                parse_mode="Markdown"
            )
            return
        elif act == "btn_tts":
            context.user_data["awaiting_input"] = ("text_speech", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]])
            await update.message.reply_text(
                "🗣 **Text-to-Speech:**\nEnter text for the computer to speak aloud:",
                reply_markup=cancel_kb,
                parse_mode="Markdown"
            )
            return
        elif act == "btn_cmd":
            context.user_data["awaiting_input"] = ("run_cmd", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]])
            await update.message.reply_text(
                "⚡ **Execute CMD Command:**\nEnter terminal command to run on PC:",
                reply_markup=cancel_kb,
                parse_mode="Markdown"
            )
            return
        elif act == "btn_clean_temp":
            cleaned_mb = 0
            temp_paths = [tempfile.gettempdir(), os.path.expandvars(r"%LOCALAPPDATA%\Temp")]
            for tp in set(temp_paths):
                if os.path.exists(tp):
                    for root, _, files in os.walk(tp):
                        for f in files:
                            try:
                                fp = os.path.join(root, f)
                                sz = os.path.getsize(fp)
                                os.remove(fp)
                                cleaned_mb += sz / (1024 * 1024)
                            except Exception:
                                pass
            await update.message.reply_text(f"🧹 Cleaned temporary cache: `{cleaned_mb:.1f} MB` reclaimed.", reply_markup=get_menu_keyboard("tools", lang), parse_mode="Markdown")
            return
        elif act == "btn_calc":
            subprocess.Popen("calc.exe", shell=True)
            await update.message.reply_text("🧮 Calculator opened on PC screen.", reply_markup=get_menu_keyboard("tools", lang))
            return

        # Direct text message: Automatically set to PC clipboard for instant copy-paste!
        ok = set_native_clipboard_text(txt)
        if ok:
            await update.message.reply_text(
                f"📋 **Copied to PC Clipboard directly:**\n`{txt}`\n\n💡 You can now press **Ctrl+V** on your PC to paste this text.",
                reply_markup=get_menu_keyboard("main", lang),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"💡 **Command:** `{txt}`\nPlease choose an option from the menu below:",
                reply_markup=get_menu_keyboard("main", lang),
                parse_mode="Markdown"
            )

    # ----------------- Command Handlers -----------------
    async def wake_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        wake_up_pc()
        await asyncio.sleep(0.5)
        await send_screenshot_message(update.message, lang)
        await update.message.reply_text("☀️ **PC display woken up & activity signaled.**", reply_markup=get_menu_keyboard("power", lang), parse_mode="Markdown")

    async def battery_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        await update.message.reply_text(get_battery_info(), reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")

    async def guard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        args = context.args or []
        if args and args[0].lower() in ["on", "enable", "start", "تفعيل", "تشغيل"]:
            SECURITY_GUARD_SETTINGS["enabled"] = True
            save_config(MASTER_APP_INSTANCE.accounts if MASTER_APP_INSTANCE else [], security_guard=SECURITY_GUARD_SETTINGS)
            update_tray_guard_status()
            await update.message.reply_text("🛡️ **تم تفعيل درع الحماية بنجاح!** ✅", reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")
            return
        elif args and args[0].lower() in ["off", "disable", "stop", "تعطيل", "ايقاف"]:
            SECURITY_GUARD_SETTINGS["enabled"] = False
            save_config(MASTER_APP_INSTANCE.accounts if MASTER_APP_INSTANCE else [], security_guard=SECURITY_GUARD_SETTINGS)
            update_tray_guard_status()
            await update.message.reply_text("🛑 **تم إيقاف درع الحماية مؤقتاً.** ❌", reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")
            return
        text, kb = get_security_guard_panel(lang)
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

    async def uptime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        await update.message.reply_text(get_system_uptime(lang), reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")

    async def wifipass_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        query_str = " ".join(context.args).strip() if context.args else None
        if query_str:
            res = get_saved_wifi_passwords(search_query=query_str, lang=lang)
            await update.message.reply_text(res, reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")
        else:
            quick_report = get_saved_wifi_passwords(search_query=None, lang=lang)
            kb = [
                [InlineKeyboardButton("🔍 بحث عن شبكة معينة", callback_data="wifi_search_prompt")],
                [InlineKeyboardButton("📋 عرض كل الشبكات المحفوظة", callback_data="wifi_show_all")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
            ]
            await update.message.reply_text(quick_report, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    async def yt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        query = " ".join(context.args).strip()
        if not query:
            context.user_data["awaiting_input"] = ("yt_search", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]])
            await update.message.reply_text("🎵 Enter title to play on YouTube:", reply_markup=cancel_kb)
            return
        context.user_data["awaiting_input"] = ("yt_search", None)
        update.message.text = query
        await text_handler(update, context)

    async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        query = " ".join(context.args).strip()
        if not query:
            context.user_data["awaiting_input"] = ("system_search", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]])
            await update.message.reply_text("🔎 Enter filename to search across PC:", reply_markup=cancel_kb)
            return
        context.user_data["awaiting_input"] = ("system_search", None)
        update.message.text = query
        await text_handler(update, context)

    async def msg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        query = " ".join(context.args).strip()
        if not query:
            context.user_data["awaiting_input"] = ("screen_msg", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]])
            await update.message.reply_text("📢 Enter text to display on PC screen:", reply_markup=cancel_kb)
            return
        show_screen_popup(query)
        await update.message.reply_text(f"📢 **Popup displayed on PC screen:**\n\"{query}\"", reply_markup=get_menu_keyboard("tools", lang), parse_mode="Markdown")

    async def say_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        query = " ".join(context.args).strip()
        if not query:
            context.user_data["awaiting_input"] = ("text_speech", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]])
            await update.message.reply_text("🗣 Enter text to speak aloud through speakers:", reply_markup=cancel_kb)
            return
        speak_text(query)
        await update.message.reply_text(f"🗣 **Speaking on PC speakers:**\n\"{query}\"", reply_markup=get_menu_keyboard("tools", lang), parse_mode="Markdown")

    async def apps_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        update.message.text = "btn_apps"
        await text_handler(update, context)

    async def hide_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        update.message.text = "btn_hide_menu"
        await text_handler(update, context)

    async def py_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        query = " ".join(context.args).strip()
        if query:
            context.user_data["awaiting_input"] = ("run_py_code", None)
            update.message.text = query
            await text_handler(update, context)
        else:
            update.message.text = "btn_cmd_py"
            await text_handler(update, context)

    async def heal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        update.message.text = "btn_smart_network"
        await text_handler(update, context)

    async def cmd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        query = " ".join(context.args).strip()
        if not query:
            context.user_data["awaiting_input"] = ("run_cmd", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]])
            await update.message.reply_text("⚡ Enter command to execute on PC:", reply_markup=cancel_kb)
            return
        context.user_data["awaiting_input"] = ("run_cmd", None)
        update.message.text = query
        await text_handler(update, context)

    async def clip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        clip = get_clipboard_text()
        await update.message.reply_text(f"📋 **PC Clipboard Text:**\n\n`{clip}`", reply_markup=get_menu_keyboard("tools", lang), parse_mode="Markdown")

    async def setclip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        query = " ".join(context.args).strip()
        if not query:
            context.user_data["awaiting_input"] = ("set_clip", None)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_input")]])
            await update.message.reply_text("📋 Enter text to copy into PC clipboard:", reply_markup=cancel_kb)
            return
        set_native_clipboard_text(query)
        await update.message.reply_text(f"📋 **Copied to PC Clipboard:**\n`{query}`", reply_markup=get_menu_keyboard("tools", lang), parse_mode="Markdown")

    async def autoclip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        sub_key = (token, update.effective_user.id)
        if sub_key in AUTO_CLIPBOARD_SUBS:
            AUTO_CLIPBOARD_SUBS.remove(sub_key)
            await update.message.reply_text("🔴 **Live Full Clipboard Sync: OFF.**", reply_markup=get_menu_keyboard("tools", lang), parse_mode="Markdown")
        else:
            AUTO_CLIPBOARD_SUBS.add(sub_key)
            await update.message.reply_text(
                "🟢 **Live Full Clipboard Sync: ON!**\nAny text, snipping screenshot, image, or copied file on PC will be delivered here instantly.",
                reply_markup=get_menu_keyboard("tools", lang),
                parse_mode="Markdown"
            )

    async def empty_bin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        ok, msg = empty_recycle_bin()
        await update.message.reply_text(msg, reply_markup=get_menu_keyboard("tools", lang), parse_mode="Markdown")

    async def screen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        await send_screenshot_message(update.message, lang)

    async def webcam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        await update.message.reply_text("📷 Capturing snapshot from PC webcam...")
        success, result = await asyncio.to_thread(capture_webcam_photo)
        if success:
            try:
                now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
                with open(result, "rb") as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=f"📷 **PC Webcam Snapshot**\n💻 Host: `{platform.node()}`\n⏰ Captured: `{now_str}`",
                        reply_markup=get_menu_keyboard("main", lang),
                        parse_mode="Markdown"
                    )
            finally:
                if os.path.exists(result):
                    try: os.remove(result)
                    except Exception: pass
        else:
            await update.message.reply_text(f"⚠️ {result}", reply_markup=get_menu_keyboard("main", lang))

    async def mic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        sec = 30
        if context.args and context.args[0].isdigit():
            sec = int(context.args[0])
        await do_record_mic(update.message, lang, sec)

    async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        await update.message.reply_text("🔄 جاري التحقق من وجود تحديث جديد للتطبيق..." if lang == "ar" else "🔄 Checking for application updates...")
        has_up, msg_or_tag, rel_info = await asyncio.to_thread(check_for_github_update)
        if has_up:
            tag = rel_info.get("tag", msg_or_tag)
            kb = [
                [InlineKeyboardButton(f"🚀 تنزيل وتثبيت التحديث ({tag})", callback_data=f"install_update:{tag}")],
                [InlineKeyboardButton("🌐 فتح صفحة الإصدارات في المتصفح", url=GITHUB_RELEASES_URL)],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
            ]
            rep_msg = (
                f"🎉 **يوجد تحديث جديد متاح!**\n\n"
                f"📌 **الإصدار الجديد:** `{tag}`\n"
                f"📝 **ملاحظات الإصدار:**\n{rel_info.get('notes', 'تحديث أمان وتحسينات شاملة')[:300]}\n\n"
                f"اضغط على زر التنزيل والتثبيت أدناه ليقوم الكمبيوتر بتحميله وتشغيل التثبيت تلقائياً:"
                if lang == "ar" else
                f"🎉 **New Update Available!**\n\n📌 **Version:** `{tag}`\n\nTap below to download and install automatically:"
            )
            await update.message.reply_text(rep_msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        else:
            kb = [
                [InlineKeyboardButton("🌐 فتح GitHub Releases", url=GITHUB_RELEASES_URL)],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="goto_main")]
            ]
            await update.message.reply_text(
                f"ℹ️ **الإصدار الحالي:** `{APP_VERSION}` (أنت تستخدم أحدث إصدار بالفعل)"
                if lang == "ar" else
                f"ℹ️ **Current Version:** `{APP_VERSION}` (Up to date)",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="Markdown"
            )

    async def specs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        await update.message.reply_text(get_full_pc_specs(lang), reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")

    async def wifi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        await update.message.reply_text(get_wifi_and_network_info(lang), reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")

    async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        await update.message.reply_text("🏓 Running ping and latency test to global servers...")
        ping_res = await asyncio.to_thread(test_network_latency)
        await update.message.reply_text(ping_res, reply_markup=get_menu_keyboard("main", lang), parse_mode="Markdown")

    async def kill_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        txt_procs, markup_procs = get_top_processes_for_kill()
        await update.message.reply_text(txt_procs, reply_markup=markup_procs, parse_mode="Markdown")

    async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        await update.message.reply_text(
            "🌐 **Select Your Language / اختر لغتك:**",
            reply_markup=build_language_markup(),
            parse_mode="Markdown"
        )

    # Receive Photos from Telegram & Copy to PC Clipboard
    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        uid = update.effective_user.id
        lang = get_user_language(uid)
        photos = update.message.photo
        if photos:
            photo = photos[-1] # Largest resolution
            await update.message.reply_text("⏳ Receiving photo & copying to PC Clipboard...")
            file_obj = await context.bot.get_file(photo.file_id)
            save_dir = os.path.join(DOWNLOADS_FOLDER, "Telegram_Photos")
            os.makedirs(save_dir, exist_ok=True)
            save_name = f"photo_{int(time.time())}.jpg"
            dest = os.path.join(save_dir, save_name)
            await file_obj.download_to_drive(dest)

            # Automatically copy to PC Clipboard
            set_image_to_clipboard(dest)

            kb = [
                [InlineKeyboardButton("🚀 Open on PC Screen", callback_data=f"open_pc:{get_path_id(dest)}")],
                [InlineKeyboardButton("🖼 Set as Desktop Wallpaper", callback_data=f"set_wallpaper:{get_path_id(dest)}")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="goto_main")]
            ]
            await update.message.reply_text(
                f"✅ **Photo received and copied to PC Clipboard!**\n\n"
                f"📂 Saved to: `{dest}`\n"
                f"⚡ You can now press **Ctrl+V** anywhere on your PC to paste this image directly!\n\n"
                f"Or choose an action below:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="Markdown"
            )

    async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        voice = update.message.voice
        if voice:
            await update.message.reply_text("🎙️ Playing voice message through PC speakers...")
            file_obj = await context.bot.get_file(voice.file_id)
            save_name = f"voice_{int(time.time())}.ogg"
            dest = os.path.join(DOWNLOADS_FOLDER, save_name)
            await file_obj.download_to_drive(dest)
            try:
                os.startfile(dest)
                await update.message.reply_text("🔊 **Voice message playing on computer speakers!**", reply_markup=get_menu_keyboard("main", lang))
            except Exception as e:
                await update.message.reply_text(f"✅ Voice note saved: {e}", reply_markup=get_menu_keyboard("main", lang))

    async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await unauthorized_feedback(update)
            return
        lang = get_user_language(update.effective_user.id)
        doc = update.message.document
        if doc:
            await update.message.reply_text(f"⏳ Saving `{doc.file_name}` into Downloads folder...", parse_mode="Markdown")
            dest = os.path.join(DOWNLOADS_FOLDER, doc.file_name)
            tg_file = await context.bot.get_file(doc.file_id)
            await tg_file.download_to_drive(dest)

            # If image document, also copy to clipboard
            if doc.file_name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
                set_image_to_clipboard(dest)

            kb = [
                [InlineKeyboardButton("🚀 Open on PC Screen", callback_data=f"open_pc:{get_path_id(dest)}")]
            ]
            if dest.lower().endswith(".py"):
                kb.insert(0, [InlineKeyboardButton("🐍 تشغيل كملف بايثون (Run Python)", callback_data=f"open_pc:{get_path_id(dest)}")])
            kb.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="goto_main")])
            await update.message.reply_text(f"✅ File saved successfully:\n`{dest}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    async def bot_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        err = context.error
        if isinstance(err, telegram.error.Conflict):
            print("⚠️ [Telegram Conflict]: Bot token active on another machine! Telegram allows one polling instance.")
        elif isinstance(err, telegram.error.NetworkError):
            print("⚠️ [Telegram Network]: Connection drop, reconnecting...")
        else:
            print(f"⚠️ [Telegram Error]: {err}")

    def register_all_handlers(app):
        app.add_error_handler(bot_error_handler)
        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("menu", start_cmd))
        app.add_handler(CommandHandler("lang", lang_cmd))
        app.add_handler(CommandHandler("wake", wake_cmd))
        app.add_handler(CommandHandler("battery", battery_cmd))
        app.add_handler(CommandHandler("yt", yt_cmd))
        app.add_handler(CommandHandler("search", search_cmd))
        app.add_handler(CommandHandler("msg", msg_cmd))
        app.add_handler(CommandHandler("say", say_cmd))
        app.add_handler(CommandHandler("apps", apps_cmd))
        app.add_handler(CommandHandler("programs", apps_cmd))
        app.add_handler(CommandHandler("hide", hide_cmd))
        app.add_handler(CommandHandler("fold", hide_cmd))
        app.add_handler(CommandHandler("py", py_cmd))
        app.add_handler(CommandHandler("python", py_cmd))
        app.add_handler(CommandHandler("heal", heal_cmd))
        app.add_handler(CommandHandler("netheal", heal_cmd))
        app.add_handler(CommandHandler("cmd", cmd_cmd))
        app.add_handler(CommandHandler("clip", clip_cmd))
        app.add_handler(CommandHandler("setclip", setclip_cmd))
        app.add_handler(CommandHandler("autoclip", autoclip_cmd))
        app.add_handler(CommandHandler("empty_bin", empty_bin_cmd))
        app.add_handler(CommandHandler("screenshot", screen_cmd))
        app.add_handler(CommandHandler("screen", screen_cmd))
        app.add_handler(CommandHandler("webcam", webcam_cmd))
        app.add_handler(CommandHandler("cam", webcam_cmd))
        app.add_handler(CommandHandler("mic", mic_cmd))
        app.add_handler(CommandHandler("record", mic_cmd))
        app.add_handler(CommandHandler("specs", specs_cmd))
        app.add_handler(CommandHandler("hw", specs_cmd))
        app.add_handler(CommandHandler("wifi", wifi_cmd))
        app.add_handler(CommandHandler("network", wifi_cmd))
        app.add_handler(CommandHandler("guard", guard_cmd))
        app.add_handler(CommandHandler("security", guard_cmd))
        app.add_handler(CommandHandler("uptime", uptime_cmd))
        app.add_handler(CommandHandler("wifipass", wifipass_cmd))
        app.add_handler(CommandHandler("wifi_keys", wifipass_cmd))
        app.add_handler(CommandHandler("play", yt_cmd))
        app.add_handler(CommandHandler("ping", ping_cmd))
        app.add_handler(CommandHandler("kill", kill_cmd))
        app.add_handler(CommandHandler("update", update_cmd))
        app.add_handler(CommandHandler("upgrade", update_cmd))
        app.add_handler(CallbackQueryHandler(inline_callback_handler))
        app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        app.add_handler(MessageHandler(filters.VOICE, handle_voice))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Resilient self-healing polling loop
    while True:
        entry = ACTIVE_BOT_REGISTRY.get(token)
        if entry and entry.get("stop_requested"):
            break

        # Check internet before polling attempt
        for _ in range(15):
            entry = ACTIVE_BOT_REGISTRY.get(token)
            if entry and entry.get("stop_requested"):
                return
            try:
                s = socket.create_connection(("api.telegram.org", 443), timeout=3)
                s.close()
                break
            except OSError:
                time.sleep(2)

        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            req = HTTPXRequest(
                connection_pool_size=16,
                connect_timeout=20.0,
                read_timeout=40.0,
                write_timeout=40.0,
                pool_timeout=15.0,
                socket_options=[(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
            )
            app = (
                Application.builder()
                .token(token)
                .request(req)
                .post_init(post_init)
                .build()
            )
            register_all_handlers(app)

            if token in ACTIVE_BOT_REGISTRY:
                ACTIVE_BOT_REGISTRY[token]["app"] = app
                ACTIVE_BOT_REGISTRY[token]["loop"] = loop

            app.run_polling(
                stop_signals=None,
                bootstrap_retries=-1,
                timeout=10,
                poll_interval=0.5,
                drop_pending_updates=False,
                close_loop=True
            )
        except telegram.error.Conflict:
            print("⚠️ [Telegram Conflict]: Another polling instance active. Retrying in 15s...")
            time.sleep(15)
        except Exception as e:
            print(f"⚠️ [Telegram Reconnecting]: Connection drop ({e}). Auto-reconnecting in 3s...")
            time.sleep(3)
        finally:
            try:
                if not loop.is_closed():
                    loop.close()
            except Exception:
                pass

        entry = ACTIVE_BOT_REGISTRY.get(token)
        if entry and entry.get("stop_requested"):
            break
        time.sleep(2)

# ----------------- Comprehensive Background Clipboard & Snipping Watcher -----------------
def clipboard_monitor_worker():
    global LAST_SEEN_CLIP_TEXT, LAST_SEEN_CLIP_IMG_HASH, LAST_SEEN_CLIP_FILES_HASH
    LAST_SEEN_CLIP_TEXT = get_native_clipboard_text()

    while True:
        try:
            if AUTO_CLIPBOARD_SUBS:
                notify_subs = list(AUTO_CLIPBOARD_SUBS)

                # 1. Check for Copied Snipping Tool Screenshots / Images in Clipboard
                try:
                    grab = ImageGrab.grabclipboard()
                except Exception:
                    grab = None

                # A. Copied Image / Screenshot
                if grab is not None and hasattr(grab, 'save') and hasattr(grab, 'tobytes'):
                    try:
                        raw_bytes = grab.tobytes()[:32000]
                        img_hash = hashlib.md5(raw_bytes).hexdigest()
                        if img_hash != LAST_SEEN_CLIP_IMG_HASH:
                            LAST_SEEN_CLIP_IMG_HASH = img_hash
                            temp_p = os.path.join(tempfile.gettempdir(), f"autoclip_img_{int(time.time())}.png")
                            grab.save(temp_p, "PNG")

                            for bot_token, chat_id in notify_subs:
                                bot_entry = ACTIVE_BOT_REGISTRY.get(bot_token)
                                if bot_entry and bot_entry.get("app") and bot_entry.get("loop"):
                                    app_inst = bot_entry["app"]
                                    bot_loop = bot_entry["loop"]
                                    if bot_loop and not bot_loop.is_closed():
                                        async def _send_img(p=temp_p, cid=chat_id, ai=app_inst):
                                            try:
                                                with open(p, "rb") as pf:
                                                    await ai.bot.send_photo(
                                                        chat_id=cid,
                                                        photo=pf,
                                                        caption=f"📋 🖼️ **Image/Snipping Copied on PC (Auto-Synced):**\n💻 Host: `{platform.node()}`",
                                                        parse_mode="Markdown"
                                                    )
                                            except Exception:
                                                pass
                                        asyncio.run_coroutine_threadsafe(_send_img(), bot_loop)
                    except Exception:
                        pass

                # B. Copied Files in File Explorer (Ctrl+C on file/photo)
                elif isinstance(grab, list) and grab:
                    try:
                        files_tuple = tuple(grab)
                        files_hash = hashlib.md5(str(files_tuple).encode()).hexdigest()
                        if files_hash != LAST_SEEN_CLIP_FILES_HASH:
                            LAST_SEEN_CLIP_FILES_HASH = files_hash
                            for fpath in grab:
                                if os.path.exists(fpath) and os.path.isfile(fpath):
                                    fsize_mb = os.path.getsize(fpath) / (1024 * 1024)
                                    is_img = fpath.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'))

                                    for bot_token, chat_id in notify_subs:
                                        bot_entry = ACTIVE_BOT_REGISTRY.get(bot_token)
                                        if bot_entry and bot_entry.get("app") and bot_entry.get("loop"):
                                            app_inst = bot_entry["app"]
                                            bot_loop = bot_entry["loop"]
                                            if bot_loop and not bot_loop.is_closed():
                                                async def _send_file(fp=fpath, cid=chat_id, ai=app_inst, img_flag=is_img, sz=fsize_mb):
                                                    try:
                                                        fname = os.path.basename(fp)
                                                        if sz <= 45:
                                                            with open(fp, "rb") as fl:
                                                                if img_flag:
                                                                    await ai.bot.send_photo(chat_id=cid, photo=fl, caption=f"📋 📁 **Copied Image File (Synced):** `{fname}`", parse_mode="Markdown")
                                                                else:
                                                                    await ai.bot.send_document(chat_id=cid, document=fl, caption=f"📋 📁 **Copied File (Synced):** `{fname}` ({sz:.1f} MB)", parse_mode="Markdown")
                                                        else:
                                                            await ai.bot.send_message(chat_id=cid, text=f"📋 📁 **Large File Copied on PC:**\n`{fp}` ({sz:.1f} MB)", parse_mode="Markdown")
                                                    except Exception:
                                                        pass
                                                asyncio.run_coroutine_threadsafe(_send_file(), bot_loop)
                    except Exception:
                        pass

                # C. Copied Text
                else:
                    current_clip = get_native_clipboard_text()
                    if current_clip and current_clip != LAST_SEEN_CLIP_TEXT:
                        LAST_SEEN_CLIP_TEXT = current_clip
                        if len(current_clip.strip()) > 0:
                            display_text = current_clip if len(current_clip) <= 3000 else current_clip[:3000] + "\n... [Truncated]"
                            msg_text = (
                                f"📋 **PC Clipboard Synced (Live):**\n\n"
                                f"```\n{display_text}\n```\n\n"
                                f"💻 Host: `{platform.node()}` • User: `{CURRENT_USER}`"
                            )
                            for bot_token, chat_id in notify_subs:
                                bot_entry = ACTIVE_BOT_REGISTRY.get(bot_token)
                                if bot_entry and bot_entry.get("app") and bot_entry.get("loop"):
                                    app_inst = bot_entry["app"]
                                    bot_loop = bot_entry["loop"]
                                    if bot_loop and not bot_loop.is_closed():
                                        asyncio.run_coroutine_threadsafe(
                                            app_inst.bot.send_message(
                                                chat_id=chat_id,
                                                text=msg_text,
                                                parse_mode="Markdown"
                                            ),
                                            bot_loop
                                        )

            time.sleep(1.0)
        except Exception:
            time.sleep(2.0)

threading.Thread(target=clipboard_monitor_worker, daemon=True).start()

# ----------------- GUI: Compact, Fully Scrollable & Responsive -----------------
class MasterMiniApp(ctk.CTk):
    def __init__(self, is_silent=False):
        if sys.platform == "win32":
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PC_Remote_Pro.UniversalHub.v4.0")
            except Exception:
                pass

        super().__init__()

        self.title(f"{APP_NAME} - {APP_VERSION}")
        # Compact dimensions optimized for small screens (370x520)
        self.geometry("370x520")
        self.minsize(350, 460)
        self.resizable(True, True)
        self.attributes('-topmost', True)
        self.available_update_info = None

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Load Application Branding Logo & Icon
        self.logo_ctk = None
        logo_png = os.path.join(BASE_DIR, "PC_Remote_Pro.png")
        if not os.path.exists(logo_png):
            logo_png = os.path.join(getattr(sys, '_MEIPASS', BASE_DIR), "PC_Remote_Pro.png")
        logo_jpg = os.path.join(BASE_DIR, "PC_Remote_Pro.jpg")
        if not os.path.exists(logo_jpg):
            logo_jpg = os.path.join(getattr(sys, '_MEIPASS', BASE_DIR), "PC_Remote_Pro.jpg")

        active_logo = logo_png if os.path.exists(logo_png) else (logo_jpg if os.path.exists(logo_jpg) else None)
        if active_logo:
            try:
                pil_logo = Image.open(active_logo)
                self.logo_ctk = ctk.CTkImage(light_image=pil_logo, dark_image=pil_logo, size=(50, 50))
            except Exception:
                pass

        ico_path = os.path.join(BASE_DIR, "PC_Remote_Pro.ico")
        if not os.path.exists(ico_path):
            ico_path = os.path.join(getattr(sys, '_MEIPASS', BASE_DIR), "PC_Remote_Pro.ico")
        if os.path.exists(ico_path):
            try:
                self.iconbitmap(ico_path)
                self.wm_iconbitmap(ico_path)
                try:
                    self.iconbitmap(default=ico_path)
                except Exception:
                    pass
                self.after(100, lambda: self.wm_iconbitmap(ico_path))
                self.after(300, lambda: self.iconbitmap(ico_path))
            except Exception:
                pass

        if is_silent:
            self.withdraw()

        self.config = load_config()
        self.accounts = self.config.get("accounts", [])
        self.active_account = None

        self.is_token_visible = False
        self.is_chatid_visible = False

        # Root Scrollable Container so nothing is ever clipped on any screen size
        self.root_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.root_scroll.pack(fill="both", expand=True, padx=4, pady=2)

        self.frame_dashboard = ctk.CTkFrame(self.root_scroll, fg_color="transparent")
        self.frame_editor = ctk.CTkFrame(self.root_scroll, fg_color="transparent")

        self.setup_dashboard_ui()
        self.setup_editor_ui()

        # Compact Footer
        self.lbl_footer = ctk.CTkLabel(
            self,
            text=f"Developed by {DEVELOPER_NAME} • {APP_VERSION}",
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color="#666666"
        )
        self.lbl_footer.pack(side="bottom", pady=(0, 3))

        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.tray_icon = None

        if HAS_PYSTRAY:
            self.setup_tray()

        global MASTER_APP_INSTANCE
        MASTER_APP_INSTANCE = self
        threading.Thread(target=run_security_guard_monitor, daemon=True).start()
        threading.Thread(target=SMART_NETWORK_WATCHDOG.run_loop, daemon=True).start()

        self.start_all_bots()
        self.watchdog_supervisor()

        if self.config.get("autostart", True):
            set_windows_autostart(True)

        # Always initialize dashboard contents so opening from tray is never blank
        self.show_dashboard()

    # ----------------- Screen 1: Dashboard UI -----------------
    def setup_dashboard_ui(self):
        self.header_dash = ctk.CTkFrame(self.frame_dashboard, fg_color="transparent")
        self.header_dash.pack(pady=(4, 2))

        if self.logo_ctk:
            self.lbl_dash_logo = ctk.CTkLabel(self.header_dash, image=self.logo_ctk, text="")
            self.lbl_dash_logo.pack(side="left", padx=(0, 8))

        title_box = ctk.CTkFrame(self.header_dash, fg_color="transparent")
        title_box.pack(side="left")

        self.lbl_dash_title = ctk.CTkLabel(
            title_box,
            text="PC Remote Pro",
            font=ctk.CTkFont(size=15, weight="bold")
        )
        self.lbl_dash_title.pack(anchor="w")

        self.lbl_dash_subtitle = ctk.CTkLabel(
            title_box,
            text="Universal Remote Control Hub",
            font=ctk.CTkFont(size=10),
            text_color="#00bcd4"
        )
        self.lbl_dash_subtitle.pack(anchor="w")

        self.scroll_accounts = ctk.CTkScrollableFrame(self.frame_dashboard, width=330, height=220)
        self.scroll_accounts.pack(padx=6, pady=4, fill="both", expand=True)

        self.chk_autostart = ctk.CTkCheckBox(
            self.frame_dashboard,
            text="Run on Windows Startup (Silent Boot)",
            font=ctk.CTkFont(size=11),
            command=self.toggle_autostart
        )
        self.chk_autostart.pack(pady=3)
        if self.config.get("autostart", True):
            self.chk_autostart.select()

        self.btn_add_new = ctk.CTkButton(
            self.frame_dashboard,
            text="➕ Register New Account",
            width=320,
            height=30,
            fg_color="#28a748",
            hover_color="#218838",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.open_new_account_form
        )
        self.btn_add_new.pack(pady=4)

        self.btn_check_update = ctk.CTkButton(
            self.frame_dashboard,
            text="🔄 Check for Updates",
            width=320,
            height=28,
            fg_color="#0d6efd",
            hover_color="#0b5ed7",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.trigger_update_check
        )
        self.btn_check_update.pack(pady=(0, 4))

        # Prominent Download & Install Button (shown when update is available)
        self.btn_download_update = ctk.CTkButton(
            self.frame_dashboard,
            text="🚀 Download & Install Update",
            width=320,
            height=32,
            fg_color="#28a748",
            hover_color="#218838",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.start_downloading_update
        )

        # Download Progress Container with Live Megabyte Counter
        self.frame_progress = ctk.CTkFrame(self.frame_dashboard, fg_color="transparent")
        self.progress_bar = ctk.CTkProgressBar(self.frame_progress, width=320, height=12)
        self.progress_bar.set(0.0)
        self.progress_bar.pack(pady=(2, 2))
        self.lbl_progress_mb = ctk.CTkLabel(
            self.frame_progress,
            text="",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#00bcd4"
        )
        self.lbl_progress_mb.pack(pady=(0, 2))

        self.lbl_dash_status = ctk.CTkLabel(
            self.frame_dashboard,
            text=f"Status: Ready • Version: {APP_VERSION}",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#28a748"
        )
        self.lbl_dash_status.pack(pady=2)

    def refresh_accounts_dashboard(self):
        for widget in self.scroll_accounts.winfo_children():
            widget.destroy()

        if not self.accounts:
            lbl_empty = ctk.CTkLabel(
                self.scroll_accounts,
                text="No accounts registered yet.\nClick (+ Register New Account) below to add one.",
                text_color="gray",
                font=ctk.CTkFont(size=11)
            )
            lbl_empty.pack(pady=30)
            return

        for acc in self.accounts:
            card = ctk.CTkFrame(self.scroll_accounts, fg_color="#252525", corner_radius=6)
            card.pack(fill="x", padx=4, pady=3)

            name = acc.get("name", "Account")
            has_pwd = bool(acc.get("pwd_hash"))
            token = acc.get("token", "").strip()
            chat_id = str(acc.get("chat_id", "")).strip()

            is_ready = bool(token and chat_id)
            status_tag = "🟢 Ready" if is_ready else "⚠️ Incomplete"
            status_color = "#28a748" if is_ready else "#e0a800"

            info_frame = ctk.CTkFrame(card, fg_color="transparent")
            info_frame.pack(side="left", padx=8, pady=4)

            lbl_name = ctk.CTkLabel(
                info_frame,
                text=f"{'🔒' if has_pwd else '👤'} {name}",
                font=ctk.CTkFont(size=11, weight="bold")
            )
            lbl_name.pack(anchor="w")

            lbl_stat = ctk.CTkLabel(
                info_frame,
                text=status_tag,
                font=ctk.CTkFont(size=10),
                text_color=status_color
            )
            lbl_stat.pack(anchor="w")

            btn_login = ctk.CTkButton(
                card,
                text="Manage ⚙️",
                width=75,
                height=24,
                fg_color="#333333",
                hover_color="#444444",
                font=ctk.CTkFont(size=10),
                command=lambda a=acc: self.login_to_account(a)
            )
            btn_login.pack(side="right", padx=8, pady=6)

    def show_dashboard(self):
        self.frame_editor.pack_forget()
        self.refresh_accounts_dashboard()
        self.frame_dashboard.pack(fill="both", expand=True)

    def toggle_autostart(self):
        status = bool(self.chk_autostart.get())
        set_windows_autostart(status)
        save_config(self.accounts, autostart=status)

    # ----------------- Screen 2: Account Editor with Real-Time Diagnostics -----------------
    def setup_editor_ui(self):
        self.header_editor = ctk.CTkFrame(self.frame_editor, fg_color="transparent")
        self.header_editor.pack(fill="x", padx=10, pady=(4, 2))

        btn_back_dash = ctk.CTkButton(
            self.header_editor,
            text="◀ Back",
            width=60,
            height=24,
            fg_color="#333333",
            hover_color="#444444",
            font=ctk.CTkFont(size=10),
            command=self.show_dashboard
        )
        btn_back_dash.pack(side="left")

        if self.logo_ctk:
            lbl_logo = ctk.CTkLabel(self.header_editor, image=self.logo_ctk, text="")
            lbl_logo.pack(side="left", padx=(6, 2))

        self.lbl_edit_title = ctk.CTkLabel(
            self.header_editor,
            text="Account Credentials",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.lbl_edit_title.pack(side="left", padx=4)

        self.lbl_acc_name = ctk.CTkLabel(self.frame_editor, text="Account Name / Label:", font=ctk.CTkFont(size=10, weight="bold"))
        self.lbl_acc_name.pack(anchor="w", padx=12, pady=(2, 0))
        self.entry_acc_name = ctk.CTkEntry(self.frame_editor, width=320, height=26, placeholder_text="e.g. Account 1, Home, Work")
        self.entry_acc_name.pack(padx=12, pady=(0, 2))

        self.lbl_token = ctk.CTkLabel(self.frame_editor, text="Telegram Bot Token:", font=ctk.CTkFont(size=10, weight="bold"))
        self.lbl_token.pack(anchor="w", padx=12, pady=(2, 0))

        self.frame_token_row = ctk.CTkFrame(self.frame_editor, fg_color="transparent")
        self.frame_token_row.pack(fill="x", padx=12, pady=(0, 2))

        self.entry_token = ctk.CTkEntry(self.frame_token_row, width=195, height=26, show="*")
        self.entry_token.pack(side="left", padx=(0, 3))

        self.btn_eye_token = ctk.CTkButton(
            self.frame_token_row, text="👁️", width=34, height=26,
            fg_color="#333333", hover_color="#444444", text_color="#FFFFFF",
            command=self.toggle_token_visibility
        )
        self.btn_eye_token.pack(side="left", padx=(0, 2))

        self.btn_paste_token = ctk.CTkButton(
            self.frame_token_row, text="📋", width=40, height=26,
            fg_color="#333333", hover_color="#444444", text_color="#FFFFFF",
            command=lambda: self.paste_text_to_entry(self.entry_token)
        )
        self.btn_paste_token.pack(side="left", padx=(0, 2))

        self.btn_clear_token = ctk.CTkButton(
            self.frame_token_row, text="✖", width=26, height=26,
            fg_color="#c9302c", hover_color="#a02622", text_color="#FFFFFF",
            command=lambda: self.clear_entry(self.entry_token)
        )
        self.btn_clear_token.pack(side="left")

        self.lbl_chatid = ctk.CTkLabel(self.frame_editor, text="Your Chat ID (Numbers only):", font=ctk.CTkFont(size=10, weight="bold"))
        self.lbl_chatid.pack(anchor="w", padx=12, pady=(2, 0))

        self.frame_chatid_row = ctk.CTkFrame(self.frame_editor, fg_color="transparent")
        self.frame_chatid_row.pack(fill="x", padx=12, pady=(0, 2))

        self.entry_chatid = ctk.CTkEntry(self.frame_chatid_row, width=195, height=26, show="*")
        self.entry_chatid.pack(side="left", padx=(0, 3))

        self.btn_eye_chatid = ctk.CTkButton(
            self.frame_chatid_row, text="👁️", width=34, height=26,
            fg_color="#333333", hover_color="#444444", text_color="#FFFFFF",
            command=self.toggle_chatid_visibility
        )
        self.btn_eye_chatid.pack(side="left", padx=(0, 2))

        self.btn_paste_chatid = ctk.CTkButton(
            self.frame_chatid_row, text="📋", width=40, height=26,
            fg_color="#333333", hover_color="#444444", text_color="#FFFFFF",
            command=lambda: self.paste_text_to_entry(self.entry_chatid)
        )
        self.btn_paste_chatid.pack(side="left", padx=(0, 2))

        self.btn_clear_chatid = ctk.CTkButton(
            self.frame_chatid_row, text="✖", width=26, height=26,
            fg_color="#c9302c", hover_color="#a02622", text_color="#FFFFFF",
            command=lambda: self.clear_entry(self.entry_chatid)
        )
        self.btn_clear_chatid.pack(side="left")

        self.btn_test_conn = ctk.CTkButton(
            self.frame_editor,
            text="⚡ Test Bot Connection",
            width=320,
            height=28,
            fg_color="#e0a800",
            hover_color="#c69500",
            text_color="#000000",
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self.run_connection_test
        )
        self.btn_test_conn.pack(pady=3)

        self.attach_context_menu(self.entry_token)
        self.attach_context_menu(self.entry_chatid)
        self.attach_context_menu(self.entry_acc_name)

        self.btn_set_pwd = ctk.CTkButton(
            self.frame_editor,
            text="🔒 Set / Change Password for this Account",
            width=320,
            height=24,
            fg_color="#333333",
            hover_color="#444444",
            text_color="#DDDDDD",
            font=ctk.CTkFont(size=10),
            command=self.set_account_password
        )
        self.btn_set_pwd.pack(pady=2)

        self.btn_save_acc = ctk.CTkButton(
            self.frame_editor,
            text="💾 Save & Activate Account",
            width=320,
            height=28,
            fg_color="#1f77b4",
            hover_color="#145a86",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.save_account_changes
        )
        self.btn_save_acc.pack(pady=3)

        self.frame_bottom_actions = ctk.CTkFrame(self.frame_editor, fg_color="transparent")
        self.frame_bottom_actions.pack(fill="x", padx=12, pady=2)

        self.btn_logout = ctk.CTkButton(
            self.frame_bottom_actions,
            text="🚪 Logout & Lock",
            width=150,
            height=26,
            fg_color="#4a4a4a",
            hover_color="#5a5a5a",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self.logout_account
        )
        self.btn_logout.pack(side="left", padx=(0, 4))

        self.btn_delete = ctk.CTkButton(
            self.frame_bottom_actions,
            text="🗑️ Delete Account",
            width=150,
            height=26,
            fg_color="#c9302c",
            hover_color="#a02622",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=10),
            command=self.delete_active_account
        )
        self.btn_delete.pack(side="left")

    def toggle_token_visibility(self):
        self.is_token_visible = not self.is_token_visible
        self.entry_token.configure(show="" if self.is_token_visible else "*")
        self.btn_eye_token.configure(text="🔒" if self.is_token_visible else "👁️")

    def toggle_chatid_visibility(self):
        self.is_chatid_visible = not self.is_chatid_visible
        self.entry_chatid.configure(show="" if self.is_chatid_visible else "*")
        self.btn_eye_chatid.configure(text="🔒" if self.is_chatid_visible else "👁️")

    def run_connection_test(self):
        token = self.entry_token.get().strip()
        chat_id = self.entry_chatid.get().strip()
        self.btn_test_conn.configure(state="disabled", text="⏳ Testing connection...")
        self.update_idletasks()

        success, msg = test_telegram_connection(token, chat_id)
        self.btn_test_conn.configure(state="normal", text="⚡ Test Bot Connection")

        if success:
            messagebox.showinfo("Connection Success", msg, parent=self)
        else:
            messagebox.showerror("Connection Diagnostic", msg, parent=self)

    def show_editor(self, acc=None):
        self.frame_dashboard.pack_forget()
        self.active_account = acc

        self.is_token_visible = False
        self.is_chatid_visible = False
        self.entry_token.configure(show="*")
        self.entry_chatid.configure(show="*")
        self.btn_eye_token.configure(text="👁️")
        self.btn_eye_chatid.configure(text="👁️")

        self.entry_acc_name.delete(0, "end")
        self.entry_token.delete(0, "end")
        self.entry_chatid.delete(0, "end")

        if acc:
            self.entry_acc_name.insert(0, acc.get("name", ""))
            self.entry_token.insert(0, acc.get("token", ""))
            self.entry_chatid.insert(0, str(acc.get("chat_id", "")))
            self.btn_delete.configure(state="normal")
        else:
            self.entry_acc_name.insert(0, f"Account {len(self.accounts) + 1}")
            self.btn_delete.configure(state="disabled")

        self.frame_editor.pack(fill="both", expand=True)

    def open_new_account_form(self):
        self.show_editor(acc=None)

    def login_to_account(self, acc):
        pwd_hash = acc.get("pwd_hash", "")
        if pwd_hash:
            dialog = ctk.CTkInputDialog(text=f"Enter password for '{acc.get('name')}':", title="Account Authentication")
            entered_pwd = dialog.get_input()
            if not entered_pwd:
                return
            h = hashlib.sha256(entered_pwd.encode()).hexdigest()
            if h != pwd_hash:
                messagebox.showerror("Authentication Failed", "Incorrect account password.", parent=self)
                return
        self.show_editor(acc)

    def set_account_password(self):
        dialog = ctk.CTkInputDialog(
            text="Enter a new password for this account (or leave empty to remove):",
            title="Account Password"
        )
        pwd = dialog.get_input()
        if pwd is not None:
            if not self.active_account:
                messagebox.showwarning("Notice", "Please save the account first before setting a password.", parent=self)
                return
            pwd_clean = pwd.strip()
            if pwd_clean:
                self.active_account["pwd_hash"] = hashlib.sha256(pwd_clean.encode()).hexdigest()
                messagebox.showinfo("Security", "Password set successfully for this account!", parent=self)
            else:
                self.active_account["pwd_hash"] = ""
                messagebox.showinfo("Security", "Password protection removed.", parent=self)
            save_config(self.accounts, autostart=bool(self.chk_autostart.get()))

    def save_account_changes(self):
        name = self.entry_acc_name.get().strip() or f"Account {len(self.accounts) + 1}"
        token = self.entry_token.get().strip()
        chat_id_str = self.entry_chatid.get().strip()

        if not token:
            messagebox.showerror("Validation Error", "Telegram Bot Token is required.", parent=self)
            return

        if not chat_id_str:
            messagebox.showerror("Validation Error", "Chat ID is required.", parent=self)
            return

        try:
            chat_id = int(chat_id_str)
        except ValueError:
            messagebox.showerror("Validation Error", "Chat ID must contain numbers only.", parent=self)
            return

        if self.active_account:
            self.active_account["name"] = name
            self.active_account["token"] = token
            self.active_account["chat_id"] = chat_id
        else:
            new_acc = {
                "id": str(int(time.time() * 1000)),
                "name": name,
                "token": token,
                "chat_id": chat_id,
                "pwd_hash": ""
            }
            self.accounts.append(new_acc)
            self.active_account = new_acc

        save_config(self.accounts, autostart=bool(self.chk_autostart.get()))
        self.start_all_bots()
        messagebox.showinfo("Saved", f"Account '{name}' saved and permanently activated!", parent=self)
        self.show_dashboard()

    def logout_account(self):
        self.show_dashboard()

    def delete_active_account(self):
        if not self.active_account:
            return
        ans = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete '{self.active_account.get('name')}'?",
            parent=self
        )
        if ans:
            token = self.active_account.get("token")
            self.accounts = [a for a in self.accounts if a.get("id") != self.active_account.get("id")]
            save_config(self.accounts, autostart=bool(self.chk_autostart.get()))
            self.stop_bot(token)
            self.start_all_bots()
            messagebox.showinfo("Deleted", "Account removed successfully.", parent=self)
            self.show_dashboard()

    def stop_bot(self, token):
        if token in ACTIVE_BOT_REGISTRY:
            entry = ACTIVE_BOT_REGISTRY[token]
            entry["stop_requested"] = True
            app_inst = entry.get("app")
            loop_inst = entry.get("loop")
            try:
                if app_inst and app_inst.updater and app_inst.updater.running:
                    app_inst.updater.stop()
            except Exception:
                pass
            try:
                if loop_inst and not loop_inst.is_closed():
                    loop_inst.call_soon_threadsafe(loop_inst.stop)
            except Exception:
                pass
            ACTIVE_BOT_REGISTRY.pop(token, None)

    def paste_text_to_entry(self, entry_widget):
        try:
            txt = self.clipboard_get()
            if txt:
                entry_widget.delete(0, "end")
                entry_widget.insert(0, txt.strip())
        except Exception:
            pass

    def clear_entry(self, entry_widget):
        entry_widget.delete(0, "end")

    def attach_context_menu(self, widget):
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Cut", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="Copy", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Paste", command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_command(label="Select All", command=lambda: widget.select_range(0, 'end'))

        def _popup(event):
            menu.tk_popup(event.x_root, event.y_root)
        widget.bind("<Button-3>", _popup)

    def start_all_bots(self):
        global ACTIVE_BOT_REGISTRY

        if not self.accounts:
            for tok in list(ACTIVE_BOT_REGISTRY.keys()):
                self.stop_bot(tok)
            self.lbl_dash_status.configure(text="Status: No accounts registered", text_color="gray")
            return

        token_groups = {}
        valid_accounts_count = 0

        for acc in self.accounts:
            token = acc.get("token", "").strip()
            cid_str = str(acc.get("chat_id", "")).strip()
            name = acc.get("name", "User").strip()

            try:
                cid = int(cid_str)
                if token and cid:
                    token_groups.setdefault(token, {})[cid] = {"name": name, "acc_id": acc.get("id")}
                    valid_accounts_count += 1
            except ValueError:
                continue

        current_active_tokens = list(ACTIVE_BOT_REGISTRY.keys())
        for tok in current_active_tokens:
            if tok not in token_groups:
                self.stop_bot(tok)

        if not token_groups:
            self.lbl_dash_status.configure(text="Status: Incomplete credentials", text_color="#c9302c")
            return

        for token, users in token_groups.items():
            if token in ACTIVE_BOT_REGISTRY and ACTIVE_BOT_REGISTRY[token].get("thread") and ACTIVE_BOT_REGISTRY[token]["thread"].is_alive():
                ACTIVE_BOT_REGISTRY[token]["user_map"].clear()
                ACTIVE_BOT_REGISTRY[token]["user_map"].update(users)
                ACTIVE_BOT_REGISTRY[token]["stop_requested"] = False
            else:
                user_dict_ref = dict(users)
                thread = threading.Thread(target=start_bot_thread, args=(token, user_dict_ref), daemon=True)
                ACTIVE_BOT_REGISTRY[token] = {
                    "thread": thread,
                    "user_map": user_dict_ref,
                    "app": None,
                    "loop": None,
                    "stop_requested": False
                }
                thread.start()

        self.lbl_dash_status.configure(
            text=f"Active: {valid_accounts_count} of {len(self.accounts)} accounts connected ✅",
            text_color="#28a748"
        )

    def watchdog_supervisor(self):
        try:
            token_groups = {}
            for acc in self.accounts:
                token = acc.get("token", "").strip()
                cid_str = str(acc.get("chat_id", "")).strip()
                name = acc.get("name", "User").strip()
                try:
                    cid = int(cid_str)
                    if token and cid:
                        token_groups.setdefault(token, {})[cid] = {"name": name, "acc_id": acc.get("id")}
                except (ValueError, TypeError):
                    continue

            for token, users in token_groups.items():
                entry = ACTIVE_BOT_REGISTRY.get(token)
                needs_restart = False
                if not entry:
                    needs_restart = True
                else:
                    t = entry.get("thread")
                    if not t or not t.is_alive():
                        needs_restart = True

                if needs_restart:
                    user_dict_ref = dict(users)
                    thread = threading.Thread(target=start_bot_thread, args=(token, user_dict_ref), daemon=True)
                    ACTIVE_BOT_REGISTRY[token] = {
                        "thread": thread,
                        "user_map": user_dict_ref,
                        "app": None,
                        "loop": None,
                        "stop_requested": False
                    }
                    thread.start()
        except Exception:
            pass
        finally:
            self.after(15000, self.watchdog_supervisor)

    def hide_to_tray(self):
        self.withdraw()

    def show_window(self, icon=None, item=None):
        self.after(0, self._do_show_window)

    def _do_show_window(self):
        try:
            if not self.frame_dashboard.winfo_ismapped() and not self.frame_editor.winfo_ismapped():
                self.show_dashboard()
            elif self.frame_dashboard.winfo_ismapped():
                self.refresh_accounts_dashboard()
            self.deiconify()
            self.state("normal")
            self.lift()
            self.attributes('-topmost', True)
            self.after_idle(self.attributes, '-topmost', False)
            self.focus_force()
            self.update_idletasks()
        except Exception:
            pass

    def trigger_update_check(self):
        self.btn_check_update.configure(text="⏳ Checking for updates...", state="disabled")
        self.lbl_dash_status.configure(text="Status: Checking for updates...", text_color="#00bcd4")

        def _worker():
            has_up, tag_or_ver, rel_info = check_for_github_update()
            self.after(0, lambda: self._handle_update_result(has_up, tag_or_ver, rel_info))

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_update_result(self, has_update: bool, tag_or_ver: str, rel_info: dict):
        self.btn_check_update.configure(text="🔄 Check for Updates", state="normal")
        if not has_update:
            self.lbl_dash_status.configure(text=f"Current Version: {APP_VERSION} (Up to date)", text_color="#28a748")
            self.btn_download_update.pack_forget()
            self.frame_progress.pack_forget()
            tkinter.messagebox.showinfo(
                "Check for Updates",
                f"Current Version: {APP_VERSION}\n\nYour application is up to date.",
                parent=self
            )
            return

        tag = rel_info.get("tag", tag_or_ver)
        self.available_update_info = rel_info
        self.lbl_dash_status.configure(text=f"Current: {APP_VERSION} • Next: {tag}", text_color="#00bcd4")

        # Show prominent Download & Install Button right below the check button!
        self.btn_download_update.configure(
            text=f"🚀 Download & Install Update ({tag})",
            state="normal",
            command=self.start_downloading_update
        )
        self.btn_download_update.pack(before=self.lbl_dash_status, pady=(0, 4))

        ans = tkinter.messagebox.askyesno(
            "Update Available",
            f"Current Version: {APP_VERSION}\nNext Version: {tag}\n\nA newer update is available!\nDo you want to download and install this update now?",
            parent=self
        )
        if ans:
            self.start_downloading_update()

    def start_downloading_update(self):
        if not self.available_update_info:
            return

        rel_info = self.available_update_info
        tag = rel_info.get("tag", "Update")
        asset = rel_info.get("asset")

        if not asset or not asset.get("browser_download_url"):
            tkinter.messagebox.showinfo(
                "Update Available",
                f"Current Version: {APP_VERSION}\nNext Version: {tag}\n\nNo executable installer found in release. Opening download page...",
                parent=self
            )
            webbrowser.open(GITHUB_RELEASES_URL)
            return

        down_url = asset["browser_download_url"]
        fname = asset.get("name", "PC_Remote_Pro_Setup.exe")

        self.btn_check_update.configure(state="disabled")
        self.btn_download_update.configure(state="disabled", text=f"⏳ Downloading {tag}...")

        # Display progress bar and megabyte counter
        self.frame_progress.pack(before=self.lbl_dash_status, pady=4)
        self.progress_bar.set(0.0)
        self.lbl_progress_mb.configure(text="Connecting to GitHub download server...", text_color="#00bcd4")
        self.lbl_dash_status.configure(text=f"Downloading {tag}...", text_color="#00bcd4")

        def _progress_cb(downloaded_bytes, total_bytes):
            mb_down = downloaded_bytes / (1024 * 1024)
            if total_bytes and total_bytes > 0:
                pct = downloaded_bytes / total_bytes
                mb_total = total_bytes / (1024 * 1024)
                text = f"Downloading: {mb_down:.1f} MB / {mb_total:.1f} MB ({int(pct * 100)}%)"
            else:
                pct = 0.5
                text = f"Downloading: {mb_down:.1f} MB"
            self.after(0, lambda: self._update_progress_ui(pct, text))

        def _download_worker():
            ok_down, out_file = download_github_release_asset(down_url, fname, progress_callback=_progress_cb)
            if ok_down and os.path.exists(out_file):
                self.after(0, lambda: self._prompt_install_update(out_file, tag))
            else:
                self.after(0, lambda: self._on_download_error(out_file))

        threading.Thread(target=_download_worker, daemon=True).start()

    def _update_progress_ui(self, pct: float, text: str):
        self.progress_bar.set(pct)
        self.lbl_progress_mb.configure(text=text)

    def _prompt_install_update(self, installer_path: str, tag: str):
        self.progress_bar.set(1.0)
        self.lbl_progress_mb.configure(text="✅ Download Complete! (100%)", text_color="#28a748")
        self.lbl_dash_status.configure(text=f"Current: {APP_VERSION} • Next: {tag} (Ready to Install)", text_color="#28a748")
        self.btn_download_update.configure(
            text="🚀 Install Now",
            state="normal",
            command=lambda: self._execute_installer(installer_path)
        )

        ans = tkinter.messagebox.askyesno(
            "Ready to Install",
            f"Version {tag} downloaded successfully!\n\nClose PC Remote Pro and start installation now?",
            parent=self
        )
        if ans:
            self._execute_installer(installer_path)

    def _execute_installer(self, installer_path: str):
        try:
            try:
                ctypes.windll.shell32.ShellExecuteW(None, "runas", installer_path, None, None, 1)
            except Exception:
                os.startfile(installer_path)
            self.after(600, self.quit_app)
        except Exception as e:
            tkinter.messagebox.showerror("Error", f"Could not launch installer: {e}", parent=self)

    def _on_download_error(self, err_msg: str):
        self.btn_check_update.configure(state="normal", text="🔄 Check for Updates")
        self.btn_download_update.configure(state="normal", text="🔄 Retry Download")
        self.lbl_progress_mb.configure(text="Download failed", text_color="#dc3545")
        self.lbl_dash_status.configure(text=f"Version: {APP_VERSION} • Download Failed", text_color="#dc3545")
        tkinter.messagebox.showerror("Download Error", f"Failed to download update:\n{err_msg}", parent=self)

    def setup_tray(self):
        png_path = os.path.join(BASE_DIR, "PC_Remote_Pro.png")
        if not os.path.exists(png_path):
            png_path = os.path.join(getattr(sys, '_MEIPASS', BASE_DIR), "PC_Remote_Pro.png")
        ico_path = os.path.join(BASE_DIR, "PC_Remote_Pro.ico")
        if not os.path.exists(ico_path):
            ico_path = os.path.join(getattr(sys, '_MEIPASS', BASE_DIR), "PC_Remote_Pro.ico")

        img = None
        target_img_path = png_path if os.path.exists(png_path) else (ico_path if os.path.exists(ico_path) else None)
        if target_img_path:
            try:
                raw_img = Image.open(target_img_path)
                img = raw_img.resize((64, 64), Image.Resampling.LANCZOS).convert("RGBA")
            except Exception:
                img = None

        if not img:
            img = Image.new('RGB', (64, 64), color=(31, 119, 180))
            d = ImageDraw.Draw(img)
            d.rectangle([16, 16, 48, 48], fill=(40, 167, 69))

        guard_active = SECURITY_GUARD_SETTINGS.get("enabled", True)
        tray_title = f"{APP_NAME} - {APP_VERSION} [🛡️ Guard Active]" if guard_active else f"{APP_NAME} - {APP_VERSION}"
        menu = pystray.Menu(
            pystray.MenuItem("Open Dashboard", self.show_window, default=True),
            pystray.MenuItem("Status: Active in Background ✅", None, enabled=False),
            pystray.MenuItem("🛡️ Security Guard: Active ✅", None, enabled=False),
            pystray.MenuItem("🔄 Check for Updates", lambda icon, item: self.after(0, self.trigger_update_check)),
            pystray.MenuItem("Exit Program", self.quit_app)
        )
        self.tray_icon = pystray.Icon("PC_Controller", img, tray_title, menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def quit_app(self, icon=None, item=None):
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.after(0, self._do_quit)

    def _do_quit(self):
        self.destroy()
        os._exit(0)

if __name__ == "__main__":
    try:
        silent_boot = "--silent" in sys.argv
        app = MasterMiniApp(is_silent=silent_boot)
        app.mainloop()
    except Exception:
        pass
