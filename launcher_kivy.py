#!/usr/bin/env python3
"""
Universal Service Launcher - Kivy Version
A powerful service manager for microservices and Python applications
"""

# pip install kivy psutil requests

import sys
import os
import subprocess
import shutil
import threading
import time
import socket
import json
import signal
import psutil
from pathlib import Path
from datetime import datetime
import requests

from kivy.config import Config

Config.set('graphics', 'width', '1200')
Config.set('graphics', 'height', '700')
Config.set('graphics', 'resizable', True)
Config.set('input', 'mouse', 'mouse')

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.dropdown import DropDown
from kivy.uix.spinner import Spinner
from kivy.uix.popup import Popup
from kivy.uix.treeview import TreeView, TreeViewLabel
from kivy.uix.recycleview import RecycleView
from kivy.uix.recycleview.views import RecycleDataViewBehavior
from kivy.uix.recycleboxlayout import RecycleBoxLayout
from kivy.uix.behaviors import FocusBehavior
from kivy.uix.recycleview.layout import LayoutSelectionBehavior
from kivy.properties import StringProperty, BooleanProperty, ObjectProperty, ListProperty
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.graphics import Color, RoundedRectangle

# Configuration
APP_NAME = "Universal Service Launcher"
APP_VERSION = "0.2"
CONFIG_DIR = Path.home() / ".service_launcher"
PROJECTS_DIR = CONFIG_DIR / "projects"
SERVICES_DIR = CONFIG_DIR / "services"
LOG_DIR = CONFIG_DIR / "logs"

DEFAULT_CONFIG = {
    "name": "Новый проект",
    "services": [],
    "settings": {
        "restart_delay": 3,
        "port_check_timeout": 10,
        "auto_start_dependencies": True,
        "graceful_shutdown_timeout": 30,
        "log_level": "INFO"
    }
}

# Colors
COLORS = {
    "running": "#52b788",
    "stopped": "#6c757d",
    "warning": "#ffb703",
    "error": "#e63946",
    "info": "#4a9eff",
    "background": "#f5f5f5",
    "card": "#ffffff",
    "text": "#333333"
}


class LogEvent:
    """Custom event for logging from threads"""

    def __init__(self, message, level="info"):
        self.message = message
        self.level = level


class ServiceWorker(threading.Thread):
    """Worker thread for service operations"""

    def __init__(self, operation, service, project_data, root_dir, callback):
        super().__init__()
        self.operation = operation
        self.service = service
        self.project_data = project_data
        self.root_dir = Path(root_dir)
        self.process = None
        self._is_running = True
        self.callback = callback
        self.daemon = True

    def run(self):
        if self.operation == 'start':
            self.start_service()
        elif self.operation == 'stop':
            self.stop_service()
        elif self.operation == 'restart':
            self.restart_service()

    def start_service(self):
        """Start a single service"""
        service_name = self.service.get("name")
        script_path = Path(self.service.get("script", ""))

        if not script_path.is_absolute():
            script_path = self.root_dir / script_path

        if not script_path.exists():
            self.callback('log', f"❌ Скрипт не найден: {script_path}", "error")
            return

        try:
            python_exe = self.get_python_interpreter()
            env = self.get_environment()

            # Определяем рабочую директорию для сервиса
            working_dir = None

            if self.service.get("working_dir"):
                working_dir = Path(self.service["working_dir"])
                if not working_dir.is_absolute():
                    working_dir = self.root_dir / working_dir

            if not working_dir and self.service.get("env_file"):
                env_path = Path(self.service["env_file"])
                if not env_path.is_absolute():
                    env_path = self.root_dir / env_path
                if env_path.parent.exists():
                    working_dir = env_path.parent

            if not working_dir:
                working_dir = script_path.parent

            if not working_dir.exists():
                self.callback('log', f"⚠️ Рабочая директория не существует: {working_dir}", "warning")
                working_dir = self.root_dir

            self.callback('log', f"🚀 Запуск {service_name}...", "info")
            self.callback('log', f"📁 Рабочая директория: {working_dir}", "info")

            # Windows-specific setup
            startupinfo = None
            creationflags = 0
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            self.process = subprocess.Popen(
                [python_exe, str(script_path)],
                cwd=str(working_dir),
                env=env,
                startupinfo=startupinfo,
                creationflags=creationflags,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace'
            )

            self.callback('process_started', service_name, self.process.pid)
            self.callback('log', f"✅ {service_name} запущен (PID: {self.process.pid})", "success")
            self.monitor_process()

        except Exception as e:
            self.callback('log', f"❌ Ошибка запуска {service_name}: {e}", "error")

    def stop_service(self):
        """Stop a service gracefully"""
        service_name = self.service.get("name")

        # Находим процесс
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline'] if proc.info['cmdline'] else [])
                if service_name in cmdline or (self.process and str(proc.pid) == str(self.process.pid)):
                    # Отправляем сигнал для graceful shutdown
                    if sys.platform == 'win32':
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        proc.terminate()

                    self.callback('log', f"🛑 Отправлен сигнал завершения {service_name} (PID: {proc.pid})", "info")

                    # Ждем завершения
                    try:
                        proc.wait(timeout=10)
                        self.callback('log', f"✅ {service_name} корректно остановлен", "success")
                    except psutil.TimeoutExpired:
                        self.callback('log', f"⚠️ {service_name} не остановился за 10 сек", "warning")

                    self.callback('process_stopped', service_name, proc.pid)
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def restart_service(self):
        """Restart a service"""
        self.stop_service()
        time.sleep(2)
        self.start_service()

    def get_python_interpreter(self):
        """Get Python interpreter path"""
        python_path = self.service.get("python_path", "system")

        if not python_path or python_path == "system":
            return sys.executable

        if not Path(python_path).is_absolute():
            python_path = self.root_dir / python_path

        return str(python_path)

    def get_environment(self):
        """Get environment variables for the service"""
        env = os.environ.copy()

        if self.root_dir.exists():
            env["PYTHONPATH"] = str(self.root_dir)

        if self.service.get("env_file"):
            env_path = Path(self.service["env_file"])
            if not env_path.is_absolute():
                env_path = self.root_dir / env_path
            env.update(self.load_env_file(env_path))

        return env

    def load_env_file(self, env_path):
        """Load .env file"""
        env_vars = {}
        if env_path.exists():
            try:
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            if '=' in line:
                                key, value = line.split('=', 1)
                                env_vars[key.strip()] = value.strip()
            except Exception as e:
                self.callback('log', f"Ошибка загрузки {env_path}: {e}", "warning")
        return env_vars

    def monitor_process(self):
        """Monitor the running process"""
        if not self.process:
            return

        while self._is_running and self.process.poll() is None:
            time.sleep(0.5)
            if self.process.stdout:
                output = self.process.stdout.readline()
                if output:
                    self.callback('log', output.strip(), "output")

        if self.process and self.process.poll() is not None:
            self.callback('process_stopped', self.service.get("name"), self.process.pid)

    def stop(self):
        """Stop the worker"""
        self._is_running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except:
                pass


