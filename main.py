#!/usr/bin/env python3
"""
Universal Service Launcher - PySide6 Version
A powerful service manager for microservices and Python applications
"""

import sys
import os
import subprocess
import shutil
import threading
import time
import socket
import json
import psutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
import requests

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QDialog, QDialogButtonBox, QMessageBox, QFileDialog, QInputDialog,
    QLineEdit, QSpinBox, QCheckBox, QSplitter, QFrame, QHeaderView,
    QListWidget, QListWidgetItem, QMenuBar, QMenu, QTabWidget, QGroupBox,
    QFormLayout, QPlainTextEdit
)
from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QObject, QSettings, QSize
)
from PySide6.QtGui import (
    QAction, QFont, QColor, QPalette, QIcon
)


# Configuration
APP_NAME = "Universal Service Launcher"
APP_VERSION = "2.0.0"
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
        "log_level": "INFO"
    }
}

# Colors
COLORS = {
    "running": "#52b788",      # Green
    "stopped": "#6c757d",      # Gray
    "warning": "#ffb703",      # Orange
    "error": "#e63946",        # Red
    "info": "#4a9eff"          # Blue
}


class LogHandler(QObject):
    """Handles logging signals"""
    log_signal = Signal(str, str)  # message, level


class ServiceWorker(QThread):
    """Worker thread for service operations"""
    status_signal = Signal(str, str)  # service_name, status
    log_signal = Signal(str, str)  # message, level
    process_started = Signal(str, int)  # service_name, pid
    process_stopped = Signal(str, int)  # service_name, pid

    def __init__(self, operation, service, project_data, root_dir):
        super().__init__()
        self.operation = operation  # 'start', 'stop', 'restart'
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
            self.log_signal.emit(f"❌ Скрипт не найден: {script_path}", "error")
            return

        try:
            python_exe = self.get_python_interpreter()
            env = self.get_environment()

            # Определяем рабочую директорию для сервиса
            working_dir = None

            # 1. Если указана явно в конфигурации
            if self.service.get("working_dir"):
                working_dir = Path(self.service["working_dir"])
                if not working_dir.is_absolute():
                    working_dir = self.root_dir / working_dir

            # 2. Если есть env_file, используем его директорию
            if not working_dir and self.service.get("env_file"):
                env_path = Path(self.service["env_file"])
                if not env_path.is_absolute():
                    env_path = self.root_dir / env_path
                if env_path.parent.exists():
                    working_dir = env_path.parent

            # 3. Используем директорию скрипта сервиса
            if not working_dir:
                working_dir = script_path.parent

            # Убеждаемся, что директория существует
            if not working_dir.exists():
                self.log_signal.emit(f"⚠️ Рабочая директория не существует: {working_dir}", "warning")
                working_dir = self.root_dir

            self.log_signal.emit(f"🚀 Запуск {service_name}...", "info")
            self.log_signal.emit(f"📁 Рабочая директория: {working_dir}", "info")

            # Windows-specific setup
            startupinfo = None
            creationflags = 0
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            self.log_signal.emit(f"🚀 Запуск {service_name}...", "info")
            self.process = subprocess.Popen(
                [python_exe, str(script_path)],
                cwd=str(working_dir),
                env=env,
                startupinfo=startupinfo,
                creationflags=creationflags,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',  # Явно указываем кодировку
                errors='replace'   # Заменяем проблемные символы
            )

            self.process_started.emit(service_name, self.process.pid)
            self.log_signal.emit(f"✅ {service_name} запущен (PID: {self.process.pid})", "success")

            # Monitor the process
            self.monitor_process()

        except Exception as e:
            self.log_signal.emit(f"❌ Ошибка запуска {service_name}: {e}", "error")

    def stop_service(self):
        """Stop a service"""
        service_name = self.service.get("name")

        # Find and kill the process by name
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline'] if proc.info['cmdline'] else [])
                if service_name in cmdline or str(proc.pid) == str(self.process.pid if self.process else ''):
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        proc.kill()
                    self.log_signal.emit(f"🛑 {service_name} остановлен (PID: {proc.pid})", "info")
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

        # Add project root to PYTHONPATH
        if self.root_dir.exists():
            env["PYTHONPATH"] = str(self.root_dir)

        # Load .env file
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
                self.log_signal.emit(f"Ошибка загрузки {env_path}: {e}", "warning")
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
                    self.log_signal.emit(output.strip(), "output")

        if self.process and self.process.poll() is not None:
            self.process_stopped.emit(self.service.get("name"), self.process.pid)

    def stop(self):
        """Stop the worker"""
        self._is_running = False
        if self.process:
            self.process.terminate()


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

    def browse_working_dir(self):
        """Browse for working directory"""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Выберите рабочую директорию",
            str(self.root_dir)
        )
        if directory:
            self.working_dir_edit.setText(directory)

    def find_python_interpreters(self):
        """Find available Python interpreters"""
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

    def browse_script(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите скрипт",
            str(self.root_dir),
            "Python files (*.py);;All files (*.*)"
        )
        if filename:
            self.script_edit.setText(filename)

    def browse_python(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите Python интерпретатор",
            str(self.root_dir),
            "Python executable (python*);;All files (*.*)"
        )
        if filename:
            self.python_combo.setCurrentText(filename)

    def browse_env(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите .env файл",
            str(self.root_dir),
            "Environment files (*.env);;All files (*.*)"
        )
        if filename:
            self.env_edit.setText(filename)

    def get_service_data(self):
        """Get service data from dialog"""
        return {
            "name": self.name_edit.text(),
            "script": self.script_edit.text(),
            "python_path": self.python_combo.currentText(),
            "host": self.host_edit.text(),
            "port": int(self.port_edit.text()) if self.port_edit.text().isdigit() else None,
            "health_path": self.health_path_edit.text(),
            "env_file": self.env_edit.text(),
            "working_dir": self.working_dir_edit.text(),  # Добавлено
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
        """Get settings from dialog"""
        return {
            "restart_delay": self.restart_delay.value(),
            "port_check_timeout": self.port_timeout.value(),
            "auto_start_dependencies": self.auto_deps.isChecked(),
            "log_level": "INFO"
        }


class MainWindow(QMainWindow):
    """Main application window"""

    def __init__(self):
        super().__init__()
        self.processes = {}  # pid -> service_name
        self.process_info = {}  # pid -> service_name
        self.process_lock = threading.Lock()
        self.running = True
        self.current_project = None
        self.project_data = None
        self.services_widgets = {}  # service_name -> tree item
        self.workers = {}  # service_name -> worker thread
        self.starting_services = set()  # Множество сервисов в процессе запуска

        self.setup_directories()
        self.setup_ui()
        self.setup_menu()
        self.load_projects_list()
        self.start_monitoring()

    def setup_directories(self):
        """Create necessary directories"""
        CONFIG_DIR.mkdir(exist_ok=True)
        PROJECTS_DIR.mkdir(exist_ok=True)
        SERVICES_DIR.mkdir(exist_ok=True)
        LOG_DIR.mkdir(exist_ok=True)

    def setup_ui(self):
        """Setup the main UI"""
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(1000, 700)

        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Top toolbar
        toolbar = self.create_toolbar()
        main_layout.addWidget(toolbar)

        # Main splitter
        splitter = QSplitter(Qt.Vertical)

        # Services tree container
        services_container = QWidget()
        services_layout = QVBoxLayout(services_container)
        services_layout.setContentsMargins(0, 0, 0, 0)

        # Services tree
        self.services_tree = QTreeWidget()
        self.services_tree.setHeaderHidden(False)
        self.services_tree.setIndentation(0)
        self.services_tree.setAlternatingRowColors(True)
        self.services_tree.setSelectionBehavior(QTreeWidget.SelectRows)

        # Устанавливаем колонки
        headers = ["Статус", "Сервис", "Порт", "PID", "Python", "Зависимости", "Действия"]
        self.services_tree.setColumnCount(len(headers))
        self.services_tree.setHeaderLabels(headers)

        # Настраиваем ширину колонок
        header = self.services_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Статус
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # Сервис
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Порт
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # PID
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Python
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Зависимости
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # Действия

        self.services_tree.setStyleSheet("""
            QTreeWidget {
                border: 1px solid #ccc;
            }
            QTreeWidget::item {
                padding: 8px;
                height: 40px;
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
                padding: 8px;
                border: 1px solid #ddd;
                font-weight: bold;
            }
        """)

        services_layout.addWidget(self.services_tree)
        splitter.addWidget(services_container)

        # Log area
        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)

        log_label = QLabel("Логи")
        log_label.setFont(QFont("Arial", 10, QFont.Bold))
        log_layout.addWidget(log_label)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(1000)
        self.log_text.setFont(QFont("Courier New", 9))
        log_layout.addWidget(self.log_text)

        splitter.addWidget(log_container)

        # Set splitter sizes (70% services, 30% logs)
        splitter.setSizes([500, 300])

        main_layout.addWidget(splitter)

        # Status bar
        self.status_label = QLabel("Готов к работе")
        self.statusBar().addWidget(self.status_label)

    def create_toolbar(self):
        """Create the top toolbar"""
        toolbar_widget = QWidget()
        layout = QHBoxLayout(toolbar_widget)
        layout.setContentsMargins(10, 5, 10, 5)

        # Project selector
        layout.addWidget(QLabel("Проект:"))
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(300)
        self.project_combo.currentTextChanged.connect(self.on_project_select)
        layout.addWidget(self.project_combo)

        load_btn = QPushButton("Загрузить")
        load_btn.clicked.connect(self.load_selected_project)
        layout.addWidget(load_btn)

        refresh_btn = QPushButton("Обновить")
        refresh_btn.clicked.connect(self.refresh_display)
        layout.addWidget(refresh_btn)

        layout.addStretch()

        # Control buttons
        start_all_btn = QPushButton("Запустить все")
        start_all_btn.clicked.connect(self.start_all)
        layout.addWidget(start_all_btn)

        stop_all_btn = QPushButton("Остановить все")
        stop_all_btn.clicked.connect(self.stop_all)
        layout.addWidget(stop_all_btn)

        restart_all_btn = QPushButton("Перезапустить все")
        restart_all_btn.clicked.connect(self.restart_all)
        layout.addWidget(restart_all_btn)

        add_service_btn = QPushButton("Добавить сервис")
        add_service_btn.clicked.connect(self.add_service)
        layout.addWidget(add_service_btn)

        return toolbar_widget

    def setup_menu(self):
        """Create the menu bar"""
        menubar = self.menuBar()

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
        """Load list of available projects"""
        self.project_combo.clear()

        for file in PROJECTS_DIR.glob("*.json"):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    name = data.get("name", file.stem)
                    self.project_combo.addItem(name, str(file))
            except:
                name = file.stem
                self.project_combo.addItem(name, str(file))

    def on_project_select(self, project_name):
        """Handle project selection from combo box"""
        if not project_name:
            return

        index = self.project_combo.findText(project_name)
        if index >= 0:
            project_file = self.project_combo.itemData(index)
            if project_file and Path(project_file).exists():
                self.load_project(Path(project_file))

    def load_selected_project(self):
        """Load the selected project"""
        current = self.project_combo.currentText()
        if current:
            self.on_project_select(current)

    def new_project(self):
        """Create a new project"""
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
        self.log(f"Создан проект: {name}")

    def open_project(self):
        """Open a project from file"""
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл проекта",
            str(PROJECTS_DIR),
            "JSON files (*.json);;All files (*.*)"
        )
        if filename:
            self.load_project(Path(filename))

    def load_project(self, path):
        """Load a project"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                self.project_data = json.load(f)

            self.current_project = path
            self.refresh_display()
            self.log(f"Загружен проект: {self.project_data.get('name')}")

            # Change working directory
            if "root_dir" in self.project_data and self.project_data["root_dir"]:
                os.chdir(self.project_data["root_dir"])

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить проект: {e}")

    def save_project(self):
        """Save the current project"""
        if not self.current_project or not self.project_data:
            self.save_project_as()
            return

        try:
            self.project_data["modified"] = datetime.now().isoformat()
            with open(self.current_project, 'w', encoding='utf-8') as f:
                json.dump(self.project_data, f, ensure_ascii=False, indent=2)
            self.log(f"Проект сохранен: {self.project_data.get('name')}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить проект: {e}")

    def save_project_as(self):
        """Save project as..."""
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

    def log(self, message, level="info"):
        """Add message to log"""
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Color coding based on level
        if level == "error":
            log_entry = f"[{timestamp}] ❌ {message}"
        elif level == "warning":
            log_entry = f"[{timestamp}] ⚠️ {message}"
        elif level == "success":
            log_entry = f"[{timestamp}] ✅ {message}"
        else:
            log_entry = f"[{timestamp}] ℹ️ {message}"

        self.log_text.appendPlainText(log_entry)
        print(log_entry)

    def refresh_display(self):
        """Refresh the services display"""
        self.services_tree.clear()
        self.services_widgets.clear()

        if not self.project_data:
            # Show message that no project is loaded
            item = QTreeWidgetItem(self.services_tree)
            item.setText(0, "ℹ️")
            item.setText(1, "Нет загруженного проекта")
            item.setTextAlignment(1, Qt.AlignCenter)
            for i in range(2, 7):
                item.setText(i, "")
            return

        services = self.project_data.get("services", [])
        if not services:
            # Show message that no services exist
            item = QTreeWidgetItem(self.services_tree)
            item.setText(0, "ℹ️")
            item.setText(1, "Нет сервисов в проекте. Нажмите 'Добавить сервис'")
            item.setTextAlignment(1, Qt.AlignCenter)
            for i in range(2, 7):
                item.setText(i, "")
            return

        # Sort by order
        services.sort(key=lambda x: x.get("order", 999))

        # Add each service
        for service in services:
            self.add_service_to_tree(service)

        # Show headers
        self.services_tree.header().show()

    def add_service_to_tree(self, service):
        """Add a service to the tree widget"""
        service_name = service.get("name", "Unknown")

        # Check if service is running
        with self.process_lock:
            is_running = service_name in self.process_info.values()

        # Create item with correct number of columns
        item = QTreeWidgetItem()
        item.setText(0, "●" if is_running else "○")  # Status
        item.setForeground(0, QColor(COLORS["running"] if is_running else COLORS["stopped"]))
        item.setTextAlignment(0, Qt.AlignCenter)

        # Service name
        item.setText(1, service_name)
        item.setFont(1, QFont("Arial", 10, QFont.Bold))

        # Port
        port = service.get("port", "-")
        item.setText(2, str(port))
        item.setTextAlignment(2, Qt.AlignCenter)

        # PID
        pid = "-"
        with self.process_lock:
            for proc_pid, proc_name in self.process_info.items():
                if proc_name == service_name:
                    pid = str(proc_pid)
                    break
        item.setText(3, pid)
        item.setTextAlignment(3, Qt.AlignCenter)

        # Python path
        python_path = service.get("python_path", "system")
        if python_path == "system":
            python_display = "🐍 system"
        else:
            python_display = f"🐍 {Path(python_path).name}"
        item.setText(4, python_display)

        # Dependencies
        deps = service.get("dependencies", [])
        deps_text = ", ".join(deps) if deps else "-"
        item.setText(5, deps_text)

        # Add item to tree
        self.services_tree.addTopLevelItem(item)

        # Create actions widget
        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_layout.setSpacing(4)

        start_btn = QPushButton("▶")
        start_btn.setFixedSize(32, 28)
        start_btn.setToolTip("Запустить")
        start_btn.clicked.connect(lambda checked, s=service: self.start_service(s))
        actions_layout.addWidget(start_btn)

        stop_btn = QPushButton("■")
        stop_btn.setFixedSize(32, 28)
        stop_btn.setToolTip("Остановить")
        stop_btn.clicked.connect(lambda checked, s=service: self.stop_service(s))
        actions_layout.addWidget(stop_btn)

        restart_btn = QPushButton("↻")
        restart_btn.setFixedSize(32, 28)
        restart_btn.setToolTip("Перезапустить")
        restart_btn.clicked.connect(lambda checked, s=service: self.restart_service(s))
        actions_layout.addWidget(restart_btn)

        edit_btn = QPushButton("✎")
        edit_btn.setFixedSize(32, 28)
        edit_btn.setToolTip("Редактировать")
        edit_btn.clicked.connect(lambda checked, s=service: self.edit_service_dialog(s))
        actions_layout.addWidget(edit_btn)

        actions_layout.addStretch()

        # Set widget in the last column
        self.services_tree.setItemWidget(item, 6, actions_widget)

        # Store reference
        self.services_widgets[service_name] = item

    def start_monitoring(self):
        """Start monitoring processes"""

        def monitor():
            while self.running:
                time.sleep(1)
                with self.process_lock:
                    for pid in list(self.process_info.keys()):
                        if not psutil.pid_exists(pid):
                            service_name = self.process_info[pid]
                            self.log(f"💀 Процесс {service_name} (PID: {pid}) завершился")
                            del self.process_info[pid]
                            # Update display
                            QTimer.singleShot(0, self.refresh_display)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()

    def find_service_by_name(self, service_name):
        """Find service by name"""
        for s in self.project_data.get("services", []):
            if s.get("name") == service_name:
                return s
        return None

    def is_service_running(self, service_name):
        """Check if service is running"""
        with self.process_lock:
            return service_name in self.process_info.values()

    def get_all_dependencies(self, service, collected=None, visited=None):
        """Собирает ВСЕ зависимости снизу вверх (включая зависимости зависимостей)"""
        if collected is None:
            collected = []
        if visited is None:
            visited = set()

        service_name = service.get("name")
        if service_name in visited:
            return collected

        visited.add(service_name)

        # Получаем прямые зависимости
        deps = service.get("dependencies", [])

        # Для каждой зависимости рекурсивно собираем её зависимости
        for dep_name in deps:
            dep_service = self.find_service_by_name(dep_name)
            if dep_service:
                # Сначала собираем зависимости зависимости (рекурсивно)
                self.get_all_dependencies(dep_service, collected, visited)
                # Затем добавляем саму зависимость
                collected.append(dep_service)

        return collected

    def get_dependency_chain_from_root(self, service):
        """Получить цепочку зависимостей от корня до целевого сервиса"""
        # Получаем все зависимости снизу вверх
        all_deps = self.get_all_dependencies(service, [], set())

        # Убираем дубликаты, сохраняя порядок (от корня к цели)
        seen = set()
        unique_deps = []
        for dep in all_deps:
            dep_name = dep.get("name")
            if dep_name not in seen:
                seen.add(dep_name)
                unique_deps.append(dep)

        return unique_deps

    def check_and_start_dependencies(self, service):
        """Проверяет и запускает все зависимости сверху вниз"""
        service_name = service.get("name")

        # Получаем все зависимости от корня до цели
        all_deps = self.get_dependency_chain_from_root(service)

        if all_deps:
            dep_names = [d.get("name") for d in all_deps]
            self.log(f"📋 Цепочка зависимостей для {service_name}: {' → '.join(dep_names)} → {service_name}")

        # Запускаем зависимости сверху вниз (именно в том порядке, как они собраны)
        for dep in all_deps:
            dep_name = dep.get("name")

            # Проверяем, запущен ли уже или запускается
            if self.is_service_running(dep_name):
                self.log(f"✅ Зависимость {dep_name} уже запущена")
                continue

            if dep_name in self.starting_services:
                self.log(f"⏳ Зависимость {dep_name} уже запускается, ждем...")
                # Ждем, пока зависимость запустится
                if not self.wait_for_service_ready(dep):
                    self.log(f"❌ Ошибка: зависимость {dep_name} не запустилась", "error")
                    return False
                continue

            # Запускаем зависимость
            self.log(f"🔄 Запуск зависимости: {dep_name}")
            if not self.start_single_service(dep):
                self.log(f"❌ Ошибка: не удалось запустить {dep_name}", "error")
                return False

            # Ждем готовности зависимости
            if not self.wait_for_service_ready(dep):
                self.log(f"❌ Ошибка: зависимость {dep_name} не запустилась", "error")
                return False

        return True

    def wait_for_service_ready(self, service, timeout=30):
        """Wait for service to be ready by checking health endpoint"""

        start = time.time()
        service_name = service.get("name")
        has_port = service.get("port") is not None
        host = service.get("host", "127.0.0.1")
        port = service.get("port")
        health_path = service.get("health_path", "/health")  # Можно задать свой путь в конфигурации

        self.log(f"⏳ Ожидание готовности {service_name} (таймаут {timeout} сек)...")

        while time.time() - start < timeout:
            # Проверяем, запущен ли процесс

            # if not self.is_service_running(service_name): # TODO: add running service
            #     time.sleep(0.5)
            #     continue

            if has_port and port:
                try:
                    # Проверяем health endpoint
                    url = f"http://{host}:{port}{health_path}"
                    response = requests.get(url, timeout=2)

                    if response.status_code == 200:
                        try:
                            data = response.json()
                            self.log(data)
                            status = data.get("status", "").lower()
                            if status in ["ok", "healthy", "up"]:
                                elapsed = int(time.time() - start)
                                self.log(f"✅ Сервис {service_name} готов (health check OK, через {elapsed} сек)")
                                return True
                        except:
                            # Если не JSON, проверяем текст ответа
                            if "ok" in response.text.lower() or "healthy" in response.text.lower():
                                elapsed = int(time.time() - start)
                                self.log(f"✅ Сервис {service_name} готов (health check OK, через {elapsed} сек)")
                                return True
                except requests.exceptions.ConnectionError:
                    # Порт еще не открыт или сервис не отвечает
                    pass
                except requests.exceptions.Timeout:
                    # Таймаут запроса
                    pass
                except Exception as e:
                    # Другие ошибки
                    pass
            else:
                # Если порта нет - просто ждем 3 секунды для инициализации
                time.sleep(3)
                self.log(f"✅ Сервис {service_name} готов (процесс запущен)")
                return True

            time.sleep(1)

        self.log(f"⚠️ Таймаут ожидания готовности {service_name} ({timeout} сек)")
        return False

    def start_single_service(self, service):
        """Start a single service"""
        service_name = service.get("name")

        # Check if already running or starting
        if self.is_service_running(service_name):
            self.log(f"Сервис {service_name} уже запущен")
            return True

        if service_name in self.starting_services:
            self.log(f"Сервис {service_name} уже запускается")
            return True

        # Check port availability before starting
        if service.get("port"):
            host = service.get("host", "127.0.0.1")
            port = service["port"]
            if not self.is_port_available(host, port):
                self.log(f"⚠️ Порт {port} уже занят, возможно сервис уже запущен")
                # Пробуем найти процесс на этом порту и убить его
                self.kill_process_on_port(port)
                time.sleep(1)
                if not self.is_port_available(host, port):
                    self.log(f"❌ Порт {port} всё ещё занят, не могу запустить {service_name}", "error")
                    return False

        # Отмечаем сервис как запускающийся
        self.starting_services.add(service_name)

        try:
            # Create and start worker thread
            root_dir = self.project_data.get("root_dir", "")
            worker = ServiceWorker('start', service, self.project_data, root_dir)
            worker.log_signal.connect(self.log)
            worker.process_started.connect(self.on_process_started)
            worker.process_stopped.connect(self.on_process_stopped)

            self.workers[service_name] = worker
            worker.start()

            # Ждем, пока процесс появится в process_info (максимум 5 секунд)
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
            self.starting_services.discard(service_name)
            return False

    def start_service(self, service):
        """Запуск сервиса с проверкой всех зависимостей"""
        service_name = service.get("name")

        # Проверяем не запущен ли уже
        if self.is_service_running(service_name):
            self.log(f"Сервис {service_name} уже запущен")
            return True

        if service_name in self.starting_services:
            self.log(f"Сервис {service_name} уже запускается")
            return True

        # Проверяем и запускаем все зависимости
        if self.project_data.get("settings", {}).get("auto_start_dependencies", True):
            self.log(f"🔍 Проверка зависимостей для {service_name}")

            # Запускаем все зависимости сверху вниз
            if not self.check_and_start_dependencies(service):
                self.log(f"❌ Не удалось запустить зависимости для {service_name}", "error")
                return False

            # После запуска зависимостей снова проверяем целевой сервис
            if self.is_service_running(service_name):
                self.log(f"Сервис {service_name} был запущен через зависимости")
                return True

        # Запускаем сам сервис
        return self.start_single_service(service)

    def stop_service(self, service):
        """Stop a service"""
        service_name = service.get("name")

        # Stop the worker if running
        if service_name in self.workers:
            self.workers[service_name].stop()
            del self.workers[service_name]

        # Find and kill the process
        with self.process_lock:
            for pid, name in list(self.process_info.items()):
                if name == service_name:
                    try:
                        proc = psutil.Process(pid)
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except psutil.TimeoutExpired:
                            proc.kill()
                        self.log(f"🛑 Остановлен {service_name} (PID: {pid})")
                        del self.process_info[pid]
                    except psutil.NoSuchProcess:
                        del self.process_info[pid]

        self.refresh_display()

    def restart_service(self, service):
        """Restart a service"""
        self.stop_service(service)
        time.sleep(2)
        self.start_service(service)

    def start_all(self):
        """Start all services with dependency order"""
        if not self.project_data:
            return

        services = self.project_data.get("services", [])

        # Find root services (no dependencies)
        all_deps = set()
        for service in services:
            for dep in service.get("dependencies", []):
                all_deps.add(dep)

        root_services = [s for s in services if s.get("name") not in all_deps]

        self.log(f"🌳 Корневые сервисы: {[s.get('name') for s in root_services]}")

        # Start root services
        for service in root_services:
            self.start_service(service)

        # Start remaining services
        for service in services:
            if service not in root_services:
                self.start_service(service)

    def stop_all(self):
        """Stop all services"""
        with self.process_lock:
            services_to_stop = list(self.process_info.values())

        for service_name in services_to_stop:
            service = self.find_service_by_name(service_name)
            if service:
                self.stop_service(service)

    def restart_all(self):
        """Restart all services"""
        self.stop_all()
        time.sleep(3)
        self.start_all()

    def on_process_started(self, service_name, pid):
        """Handle process start"""
        with self.process_lock:
            self.process_info[pid] = service_name
        # Убираем из списка запускающихся
        self.starting_services.discard(service_name)
        self.log(f"✅ {service_name} запущен (PID: {pid})", "success")
        QTimer.singleShot(0, self.refresh_display)

    def on_process_stopped(self, service_name, pid):
        """Handle process stop"""
        with self.process_lock:
            if pid in self.process_info:
                del self.process_info[pid]
        # Убираем из списка запускающихся
        self.starting_services.discard(service_name)
        self.log(f"🛑 {service_name} остановлен (PID: {pid})")
        QTimer.singleShot(0, self.refresh_display)

    def add_service(self):
        """Add a new service"""
        self.edit_service_dialog()

    def edit_service(self):
        """Edit selected service"""
        current = self.services_tree.currentItem()
        if current:
            service_name = current.text(1)
            service = self.find_service_by_name(service_name)
            if service:
                self.edit_service_dialog(service)

    def edit_service_dialog(self, service=None):
        """Dialog for editing service"""
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
                # Update existing service
                for i, s in enumerate(services):
                    if s.get("name") == service.get("name"):
                        services[i] = service_data
                        break
            else:
                # Add new service
                services.append(service_data)

            self.project_data["services"] = services
            self.save_project()
            self.refresh_display()
            self.log(f"Сервис {'обновлен' if service else 'добавлен'}: {service_data['name']}")

    def delete_service(self):
        """Delete selected service"""
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
            self.log(f"Сервис удален: {service_name}")

    def import_config(self):
        """Import configuration"""
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
                    self.log(f"Импортирован проект: {Path(filename).name}")
                else:
                    dest = SERVICES_DIR / Path(filename).name
                    shutil.copy2(filename, dest)
                    self.log(f"Импортирован сервис: {Path(filename).name}")

            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать: {e}")

    def export_config(self):
        """Export current configuration"""
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
                self.log(f"Экспортирован проект: {Path(filename).name}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось экспортировать: {e}")

    def import_service(self):
        """Import a service"""
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
                    self.log(f"Импортирован сервис: {service_data.get('name')}")

            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать сервис: {e}")

    def project_settings(self):
        """Show project settings dialog"""
        if not self.project_data:
            return

        settings = self.project_data.get("settings", DEFAULT_CONFIG["settings"])
        dialog = ProjectSettingsDialog(self, settings)

        if dialog.exec() == QDialog.Accepted:
            self.project_data["settings"] = dialog.get_settings()
            self.save_project()
            self.log("Настройки проекта сохранены")

    def global_settings(self):
        """Show global settings"""
        QMessageBox.information(self, "Информация", "Глобальные настройки будут доступны в следующей версии")

    def show_help(self):
        """Show help dialog"""
        help_text = f"""
{APP_NAME} v{APP_VERSION}

