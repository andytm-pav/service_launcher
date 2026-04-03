#!/usr/bin/env python3

# pip install PyInstaller
# pip install --upgrade PyInstaller pyinstaller-hooks-contrib

# pyinstaller --windowed --onefile --name Service_launcher launcher.py

"""
Universal Service Launcher - PySide6 Version
A powerful service processor for microservices and Python applications
"""

import sys
import os
import subprocess
import shutil
import threading
import time
import socket
import json
import signal
from os import terminal_size
import re

import psutil
from pathlib import Path
from datetime import datetime
import requests

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QTreeWidget, QTreeWidgetItem,
    QDialog, QDialogButtonBox, QMessageBox, QFileDialog, QInputDialog,
    QLineEdit, QSpinBox, QCheckBox, QSplitter, QHeaderView,
    QListWidget, QListWidgetItem, QFormLayout, QPlainTextEdit,
    QMenuBar, QMenu
)
from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QEvent
)
from PySide6.QtGui import (
    QAction, QFont, QColor
)

# Configuration
APP_NAME = "Universal Service Launcher"
APP_VERSION = "0.4"
# CONFIG_DIR = Path.home() / "./service_launcher/configurations"
CONFIG_DIR = Path.cwd() / "configurations"
PROJECTS_DIR = CONFIG_DIR / "projects"
SERVICES_DIR = CONFIG_DIR / "services"
# LOG_DIR = CONFIG_DIR / "logs"
LOG_DIR = Path.cwd() / "logs"

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
    "info": "#4a9eff"
}

GAP = 26  # string gap for log messages


class LogEvent(QEvent):
    """Custom event for logging from threads"""
    EVENT_TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, message, level="info"):
        super().__init__(LogEvent.EVENT_TYPE)
        self.message = message
        self.level = level


class ServiceWorker(QThread):
    """Worker thread for service operations"""
    status_signal = Signal(str, str)
    log_signal = Signal(str, str)
    process_started = Signal(str, int)
    process_stopped = Signal(str, int)

    def __init__(self, operation, service, project_data, root_dir):
        super().__init__()
        self.operation = operation
        self.service = service
        self.project_data = project_data
        self.root_dir = Path(root_dir)
        self.process = None
        self._is_running = True

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
            self.log_signal.emit(f"[{service_name}]{' '*(GAP-2-len(service_name))} ❌ Скрипт не найден: {script_path}", "error")
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
                self.log_signal.emit(f"[{service_name}]{' '*(GAP-2-len(service_name))} ⚠️ Рабочая директория не существует: {working_dir}", "warning")
                working_dir = self.root_dir

            self.log_signal.emit(f"[{service_name}]{' '*(GAP-2-len(service_name))} 🚀 Запуск {service_name}...", "info")
            self.log_signal.emit(f"[{service_name}]{' '*(GAP-2-len(service_name))} 📁 Рабочая директория: {working_dir}", "info")

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

            self.process_started.emit(service_name, self.process.pid)
            # self.log_signal.emit(f"[{service_name}]{' '*(GAP-2-len(service_name))} ✅ {service_name} запущен (PID: {self.process.pid})", "success")
            self.monitor_process()

        except Exception as e:
            self.log_signal.emit(f"[{service_name}]{' '*(GAP-2-len(service_name))} ❌ Ошибка запуска {service_name}: {e}", "error")

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

                    self.log_signal.emit(f"[{service_name}]{' '*(GAP-2-len(service_name))} 🛑 Отправлен сигнал завершения {service_name} (PID: {proc.pid})", "info")

                    # Ждем завершения
                    try:
                        proc.wait(timeout=10)
                        self.log_signal.emit(f"[{service_name}]{' '*(GAP-2-len(service_name))} ✅ {service_name} корректно остановлен", "success")
                    except psutil.TimeoutExpired:
                        self.log_signal.emit(f"[{service_name}]{' '*(GAP-2-len(service_name))} ⚠️ {service_name} не остановился за 10 сек", "warning")

                    self.process_stopped.emit(service_name, proc.pid)
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
                self.log_signal.emit(f"[Service Launcher]{' '*(GAP-18)} Ошибка загрузки {env_path}: {e}", "warning")
        return env_vars

    def monitor_process(self):
        """Monitor the running process"""
        if not self.process:
            return

        service_name = self.service.get("name")

        while self._is_running and self.process.poll() is None:
            time.sleep(0.5)
            if self.process.stdout:
                output = self.process.stdout.readline()
                if output:
                    # self.log_signal.emit(output.strip(), "output")
                    self.log_signal.emit(f"[{service_name}]{' '*(GAP-2-len(service_name))} {output.strip()}", "output")

        if self.process and self.process.poll() is not None:
            self.process_stopped.emit(self.service.get("name"), self.process.pid)

    def stop(self):
        """Stop the worker"""
        self._is_running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except:
                pass