class ServiceRow(BoxLayout):
    """Service row widget for the services list"""
    service_data = ObjectProperty(None)
    status = StringProperty("○")
    status_color = StringProperty(COLORS["stopped"])
    service_name = StringProperty("")
    port = StringProperty("-")
    pid = StringProperty("-")
    python_path = StringProperty("system")
    dependencies = StringProperty("-")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_y = None
        self.height = dp(50)

    def on_service_data(self, instance, value):
        if value:
            self.service_name = value.get("name", "Unknown")
            self.port = str(value.get("port", "-"))
            python = value.get("python_path", "system")
            self.python_path = f"🐍 {Path(python).name}" if python != "system" else "🐍 system"
            deps = value.get("dependencies", [])
            self.dependencies = ", ".join(deps) if deps else "-"

    def update_status(self, is_running):
        if is_running:
            self.status = "●"
            self.status_color = COLORS["running"]
        else:
            self.status = "○"
            self.status_color = COLORS["stopped"]


class ServicesList(RecycleView):
    """RecycleView for displaying services"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.layout_manager = RecycleBoxLayout(
            default_size=(None, dp(50)),
            default_size_hint=(1, None),
            size_hint_y=None,
            height=self.height,
            orientation='vertical',
            spacing=dp(2)
        )
        self.layout_manager.bind(minimum_height=self.layout_manager.setter('height'))
        self.add_widget(self.layout_manager)


class ServiceDialog(Popup):
    """Dialog for adding/editing services"""

    def __init__(self, parent=None, service=None, project_data=None, root_dir=None, **kwargs):
        super().__init__(**kwargs)
        self.parent_app = parent
        self.service = service
        self.project_data = project_data
        self.root_dir = Path(root_dir) if root_dir else Path.cwd()
        self.title = "Редактирование сервиса" if service else "Новый сервис"
        self.size_hint = (0.8, 0.9)
        self.auto_dismiss = False
        self.setup_ui()

    def setup_ui(self):
        layout = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))

        # ScrollView for form
        scroll = ScrollView()
        form_layout = GridLayout(cols=2, spacing=dp(10), size_hint_y=None)
        form_layout.bind(minimum_height=form_layout.setter('height'))

        # Name
        form_layout.add_widget(Label(text="Имя сервиса*:", size_hint_x=0.3, halign='right'))
        self.name_input = TextInput(text=self.service.get("name", "") if self.service else "")
        form_layout.add_widget(self.name_input)

        # Script path
        form_layout.add_widget(Label(text="Путь к скрипту*:", size_hint_x=0.3, halign='right'))
        script_layout = BoxLayout(size_hint_x=0.7)
        self.script_input = TextInput(text=self.service.get("script", "") if self.service else "")
        script_layout.add_widget(self.script_input)
        browse_btn = Button(text="Обзор", size_hint_x=0.3)
        browse_btn.bind(on_press=self.browse_script)
        script_layout.add_widget(browse_btn)
        form_layout.add_widget(script_layout)

        # Python path
        form_layout.add_widget(Label(text="Python интерпретатор:", size_hint_x=0.3, halign='right'))
        python_layout = BoxLayout(size_hint_x=0.7)
        self.python_input = TextInput(text=self.service.get("python_path", "system") if self.service else "system")
        python_layout.add_widget(self.python_input)
        python_browse = Button(text="Обзор", size_hint_x=0.3)
        python_browse.bind(on_press=self.browse_python)
        python_layout.add_widget(python_browse)
        form_layout.add_widget(python_layout)

        # Host
        form_layout.add_widget(Label(text="Хост:", size_hint_x=0.3, halign='right'))
        self.host_input = TextInput(text=self.service.get("host", "127.0.0.1") if self.service else "127.0.0.1")
        form_layout.add_widget(self.host_input)

        # Port
        form_layout.add_widget(Label(text="Порт:", size_hint_x=0.3, halign='right'))
        self.port_input = TextInput(
            text=str(self.service.get("port", "")) if self.service and self.service.get("port") else "")
        form_layout.add_widget(self.port_input)

        # Health check path
        form_layout.add_widget(Label(text="Health check path:", size_hint_x=0.3, halign='right'))
        self.health_input = TextInput(text=self.service.get("health_path", "/health") if self.service else "/health")
        form_layout.add_widget(self.health_input)

        # Env file
        form_layout.add_widget(Label(text="Файл .env:", size_hint_x=0.3, halign='right'))
        env_layout = BoxLayout(size_hint_x=0.7)
        self.env_input = TextInput(text=self.service.get("env_file", "") if self.service else "")
        env_layout.add_widget(self.env_input)
        env_browse = Button(text="Обзор", size_hint_x=0.3)
        env_browse.bind(on_press=self.browse_env)
        env_layout.add_widget(env_browse)
        form_layout.add_widget(env_layout)

        # Working directory
        form_layout.add_widget(Label(text="Рабочая директория:", size_hint_x=0.3, halign='right'))
        wd_layout = BoxLayout(size_hint_x=0.7)
        self.wd_input = TextInput(text=self.service.get("working_dir", "") if self.service else "")
        wd_layout.add_widget(self.wd_input)
        wd_browse = Button(text="Обзор", size_hint_x=0.3)
        wd_browse.bind(on_press=self.browse_working_dir)
        wd_layout.add_widget(wd_browse)
        form_layout.add_widget(wd_layout)

        # Order
        form_layout.add_widget(Label(text="Порядок запуска:", size_hint_x=0.3, halign='right'))
        self.order_input = TextInput(text=str(self.service.get("order", 999)) if self.service else "999")
        form_layout.add_widget(self.order_input)

        scroll.add_widget(form_layout)
        layout.add_widget(scroll)

        # Buttons
        btn_layout = BoxLayout(size_hint_y=0.1, spacing=dp(10))
        save_btn = Button(text="Сохранить")
        save_btn.bind(on_press=self.save)
        cancel_btn = Button(text="Отмена")
        cancel_btn.bind(on_press=self.dismiss)
        btn_layout.add_widget(save_btn)
        btn_layout.add_widget(cancel_btn)
        layout.add_widget(btn_layout)

        self.add_widget(layout)

    def browse_working_dir(self, instance):
        from kivy.uix.filechooser import FileChooserListView
        filechooser = FileChooserListView(path=str(self.root_dir))
        popup = Popup(title="Выберите рабочую директорию", content=filechooser, size_hint=(0.9, 0.9))
        filechooser.bind(on_submit=lambda *args: self.on_dir_selected(filechooser.selection, popup))
        popup.open()

    def on_dir_selected(self, selection, popup):
        if selection:
            self.wd_input.text = selection[0]
        popup.dismiss()

    def browse_script(self, instance):
        from kivy.uix.filechooser import FileChooserListView
        filechooser = FileChooserListView(path=str(self.root_dir), filters=['*.py'])
        popup = Popup(title="Выберите скрипт", content=filechooser, size_hint=(0.9, 0.9))
        filechooser.bind(on_submit=lambda *args: self.on_file_selected(filechooser.selection, popup, self.script_input))
        popup.open()

    def browse_python(self, instance):
        from kivy.uix.filechooser import FileChooserListView
        filechooser = FileChooserListView(path=str(self.root_dir))
        popup = Popup(title="Выберите Python интерпретатор", content=filechooser, size_hint=(0.9, 0.9))
        filechooser.bind(on_submit=lambda *args: self.on_file_selected(filechooser.selection, popup, self.python_input))
        popup.open()

    def browse_env(self, instance):
        from kivy.uix.filechooser import FileChooserListView
        filechooser = FileChooserListView(path=str(self.root_dir), filters=['*.env'])
        popup = Popup(title="Выберите .env файл", content=filechooser, size_hint=(0.9, 0.9))
        filechooser.bind(on_submit=lambda *args: self.on_file_selected(filechooser.selection, popup, self.env_input))
        popup.open()

    def on_file_selected(self, selection, popup, input_widget):
        if selection:
            input_widget.text = selection[0]
        popup.dismiss()

    def save(self, instance):
        try:
            service_data = {
                "name": self.name_input.text,
                "script": self.script_input.text,
                "python_path": self.python_input.text,
                "host": self.host_input.text,
                "port": int(self.port_input.text) if self.port_input.text.isdigit() else None,
                "health_path": self.health_input.text,
                "env_file": self.env_input.text,
                "working_dir": self.wd_input.text,
                "order": int(self.order_input.text) if self.order_input.text.isdigit() else 999,
                "dependencies": []  # Will be added later
            }

            if not service_data["name"] or not service_data["script"]:
                error_popup = Popup(title="Ошибка", content=Label(text="Имя и путь к скрипту обязательны"),
                                    size_hint=(0.6, 0.3))
                error_popup.open()
                return

            if self.parent_app:
                if self.service:
                    self.parent_app.update_service(self.service, service_data)
                else:
                    self.parent_app.add_service_to_project(service_data)

            self.dismiss()
        except Exception as e:
            error_popup = Popup(title="Ошибка", content=Label(text=f"Ошибка сохранения: {e}"), size_hint=(0.6, 0.3))
            error_popup.open()


class ProjectSettingsDialog(Popup):
    """Dialog for project settings"""

    def __init__(self, parent=None, settings=None, **kwargs):
        super().__init__(**kwargs)
        self.parent_app = parent
        self.settings = settings or DEFAULT_CONFIG["settings"]
        self.title = "Настройки проекта"
        self.size_hint = (0.6, 0.6)
        self.auto_dismiss = False
        self.setup_ui()

    def setup_ui(self):
        layout = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))

        form_layout = GridLayout(cols=2, spacing=dp(10), size_hint_y=None)
        form_layout.bind(minimum_height=form_layout.setter('height'))

        # Restart delay
        form_layout.add_widget(Label(text="Задержка перезапуска (сек):", size_hint_x=0.6, halign='right'))
        self.restart_delay = TextInput(text=str(self.settings.get("restart_delay", 3)))
        form_layout.add_widget(self.restart_delay)

        # Port check timeout
        form_layout.add_widget(Label(text="Таймаут проверки порта:", size_hint_x=0.6, halign='right'))
        self.port_timeout = TextInput(text=str(self.settings.get("port_check_timeout", 10)))
        form_layout.add_widget(self.port_timeout)

        # Graceful shutdown timeout
        form_layout.add_widget(Label(text="Таймаут graceful shutdown (сек):", size_hint_x=0.6, halign='right'))
        self.shutdown_timeout = TextInput(text=str(self.settings.get("graceful_shutdown_timeout", 30)))
        form_layout.add_widget(self.shutdown_timeout)

        layout.add_widget(form_layout)

        # Buttons
        btn_layout = BoxLayout(size_hint_y=0.1, spacing=dp(10))
        save_btn = Button(text="Сохранить")
        save_btn.bind(on_press=self.save)
        cancel_btn = Button(text="Отмена")
        cancel_btn.bind(on_press=self.dismiss)
        btn_layout.add_widget(save_btn)
        btn_layout.add_widget(cancel_btn)
        layout.add_widget(btn_layout)

        self.add_widget(layout)

    def save(self, instance):
        try:
            settings = {
                "restart_delay": int(self.restart_delay.text),
                "port_check_timeout": int(self.port_timeout.text),
                "graceful_shutdown_timeout": int(self.shutdown_timeout.text),
                "auto_start_dependencies": True,
                "log_level": "INFO"
            }
            if self.parent_app:
                self.parent_app.update_project_settings(settings)
            self.dismiss()
        except Exception as e:
            error_popup = Popup(title="Ошибка", content=Label(text=f"Ошибка сохранения: {e}"), size_hint=(0.6, 0.3))
            error_popup.open()


class MainApp(App):
    """Main application class"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.process_info = {}
        self.service_root_pids = {}
        self.process_lock = threading.RLock()
        self.stopped_service_pids = []
        self.running = True
        self.current_project = None
        self.project_data = None
        self.services_widgets = {}
        self.workers = {}
        self.starting_services = set()
        self.monitor_thread = None
        self.monitor_stop_event = threading.Event()
        self._is_closing = False
        self._closing_started = False

    def build(self):
        self.title = f"{APP_NAME} v{APP_VERSION}"
        self.setup_directories()

        main_layout = BoxLayout(orientation='vertical')

        # Toolbar
        toolbar = BoxLayout(size_hint_y=0.08, spacing=dp(5), padding=dp(5))

        toolbar.add_widget(Label(text="Проект:", size_hint_x=0.1))
        self.project_spinner = Spinner(text="Выберите проект", values=[], size_hint_x=0.3)
        self.project_spinner.bind(text=self.on_project_select)
        toolbar.add_widget(self.project_spinner)

        toolbar.add_widget(Label(size_hint_x=0.3))

        self.start_all_btn = Button(text="Запустить все", size_hint_x=0.15)
        self.start_all_btn.bind(on_press=self.start_all)
        toolbar.add_widget(self.start_all_btn)

        self.stop_all_btn = Button(text="Остановить все", size_hint_x=0.15)
        self.stop_all_btn.bind(on_press=self.stop_all)
        toolbar.add_widget(self.stop_all_btn)

        self.restart_all_btn = Button(text="Перезапустить все", size_hint_x=0.15)
        self.restart_all_btn.bind(on_press=self.restart_all)
        toolbar.add_widget(self.restart_all_btn)

        main_layout.add_widget(toolbar)

        # Menu bar
        menubar = BoxLayout(size_hint_y=0.06, spacing=dp(2))

        # File menu
        file_btn = Button(text="Файл", size_hint_x=0.1)
        file_dropdown = DropDown()
        new_item = Button(text="Новый проект", size_hint_y=None, height=dp(40))
        new_item.bind(on_release=lambda btn: self.new_project())
        file_dropdown.add_widget(new_item)
        open_item = Button(text="Открыть проект", size_hint_y=None, height=dp(40))
        open_item.bind(on_release=lambda btn: self.open_project())
        file_dropdown.add_widget(open_item)
        save_item = Button(text="Сохранить проект", size_hint_y=None, height=dp(40))
        save_item.bind(on_release=lambda btn: self.save_project())
        file_dropdown.add_widget(save_item)
        import_item = Button(text="Импорт конфигурации", size_hint_y=None, height=dp(40))
        import_item.bind(on_release=lambda btn: self.import_config())
        file_dropdown.add_widget(import_item)
        export_item = Button(text="Экспорт конфигурации", size_hint_y=None, height=dp(40))
        export_item.bind(on_release=lambda btn: self.export_config())
        file_dropdown.add_widget(export_item)
        exit_item = Button(text="Выход", size_hint_y=None, height=dp(40))
        exit_item.bind(on_release=lambda btn: self.stop())
        file_dropdown.add_widget(exit_item)
        file_btn.bind(on_release=file_dropdown.open)
        menubar.add_widget(file_btn)

        # Services menu
        services_btn = Button(text="Сервисы", size_hint_x=0.1)
        services_dropdown = DropDown()
        add_item = Button(text="Добавить сервис", size_hint_y=None, height=dp(40))
        add_item.bind(on_release=lambda btn: self.add_service())
        services_dropdown.add_widget(add_item)
        edit_item = Button(text="Редактировать сервис", size_hint_y=None, height=dp(40))
        edit_item.bind(on_release=lambda btn: self.edit_service())
        services_dropdown.add_widget(edit_item)
        delete_item = Button(text="Удалить сервис", size_hint_y=None, height=dp(40))
        delete_item.bind(on_release=lambda btn: self.delete_service())
        services_dropdown.add_widget(delete_item)
        services_btn.bind(on_release=services_dropdown.open)
        menubar.add_widget(services_btn)

        # Settings menu
        settings_btn = Button(text="Настройки", size_hint_x=0.1)
        settings_dropdown = DropDown()
        project_settings_item = Button(text="Настройки проекта", size_hint_y=None, height=dp(40))
        project_settings_item.bind(on_release=lambda btn: self.project_settings())
        settings_dropdown.add_widget(project_settings_item)
        settings_btn.bind(on_release=settings_dropdown.open)
        menubar.add_widget(settings_btn)

        # Help menu
        help_btn = Button(text="Помощь", size_hint_x=0.1)
        help_dropdown = DropDown()
        help_item = Button(text="Справка", size_hint_y=None, height=dp(40))
        help_item.bind(on_release=lambda btn: self.show_help())
        help_dropdown.add_widget(help_item)
        about_item = Button(text="О программе", size_hint_y=None, height=dp(40))
        about_item.bind(on_release=lambda btn: self.show_about())
        help_dropdown.add_widget(about_item)
        help_btn.bind(on_release=help_dropdown.open)
        menubar.add_widget(help_btn)

        menubar.add_widget(Label())
        main_layout.add_widget(menubar)

        # Splitter
        splitter = BoxLayout(orientation='vertical', spacing=dp(5))

        # Services list
        services_container = BoxLayout(orientation='vertical', size_hint_y=0.7)
        services_container.add_widget(Label(text="Сервисы", size_hint_y=0.05, font_size=dp(14), bold=True))

        # Header
        header = BoxLayout(size_hint_y=0.07, spacing=dp(2))
        headers = ["Статус", "Сервис", "Порт", "PID", "Python", "Зависимости", "Действия"]
        header_widths = [0.1, 0.25, 0.1, 0.1, 0.15, 0.2, 0.1]
        for h, w in zip(headers, header_widths):
            header.add_widget(Label(text=h, size_hint_x=w, bold=True))
        services_container.add_widget(header)

        # Scrollable services list
        self.services_scroll = ScrollView()
        self.services_layout = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
        self.services_layout.bind(minimum_height=self.services_layout.setter('height'))
        self.services_scroll.add_widget(self.services_layout)
        services_container.add_widget(self.services_scroll)

        splitter.add_widget(services_container)

        # Log area
        log_container = BoxLayout(orientation='vertical', size_hint_y=0.3)
        log_container.add_widget(Label(text="Логи", size_hint_y=0.1, font_size=dp(14), bold=True))
        self.log_text = ScrollView()
        self.log_layout = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
        self.log_layout.bind(minimum_height=self.log_layout.setter('height'))
        self.log_text.add_widget(self.log_layout)
        log_container.add_widget(self.log_text)

        splitter.add_widget(log_container)

        main_layout.add_widget(splitter)

        # Status bar
        self.status_label = Label(text="Готов к работе", size_hint_y=0.05)
        main_layout.add_widget(self.status_label)

        self.load_projects_list()
        self.start_monitoring()

        return main_layout

    def setup_directories(self):
        CONFIG_DIR.mkdir(exist_ok=True)
        PROJECTS_DIR.mkdir(exist_ok=True)
        SERVICES_DIR.mkdir(exist_ok=True)
        LOG_DIR.mkdir(exist_ok=True)

    def lock_unlock(self, stage=1):
        if stage == 1:
            self.start_all_btn.disabled = False
            self.stop_all_btn.disabled = True
            self.restart_all_btn.disabled = True
            self.project_spinner.disabled = False
        elif stage == 2:
            self.start_all_btn.disabled = True
            self.stop_all_btn.disabled = False
            self.restart_all_btn.disabled = False
            self.project_spinner.disabled = True

    def load_projects_list(self):
        projects = []
        for file in PROJECTS_DIR.glob("*.json"):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    name = data.get("name", file.stem)
                    projects.append(name)
            except:
                projects.append(file.stem)

        self.project_spinner.values = projects
        self.lock_unlock(1)

    def on_project_select(self, spinner, project_name):
        if not project_name or project_name == "Выберите проект":
            return

        for file in PROJECTS_DIR.glob("*.json"):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if data.get("name", file.stem) == project_name:
                        self.load_project(file)
                        break
            except:
                if file.stem == project_name:
                    self.load_project(file)
                    break

    def load_project(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                self.project_data = json.load(f)

            self.current_project = path
            self.refresh_display()
            self.log(f"Загружен проект: {self.project_data.get('name')}")

            if "root_dir" in self.project_data and self.project_data["root_dir"]:
                os.chdir(self.project_data["root_dir"])

        except Exception as e:
            error_popup = Popup(title="Ошибка", content=Label(text=f"Не удалось загрузить проект: {e}"),
                                size_hint=(0.6, 0.3))
            error_popup.open()

    def save_project(self):
        if not self.current_project or not self.project_data:
            self.save_project_as()
            return

        try:
            self.project_data["modified"] = datetime.now().isoformat()
            with open(self.current_project, 'w', encoding='utf-8') as f:
                json.dump(self.project_data, f, ensure_ascii=False, indent=2)
            self.log(f"Проект сохранен: {self.project_data.get('name')}")
        except Exception as e:
            error_popup = Popup(title="Ошибка", content=Label(text=f"Не удалось сохранить проект: {e}"),
                                size_hint=(0.6, 0.3))
            error_popup.open()

    def save_project_as(self):
        # Simplified - just save with current name
        if self.project_data:
            filename = PROJECTS_DIR / f"{self.project_data.get('name', 'project')}.json"
            self.current_project = filename
            self.save_project()

    def new_project(self):
        # Simplified new project dialog
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        content.add_widget(Label(text="Имя проекта:"))
        name_input = TextInput()
        content.add_widget(name_input)
        content.add_widget(Label(text="Корневая директория:"))
        root_input = TextInput()
        root_btn = Button(text="Обзор", size_hint_y=0.3)

        def browse_root(instance):
            from kivy.uix.filechooser import FileChooserListView
            fc = FileChooserListView(path=str(Path.cwd()))
            popup = Popup(title="Выберите корневую директорию", content=fc, size_hint=(0.9, 0.9))
            fc.bind(on_submit=lambda *args: self.on_root_selected(fc.selection, popup, root_input))
            popup.open()

        root_btn.bind(on_press=browse_root)
        content.add_widget(root_input)
        content.add_widget(root_btn)

        btn_layout = BoxLayout(size_hint_y=0.3, spacing=dp(10))
        create_btn = Button(text="Создать")
        cancel_btn = Button(text="Отмена")
        btn_layout.add_widget(create_btn)
        btn_layout.add_widget(cancel_btn)
        content.add_widget(btn_layout)

        popup = Popup(title="Новый проект", content=content, size_hint=(0.6, 0.5))

        def create_project(instance):
            name = name_input.text
            root_dir = root_input.text
            if name and root_dir:
                project_data = {
                    "name": name,
                    "root_dir": root_dir,
                    "description": "",
                    "services": [],
                    "settings": DEFAULT_CONFIG["settings"].copy(),
                    "created": datetime.now().isoformat(),
                    "modified": datetime.now().isoformat()
                }

                filename = PROJECTS_DIR / f"{name}.json"
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(project_data, f, ensure_ascii=False, indent=2)

                self.load_projects_list()
                self.project_spinner.text = name
                self.current_project = filename
                self.project_data = project_data
                self.refresh_display()
                self.log(f"Создан проект: {name}")
                popup.dismiss()

        create_btn.bind(on_press=create_project)
        cancel_btn.bind(on_press=popup.dismiss)
        popup.open()

    def on_root_selected(self, selection, popup, input_widget):
        if selection:
            input_widget.text = selection[0]
        popup.dismiss()

    def open_project(self):
        from kivy.uix.filechooser import FileChooserListView
        fc = FileChooserListView(path=str(PROJECTS_DIR), filters=['*.json'])
        popup = Popup(title="Выберите файл проекта", content=fc, size_hint=(0.9, 0.9))
        fc.bind(on_submit=lambda *args: self.on_project_file_selected(fc.selection, popup))
        popup.open()

    def on_project_file_selected(self, selection, popup):
        if selection:
            self.load_project(Path(selection[0]))
        popup.dismiss()

    def log(self, message, level="info"):
        if self._is_closing:
            return

        Clock.schedule_once(lambda dt: self._log(message, level))

    def _log(self, message, level="info"):
        if self._is_closing:
            return

        timestamp = datetime.now().strftime("%H:%M:%S")

        if level == "error":
            log_entry = f"[{timestamp}] ❌ {message}"
        elif level == "warning":
            log_entry = f"[{timestamp}] ⚠️ {message}"
        elif level == "success":
            log_entry = f"[{timestamp}] ✅ {message}"
        else:
            log_entry = f"[{timestamp}] ℹ️ {message}"

        log_label = Label(text=log_entry, size_hint_y=None, height=dp(25), text_size=(self.log_text.width, None),
                          valign='top')
        log_label.bind(texture_size=log_label.setter('size'))
        self.log_layout.add_widget(log_label)

        # Auto-scroll to bottom
        Clock.schedule_once(lambda dt: setattr(self.log_text, 'scroll_y', 0))

        print(log_entry)

    def refresh_display(self):
        Clock.schedule_once(lambda dt: self._do_refresh_display())

    def _do_refresh_display(self):
        if self._is_closing:
            return

        try:
            self.services_layout.clear_widgets()
            self.services_widgets.clear()

            if not self.project_data:
                label = Label(text="Нет загруженного проекта", size_hint_y=None, height=dp(50))
                self.services_layout.add_widget(label)
                return

            services = self.project_data.get("services", [])
            if not services:
                label = Label(text="Нет сервисов в проекте. Нажмите 'Добавить сервис'", size_hint_y=None, height=dp(50))
                self.services_layout.add_widget(label)
                return

            services.sort(key=lambda x: x.get("order", 999))

            for service in services:
                self.add_service_to_list(service)

        except Exception as e:
            print(f"Error in refresh_display: {e}")

    def add_service_to_list(self, service):
        if self._is_closing:
            return

        service_name = service.get("name", "Unknown")

        with self.process_lock:
            is_running = service_name in self.service_root_pids

        # Service row
        row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(2))

        # Status
        status_label = Label(text="●" if is_running else "○",
                             color=COLORS["running"] if is_running else COLORS["stopped"], size_hint_x=0.1,
                             font_size=dp(20))
        row.add_widget(status_label)

        # Service name
        name_label = Label(text=service_name, size_hint_x=0.25, bold=True)
        row.add_widget(name_label)

        # Port
        port = service.get("port", "-")
        port_label = Label(text=str(port), size_hint_x=0.1)
        row.add_widget(port_label)

        # PID
        pid = "-"
        with self.process_lock:
            root_pid = self.service_root_pids.get(service_name)
            if root_pid:
                pid = str(root_pid)
        pid_label = Label(text=pid, size_hint_x=0.1)
        row.add_widget(pid_label)

        # Python
        python_path = service.get("python_path", "system")
        python_display = f"🐍 system" if python_path == "system" else f"🐍 {Path(python_path).name}"
        python_label = Label(text=python_display, size_hint_x=0.15)
        row.add_widget(python_label)

        # Dependencies
        deps = service.get("dependencies", [])
        deps_text = ", ".join(deps) if deps else "-"
        deps_label = Label(text=deps_text, size_hint_x=0.2)
        row.add_widget(deps_label)

        # Actions
        actions_layout = BoxLayout(size_hint_x=0.1, spacing=dp(4))

        start_btn = Button(text="▶️", size_hint_x=0.25)
        start_btn.bind(on_press=lambda btn, s=service: self.start_service(s))
        actions_layout.add_widget(start_btn)

        stop_btn = Button(text="⏹️", size_hint_x=0.25)
        stop_btn.bind(on_press=lambda btn, s=service: self.stop_service(s))
        actions_layout.add_widget(stop_btn)

        restart_btn = Button(text="🔄", size_hint_x=0.25)
        restart_btn.bind(on_press=lambda btn, s=service: self.restart_service(s))
        actions_layout.add_widget(restart_btn)

        edit_btn = Button(text="⚙️", size_hint_x=0.25)
        edit_btn.bind(on_press=lambda btn, s=service: self.edit_service_dialog(s))
        actions_layout.add_widget(edit_btn)

        row.add_widget(actions_layout)

        self.services_layout.add_widget(row)
        self.services_widgets[service_name] = row

    def start_monitoring(self):
        def monitor():
            while not self.monitor_stop_event.is_set():
                self.monitor_stop_event.wait(2)

                if self.monitor_stop_event.is_set() or self._is_closing:
                    break

                dead_services = []

                with self.process_lock:
                    for service_name, root_pid in list(self.service_root_pids.items()):
                        try:
                            if not psutil.pid_exists(root_pid):
                                dead_services.append(service_name)
                            else:
                                proc = psutil.Process(root_pid)
                                if proc.status() == psutil.STATUS_ZOMBIE:
                                    dead_services.append(service_name)
                        except:
                            dead_services.append(service_name)

                if dead_services:
                    with self.process_lock:
                        for service_name in dead_services:
                            if service_name in self.service_root_pids:
                                del self.service_root_pids[service_name]

                    if not self._is_closing:
                        for service_name in dead_services:
                            self.log(f"💀 Сервис {service_name} завершился", "warning")
                        Clock.schedule_once(lambda dt: self.refresh_display())

        self.monitor_stop_event.clear()
        self.monitor_thread = threading.Thread(target=monitor, daemon=True)
        self.monitor_thread.start()

    def find_service_by_name(self, service_name):
        if not self.project_data:
            return None
        for s in self.project_data.get("services", []):
            if s.get("name") == service_name:
                return s
        return None

    def is_service_running(self, service_name):
        with self.process_lock:
            return service_name in self.service_root_pids

    def get_all_child_processes(self, root_pid):
        children = set()
        try:
            root = psutil.Process(root_pid)
            for child in root.children(recursive=True):
                children.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return children

    def find_all_processes_by_service(self, service_name, script_path=None):
        processes = []

        root_pid = None
        with self.process_lock:
            root_pid = self.service_root_pids.get(service_name)

        if root_pid:
            try:
                root_proc = psutil.Process(root_pid)
                processes.append(root_proc)
                for child in root_proc.children(recursive=True):
                    processes.append(child)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if not processes and script_path:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = ' '.join(proc.info['cmdline'] if proc.info['cmdline'] else [])
                    if script_path in cmdline or service_name in cmdline:
                        processes.append(proc)
                        try:
                            for child in proc.children(recursive=True):
                                if child not in processes:
                                    processes.append(child)
                        except:
                            pass
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        return processes

    def stop_service_gracefully(self, service):
        service_name = service.get("name")
        script_path = service.get("script", "")

        graceful_timeout = 30
        if self.project_data:
            graceful_timeout = self.project_data.get("settings", {}).get("graceful_shutdown_timeout", 30)

        # Stop worker
        worker = None
        with self.process_lock:
            worker = self.workers.pop(service_name, None)

        if worker:
            try:
                worker.stop()
                if worker.is_alive():
                    worker.join(timeout=3)
            except Exception as e:
                print(f"Ошибка остановки worker: {e}")

        # Find all processes
        processes = self.find_all_processes_by_service(service_name, script_path)

        if not processes:
            return True

        print(f"Найдено процессов: {len(processes)}")

        # Send graceful shutdown signal
        for proc in processes:
            try:
                if sys.platform == 'win32':
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
            except Exception as e:
                print(f"Ошибка отправки сигнала процессу {proc.pid}: {e}")

        # Wait for graceful shutdown
        wait_time = 0
        check_interval = 1

        while wait_time < graceful_timeout:
            all_stopped = True
            for proc in processes[:]:
                try:
                    if proc.is_running():
                        all_stopped = False
                    else:
                        if proc in processes:
                            processes.remove(proc)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    if proc in processes:
                        processes.remove(proc)
                    continue

            if not processes:
                elapsed = int(wait_time)
                print(f"✅ {service_name} корректно остановлен за {elapsed} сек")

                with self.process_lock:
                    if service_name in self.service_root_pids:
                        del self.service_root_pids[service_name]
                    self.starting_services.discard(service_name)

                return True

            if wait_time % 5 == 0 and wait_time > 0:
                remaining_pids = [proc.pid for proc in processes]
                print(f"Ожидание завершения... ({wait_time}/{graceful_timeout} сек), осталось: {remaining_pids}")

            time.sleep(check_interval)
            wait_time += check_interval

        print(f"⚠️ {service_name} не завершился за {graceful_timeout} сек")
        return False

    def stop_service(self, service):
        if self._is_closing:
            return

        service_name = service.get("name")
        print(f"\nОстанавливаю {service_name}...")
        success = self.stop_service_gracefully(service)

        if not success:
            # Simplified - just kill processes
            print(f"Принудительное завершение {service_name}...")
            processes = self.find_all_processes_by_service(service_name, service.get("script", ""))
            for proc in processes:
                try:
                    proc.kill()
                except:
                    pass

            with self.process_lock:
                if service_name in self.service_root_pids:
                    del self.service_root_pids[service_name]
                self.starting_services.discard(service_name)

        self.refresh_display()

    def get_all_dependencies(self, service, collected=None, visited=None):
        if collected is None:
            collected = []
        if visited is None:
            visited = set()

        service_name = service.get("name")
        if service_name in visited:
            return collected

        visited.add(service_name)

        deps = service.get("dependencies", [])

        for dep_name in deps:
            dep_service = self.find_service_by_name(dep_name)
            if dep_service:
                self.get_all_dependencies(dep_service, collected, visited)
                if dep_service not in collected:
                    collected.append(dep_service)

        return collected

    def get_dependency_chain_from_root(self, service):
        all_deps = self.get_all_dependencies(service, [], set())

        seen = set()
        unique_deps = []
        for dep in all_deps:
            dep_name = dep.get("name")
            if dep_name not in seen:
                seen.add(dep_name)
                unique_deps.append(dep)

        return unique_deps

    def order_services_by_dependencies_reverse(self, services):
        dep_graph = {}
        for service in services:
            service_name = service.get("name")
            deps = service.get("dependencies", [])
            active_deps = [d for d in deps if any(s.get("name") == d for s in services)]
            dep_graph[service_name] = active_deps

        ordered = []
        visited = set()
        temp_mark = set()

        def visit(name):
            if name in temp_mark:
                return
            if name in visited:
                return

            temp_mark.add(name)
            for dep in dep_graph.get(name, []):
                if dep in [s.get("name") for s in services]:
                    visit(dep)
            temp_mark.remove(name)
            visited.add(name)

            service = next((s for s in services if s.get("name") == name), None)
            if service:
                ordered.insert(0, service)

        for service in services:
            if service.get("name") not in visited:
                visit(service.get("name"))

        return ordered

    def check_and_start_dependencies(self, service):
        service_name = service.get("name")

        all_deps = self.get_dependency_chain_from_root(service)

        if all_deps:
            dep_names = [d.get("name") for d in all_deps]
            self.log(f"📋 Цепочка зависимостей для {service_name}: {' → '.join(dep_names)} → {service_name}")

        for dep in all_deps:
            dep_name = dep.get("name")

            if self.is_service_running(dep_name):
                self.log(f"✅ Зависимость {dep_name} уже запущена")
                continue

            if dep_name in self.starting_services:
                self.log(f"⏳ Зависимость {dep_name} уже запускается, ждем...")
                if not self.wait_for_service_ready(dep):
                    self.log(f"❌ Ошибка: зависимость {dep_name} не запустилась", "error")
                    return False
                continue

            self.log(f"🔄 Запуск зависимости: {dep_name}")
            if not self.start_single_service(dep):
                self.log(f"❌ Ошибка: не удалось запустить {dep_name}", "error")
                return False

            if not self.wait_for_service_ready(dep):
                self.log(f"❌ Ошибка: зависимость {dep_name} не запустилась", "error")
                return False

        return True

    def wait_for_service_ready(self, service, timeout=30):
        start = time.time()
        service_name = service.get("name")
        has_port = service.get("port") is not None
        host = service.get("host", "127.0.0.1")
        port = service.get("port")
        health_path = service.get("health_path", "/health")

        self.log(f"⏳ Ожидание готовности {service_name} ...")

        wait_start = time.time()
        while time.time() - wait_start < 5:
            if self.is_service_running(service_name):
                break
            time.sleep(0.1)

        if not self.is_service_running(service_name):
            self.log(f"⚠️ Сервис {service_name} не зарегистрирован в системе")
            return False

        while time.time() - start < timeout:
            if has_port and port:
                try:
                    url = f"http://{host}:{port}{health_path}"
                    response = requests.get(url, timeout=2)

                    if response.status_code == 200:
                        try:
                            data = response.json()
                            status = data.get("status", "").lower()
                            if status in ["ok", "healthy", "up"]:
                                elapsed = int(time.time() - start)
                                self.log(f"✅ Сервис {service_name} готов (health check OK, через {elapsed} сек)")
                                return True
                        except:
                            if "ok" in response.text.lower() or "healthy" in response.text.lower():
                                elapsed = int(time.time() - start)
                                self.log(f"✅ Сервис {service_name} готов (health check OK, через {elapsed} сек)")
                                return True
                except:
                    pass
            else:
                time.sleep(3)
                self.log(f"✅ Сервис {service_name} готов (процесс запущен)")
                return True

            time.sleep(1)

        self.log(f"⚠️ Таймаут ожидания готовности {service_name} ({timeout} сек)")
        return False

    def start_single_service(self, service):
        if self._is_closing:
            return False

        service_name = service.get("name")

        with self.process_lock:
            if self.is_service_running(service_name):
                self.log(f"Сервис {service_name} уже запущен")
                return True

            if service_name in self.starting_services:
                self.log(f"Сервис {service_name} уже запускается")
                return True

        if service.get("port"):
            host = service.get("host", "127.0.0.1")
            port = service["port"]
            if not self.is_port_available(host, port):
                self.log(f"⚠️ Порт {port} уже занят, возможно сервис уже запущен")
                self.kill_process_on_port(port)
                time.sleep(1)
                if not self.is_port_available(host, port):
                    self.log(f"❌ Порт {port} всё ещё занят, не могу запустить {service_name}", "error")
                    return False

        with self.process_lock:
            self.starting_services.add(service_name)

        try:
            root_dir = self.project_data.get("root_dir", "")
            worker = ServiceWorker('start', service, self.project_data, root_dir, self.handle_worker_callback)

            with self.process_lock:
                self.workers[service_name] = worker
            worker.start()

            timeout = 5
            start_time = time.time()
            while time.time() - start_time < timeout:
                if self.is_service_running(service_name):
                    self.log(f"✅ Сервис {service_name} зарегистрирован в системе")
                    return True
                time.sleep(0.1)

            self.log(f"⚠️ Сервис {service_name} запущен, но не зарегистрирован в системе")
            return True

        except Exception as e:
            self.log(f"❌ Ошибка запуска {service_name}: {e}", "error")
            with self.process_lock:
                self.starting_services.discard(service_name)
            return False

    def handle_worker_callback(self, event_type, *args):
        if event_type == 'log':
            self.log(args[0], args[1] if len(args) > 1 else "info")
        elif event_type == 'process_started':
            Clock.schedule_once(lambda dt: self.on_process_started(args[0], args[1]))
        elif event_type == 'process_stopped':
            Clock.schedule_once(lambda dt: self.on_process_stopped(args[0], args[1]))

    def start_service(self, service):
        if self._is_closing:
            return False

        service_name = service.get("name")

        if self.is_service_running(service_name):
            self.log(f"Сервис {service_name} уже запущен")
            return True

        if service_name in self.starting_services:
            self.log(f"Сервис {service_name} уже запускается")
            return True

        if self.project_data and self.project_data.get("settings", {}).get("auto_start_dependencies", True):
            self.log(f"🔍 Проверка зависимостей для {service_name}")

            if not self.check_and_start_dependencies(service):
                self.log(f"❌ Не удалось запустить зависимости для {service_name}", "error")
                return False

            if self.is_service_running(service_name):
                self.log(f"Сервис {service_name} был запущен через зависимости")
                return True

        return self.start_single_service(service)

    def restart_service(self, service):
        self.stop_service(service)
        time.sleep(2)
        self.start_service(service)

    def start_all(self, instance):
        if not self.project_data or self._is_closing:
            return

        self.lock_unlock(2)

        services = self.project_data.get("services", [])

        all_deps = set()
        for service in services:
            for dep in service.get("dependencies", []):
                all_deps.add(dep)

        root_services = [s for s in services if s.get("name") not in all_deps]

        self.log(f"🌳 Корневые сервисы: {[s.get('name') for s in root_services]}")

        for service in root_services:
            self.start_service(service)

        for service in services:
            if service not in root_services:
                self.start_service(service)

    def stop_all(self, instance):
        if self._is_closing:
            return

        if not self.project_data:
            return

        running_services = []
        for service in self.project_data.get("services", []):
            if self.is_service_running(service.get("name")):
                running_services.append(service)

        if not running_services:
            self.log("Нет запущенных сервисов")
            return

        services_to_stop = self.order_services_by_dependencies_reverse(running_services)

        self.log(f"Остановка {len(services_to_stop)} сервисов в правильном порядке...")

        for service in services_to_stop:
            self.stop_service_gracefully(service)

        self.lock_unlock(1)
        self.refresh_display()

    def restart_all(self, instance):
        self.stop_all(None)
        time.sleep(3)
        self.start_all(None)

    def on_process_started(self, service_name, pid):
        if self._is_closing:
            return

        with self.process_lock:
            self.service_root_pids[service_name] = pid
        self.starting_services.discard(service_name)
        self.log(f"✅ {service_name} запущен (PID: {pid})", "success")
        Clock.schedule_once(lambda dt: self.refresh_display())

    def on_process_stopped(self, service_name, pid):
        if self._is_closing:
            return

        with self.process_lock:
            if pid not in self.stopped_service_pids:
                self.stopped_service_pids.append(pid)

            if service_name in self.service_root_pids:
                del self.service_root_pids[service_name]
            self.starting_services.discard(service_name)

        self.log(f"🛑 {service_name} остановлен (PID: {pid})")
        Clock.schedule_once(lambda dt: self.refresh_display())

    def add_service(self):
        self.edit_service_dialog()

    def edit_service(self):
        # Simplified - edit selected service
        pass

    def edit_service_dialog(self, service=None):
        dialog = ServiceDialog(
            parent=self,
            service=service,
            project_data=self.project_data,
            root_dir=self.project_data.get("root_dir") if self.project_data else None
        )
        dialog.open()

    def add_service_to_project(self, service_data):
        if not self.project_data:
            self.project_data = DEFAULT_CONFIG.copy()

        services = self.project_data.get("services", [])
        services.append(service_data)
        self.project_data["services"] = services
        self.save_project()
        self.refresh_display()
        self.log(f"Сервис добавлен: {service_data['name']}")

    def update_service(self, old_service, new_service):
        if not self.project_data:
            return

        services = self.project_data.get("services", [])
        for i, s in enumerate(services):
            if s.get("name") == old_service.get("name"):
                services[i] = new_service
                break

        self.project_data["services"] = services
        self.save_project()
        self.refresh_display()
        self.log(f"Сервис обновлен: {new_service['name']}")

    def delete_service(self):
        # Simplified delete
        pass

    def import_config(self):
        from kivy.uix.filechooser import FileChooserListView
        fc = FileChooserListView(path=str(CONFIG_DIR), filters=['*.json'])
        popup = Popup(title="Выберите файл для импорта", content=fc, size_hint=(0.9, 0.9))
        fc.bind(on_submit=lambda *args: self.on_import_selected(fc.selection, popup))
        popup.open()

    def on_import_selected(self, selection, popup):
        if selection:
            filename = selection[0]
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if "services" in data:
                    dest = PROJECTS_DIR / Path(filename).name
                    shutil.copy2(filename, dest)
                    self.load_projects_list()
                    self.log(f"Импортирован проект: {Path(filename).name}")
                else:
                    dest = SERVICES_DIR / Path(filename).name
                    shutil.copy2(filename, dest)
                    self.log(f"Импортирован сервис: {Path(filename).name}")

            except Exception as e:
                error_popup = Popup(title="Ошибка", content=Label(text=f"Не удалось импортировать: {e}"),
                                    size_hint=(0.6, 0.3))
                error_popup.open()

        popup.dismiss()

    def export_config(self):
        if not self.project_data:
            return

        # Simplified export
        pass

    def project_settings(self):
        if not self.project_data:
            return

        settings = self.project_data.get("settings", DEFAULT_CONFIG["settings"])
        dialog = ProjectSettingsDialog(parent=self, settings=settings)
        dialog.open()

    def update_project_settings(self, settings):
        if self.project_data:
            self.project_data["settings"] = settings
            self.save_project()
            self.log("Настройки проекта сохранены")

    def show_help(self):
        help_text = f"""
{APP_NAME} v{APP_VERSION}

Универсальный лаунчер сервисов

Основные возможности:
- Управление несколькими проектами
- Запуск/остановка сервисов
- Автоматический запуск зависимостей
- Поддержка индивидуальных Python окружений
- Graceful shutdown сервисов
- Редактор конфигураций
- Импорт/экспорт проектов

Директория конфигурации:
{CONFIG_DIR}
        """
        popup = Popup(title="Справка", content=Label(text=help_text, text_size=(dp(500), None)), size_hint=(0.7, 0.7))
        popup.open()

    def show_about(self):
        about_text = f"""
{APP_NAME}
Версия: {APP_VERSION}

Универсальный инструмент для управления микросервисами

Особенности:
- Корректное завершение сервисов (graceful shutdown)
- Поддержка зависимостей между сервисами
- Индивидуальные Python окружения для каждого сервиса
- Автоматический health check
- Обнаружение и остановка всех дочерних процессов

Лицензия: MIT

Директория конфигурации:
{CONFIG_DIR}
        """
        popup = Popup(title="О программе", content=Label(text=about_text, text_size=(dp(500), None)),
                      size_hint=(0.7, 0.7))
        popup.open()

    def is_port_available(self, host, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return True
            except OSError:
                return False

    def kill_process_on_port(self, port):
        try:
            if sys.platform == 'win32':
                result = subprocess.run(
                    f'netstat -ano | findstr :{port}',
                    shell=True,
                    capture_output=True,
                    text=True
                )

                for line in result.stdout.split('\n'):
                    if f':{port}' in line:
                        parts = line.strip().split()
                        if len(parts) >= 5:
                            pid = parts[-1]
                            if pid.isdigit():
                                pid = int(pid)
                                try:
                                    proc = psutil.Process(pid)
                                    proc.terminate()
                                    try:
                                        proc.wait(timeout=3)
                                    except psutil.TimeoutExpired:
                                        proc.kill()
                                    return True
                                except:
                                    pass
            else:
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        connections = proc.connections(kind='inet')
                        for conn in connections:
                            if conn.laddr.port == port:
                                proc.terminate()
                                try:
                                    proc.wait(timeout=3)
                                except psutil.TimeoutExpired:
                                    proc.kill()
                                return True
                    except:
                        pass
        except Exception as e:
            self.log(f"Ошибка при убийстве процесса на порту {port}: {e}")

        return False

    def on_stop(self):
        """Called when the app is stopping"""
        print("\n" + "=" * 60)
        print("Завершение работы программы...")
        print("=" * 60)

        self._is_closing = True
        self.running = False

        # Stop monitoring
        self.monitor_stop_event.set()

        # Stop all services
        if self.project_data:
            running_services = []
            for service in self.project_data.get("services", []):
                if self.is_service_running(service.get("name")):
                    running_services.append(service)

            if running_services:
                print(f"\nНайдено запущенных сервисов: {len(running_services)}")
                services_to_stop = self.order_services_by_dependencies_reverse(running_services)

                print("\n🔄 Корректная остановка сервисов (graceful shutdown)...")
                for service in services_to_stop:
                    self.stop_service_gracefully(service)

                time.sleep(2)

        # Stop workers
        with self.process_lock:
            for service_name, worker in self.workers.items():
                try:
                    if worker and worker.is_alive():
                        worker.stop()
                        worker.join(timeout=1)
                except:
                    pass

        print("✅ Завершение программы")
        print("=" * 60)

        return True


def main():
    """Main entry point"""
    app = MainApp()
    app.run()


if __name__ == "__main__":
    main()