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
# from typing import Dict, List, Optional, Any
import requests

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QTreeWidget, QTreeWidgetItem,
    QDialog, QDialogButtonBox, QMessageBox, QFileDialog, QInputDialog,
    QLineEdit, QSpinBox, QCheckBox, QSplitter, QHeaderView,
    QListWidget, QListWidgetItem, QFormLayout, QPlainTextEdit,
    QMenuBar, QMenu, QTabWidget, QGroupBox, QTextEdit, QFrame
)
from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QEvent,
    # QObject, QSettings, QSize, Q_ARG, QMetaObject, Slot
)
from PySide6.QtGui import (
    QAction, QFont, QColor,
    # QPalette, QIcon
)


# Configuration
APP_NAME = "Universal Service Launcher"
APP_VERSION = "0.2.0"
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


class LogEvent(QEvent):
    """Custom event for logging from threads"""
    EVENT_TYPE = QEvent.Type(QEvent.registerEventType())
    
    def __init__(self, message, level="info"):
        super().__init__(LogEvent.EVENT_TYPE)
        self.message = message
        self.level = level


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
                if service_name in cmdline or (self.process and str(proc.pid) == str(self.process.pid)):
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
        self.process_info = {}  # pid -> service_name
        self.process_lock = threading.RLock()  # Используем RLock для реентерабельности
        self.stopped_service_pids = []  # Сохраняем PID остановленных сервисов
        self.running = True
        self.current_project = None
        self.project_data = None
        self.services_widgets = {}  # service_name -> tree item
        self.workers = {}  # service_name -> worker thread
        self.starting_services = set()  # Множество сервисов в процессе запуска
        self.monitor_thread = None  # Сохраняем ссылку на поток мониторинга
        self.monitor_stop_event = threading.Event()  # Событие для остановки мониторинга
        self._is_closing = False  # Флаг закрытия

        self.start_all_btn = QPushButton("Запустить все")
        self.stop_all_btn = QPushButton("Остановить все")
        self.restart_all_btn = QPushButton("Перезапустить все")
        self.project_combo = QComboBox()

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
        """Create necessary directories"""
        CONFIG_DIR.mkdir(exist_ok=True)
        PROJECTS_DIR.mkdir(exist_ok=True)
        SERVICES_DIR.mkdir(exist_ok=True)
        LOG_DIR.mkdir(exist_ok=True)

    def setup_ui(self):
        """Setup the main UI"""
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(1200, 700)

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
        self.project_combo.setMinimumWidth(300)
        self.project_combo.currentTextChanged.connect(self.on_project_select)
        layout.addWidget(self.project_combo)

        # load_btn = QPushButton("Загрузить")
        # load_btn.clicked.connect(self.load_selected_project)
        # layout.addWidget(load_btn)
        #
        # refresh_btn = QPushButton("Обновить")
        # refresh_btn.clicked.connect(self.refresh_display)
        # layout.addWidget(refresh_btn)

        layout.addStretch()

        # Control buttons
        self.start_all_btn.clicked.connect(self.start_all)
        layout.addWidget(self.start_all_btn)

        self.stop_all_btn.clicked.connect(self.stop_all)
        layout.addWidget(self.stop_all_btn)

        self.restart_all_btn.clicked.connect(self.restart_all)
        layout.addWidget(self.restart_all_btn)

        # add_service_btn = QPushButton("Добавить сервис")
        # add_service_btn.clicked.connect(self.add_service)
        # layout.addWidget(add_service_btn)

        return toolbar_widget

    def setup_menu(self, menubar):
        """Create the menu bar"""

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
                    self.lock_unlock(1)
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

    def customEvent(self, event):
        """Handle custom events (for logging from threads)"""
        if isinstance(event, LogEvent):
            try:
                self._log(event.message, event.level)
            except Exception as e:
                print(f"Error in customEvent: {e}")
        else:
            super().customEvent(event)

    def log(self, message, level="info"):
        """Add message to log (thread-safe via event)"""
        if self._is_closing:
            return
        
        try:
            event = LogEvent(message, level)
            QApplication.postEvent(self, event)
        except Exception as e:
            print(f"{message}")

    def _log(self, message, level="info"):
        """Internal log method"""
        if self._is_closing:
            return
            
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

        try:
            self.log_text.appendPlainText(log_entry)
            print(log_entry)
        except Exception as e:
            print(f"Error writing log: {e}")

    def refresh_display(self):
        """Refresh the services display"""
        if self._is_closing:
            return
        QTimer.singleShot(0, self._do_refresh_display)
    
    def _do_refresh_display(self):
        """Actual refresh display method"""
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

            # Sort by order
            services.sort(key=lambda x: x.get("order", 999))

            # Add each service
            for service in services:
                self.add_service_to_tree(service)

            # Show headers
            self.services_tree.header().show()
        except Exception as e:
            print(f"Error in refresh_display: {e}")

    def add_service_to_tree(self, service):
        """Add a service to the tree widget"""
        if self._is_closing:
            return
            
        service_name = service.get("name", "Unknown")

        # Check if service is running
        with self.process_lock:
            is_running = service_name in self.process_info.values()

        # Create item with correct number of columns
        item = QTreeWidgetItem()
        font = QFont()
        if is_running:
            item.setText(0, "●")  # Status
            # Создаем шрифт с нужным размером
            font.setPointSize(20)  # Размер шрифта 12
            # Или font.setPixelSize(16) для размера в пикселях
        else:
            item.setText(0, "○")  # Status
            # Создаем шрифт с нужным размером
            font.setPointSize(12)  # Размер шрифта 12
            # Или font.setPixelSize(16) для размера в пикселях
        item.setFont(0, font)  # Применяем шрифт к первой колонке
        item.setForeground(0, QColor(COLORS["running"] if is_running else COLORS["stopped"]))
        item.setTextAlignment(0, Qt.AlignCenter)

        # Service name
        item.setText(1, service_name)
        item.setFont(1, QFont("Arial", 10, QFont.Bold))

        # Port
        port = service.get("port", "-")
        item.setText(2, str(port))
        item.setTextAlignment(2, Qt.AlignCenter)

        # PID - безопасно получаем PID
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

        # start_btn = QPushButton("▶")
        start_btn = QPushButton("▶️")
        # start_btn = QPushButton("▶")
        # start_btn.setStyleSheet("color: #2ecc71; font-size: 18px;")  # Зеленый
        # start_btn = QPushButton("🚀")
        # start_btn.setStyleSheet("font-size: 14px;")
        start_btn.setFixedSize(32, 28)
        start_btn.setToolTip("Запустить")
        start_btn.clicked.connect(lambda checked, s=service: self.start_service(s))
        actions_layout.addWidget(start_btn)

        # stop_btn = QPushButton("■")
        stop_btn = QPushButton("⏹️")
        # stop_btn.setStyleSheet("color: #e74c3c; font-size: 12px;")  # Красный
        stop_btn.setFixedSize(32, 28)
        stop_btn.setToolTip("Остановить")
        stop_btn.clicked.connect(lambda checked, s=service: self.stop_service(s))
        actions_layout.addWidget(stop_btn)

        # restart_btn = QPushButton("↻")
        restart_btn = QPushButton("🔄")
        # restart_btn.setStyleSheet("color: #f39c12; font-size: 16px; font-weight: bold;") # Оранжевый
        restart_btn.setFixedSize(32, 28)
        restart_btn.setToolTip("Перезапустить")
        restart_btn.clicked.connect(lambda checked, s=service: self.restart_service(s))
        actions_layout.addWidget(restart_btn)

        # edit_btn = QPushButton("✎")
        edit_btn = QPushButton("⚙️")
        # edit_btn.setStyleSheet("color: #3498db; font-size: 12px;")  # Синий
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
            while not self.monitor_stop_event.is_set():
                self.monitor_stop_event.wait(1)
                
                if self.monitor_stop_event.is_set() or self._is_closing:
                    break
                
                dead_processes = []
                
                # Безопасно копируем словарь
                try:
                    with self.process_lock:
                        if not self.process_info:
                            continue
                        processes = list(self.process_info.items())
                except:
                    continue
                
                # Проверяем процессы
                for pid, service_name in processes:
                    try:
                        if not psutil.pid_exists(pid):
                            dead_processes.append((service_name, pid))
                        else:
                            proc = psutil.Process(pid)
                            if proc.status() == psutil.STATUS_ZOMBIE:
                                dead_processes.append((service_name, pid))
                    except:
                        dead_processes.append((service_name, pid))
                
                # Удаляем мертвые процессы
                if dead_processes:
                    with self.process_lock:
                        for service_name, pid in dead_processes:
                            if pid in self.process_info:
                                del self.process_info[pid]
                    
                    # Логируем только если не закрываемся
                    if not self._is_closing:
                        for service_name, pid in dead_processes:
                            self.log(f"💀 Процесс {service_name} (PID: {pid}) завершился", "warning")
                        
                        # Обновляем UI
                        QTimer.singleShot(0, self.refresh_display)

        self.monitor_stop_event.clear()
        self.monitor_thread = threading.Thread(target=monitor, daemon=True)  # daemon=True для автоматического завершения
        self.monitor_thread.start()

    def find_service_by_name(self, service_name):
        """Find service by name"""
        if not self.project_data:
            return None
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

        # self.log(f"⏳ Ожидание готовности {service_name} (таймаут {timeout} сек)...")
        self.log(f"⏳ Ожидание готовности {service_name} ...")

        # Сначала ждем регистрации процесса
        wait_start = time.time()
        while time.time() - wait_start < 5:
            if self.is_service_running(service_name):
                break
            QApplication.processEvents()
            time.sleep(0.1)

        if not self.is_service_running(service_name):
            self.log(f"⚠️ Сервис {service_name} не зарегистрирован в системе")
            return False

        while time.time() - start < timeout:
            # Проверяем, запущен ли процесс

            if has_port and port:  # TODO: redefine?
                try:
                    # Проверяем health endpoint
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
                            # Если не JSON, проверяем текст ответа
                            if "ok" in response.text.lower() or "healthy" in response.text.lower():
                                elapsed = int(time.time() - start)
                                self.log(f"✅ Сервис {service_name} готов (health check OK, через {elapsed} сек)")
                                return True
                except:
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
        if self._is_closing:
            return False
            
        service_name = service.get("name")

        # Check if already running or starting
        with self.process_lock:
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
        with self.process_lock:
            self.starting_services.add(service_name)

        try:
            # Create and start worker thread
            root_dir = self.project_data.get("root_dir", "")
            worker = ServiceWorker('start', service, self.project_data, root_dir)
            worker.log_signal.connect(self.log)
            worker.process_started.connect(self.on_process_started)
            worker.process_stopped.connect(self.on_process_stopped)

            with self.process_lock:
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
            with self.process_lock:
                self.starting_services.discard(service_name)
            return False

    def start_service(self, service):
        """Запуск сервиса с проверкой всех зависимостей"""
        if self._is_closing:
            return False
            
        service_name = service.get("name")

        # Проверяем не запущен ли уже
        if self.is_service_running(service_name):
            self.log(f"Сервис {service_name} уже запущен")
            return True

        if service_name in self.starting_services:
            self.log(f"Сервис {service_name} уже запускается")
            return True

        # Проверяем и запускаем все зависимости
        if self.project_data and self.project_data.get("settings", {}).get("auto_start_dependencies", True):
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
        if self._is_closing:
            return

        service_name = service.get("name")

        # Останавливаем worker
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
                    worker.wait(500)
            except Exception as e:
                print(f"Error stopping worker for {service_name}: {e}")

        # Останавливаем процессы и СОХРАНЯЕМ PID
        pids_to_stop = []
        with self.process_lock:
            pids_to_stop = [pid for pid, name in self.process_info.items() if name == service_name]

        for pid in pids_to_stop:
            # СОХРАНЯЕМ PID ПЕРЕД УДАЛЕНИЕМ
            with self.process_lock:
                self.stopped_service_pids.append(pid)

            try:
                if psutil.pid_exists(pid):
                    proc = psutil.Process(pid)
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                        if not self._is_closing:
                            self.log(f"🛑 Остановлен {service_name} (PID: {pid})")
                    except psutil.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=1)
                        if not self._is_closing:
                            self.log(f"🛑 Принудительно остановлен {service_name} (PID: {pid})")
                else:
                    if not self._is_closing:
                        self.log(f"💀 Процесс {service_name} (PID: {pid}) завершился", "warning")
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                print(f"Error stopping {service_name} (PID: {pid}): {e}")
            finally:
                with self.process_lock:
                    if pid in self.process_info:
                        del self.process_info[pid]

        if not self._is_closing:
            self.refresh_display()

    def restart_service(self, service):
        """Restart a service"""
        self.stop_service(service)
        time.sleep(2)
        self.start_service(service)

    def start_all(self):
        """Start all services with dependency order"""
        if not self.project_data or self._is_closing:
            return

        self.lock_unlock(2)

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
        if self._is_closing:
            return
            
        try:
            with self.process_lock:
                services_to_stop = list(self.process_info.values())
            
            for service_name in services_to_stop:
                service = self.find_service_by_name(service_name)
                if service:
                    self.stop_service(service)
                    self.lock_unlock(1)
        except Exception as e:
            print(f"Error stopping all services: {e}")

    def restart_all(self):
        """Restart all services"""
        self.stop_all()
        time.sleep(3)
        self.start_all()

    def on_process_started(self, service_name, pid):
        """Handle process start"""
        if self._is_closing:
            return
            
        with self.process_lock:
            self.process_info[pid] = service_name
        # Убираем из списка запускающихся
        self.starting_services.discard(service_name)
        self.log(f"✅ {service_name} запущен (PID: {pid})", "success")
        QTimer.singleShot(0, self.refresh_display)

    def on_process_stopped(self, service_name, pid):
        """Handle process stop"""
        if self._is_closing:
            return

        # print(f"[DEBUG] on_process_stopped вызван для {service_name} (PID: {pid})")

        # СОХРАНЯЕМ PID ДАЖЕ ПРИ СПОНТАННОМ ЗАВЕРШЕНИИ
        with self.process_lock:
            if pid not in self.stopped_service_pids:
                self.stopped_service_pids.append(pid)
                # print(f"[DEBUG] PID {pid} добавлен в stopped_service_pids (из on_process_stopped)")

            if pid in self.process_info:
                del self.process_info[pid]
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