class ServiceDialog(QDialog):
    """Dialog for adding/editing services"""

    def __init__(self, parent=None, service=None, project_data=None, root_dir=None):
        super().__init__(parent)
        self.service = service
        self.project_data = project_data
        self.root_dir = Path(root_dir) if root_dir else Path.cwd()
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Редактирование сервиса" if self.service else "Новый сервис")
        self.setMinimumWidth(600)
        self.setMinimumHeight(550)

        layout = QVBoxLayout()
        form_layout = QFormLayout()

        # Name
        self.name_edit = QLineEdit()
        if self.service:
            self.name_edit.setText(self.service.get("name", ""))
        form_layout.addRow("Имя сервиса*:", self.name_edit)

        # Script path
        script_layout = QHBoxLayout()
        self.script_edit = QLineEdit()
        if self.service:
            self.script_edit.setText(self.service.get("script", ""))
        script_layout.addWidget(self.script_edit)

        script_browse = QPushButton("Обзор")
        script_browse.clicked.connect(self.browse_script)
        script_layout.addWidget(script_browse)
        form_layout.addRow("Путь к скрипту*:", script_layout)

        # Python path
        python_layout = QHBoxLayout()
        self.python_combo = QComboBox()
        self.python_combo.setEditable(True)
        self.python_combo.addItems(["system"] + self.find_python_interpreters())
        if self.service:
            self.python_combo.setCurrentText(self.service.get("python_path", "system"))
        python_layout.addWidget(self.python_combo)

        python_browse = QPushButton("Обзор")
        python_browse.clicked.connect(self.browse_python)
        python_layout.addWidget(python_browse)
        form_layout.addRow("Python интерпретатор:", python_layout)

        # Host
        self.host_edit = QLineEdit()
        self.host_edit.setText(self.service.get("host", "127.0.0.1") if self.service else "127.0.0.1")
        form_layout.addRow("Хост:", self.host_edit)

        # Port
        self.port_edit = QLineEdit()
        if self.service and self.service.get("port"):
            self.port_edit.setText(str(self.service.get("port")))
        form_layout.addRow("Порт:", self.port_edit)

        # Health check path
        self.health_path_edit = QLineEdit()
        self.health_path_edit.setText(self.service.get("health_path", "/health") if self.service else "/health")
        form_layout.addRow("Health check path:", self.health_path_edit)

        # Env file
        env_layout = QHBoxLayout()
        self.env_edit = QLineEdit()
        if self.service:
            self.env_edit.setText(self.service.get("env_file", ""))
        env_layout.addWidget(self.env_edit)

        env_browse = QPushButton("Обзор")
        env_browse.clicked.connect(self.browse_env)
        env_layout.addWidget(env_browse)
        form_layout.addRow("Файл .env:", env_layout)

        # Working directory
        wd_layout = QHBoxLayout()
        self.working_dir_edit = QLineEdit()
        if self.service:
            self.working_dir_edit.setText(self.service.get("working_dir", ""))
        wd_layout.addWidget(self.working_dir_edit)

        wd_browse = QPushButton("Обзор")
        wd_browse.clicked.connect(self.browse_working_dir)
        wd_layout.addWidget(wd_browse)
        form_layout.addRow("Рабочая директория (опционально):", wd_layout)

        # Order
        self.order_spin = QSpinBox()
        self.order_spin.setRange(0, 999)
        self.order_spin.setValue(self.service.get("order", 999) if self.service else 999)
        form_layout.addRow("Порядок запуска:", self.order_spin)

        # Dependencies
        form_layout.addRow(QLabel("Зависимости:"))
        self.deps_list = QListWidget()
        self.deps_list.setSelectionMode(QListWidget.MultiSelection)

        if self.project_data:
            for s in self.project_data.get("services", []):
                if not self.service or s.get("name") != self.service.get("name"):
                    item = QListWidgetItem(s.get("name"))
                    self.deps_list.addItem(item)

        if self.service:
            current_deps = self.service.get("dependencies", [])
            for i in range(self.deps_list.count()):
                if self.deps_list.item(i).text() in current_deps:
                    self.deps_list.item(i).setSelected(True)

        form_layout.addRow(self.deps_list)

        layout.addLayout(form_layout)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    # def browse_working_dir(self):
    #     directory = QFileDialog.getExistingDirectory(
    #         self,
    #         "Выберите рабочую директорию",
    #         str(self.root_dir)
    #     )
    #     if directory:
    #         self.working_dir_edit.setText(directory)

    def browse_working_dir(self):
        # Получаем текущую директорию из поля ввода
        current_dir = self.working_dir_edit.text()

        # Определяем начальную директорию
        if current_dir and os.path.exists(current_dir) and os.path.isdir(current_dir):
            # Если директория существует, используем её
            start_dir = current_dir
        else:
            # Иначе используем root_dir
            start_dir = str(self.root_dir)

        directory = QFileDialog.getExistingDirectory(
            self,
            "Выберите рабочую директорию",
            start_dir
        )

        if directory:
            self.working_dir_edit.setText(directory)

    def find_python_interpreters(self):
        interpreters = []

        if self.root_dir.exists():
            for venv_dir in [".venv", "venv", "env", "virtualenv"]:
                venv_path = self.root_dir / venv_dir
                if sys.platform == 'win32':
                    python_path = venv_path / "Scripts" / "python.exe"
                else:
                    python_path = venv_path / "bin" / "python3"

                if python_path.exists():
                    interpreters.append(str(python_path))

        return interpreters

    # def browse_script(self):
    #     filename, _ = QFileDialog.getOpenFileName(
    #         self,
    #         "Выберите скрипт",
    #         str(self.root_dir),
    #         "Python files (*.py);;All files (*.*)"
    #     )
    #     if filename:
    #         self.script_edit.setText(filename)

    def browse_script(self):
        # Получаем текущий путь из поля ввода
        current_script = self.script_edit.text()

        # Определяем начальную директорию
        if current_script and os.path.exists(current_script):
            # Если файл существует, используем его директорию
            start_dir = os.path.dirname(current_script)
        else:
            # Иначе используем root_dir
            start_dir = str(self.root_dir)

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите скрипт",
            start_dir,
            "Python files (*.py);;All files (*.*)"
        )

        if filename:
            self.script_edit.setText(filename)

    # def browse_python(self):
    #     filename, _ = QFileDialog.getOpenFileName(
    #         self,
    #         "Выберите Python интерпретатор",
    #         str(self.root_dir),
    #         "Python executable (python*);;All files (*.*)"
    #     )
    #     if filename:
    #         self.python_combo.setCurrentText(filename)

    def browse_python(self):
        # Получаем текущий путь из комбобокса
        current_python = self.python_combo.currentText()

        # Определяем начальную директорию
        if current_python and current_python != "system":
            # Проверяем, существует ли файл (если это не "system")
            if os.path.exists(current_python):
                # Если файл существует, используем его директорию
                start_dir = os.path.dirname(current_python)
            else:
                # Если путь указан, но файл не существует, используем root_dir
                start_dir = str(self.root_dir)
        else:
            # Если выбрано "system" или поле пустое, используем root_dir
            start_dir = str(self.root_dir)

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите Python интерпретатор",
            start_dir,
            "Python executable (python*);;All files (*.*)"
        )

        if filename:
            self.python_combo.setCurrentText(filename)

    def browse_env(self):
        # Получаем текущий путь из поля ввода
        current_script = self.env_edit.text()

        # Определяем начальную директорию
        if current_script and os.path.exists(current_script):
            # Если файл существует, используем его директорию
            start_dir = os.path.dirname(current_script)
        else:
            # Иначе используем root_dir
            start_dir = str(self.root_dir)

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите .env файл",
            str(start_dir),
            "Environment files (*.env);;All files (*.*)"
        )

        if filename:
            self.env_edit.setText(filename)

    def get_service_data(self):
        return {
            "name": self.name_edit.text(),
            "script": self.script_edit.text(),
            "python_path": self.python_combo.currentText(),
            "host": self.host_edit.text(),
            "port": int(self.port_edit.text()) if self.port_edit.text().isdigit() else None,
            "health_path": self.health_path_edit.text(),
            "env_file": self.env_edit.text(),
            "working_dir": self.working_dir_edit.text(),
            "order": self.order_spin.value(),
            "dependencies": [item.text() for item in self.deps_list.selectedItems()]
        }


class ProjectSettingsDialog(QDialog):
    """Dialog for project settings"""

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = settings or DEFAULT_CONFIG["settings"]
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Настройки проекта")
        self.setMinimumWidth(400)

        layout = QVBoxLayout()
        form_layout = QFormLayout()

        # Restart delay
        self.restart_delay = QSpinBox()
        self.restart_delay.setRange(1, 60)
        self.restart_delay.setValue(self.settings.get("restart_delay", 3))
        form_layout.addRow("Задержка перезапуска (сек):", self.restart_delay)

        # Port check timeout
        self.port_timeout = QSpinBox()
        self.port_timeout.setRange(1, 60)
        self.port_timeout.setValue(self.settings.get("port_check_timeout", 10))
        form_layout.addRow("Таймаут проверки порта:", self.port_timeout)

        # Graceful shutdown timeout
        self.shutdown_timeout = QSpinBox()
        self.shutdown_timeout.setRange(5, 120)
        self.shutdown_timeout.setValue(self.settings.get("graceful_shutdown_timeout", 30))
        form_layout.addRow("Таймаут graceful shutdown (сек):", self.shutdown_timeout)

        # Auto start dependencies
        self.auto_deps = QCheckBox("Автоматически запускать зависимости")
        self.auto_deps.setChecked(self.settings.get("auto_start_dependencies", True))
        form_layout.addRow(self.auto_deps)

        layout.addLayout(form_layout)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def get_settings(self):
        return {
            "restart_delay": self.restart_delay.value(),
            "port_check_timeout": self.port_timeout.value(),
            "graceful_shutdown_timeout": self.shutdown_timeout.value(),
            "auto_start_dependencies": self.auto_deps.isChecked(),
            "log_level": "INFO"
        }