Универсальный лаунчер сервисов

Основные возможности:
- Управление несколькими проектами
- Запуск/остановка сервисов
- Автоматический запуск зависимостей
- Поддержка индивидуальных Python окружений
- Редактор конфигураций
- Импорт/экспорт проектов

Директория конфигурации:
{CONFIG_DIR}
        """
        QMessageBox.information(self, "Справка", help_text)

    def show_about(self):
        """Show about dialog"""
        about_text = f"""
{APP_NAME}
Версия: {APP_VERSION}

Универсальный инструмент для управления микросервисами

Автор: Команда №4
Лицензия: MIT

Директория конфигурации:
{CONFIG_DIR}
        """
        QMessageBox.about(self, "О программе", about_text)

    def is_port_available(self, host, port):
        """Check if port is available (returns True if port is FREE)"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return True  # Порт свободен
            except OSError:
                return False  # Порт занят

    def kill_process_on_port(self, port):
        """Kill process using the specified port"""
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

    def wait_for_port(self, host, port, timeout=30):
        """Wait for port to become available"""
        start = time.time()
        while time.time() - start < timeout:
            if self.is_port_available(host, port):
                return True
            time.sleep(1)
        self.log(f"Таймаут ожидания порта {port} ({timeout} сек)")
        return False

    def closeEvent(self, event):
        """Handle application close"""
        self.running = False
        self.stop_all()

        # Stop all workers
        for worker in self.workers.values():
            worker.stop()

        event.accept()


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ServiceLauncher")

    # Set application style
    app.setStyle('Fusion')

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()