Автор: ...
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
        # Защита от повторного вызова
        if hasattr(self, '_closing_started') and self._closing_started:
            print("closeEvent уже выполняется, игнорируем")
            event.ignore()
            return

        self._closing_started = True
        self._is_closing = True
        self.running = False

        print("Завершение работы программы...")

        import psutil
        import time

        # Используем сохраненные PID сервисов
        service_pids = list(self.stopped_service_pids)  # Копируем
        print(f"Сохраненные PID сервисов из stopped_service_pids: {service_pids}")

        # Сначала останавливаем мониторинг
        self.monitor_stop_event.set()

        # Останавливаем все сервисы (если еще не остановлены)
        try:
            with self.process_lock:
                service_names = list(self.process_info.values())

            for service_name in service_names:
                service = self.find_service_by_name(service_name)
                if service:
                    self.stop_service(service)
        except Exception as e:
            print(f"Error stopping services: {e}")

        # Останавливаем все worker-ы
        workers_to_stop = []
        with self.process_lock:
            workers_to_stop = list(self.workers.items())

        for service_name, worker in workers_to_stop:
            try:
                try:
                    worker.log_signal.disconnect()
                    worker.process_started.disconnect()
                    worker.process_stopped.disconnect()
                except:
                    pass

                if worker and worker.isRunning():
                    worker.stop()
                    worker.quit()
                    worker.wait(500)
            except Exception as e:
                print(f"Error stopping worker {service_name}: {e}")

        # Очищаем словари
        with self.process_lock:
            self.process_info.clear()
            self.starting_services.clear()
            self.workers.clear()

        # Получаем текущий PID
        current_pid = os.getpid()
        print(f"Текущий PID: {current_pid}")

        # Добавляем сохраненные PID сервисов к списку для проверки
        all_pids_to_check = service_pids + [current_pid]
        print(f"Проверяем процессы с родителями: {all_pids_to_check}")

        # Ищем процессы, у которых родительский PID в нашем списке
        killed_pids = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'ppid']):
            try:
                # Если родительский PID в нашем списке
                if proc.info['ppid'] in all_pids_to_check:
                    print(f"Найден процесс: PID={proc.info['pid']}, NAME={proc.info['name']}, PPID={proc.info['ppid']}")
                    # Пропускаем conhost и текущий процесс
                    if proc.info['name'].lower() == 'conhost.exe' or proc.info['pid'] == current_pid:
                        print(f"  Пропускаем")
                        continue
                    try:
                        proc.terminate()
                        killed_pids.append(proc.info['pid'])
                        print(f"  terminate() вызван для PID {proc.info['pid']}")
                    except Exception as e:
                        print(f"  Ошибка terminate: {e}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Ждем завершения
        if killed_pids:
            print(f"Ждем завершения процессов: {killed_pids}")
            time.sleep(2)

            # Убиваем те, которые не завершились
            for pid in killed_pids:
                try:
                    proc = psutil.Process(pid)
                    if proc.is_running():
                        print(f"Принудительно убиваем процесс {pid}")
                        proc.kill()
                except:
                    pass

        # Также завершаем conhost процессы
        for proc in psutil.process_iter(['pid', 'name', 'ppid']):
            try:
                if proc.info['ppid'] == current_pid and 'conhost' in proc.info['name'].lower():
                    print(f"Найден conhost процесс: PID={proc.info['pid']}")
                    proc.terminate()
            except:
                pass

        print("Все процессы завершены")

        # Даем время на завершение
        time.sleep(0.5)

        # Принудительно завершаем поток мониторинга
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=1)

        # Принимаем событие закрытия
        event.accept()

        # Принудительно завершаем приложение
        QApplication.quit()

        # Принудительный выход без финализации
        os._exit(0)


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
        # Принудительный выход, если что-то пошло не так
        os._exit(0)


if __name__ == "__main__":
    main()