class MainWindow(QMainWindow):
    """Main application window"""

    def __init__(self):
        super().__init__()
        self.hide_pings_checkbox = None
        self.clear_log_btn = None
        self.hide_health_checks = False  # Флаг для скрытия health check логов
        self.process_info = {}  # pid -> service_name
        self.service_root_pids = {}  # service_name -> root_pid
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

        # Хранилище для логов
        self.all_log_entries = []  # Список всех логов (каждый элемент - строка)
        self.log_filters = set()   # Уникальные имена из квадратных скобок
        self.current_log_filter = None  # Текущий выбранный фильтр

        self.start_all_btn = QPushButton("Запустить все")
        self.stop_all_btn = QPushButton("Остановить все")
        self.restart_all_btn = QPushButton("Перезапустить все")
        self.project_combo = QComboBox()
        self.log_filter_combo = QComboBox()  # Выпадающий список для фильтрации логов
        self.clear_filter_btn = QPushButton("Сбросить фильтр")  # Кнопка сброса фильтра

        self.menubar = self.menuBar()

        self.setup_directories()
        self.setup_ui()
        self.setup_menu(self.menubar)
        self.load_projects_list()
        self.start_monitoring()

    def lock_unlock(self, stage=1):
        if stage == 1:
            self.start_all_btn.setEnabled(True)
            self.stop_all_btn.setEnabled(False)
            self.restart_all_btn.setEnabled(False)
            self.project_combo.setEnabled(True)
            self.menubar.setEnabled(True)
        elif stage == 2:
            self.start_all_btn.setEnabled(False)
            self.stop_all_btn.setEnabled(True)
            self.restart_all_btn.setEnabled(True)
            self.project_combo.setEnabled(False)
            self.menubar.setEnabled(False)

    def setup_directories(self):
        CONFIG_DIR.mkdir(exist_ok=True)
        PROJECTS_DIR.mkdir(exist_ok=True)
        SERVICES_DIR.mkdir(exist_ok=True)
        LOG_DIR.mkdir(exist_ok=True)

    def setup_ui(self):
        # self.hide_health_checks = False  # Флаг для скрытия health check логов
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(1200, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        toolbar = self.create_toolbar()
        main_layout.addWidget(toolbar)

        splitter = QSplitter(Qt.Vertical)

        services_container = QWidget()
        services_layout = QVBoxLayout(services_container)
        services_layout.setContentsMargins(0, 0, 0, 0)

        self.services_tree = QTreeWidget()
        self.services_tree.setHeaderHidden(False)
        self.services_tree.setIndentation(0)
        self.services_tree.setAlternatingRowColors(True)
        self.services_tree.setSelectionBehavior(QTreeWidget.SelectRows)

        headers = ["Статус", "Сервис", "Порт", "PID", "Python", "Зависимости", "Действия"]
        self.services_tree.setColumnCount(len(headers))
        self.services_tree.setHeaderLabels(headers)

        header = self.services_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)

        self.services_tree.setStyleSheet("""
            QTreeWidget {
                border: 1px solid #ccc;
            }
            QTreeWidget::item {
                padding: 5px;
                height: 30px;
                border-bottom: 1px solid #eee;
            }
            QTreeWidget::item:hover {
                background-color: #e3f2fd;
            }
            QTreeWidget::item:selected {
                background-color: #bbdef5;
            }
            QHeaderView::section {
                background-color: #f0f0f0;
                padding: 5px;
                border: 1px solid #ddd;
                font-weight: bold;
            }
        """)

        services_layout.addWidget(self.services_tree)
        splitter.addWidget(services_container)

        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)

        # Панель фильтрации логов
        filter_panel = QWidget()
        filter_layout = QHBoxLayout(filter_panel)
        filter_layout.setContentsMargins(0, 0, 0, 5)
        
        filter_label = QLabel("Логи:")
        filter_label.setFont(QFont("Arial", 9, QFont.Bold))
        filter_layout.addWidget(filter_label)
        
        self.log_filter_combo.setMinimumWidth(300)
        self.log_filter_combo.addItem("Все логи", None)  # Добавляем пункт "Все логи"
        self.log_filter_combo.currentIndexChanged.connect(self.on_log_filter_changed)
        filter_layout.addWidget(self.log_filter_combo)
        
        self.clear_filter_btn.clicked.connect(self.clear_log_filter)
        self.clear_filter_btn.setFixedWidth(120)
        filter_layout.addWidget(self.clear_filter_btn)

        # Чекбокс для скрытия пингов
        self.hide_pings_checkbox = QCheckBox("Убрать пинги")
        self.hide_pings_checkbox.stateChanged.connect(self.on_hide_pings_changed)
        filter_layout.addWidget(self.hide_pings_checkbox)

        filter_layout.addStretch()

        self.clear_log_btn = QPushButton("Очистить лог")
        self.clear_log_btn.setFixedWidth(120)
        self.clear_log_btn.clicked.connect(self.clear_log)
        filter_layout.addWidget(self.clear_log_btn)
        
        # filter_layout.addStretch()
        
        log_layout.addWidget(filter_panel)

        # log_label = QLabel("Логи")
        # log_label.setFont(QFont("Arial", 10, QFont.Bold))
        # log_layout.addWidget(log_label)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(1000)
        self.log_text.setFont(QFont("Courier New", 9))
        log_layout.addWidget(self.log_text)

        splitter.addWidget(log_container)

        splitter.setSizes([500, 300])

        main_layout.addWidget(splitter)

        self.status_label = QLabel("Готов к работе")
        self.statusBar().addWidget(self.status_label)

    def on_hide_pings_changed(self, state):
        """Обработка изменения состояния чекбокса 'Убрать пинги'"""
        self.hide_health_checks = self.hide_pings_checkbox.isChecked()
        # print(f"DEBUG: hide_health_checks установлен в {self.hide_health_checks}")
        self.apply_log_filter()

    def is_ping_message(self, log_message):
        """Проверка, является ли сообщение пингом (health check или API опрос)"""
        # Удаляем ANSI escape последовательности (цвета)
        clean_message = re.sub(r'\x1b\[[0-9;]*m', '', log_message)

        # Простая проверка
        if '/health' in clean_message:
            # print(f"DEBUG: /health найден в: {clean_message[:80]}")
            return True
        if '/api/data?limit=' in clean_message:
            # print(f"DEBUG: /api/data найден в: {clean_message[:80]}")
            return True

        return False

    def clear_log(self):
        """Очистка логов"""
        if self._is_closing:
            return

        # Очищаем хранилище логов
        self.all_log_entries = []

        # Очищаем виджет логов
        self.log_text.clear()

        # Не очищаем фильтры, только логи
        self.log(f"[Service Launcher]{' ' * (GAP - 18)} Логи очищены")

    def create_toolbar(self):
        toolbar_widget = QWidget()
        layout = QHBoxLayout(toolbar_widget)
        layout.setContentsMargins(10, 5, 10, 5)

        layout.addWidget(QLabel("Проект:"))
        self.project_combo.setMinimumWidth(300)
        self.project_combo.currentTextChanged.connect(self.on_project_select)
        layout.addWidget(self.project_combo)

        layout.addStretch()

        self.start_all_btn.clicked.connect(self.start_all)
        layout.addWidget(self.start_all_btn)

        self.stop_all_btn.clicked.connect(self.stop_all)
        layout.addWidget(self.stop_all_btn)

        self.restart_all_btn.clicked.connect(self.restart_all)
        layout.addWidget(self.restart_all_btn)

        return toolbar_widget

    def setup_menu(self, menubar):
        # File menu
        file_menu = menubar.addMenu("Файл")

        new_action = QAction("Новый проект", self)
        new_action.triggered.connect(self.new_project)
        file_menu.addAction(new_action)

        open_action = QAction("Открыть проект", self)
        open_action.triggered.connect(self.open_project)
        file_menu.addAction(open_action)

        save_action = QAction("Сохранить проект", self)
        save_action.triggered.connect(self.save_project)
        file_menu.addAction(save_action)

        save_as_action = QAction("Сохранить как...", self)
        save_as_action.triggered.connect(self.save_project_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        import_action = QAction("Импорт конфигурации", self)
        import_action.triggered.connect(self.import_config)
        file_menu.addAction(import_action)

        export_action = QAction("Экспорт конфигурации", self)
        export_action.triggered.connect(self.export_config)
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Services menu
        services_menu = menubar.addMenu("Сервисы")

        add_action = QAction("Добавить сервис", self)
        add_action.triggered.connect(self.add_service)
        services_menu.addAction(add_action)

        edit_action = QAction("Редактировать сервис", self)
        edit_action.triggered.connect(self.edit_service)
        services_menu.addAction(edit_action)

        delete_action = QAction("Удалить сервис", self)
        delete_action.triggered.connect(self.delete_service)
        services_menu.addAction(delete_action)

        services_menu.addSeparator()

        import_service_action = QAction("Импорт сервиса", self)
        import_service_action.triggered.connect(self.import_service)
        services_menu.addAction(import_service_action)

        # Settings menu
        settings_menu = menubar.addMenu("Настройки")

        project_settings_action = QAction("Настройки проекта", self)
        project_settings_action.triggered.connect(self.project_settings)
        settings_menu.addAction(project_settings_action)

        global_settings_action = QAction("Глобальные настройки", self)
        global_settings_action.triggered.connect(self.global_settings)
        settings_menu.addAction(global_settings_action)

        # Help menu
        help_menu = menubar.addMenu("Помощь")

        help_action = QAction("Справка", self)
        help_action.triggered.connect(self.show_help)
        help_menu.addAction(help_action)

        about_action = QAction("О программе", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def load_projects_list(self):
        self.project_combo.clear()

        for file in PROJECTS_DIR.glob("*.json"):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    name = data.get("name", file.stem)
                    self.project_combo.addItem(name, str(file))
                    self.lock_unlock(1)
            except:
                name = file.stem
                self.project_combo.addItem(name, str(file))

    def on_project_select(self, project_name):
        if not project_name:
            return

        index = self.project_combo.findText(project_name)
        if index >= 0:
            project_file = self.project_combo.itemData(index)
            if project_file and Path(project_file).exists():
                self.load_project(Path(project_file))

    def new_project(self):
        name, ok = QInputDialog.getText(self, "Новый проект", "Имя проекта:")
        if not ok or not name:
            return

        root_dir = QFileDialog.getExistingDirectory(self, "Корневая директория", str(Path.cwd()))
        if not root_dir:
            return

        description, ok = QInputDialog.getMultiLineText(self, "Новый проект", "Описание:")
        if not ok:
            description = ""

        project_data = {
            "name": name,
            "root_dir": root_dir,
            "description": description,
            "services": [],
            "settings": DEFAULT_CONFIG["settings"].copy(),
            "created": datetime.now().isoformat(),
            "modified": datetime.now().isoformat()
        }

        filename = PROJECTS_DIR / f"{name}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(project_data, f, ensure_ascii=False, indent=2)

        self.load_projects_list()
        self.project_combo.setCurrentText(name)
        self.current_project = filename
        self.project_data = project_data
        self.refresh_display()
        self.log(f"[Service Launcher]{' '*(GAP-18)} Создан проект: {name}")

    def open_project(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл проекта",
            str(PROJECTS_DIR),
            "JSON files (*.json);;All files (*.*)"
        )
        if filename:
            self.load_project(Path(filename))

    def load_project(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                self.project_data = json.load(f)

            self.current_project = path
            self.refresh_display()
            self.log(f"[Service Launcher]{' '*(GAP-18)} Загружен проект: {self.project_data.get('name')}")

            if "root_dir" in self.project_data and self.project_data["root_dir"]:
                os.chdir(self.project_data["root_dir"])

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить проект: {e}")

    def save_project(self):
        if not self.current_project or not self.project_data:
            self.save_project_as()
            return

        try:
            self.project_data["modified"] = datetime.now().isoformat()
            with open(self.current_project, 'w', encoding='utf-8') as f:
                json.dump(self.project_data, f, ensure_ascii=False, indent=2)
            self.log(f"[Service Launcher]{' '*(GAP-18)} Проект сохранен: {self.project_data.get('name')}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить проект: {e}")

    def save_project_as(self):
        if not self.project_data:
            self.project_data = DEFAULT_CONFIG.copy()

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить проект как",
            str(PROJECTS_DIR / f"{self.project_data.get('name', 'project')}.json"),
            "JSON files (*.json);;All files (*.*)"
        )
        if filename:
            self.current_project = Path(filename)
            self.project_data["modified"] = datetime.now().isoformat()
            self.save_project()

    def extract_service_name_from_log(self, log_message):
        """Извлечение имени сервиса из квадратных скобок в логе"""
        # Паттерн для поиска текста в квадратных скобках в начале строки
        # Например: [14:28:23] ℹ️ [Service Launcher] -> Service Launcher
        pattern = r'\[\d{2}:\d{2}:\d{2}\]\s+[^[]*\[([^\]]+)\]'
        match = re.search(pattern, log_message)
        if match:
            return match.group(1).strip()
        return None

    def update_log_filters(self, log_message):
        """Обновление списка уникальных фильтров на основе нового лога"""
        service_name = self.extract_service_name_from_log(log_message)
        if service_name and service_name not in self.log_filters:
            self.log_filters.add(service_name)
            # Обновляем выпадающий список
            self.refresh_log_filter_combo()

    def refresh_log_filter_combo(self):
        """Обновление выпадающего списка фильтров"""
        # Сохраняем текущее выделение
        current_text = self.log_filter_combo.currentText()
        
        # Очищаем и перезаполняем
        self.log_filter_combo.clear()
        self.log_filter_combo.addItem("Все логи", None)
        
        # Добавляем отсортированные уникальные имена
        for name in sorted(self.log_filters):
            self.log_filter_combo.addItem(name, name)
        
        # Восстанавливаем выделение, если возможно
        if current_text and current_text != "Все логи":
            index = self.log_filter_combo.findText(current_text)
            if index >= 0:
                self.log_filter_combo.setCurrentIndex(index)
            else:
                self.log_filter_combo.setCurrentIndex(0)
        else:
            self.log_filter_combo.setCurrentIndex(0)

    def on_log_filter_changed(self, index):
        """Обработка изменения фильтра логов"""
        if index >= 0:
            self.current_log_filter = self.log_filter_combo.itemData(index)
            self.apply_log_filter()

    def clear_log_filter(self):
        """Сброс фильтра логов"""
        self.log_filter_combo.setCurrentIndex(0)
        self.current_log_filter = None
        self.apply_log_filter()

    def apply_log_filter(self):
        """Применение фильтра к отображаемым логам (перерисовка всех логов)"""
        if self._is_closing:
            return

        # Очищаем виджет логов
        self.log_text.clear()

        # print(f"DEBUG: apply_log_filter вызван, hide_health_checks={self.hide_health_checks}")

        # Перебираем все сохраненные логи
        for log_entry in self.all_log_entries:
            show_log = True

            # Проверка фильтра по сервису
            if self.current_log_filter:
                service_name = self.extract_service_name_from_log(log_entry)
                if service_name != self.current_log_filter:
                    show_log = False

            # Проверка на пинги
            if show_log and self.hide_health_checks:
                is_ping = self.is_ping_message(log_entry)
                if is_ping:
                    # print(f"DEBUG: Скрываем пинг: {log_entry[:80]}")
                    show_log = False

            if show_log:
                self.log_text.appendPlainText(log_entry)

    def customEvent(self, event):
        if isinstance(event, LogEvent):
            try:
                self._log(event.message, event.level)
            except Exception as e:
                print(f"Error in customEvent: {e}")
        else:
            super().customEvent(event)

    def log(self, message, level="info"):
        if self._is_closing:
            return

        try:
            event = LogEvent(message, level)
            QApplication.postEvent(self, event)
        except Exception as e:
            print(f"{message}")

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

        try:
            # Сохраняем запись в хранилище
            self.all_log_entries.append(log_entry)

            # Ограничиваем количество хранимых записей
            if len(self.all_log_entries) > 10000:
                self.all_log_entries = self.all_log_entries[-5000:]

            # Обновляем фильтры на основе нового сообщения
            self.update_log_filters(log_entry)

            # Проверяем, нужно ли показывать этот лог с учетом текущих фильтров
            show_log = True

            # Проверка фильтра по сервису
            if self.current_log_filter:
                service_name = self.extract_service_name_from_log(log_entry)
                if service_name != self.current_log_filter:
                    show_log = False

            # Проверка на пинги
            if show_log and self.hide_health_checks:
                is_ping = self.is_ping_message(log_entry)
                # print(f"DEBUG: hide_health_checks={self.hide_health_checks}, is_ping={is_ping}, msg={log_entry[:80]}")
                if is_ping:
                    show_log = False

            # Показываем лог, если прошел все фильтры
            if show_log:
                self.log_text.appendPlainText(log_entry)

            print(log_entry)
        except Exception as e:
            print(f"Error writing log: {e}")

    def refresh_display(self):
        if self._is_closing:
            return
        QTimer.singleShot(0, self._do_refresh_display)

    def _do_refresh_display(self):
        if self._is_closing:
            return

        try:
            self.services_tree.clear()
            self.services_widgets.clear()

            if not self.project_data:
                item = QTreeWidgetItem(self.services_tree)
                item.setText(0, "ℹ️")
                item.setText(1, "Нет загруженного проекта")
                item.setTextAlignment(1, Qt.AlignCenter)
                for i in range(2, 7):
                    item.setText(i, "")
                return

            services = self.project_data.get("services", [])
            if not services:
                item = QTreeWidgetItem(self.services_tree)
                item.setText(0, "ℹ️")
                item.setText(1, "Нет сервисов в проекте. Нажмите 'Добавить сервис'")
                item.setTextAlignment(1, Qt.AlignCenter)
                for i in range(2, 7):
                    item.setText(i, "")
                return

            services.sort(key=lambda x: x.get("order", 999))

            for service in services:
                self.add_service_to_tree(service)

            self.services_tree.header().show()
        except Exception as e:
            print(f"Error in refresh_display: {e}")

    def add_service_to_tree(self, service):
        if self._is_closing:
            return

        service_name = service.get("name", "Unknown")

        with self.process_lock:
            is_running = service_name in self.service_root_pids

        item = QTreeWidgetItem()
        font = QFont()
        if is_running:
            item.setText(0, "●")
            font.setPointSize(20)
        else:
            item.setText(0, "○")
            font.setPointSize(12)
        item.setFont(0, font)
        item.setForeground(0, QColor(COLORS["running"] if is_running else COLORS["stopped"]))
        item.setTextAlignment(0, Qt.AlignCenter)

        item.setText(1, service_name)
        item.setFont(1, QFont("Arial", 10, QFont.Bold))

        port = service.get("port", "-")
        item.setText(2, str(port))
        item.setTextAlignment(2, Qt.AlignCenter)

        pid = "-"
        with self.process_lock:
            root_pid = self.service_root_pids.get(service_name)
            if root_pid:
                pid = str(root_pid)
        item.setText(3, pid)
        item.setTextAlignment(3, Qt.AlignCenter)

        python_path = service.get("python_path", "system")
        if python_path == "system":
            python_display = "🐍 system"
        else:
            python_display = f"🐍 {Path(python_path).name}"
        item.setText(4, python_display)

        deps = service.get("dependencies", [])
        deps_text = ", ".join(deps) if deps else "-"
        item.setText(5, deps_text)

        self.services_tree.addTopLevelItem(item)

        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_layout.setSpacing(4)

        start_btn = QPushButton("▶️")
        start_btn.setFixedSize(32, 28)
        start_btn.setToolTip("Запустить")
        start_btn.clicked.connect(lambda checked, s=service: self.start_service(s))
        actions_layout.addWidget(start_btn)

        stop_btn = QPushButton("⏹️")
        stop_btn.setFixedSize(32, 28)
        stop_btn.setToolTip("Остановить")
        stop_btn.clicked.connect(lambda checked, s=service: self.stop_service(s))
        actions_layout.addWidget(stop_btn)

        restart_btn = QPushButton("🔄")
        restart_btn.setFixedSize(32, 28)
        restart_btn.setToolTip("Перезапустить")
        restart_btn.clicked.connect(lambda checked, s=service: self.restart_service(s))
        actions_layout.addWidget(restart_btn)

        edit_btn = QPushButton("⚙️")
        edit_btn.setFixedSize(32, 28)
        edit_btn.setToolTip("Редактировать")
        edit_btn.clicked.connect(lambda checked, s=service: self.edit_service_dialog(s))
        actions_layout.addWidget(edit_btn)

        actions_layout.addStretch()

        self.services_tree.setItemWidget(item, 6, actions_widget)
        self.services_widgets[service_name] = item

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
                            self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} 💀 Сервис {service_name} завершился", "warning")
                        QTimer.singleShot(0, self.refresh_display)

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
        """Получить все дочерние процессы для заданного PID"""
        children = set()
        try:
            root = psutil.Process(root_pid)
            # Получаем всех потомков рекурсивно
            for child in root.children(recursive=True):
                children.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return children

    def find_all_processes_by_service(self, service_name, script_path=None):
        """Найти все процессы сервиса включая дочерние"""
        processes = []

        # Получаем корневой PID сервиса
        root_pid = None
        with self.process_lock:
            root_pid = self.service_root_pids.get(service_name)

        if root_pid:
            # Если есть корневой PID, собираем все дочерние процессы
            try:
                root_proc = psutil.Process(root_pid)
                processes.append(root_proc)
                for child in root_proc.children(recursive=True):
                    processes.append(child)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Также ищем процессы по имени скрипта (на случай если root_pid не сохранен)
        if not processes and script_path:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = ' '.join(proc.info['cmdline'] if proc.info['cmdline'] else [])
                    if script_path in cmdline or service_name in cmdline:
                        processes.append(proc)
                        # Добавляем и дочерние процессы
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
        """Корректная остановка сервиса с graceful shutdown и завершением всех дочерних процессов"""
        service_name = service.get("name")

        # ===== ДОБАВЛЯЕМ ОТЛАДКУ =====
        self.debug_service_processes(service_name)
        # ===== КОНЕЦ ОТЛАДКИ =====

        script_path = service.get("script", "")

        graceful_timeout = 30
        if self.project_data:
            graceful_timeout = self.project_data.get("settings", {}).get("graceful_shutdown_timeout", 30)

        # 1. Останавливаем worker (если есть)
        worker = None
        with self.process_lock:
            worker = self.workers.pop(service_name, None)

        if worker:
            try:
                try:
                    worker.log_signal.disconnect()
                    worker.process_started.disconnect()
                    worker.process_stopped.disconnect()
                except:
                    pass

                worker.stop()
                if worker.isRunning():
                    worker.quit()
                    worker.wait(3000)
            except Exception as e:
                print(f"    Ошибка остановки worker: {e}")

        # 2. Находим ВСЕ процессы сервиса (включая дочерние)
        processes = self.find_all_processes_by_service(service_name, script_path)

        if not processes:
            print(f"    Процессы не найдены")
            return True

        print(f"    Найдено процессов: {len(processes)}")
        for proc in processes:
            try:
                print(f"      PID={proc.pid}, PPID={proc.ppid()}, NAME={proc.name()}")
            except:
                print(f"      PID={proc.pid}")

        # 3. Отправляем сигнал graceful shutdown всем процессам
        print(f"    Отправка сигнала завершения...")

        for proc in processes:
            try:
                if sys.platform == 'win32':
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                    print(f"      SIGBREAK отправлен процессу {proc.pid}")
                else:
                    proc.terminate()
                    print(f"      SIGTERM отправлен процессу {proc.pid}")
            except Exception as e:
                print(f"      Ошибка отправки сигнала процессу {proc.pid}: {e}")

        # 4. Ждем корректного завершения
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
                print(f"    ✅ {service_name} корректно остановлен за {elapsed} сек")

                # Очищаем данные
                with self.process_lock:
                    if service_name in self.service_root_pids:
                        del self.service_root_pids[service_name]
                    self.starting_services.discard(service_name)

                return True

            if wait_time % 5 == 0 and wait_time > 0:
                remaining_pids = [proc.pid for proc in processes]
                print(f"    Ожидание завершения... ({wait_time}/{graceful_timeout} сек), осталось: {remaining_pids}")

            time.sleep(check_interval)
            wait_time += check_interval

        # 5. Если не остановились - логируем
        remaining_pids = [proc.pid for proc in processes]
        print(f"    ⚠️ {service_name} не завершился за {graceful_timeout} сек")
        print(f"    Оставшиеся процессы: {remaining_pids}")

        return False

    def stop_service(self, service):
        """Stop a service gracefully"""
        if self._is_closing:
            return

        service_name = service.get("name")
        print(f"\n  Останавливаю {service_name}...")
        success = self.stop_service_gracefully(service)

        # Если graceful shutdown не удался, спрашиваем пользователя
        if not success:
            reply = QMessageBox.question(
                self,
                f"Сервис {service_name} не остановился",
                f"Сервис {service_name} не завершил работу за отведенное время.\n\n"
                f"Принудительно завершить сервис? (может привести к потере данных)",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                print(f"    Принудительное завершение {service_name}...")
                processes = self.find_all_processes_by_service(service_name, service.get("script", ""))
                for proc in processes:
                    try:
                        proc.kill()
                        print(f"      Процесс {proc.pid} убит")
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
        """Упорядочить сервисы для остановки - зависимости останавливаем первыми"""
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
            self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} 📋 Цепочка зависимостей для {service_name}: {' → '.join(dep_names)} → {service_name}")

        for dep in all_deps:
            dep_name = dep.get("name")

            if self.is_service_running(dep_name):
                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ✅ Зависимость {dep_name} уже запущена")
                continue

            if dep_name in self.starting_services:
                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ⏳ Зависимость {dep_name} уже запускается, ждем...")
                if not self.wait_for_service_ready(dep):
                    self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ❌ Ошибка: зависимость {dep_name} не запустилась", "error")
                    return False
                continue

            self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} 🔄 Запуск зависимости: {dep_name}")
            if not self.start_single_service(dep):
                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ❌ Ошибка: не удалось запустить {dep_name}", "error")
                return False

            if not self.wait_for_service_ready(dep):
                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ❌ Ошибка: зависимость {dep_name} не запустилась", "error")
                return False

        return True

    def wait_for_service_ready(self, service, timeout=30):
        start = time.time()
        service_name = service.get("name")
        has_port = service.get("port") is not None
        host = service.get("host", "127.0.0.1")
        port = service.get("port")
        health_path = service.get("health_path", "/health")

        self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ⏳ Ожидание готовности {service_name} ...")

        wait_start = time.time()
        while time.time() - wait_start < 5:
            if self.is_service_running(service_name):
                break
            QApplication.processEvents()
            time.sleep(0.1)

        if not self.is_service_running(service_name):
            self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ⚠️ Сервис {service_name} не зарегистрирован в системе")
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
                                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ✅ Сервис {service_name} готов (health check OK, через {elapsed} сек)")
                                return True
                        except:
                            if "ok" in response.text.lower() or "healthy" in response.text.lower():
                                elapsed = int(time.time() - start)
                                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ✅ Сервис {service_name} готов (health check OK, через {elapsed} сек)")
                                return True
                except:
                    pass
            else:
                time.sleep(3)
                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ✅ Сервис {service_name} готов (процесс запущен)")
                return True

            time.sleep(1)

        self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ⚠️ Таймаут ожидания готовности {service_name} ({timeout} сек)")
        return False

    def start_single_service(self, service):
        if self._is_closing:
            return False

        service_name = service.get("name")

        with self.process_lock:
            if self.is_service_running(service_name):
                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} Сервис {service_name} уже запущен")
                return True

            if service_name in self.starting_services:
                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} Сервис {service_name} уже запускается")
                return True

        if service.get("port"):
            host = service.get("host", "127.0.0.1")
            port = service["port"]
            if not self.is_port_available(host, port):
                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ⚠️ Порт {port} уже занят, возможно сервис уже запущен")
                self.kill_process_on_port(port)
                time.sleep(1)
                if not self.is_port_available(host, port):
                    self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ❌ Порт {port} всё ещё занят, не могу запустить {service_name}", "error")
                    return False

        with self.process_lock:
            self.starting_services.add(service_name)

        try:
            root_dir = self.project_data.get("root_dir", "")
            worker = ServiceWorker('start', service, self.project_data, root_dir)
            worker.log_signal.connect(self.log)
            worker.process_started.connect(self.on_process_started)
            worker.process_stopped.connect(self.on_process_stopped)

            with self.process_lock:
                self.workers[service_name] = worker
            worker.start()

            timeout = 5
            start_time = time.time()
            while time.time() - start_time < timeout:
                if self.is_service_running(service_name):
                    self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ✅ Сервис {service_name} зарегистрирован в системе")
                    return True
                time.sleep(0.1)

            self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ⚠️ Сервис {service_name} запущен, но не зарегистрирован в системе")
            return True

        except Exception as e:
            self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ❌ Ошибка запуска {service_name}: {e}", "error")
            with self.process_lock:
                self.starting_services.discard(service_name)
            return False

    def start_service(self, service):
        if self._is_closing:
            return False

        service_name = service.get("name")

        if self.is_service_running(service_name):
            self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} Сервис {service_name} уже запущен")
            return True

        if service_name in self.starting_services:
            self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} Сервис {service_name} уже запускается")
            return True

        if self.project_data and self.project_data.get("settings", {}).get("auto_start_dependencies", True):
            self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} 🔍 Проверка зависимостей для {service_name}")

            if not self.check_and_start_dependencies(service):
                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ❌ Не удалось запустить зависимости для {service_name}", "error")
                return False

            if self.is_service_running(service_name):
                self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} Сервис {service_name} был запущен через зависимости")
                return True

        return self.start_single_service(service)

    def restart_service(self, service):
        self.stop_service(service)
        time.sleep(2)
        self.start_service(service)

    def start_all(self):
        if not self.project_data or self._is_closing:
            return

        self.lock_unlock(2)

        services = self.project_data.get("services", [])

        all_deps = set()
        for service in services:
            for dep in service.get("dependencies", []):
                all_deps.add(dep)

        terminal_services = [s for s in services if s.get("name") not in all_deps]

        self.log(f"[Service Launcher]{' '*(GAP-18)} 🍃 Терминальные сервисы: {[s.get('name') for s in terminal_services]}")

        for service in terminal_services:
            self.start_service(service)

        for service in services:
            if service not in terminal_services:
                self.start_service(service)

    def stop_all(self):
        """Stop all services gracefully in reverse dependency order"""
        if self._is_closing:
            return

        if not self.project_data:
            return

        # Найти все запущенные сервисы
        running_services = []
        for service in self.project_data.get("services", []):
            if self.is_service_running(service.get("name")):
                running_services.append(service)

        if not running_services:
            self.log("Service Launcher: Нет запущенных сервисов")
            return

        # Упорядочить для остановки (зависимости останавливаем первыми)
        services_to_stop = self.order_services_by_dependencies_reverse(running_services)

        self.log(f"[Service Launcher]{' '*(GAP-18)} Остановка {len(services_to_stop)} сервисов в правильном порядке...")

        for service in services_to_stop:
            self.stop_service_gracefully(service)

        self.lock_unlock(1)
        self.refresh_display()

    def restart_all(self):
        self.stop_all()
        time.sleep(3)
        self.start_all()

    def on_process_started(self, service_name, pid):
        if self._is_closing:
            return

        with self.process_lock:
            self.service_root_pids[service_name] = pid
        self.starting_services.discard(service_name)
        self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ✅ {service_name} запущен (PID: {pid})", "success")
        QTimer.singleShot(0, self.refresh_display)

    def on_process_stopped(self, service_name, pid):
        if self._is_closing:
            return

        with self.process_lock:
            if pid not in self.stopped_service_pids:
                self.stopped_service_pids.append(pid)

            if service_name in self.service_root_pids:
                del self.service_root_pids[service_name]
            self.starting_services.discard(service_name)

        self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} 🛑 {service_name} остановлен (PID: {pid})")
        QTimer.singleShot(0, self.refresh_display)

    def add_service(self):
        self.edit_service_dialog()

    def edit_service(self):
        current = self.services_tree.currentItem()
        if current:
            service_name = current.text(1)
            service = self.find_service_by_name(service_name)
            if service:
                self.edit_service_dialog(service)

    def edit_service_dialog(self, service=None):
        dialog = ServiceDialog(
            self,
            service,
            self.project_data,
            self.project_data.get("root_dir") if self.project_data else None
        )

        if dialog.exec() == QDialog.Accepted:
            service_data = dialog.get_service_data()

            if not service_data["name"] or not service_data["script"]:
                QMessageBox.warning(self, "Ошибка", "Имя и путь к скрипту обязательны")
                return

            if not self.project_data:
                self.project_data = DEFAULT_CONFIG.copy()

            services = self.project_data.get("services", [])

            if service:
                for i, s in enumerate(services):
                    if s.get("name") == service.get("name"):
                        services[i] = service_data
                        break
            else:
                services.append(service_data)

            self.project_data["services"] = services
            self.save_project()
            self.refresh_display()
            self.log(f"[Service Launcher]{' '*(GAP-18)} Сервис {'обновлен' if service else 'добавлен'}: {service_data['name']}")

    def delete_service(self):
        current = self.services_tree.currentItem()
        if not current:
            return

        service_name = current.text(1)
        reply = QMessageBox.question(
            self,
            "Подтверждение",
            f"Удалить сервис '{service_name}'?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            services = self.project_data.get("services", [])
            services = [s for s in services if s.get("name") != service_name]
            self.project_data["services"] = services
            self.save_project()
            self.refresh_display()
            self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} Сервис удален: {service_name}")

    def import_config(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл для импорта",
            str(CONFIG_DIR),
            "JSON files (*.json);;All files (*.*)"
        )
        if filename:
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if "services" in data:
                    dest = PROJECTS_DIR / Path(filename).name
                    shutil.copy2(filename, dest)
                    self.load_projects_list()
                    self.log(f"[Service Launcher]{' '*(GAP-18)} Импортирован проект: {Path(filename).name}")
                else:
                    dest = SERVICES_DIR / Path(filename).name
                    shutil.copy2(filename, dest)
                    self.log(f"[Service Launcher]{' '*(GAP-18)} Импортирован сервис: {Path(filename).name}")

            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать: {e}")

    def export_config(self):
        if not self.project_data:
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт проекта",
            str(CONFIG_DIR / f"{self.project_data.get('name', 'project')}.json"),
            "JSON files (*.json);;All files (*.*)"
        )
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(self.project_data, f, ensure_ascii=False, indent=2)
                self.log(f"[Service Launcher]{' '*(GAP-18)} Экспортирован проект: {Path(filename).name}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось экспортировать: {e}")

    def import_service(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл сервиса",
            str(SERVICES_DIR),
            "JSON files (*.json);;All files (*.*)"
        )
        if filename:
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    service_data = json.load(f)

                if self.project_data:
                    services = self.project_data.get("services", [])
                    services.append(service_data)
                    self.project_data["services"] = services
                    self.save_project()
                    self.refresh_display()
                    self.log(f"[Service Launcher]{' '*(GAP-18)} Импортирован сервис: {service_data.get('name')}")

            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать сервис: {e}")

    def project_settings(self):
        if not self.project_data:
            return

        settings = self.project_data.get("settings", DEFAULT_CONFIG["settings"])
        dialog = ProjectSettingsDialog(self, settings)

        if dialog.exec() == QDialog.Accepted:
            self.project_data["settings"] = dialog.get_settings()
            self.save_project()
            self.log("Service Launcher: Настройки проекта сохранены")

    def global_settings(self):
        QMessageBox.information(self, "Информация", "Глобальные настройки будут доступны в следующей версии")

    def show_help(self):
        help_text = f"""
{APP_NAME} v{APP_VERSION}

Универсальный лаунчер сервисов

Основные возможности:
- Управление несколькими проектами
- Запуск/остановка сервисов
- Автоматический запуск зависимостей
- Поддержка индивидуальных Python окружений
- Graceful shutdown сервисов (корректное завершение)
- Редактор конфигураций
- Импорт/экспорт проектов
- Фильтрация логов по сервисам

Директория конфигурации:
{CONFIG_DIR}
        """
        QMessageBox.information(self, "Справка", help_text)

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
- Обнаружение и остановка всех дочерних процессов (multiprocessing)
- Фильтрация логов по сервисам (автоматическое извлечение имен из квадратных скобок)

Лицензия: MIT

Директория конфигурации:
{CONFIG_DIR}
        """
        QMessageBox.about(self, "О программе", about_text)

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
            self.log(f"[Service Launcher]{' '*(GAP-18)} Ошибка при убийстве процесса на порту {port}: {e}")

        return False

    def wait_for_port(self, host, port, timeout=30):
        start = time.time()
        while time.time() - start < timeout:
            if self.is_port_available(host, port):
                return True
            time.sleep(1)
        self.log(f"[Service Launcher]{' '*(GAP-18)} Таймаут ожидания порта {port} ({timeout} сек)")
        return False

    def cleanup_and_exit(self, event):
        """Очистка ресурсов и выход"""
        print("\n🧹 Очистка ресурсов...")

        # Останавливаем оставшиеся worker-ы
        with self.process_lock:
            for service_name, worker in self.workers.items():
                try:
                    if worker and worker.isRunning():
                        worker.stop()
                        worker.quit()
                        worker.wait(1000)
                except:
                    pass

        print("✅ Завершение программы")
        print("=" * 60)

        event.accept()
        QApplication.quit()
        os._exit(0)

    def debug_service_processes(self, service_name):
        """Отладочный метод для поиска процессов сервиса"""
        # print(f"\n[DEBUG] Поиск процессов для сервиса {service_name}")

        # Ищем по корневому PID
        with self.process_lock:
            root_pid = self.service_root_pids.get(service_name)
            # print(f"[DEBUG] root_pid: {root_pid}")

        if root_pid:
            try:
                proc = psutil.Process(root_pid)
                # print(f"[DEBUG] Процесс {root_pid}:")
                print(f"  Статус: {proc.status()}")
                print(f"  Командная строка: {proc.cmdline()}")
                print(f"  Дочерние процессы:")
                for child in proc.children(recursive=True):
                    print(f"    PID={child.pid}, CMD={child.cmdline()[:100]}")
            except Exception as e:
                pass
                # print(f"[DEBUG] Ошибка: {e}")

        # Ищем по имени скрипта
        service = self.find_service_by_name(service_name)
        if service:
            script_path = service.get("script", "")
            # print(f"[DEBUG] script_path: {script_path}")

            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'ppid']):
                try:
                    cmdline = ' '.join(proc.info['cmdline'] if proc.info['cmdline'] else [])
                    if script_path in cmdline or service_name in cmdline:
                        pass
                        # print(f"[DEBUG] Найден процесс по скрипту: PID={proc.info['pid']}, CMD={cmdline[:100]}")
                except:
                    pass

        # print("[DEBUG] ====================")

    def closeEvent(self, event):
        """Handle application close - корректная остановка всех сервисов"""
        if self._closing_started:
            event.ignore()
            return

        # self.log(f"[{service_name}]{' '*(GAP-2-len(service_name))} ✅ Завершение программы ...")

        self._closing_started = True
        self._is_closing = True
        self.running = False

        print("\n" + "=" * 60)
        print("Завершение работы программы...")
        print("=" * 60)

        # ===== ДОБАВЛЯЕМ ОТЛАДКУ =====
        # print("\n[DEBUG] Шаг 1: Проверка service_root_pids")
        with self.process_lock:
            pass
            # print(f"[DEBUG] service_root_pids: {self.service_root_pids}")

        # print("\n[DEBUG] Шаг 2: Поиск всех процессов в системе")
        current_pid = os.getpid()
        # print(f"[DEBUG] Текущий PID лаунчера: {current_pid}")

        # Ищем все Python процессы
        python_processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'ppid']):
            try:
                if 'python' in proc.info['name'].lower():
                    python_processes.append(proc)
                    cmd = ' '.join(proc.info['cmdline'] if proc.info['cmdline'] else [])
                    # print(f"[DEBUG] Python процесс: PID={proc.info['pid']}, PPID={proc.info['ppid']}, CMD={cmd[:100]}")
            except:
                pass

        # print(f"\n[DEBUG] Всего Python процессов: {len(python_processes)}")

        # ===== КОНЕЦ ОТЛАДКИ =====

        # Останавливаем мониторинг
        self.monitor_stop_event.set()

        # Получаем ВСЕ запущенные сервисы
        running_services = []
        if self.project_data:
            for service in self.project_data.get("services", []):
                service_name = service.get("name")
                # Проверяем по service_root_pids
                with self.process_lock:
                    is_running = service_name in self.service_root_pids
                # print(f"[DEBUG] Сервис {service_name}: is_running={is_running}")
                if is_running:
                    running_services.append(service)

        if not running_services:
            print("✅ Запущенных сервисов не найдено")
            self.cleanup_and_exit(event)
            return

        print(f"\nНайдено запущенных сервисов: {len(running_services)}")
        for service in running_services:
            print(f"  • {service.get('name')}")

        # Останавливаем сервисы
        print("\n🔄 Корректная остановка сервисов (graceful shutdown)...")
        services_to_stop = self.order_services_by_dependencies_reverse(running_services)

        for service in services_to_stop:
            service_name = service.get("name")
            # print(f"\n[DEBUG] Останавливаю {service_name}...")

            # Проверяем корневой PID
            with self.process_lock:
                root_pid = self.service_root_pids.get(service_name)
                # print(f"[DEBUG] root_pid для {service_name} = {root_pid}")

            if root_pid:
                try:
                    # Проверяем существует ли процесс
                    if psutil.pid_exists(root_pid):
                        proc = psutil.Process(root_pid)
                        # print(f"[DEBUG] Процесс {root_pid} существует, статус: {proc.status()}")
                        # print(f"[DEBUG] Командная строка: {proc.cmdline()}")

                        # Ищем дочерние процессы
                        children = proc.children(recursive=True)
                        # print(f"[DEBUG] Найдено дочерних процессов: {len(children)}")
                        for child in children:
                            print(f"  Дочерний: PID={child.pid}, CMD={child.cmdline()[:100]}")
                    else:
                        pass
                        # print(f"[DEBUG] Процесс {root_pid} не существует")
                except Exception as e:
                    pass
                    # print(f"[DEBUG] Ошибка при проверке процесса {root_pid}: {e}")
            else:
                pass
                # print(f"[DEBUG] root_pid для {service_name} не найден в service_root_pids")

            # Вызываем остановку
            self.stop_service_gracefully(service)

        # Ждем немного
        time.sleep(2)

        # Проверяем остались ли процессы
        # print("\n[DEBUG] Шаг 3: Проверка оставшихся процессов")
        remaining = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'ppid']):
            try:
                # Пропускаем текущий процесс
                if proc.info['pid'] == current_pid:
                    continue

                # Проверяем, является ли процесс потомком нашего лаунчера
                parent_pid = proc.info['ppid']
                if parent_pid == current_pid:
                    remaining.append(proc)
                    cmd = ' '.join(proc.info['cmdline'] if proc.info['cmdline'] else [])
                    # print(f"[DEBUG] Найден прямой потомок: PID={proc.info['pid']}, PPID={parent_pid}, CMD={cmd[:100]}")

                # Также ищем процессы, которые могли быть запущены от наших сервисов
                with self.process_lock:
                    for service_name, root_pid in self.service_root_pids.items():
                        if proc.info['pid'] == root_pid or parent_pid == root_pid:
                            if proc not in remaining:
                                remaining.append(proc)
                                cmd = ' '.join(proc.info['cmdline'] if proc.info['cmdline'] else [])
                                # print(f"[DEBUG] Найден процесс от сервиса {service_name}: PID={proc.info['pid']}, PPID={parent_pid}, CMD={cmd[:100]}")
            except:
                pass

        if remaining:
            print(f"\n⚠️ Осталось {len(remaining)} процессов:")
            pid_list = ''
            for proc in remaining:
                try:
                    print(f"    PID={proc.pid}\nNAME={proc.name()}\nCMD={proc.cmdline()[:100]}")
                    pid_list += f"● PID={proc.pid}\n● NAME={proc.name()}\n● CMD={proc.cmdline()[:100]}\n\n"
                except:
                    print(f"    PID={proc.pid}")
                    pid_list += f"● PID={proc.pid}\n\n"

            # Спрашиваем пользователя
            reply = QMessageBox.question(
                self,
                "Незавершенные процессы",
                f"Осталось {len(remaining)} незавершенных процессов.\n\n{pid_list}\n"
                f"Принудительно завершить их?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                print("\n  Принудительное завершение оставшихся процессов...")
                for proc in remaining:
                    try:
                        proc.kill()
                        print(f"    Процесс {proc.pid} убит")
                    except:
                        pass
        else:
            pass
            # print("\n[DEBUG] Оставшихся процессов не найдено")

        self.cleanup_and_exit(event)


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ServiceLauncher")

    app.setStyle('Fusion')

    window = MainWindow()
    window.show()

    try:
        sys.exit(app.exec())
    except SystemExit:
        os._exit(0)


if __name__ == "__main__":
    main()