#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import subprocess
import sys
import shutil
import threading
import time
import os
import socket
import psutil
import json
from pathlib import Path
from datetime import datetime

# Конфигурация
APP_NAME = "Universal Service Launcher"
CONFIG_DIR = Path.home() / ".service_launcher"
PROJECTS_DIR = CONFIG_DIR / "projects"
SERVICES_DIR = CONFIG_DIR / "services"
DEFAULT_CONFIG = {
    "name": "Новый проект",
    "services": [],
    "settings": {
        "restart_delay": 3,
        "port_check_timeout": 10,
        "auto_start_dependencies": True
    }
}

COLORS = {
    "running": "#ff69b4",
    "stopped": "#808080",
    "warning": "#ffa500",
    "error": "#ff0000"
}
PADDING = 10


class UniversalServiceLauncher:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("750x550")

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Директории
        CONFIG_DIR.mkdir(exist_ok=True)
        PROJECTS_DIR.mkdir(exist_ok=True)
        SERVICES_DIR.mkdir(exist_ok=True)

        # Данные
        self.current_project = None
        self.project_data = None
        self.processes = {}
        self.process_info = {}
        self.process_lock = threading.Lock()
        self.running = True
        self.restart_pending = False
        self.available_projects = []
        self.available_services = []
        self.starting_services = set()  # Множество запускаемых сервисов для предотвращения циклов

        # UI
        self.setup_menu()
        self.setup_ui()

        # Загрузка проектов
        self.load_projects_list()

        # Мониторинг
        self.start_monitoring()

        self.root.update_idletasks()

    def setup_menu(self):
        """Создание меню."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # Меню Файл
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Файл", menu=file_menu)
        file_menu.add_command(label="Новый проект", command=self.new_project)
        file_menu.add_command(label="Открыть проект", command=self.open_project)
        file_menu.add_command(label="Сохранить проект", command=self.save_project)
        file_menu.add_command(label="Сохранить как...", command=self.save_project_as)
        file_menu.add_separator()
        file_menu.add_command(label="Импорт конфигурации", command=self.import_config)
        file_menu.add_command(label="Экспорт конфигурации", command=self.export_config)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self.on_closing)

        # Меню Сервисы
        services_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Сервисы", menu=services_menu)
        services_menu.add_command(label="Добавить сервис", command=self.add_service)
        services_menu.add_command(label="Редактировать сервис", command=self.edit_service)
        services_menu.add_command(label="Удалить сервис", command=self.delete_service)
        services_menu.add_separator()
        services_menu.add_command(label="Импорт сервиса", command=self.import_service)
        services_menu.add_command(label="Экспорт сервиса", command=self.export_service)

        # Меню Настройки
        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Настройки", menu=settings_menu)
        settings_menu.add_command(label="Настройки проекта", command=self.project_settings)
        settings_menu.add_command(label="Глобальные настройки", command=self.global_settings)
        settings_menu.add_command(label="Шаблоны", command=self.manage_templates)

        # Меню Вид
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Вид", menu=view_menu)
        view_menu.add_command(label="Показать логи", command=self.show_logs)
        view_menu.add_command(label="Обновить", command=self.refresh_display)

        # Меню Помощь
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Помощь", menu=help_menu)
        help_menu.add_command(label="Справка", command=self.show_help)
        help_menu.add_command(label="О программе", command=self.show_about)

    def setup_ui(self):
        """Основной интерфейс."""
        # Верхняя панель
        top_frame = tk.Frame(self.root, bg=COLORS["stopped"], padx=PADDING, pady=5)
        top_frame.pack(fill=tk.X)

        tk.Label(top_frame, text="Проект:", bg=COLORS["stopped"]).pack(side=tk.LEFT, padx=5)

        self.project_var = tk.StringVar()
        self.project_combo = ttk.Combobox(
            top_frame,
            textvariable=self.project_var,
            values=[],
            width=40,
            state='readonly'
        )
        self.project_combo.pack(side=tk.LEFT, padx=5)
        self.project_combo.bind('<<ComboboxSelected>>', self.on_project_select)

        ttk.Button(top_frame, text="Загрузить", command=self.load_selected_project).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="Обновить", command=self.refresh_display).pack(side=tk.LEFT, padx=5)

        # Панель управления
        control_frame = tk.Frame(self.root, bg=COLORS["stopped"], padx=PADDING, pady=5)
        control_frame.pack(fill=tk.X)

        ttk.Button(control_frame, text="Запустить все", command=self.start_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="Остановить все", command=self.stop_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="Перезапустить все", command=self.restart_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(control_frame, text="Добавить сервис", command=self.add_service).pack(side=tk.LEFT, padx=2)

        # Статус
        self.status_var = tk.StringVar(value="Готов к работе")
        status_label = tk.Label(control_frame, textvariable=self.status_var, bg=COLORS["stopped"])
        status_label.pack(side=tk.RIGHT, padx=5)

        # Основная область с сервисами
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=PADDING, pady=5)

        # Заголовки
        headers = ["Статус", "Сервис", "Порт", "PID", "Python", "Зависимости", "Действия"]
        for i, header in enumerate(headers):
            tk.Label(main_frame, text=header, font=('Arial', 10, 'bold')).grid(row=0, column=i, padx=5, pady=5,
                                                                               sticky=tk.W)

        # Canvas для прокрутки
        self.canvas = tk.Canvas(main_frame)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.grid(row=1, column=0, columnspan=7, sticky="nsew")
        scrollbar.grid(row=1, column=7, sticky="ns")

        main_frame.grid_rowconfigure(1, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)

        # Нижняя панель с логами
        log_frame = tk.LabelFrame(self.root, text="Логи", padx=5, pady=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=PADDING, pady=5)

        self.log_text = tk.Text(log_frame, height=6, width=80, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        scrollbar_log = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar_log.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar_log.set)

    def log(self, message, level="info"):
        """Добавление сообщения в лог."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"

        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, log_entry)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        print(message)

    def load_projects_list(self):
        """Загрузка списка доступных проектов."""
        projects = []
        for file in PROJECTS_DIR.glob("*.json"):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    projects.append(data.get("name", file.stem))
            except:
                projects.append(file.stem)

        self.available_projects = projects
        self.project_combo['values'] = projects

    def new_project(self):
        """Создание нового проекта."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Новый проект")
        dialog.geometry("500x350")
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text="Имя проекта:").pack(pady=5)
        name_entry = tk.Entry(dialog, width=50)
        name_entry.pack(pady=5)

        tk.Label(dialog, text="Корневая директория:").pack(pady=5)
        dir_frame = tk.Frame(dialog)
        dir_frame.pack(pady=5)

        dir_entry = tk.Entry(dir_frame, width=40)
        dir_entry.pack(side=tk.LEFT, padx=5)
        dir_entry.insert(0, str(Path.cwd()))

        ttk.Button(dir_frame, text="Обзор", command=lambda: dir_entry.insert(0, filedialog.askdirectory())).pack(
            side=tk.LEFT)

        tk.Label(dialog, text="Описание:").pack(pady=5)
        desc_text = tk.Text(dialog, height=5, width=50)
        desc_text.pack(pady=5)

        def save():
            project_data = {
                "name": name_entry.get(),
                "root_dir": dir_entry.get(),
                "description": desc_text.get(1.0, tk.END).strip(),
                "services": [],
                "settings": DEFAULT_CONFIG["settings"].copy(),
                "created": datetime.now().isoformat(),
                "modified": datetime.now().isoformat()
            }

            filename = PROJECTS_DIR / f"{project_data['name']}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(project_data, f, ensure_ascii=False, indent=2)

            self.load_projects_list()
            self.project_var.set(project_data['name'])
            self.current_project = filename
            self.project_data = project_data
            self.refresh_display()
            self.log(f"Создан проект: {project_data['name']}")
            dialog.destroy()

        ttk.Button(dialog, text="Создать", command=save).pack(pady=10)

    def open_project(self):
        """Открыть проект из файла."""
        filename = filedialog.askopenfilename(
            title="Выберите файл проекта",
            initialdir=PROJECTS_DIR,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.load_project(Path(filename))

    def load_project(self, path):
        """Загрузка проекта."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                self.project_data = json.load(f)

            self.current_project = path
            self.project_var.set(self.project_data.get("name", ""))
            self.refresh_display()
            self.log(f"Загружен проект: {self.project_data.get('name')}")

            # Установка рабочей директории
            if "root_dir" in self.project_data:
                os.chdir(self.project_data["root_dir"])

        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить проект: {e}")

    def save_project(self):
        """Сохранение проекта."""
        if not self.current_project or not self.project_data:
            self.save_project_as()
            return

        try:
            self.project_data["modified"] = datetime.now().isoformat()
            with open(self.current_project, 'w', encoding='utf-8') as f:
                json.dump(self.project_data, f, ensure_ascii=False, indent=2)
            self.log(f"Проект сохранен: {self.project_data.get('name')}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить проект: {e}")

    def save_project_as(self):
        """Сохранить проект как..."""
        if not self.project_data:
            self.project_data = DEFAULT_CONFIG.copy()

        filename = filedialog.asksaveasfilename(
            title="Сохранить проект как",
            initialdir=PROJECTS_DIR,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            self.current_project = Path(filename)
            self.project_data["modified"] = datetime.now().isoformat()
            self.save_project()

    def on_project_select(self, event):
        """Выбор проекта из списка."""
        project_name = self.project_combo.get()
        if project_name:
            project_file = PROJECTS_DIR / f"{project_name}.json"
            if project_file.exists():
                self.load_project(project_file)

    def load_selected_project(self):
        """Загрузка выбранного проекта."""
        self.on_project_select(None)

    def refresh_display(self):
        """Обновление отображения сервисов."""
        # Очистка
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        if not self.project_data:
            tk.Label(self.scrollable_frame, text="Нет загруженного проекта").pack(pady=20)
            return

        services = self.project_data.get("services", [])
        if not services:
            tk.Label(self.scrollable_frame, text="Нет сервисов в проекте").pack(pady=20)
            return

        # Сортировка по порядку
        services.sort(key=lambda x: x.get("order", 999))

        for i, service in enumerate(services):
            self.create_service_row(i, service)

    def create_service_row(self, row, service):
        """Создание строки для сервиса."""
        frame = tk.Frame(self.scrollable_frame)
        frame.pack(fill=tk.X, pady=2)

        # Статус
        status = "●"
        with self.process_lock:
            if service.get("name") in self.process_info.values():
                status_color = COLORS["running"]
            else:
                status_color = COLORS["stopped"]

        status_label = tk.Label(frame, text=status, fg=status_color, font=('Arial', 12))
        status_label.grid(row=0, column=0, padx=5)

        # Имя сервиса
        tk.Label(frame, text=service.get("name", "Unknown"), width=15, anchor=tk.W).grid(row=0, column=1, padx=5,
                                                                                         sticky=tk.W)

        # Порт
        port = service.get("port", "-")
        tk.Label(frame, text=port, width=8).grid(row=0, column=2, padx=5)

        # PID
        pid = "-"
        with self.process_lock:
            for proc_pid, proc_name in self.process_info.items():
                if proc_name == service.get("name"):
                    pid = proc_pid
                    break
        tk.Label(frame, text=pid, width=8).grid(row=0, column=3, padx=5)

        # Python путь
        python_path = service.get("python_path", "system")
        if python_path == "system":
            python_display = "🐍 system"
        else:
            python_display = f"🐍 {Path(python_path).name}"
        tk.Label(frame, text=python_display, width=15, anchor=tk.W).grid(row=0, column=4, padx=5)

        # Зависимости
        deps = service.get("dependencies", [])
        deps_text = ", ".join(deps) if deps else "-"
        tk.Label(frame, text=deps_text, width=15, anchor=tk.W).grid(row=0, column=5, padx=5)

        # Действия
        btn_frame = tk.Frame(frame)
        btn_frame.grid(row=0, column=6, padx=5)

        ttk.Button(btn_frame, text="▶", width=3,
                   command=lambda s=service: self.start_service(s)).pack(side=tk.LEFT, padx=1)
        ttk.Button(btn_frame, text="■", width=3,
                   command=lambda s=service: self.stop_service(s)).pack(side=tk.LEFT, padx=1)
        ttk.Button(btn_frame, text="↻", width=3,
                   command=lambda s=service: self.restart_service(s)).pack(side=tk.LEFT, padx=1)
        ttk.Button(btn_frame, text="✎", width=3,
                   command=lambda s=service: self.edit_service_dialog(s)).pack(side=tk.LEFT, padx=1)

    def start_monitoring(self):
        """Запуск мониторинга процессов."""

        def monitor():
            while self.running:
                time.sleep(1)
                with self.process_lock:
                    for pid in list(self.process_info.keys()):
                        if not psutil.pid_exists(pid):
                            service_name = self.process_info[pid]
                            self.log(f"💀 Процесс {service_name} (PID: {pid}) завершился")
                            del self.process_info[pid]

                            # Обновление отображения
                            self.root.after(0, self.refresh_display)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()

    def get_python_interpreter(self, service):
        """Получение пути к Python интерпретатору для сервиса."""
        python_path = service.get("python_path", "")

        if not python_path or python_path == "system":
            return sys.executable

        # Если путь относительный, делаем абсолютный относительно корня проекта
        if not Path(python_path).is_absolute():
            root_dir = Path(self.project_data.get("root_dir", ""))
            python_path = root_dir / python_path

        return str(python_path)

    def find_dependency_chain(self, service, chain=None, visited=None):
        """Находит цепочку зависимостей (от кого зависит сервис)."""
        if chain is None:
            chain = []
        if visited is None:
            visited = set()

        service_name = service.get("name")
        if service_name in visited:
            return chain

        visited.add(service_name)

        # Добавляем себя
        chain.append(service)

        # Ищем от кого зависит сервис (его dependencies)
        deps = service.get("dependencies", [])
        for dep_name in deps:
            dep_service = next(
                (s for s in self.project_data.get("services", [])
                 if s.get("name") == dep_name),
                None
            )
            if dep_service:
                self.find_dependency_chain(dep_service, chain, visited)

        return chain

    def find_service_by_name(self, service_name):
        """Найти сервис по имени."""
        for s in self.project_data.get("services", []):
            if s.get("name") == service_name:
                return s
        return None

    def is_service_running(self, service_name):
        """Проверить, запущен ли сервис."""
        with self.process_lock:
            return service_name in self.process_info.values()

    def wait_for_service_ready(self, service, timeout=15):
        """Ожидание готовности сервиса (порт или процесс)."""
        start = time.time()
        service_name = service.get("name")

        while time.time() - start < timeout:
            # Проверяем, что процесс вообще жив
            with self.process_lock:
                if service_name not in self.process_info.values():
                    time.sleep(0.5)
                    continue

            # Если есть порт - ждем, пока он откроется
            if service.get("port"):
                if not self.is_port_available(service.get("host", "127.0.0.1"), service["port"]):
                    self.log(f"✅ Сервис {service_name} готов (порт {service['port']} открыт)")
                    return True
            else:
                # Если порта нет, просто ждем 2 секунды
                self.log(f"⏳ Сервис {service_name} запущен, даем время на инициализацию...")
                time.sleep(2)
                return True

            time.sleep(0.5)

        self.log(f"⚠️ Таймаут ожидания готовности {service_name}")
        return False

    def check_and_start_dependencies(self, service):
        """Проверяет и запускает все зависимости сверху вниз с ожиданием готовности."""
        service_name = service.get("name")

        # Строим дерево зависимостей (всех предков)
        def collect_all_dependencies(current_service, collected=None, visited=None):
            """Собирает все зависимости рекурсивно (без дубликатов)."""
            if collected is None:
                collected = []
            if visited is None:
                visited = set()

            current_name = current_service.get("name")
            if current_name in visited:
                return collected
            visited.add(current_name)

            # Сначала собираем зависимости текущего сервиса
            deps = current_service.get("dependencies", [])
            for dep_name in deps:
                dep_service = self.find_service_by_name(dep_name)
                if dep_service:
                    # Рекурсивно собираем зависимости зависимости
                    collect_all_dependencies(dep_service, collected, visited)
                    collected.append(dep_service)

            return collected

        # Получаем всех предков (от корня к целевому)
        all_deps = collect_all_dependencies(service, [], set())

        # Убираем дубликаты, сохраняя порядок
        seen = set()
        unique_deps = []
        for dep in all_deps:
            dep_name = dep.get("name")
            if dep_name not in seen:
                seen.add(dep_name)
                unique_deps.append(dep)

        if unique_deps:
            self.log(
                f"📋 Цепочка зависимостей для {service_name}: {' → '.join([d.get('name') for d in unique_deps])} → {service_name}")

        # Запускаем все зависимости по порядку (сверху вниз)
        for dep in unique_deps:
            dep_name = dep.get("name")

            # Проверяем, не запущен ли уже
            if not self.is_service_running(dep_name):
                self.log(f"🔄 Запуск зависимости: {dep_name}")
                self._start_single_service(dep)  # Используем новый метод

                # Ждем, пока зависимость реально запустится
                if not self.wait_for_service_ready(dep):
                    self.log(f"❌ Ошибка: зависимость {dep_name} не запустилась", "error")
                    return False

        return True

    def _start_single_service(self, service):
        """Запуск одного сервиса без проверки зависимостей (внутренний метод)."""
        service_name = service.get("name")

        # ИНИЦИАЛИЗАЦИЯ ВСЕХ ПЕРЕМЕННЫХ В НАЧАЛЕ
        root_dir = Path(self.project_data.get("root_dir", ""))
        env = os.environ.copy()
        startupinfo = None

        # Windows-specific настройки
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        # Проверяем не запущен ли уже
        with self.process_lock:
            if service_name in self.process_info.values():
                self.log(f"Сервис {service_name} уже запущен")
                return True

        # Проверка порта
        if service.get("port"):
            if not self.is_port_available(service.get("host", "127.0.0.1"), service["port"]):
                self.log(f"Порт {service['port']} занят, освобождаем...")
                self.kill_process_on_port(service["port"])
                if not self.wait_for_port(service.get("host", "127.0.0.1"), service["port"], timeout=10):
                    self.log(f"Не удалось освободить порт {service['port']}", "error")
                    return False

        # Получаем путь к скрипту
        script_path = Path(service.get("script", ""))
        if not script_path.is_absolute():
            script_path = root_dir / script_path

        if script_path.exists():
            try:
                python_exe = self.get_python_interpreter(service)

                if not Path(python_exe).exists():
                    self.log(f"Python не найден: {python_exe}", "error")
                    return False

                # Настраиваем окружение
                if root_dir.exists():
                    env["PYTHONPATH"] = str(root_dir)

                if service.get("env_file"):
                    env_path = Path(service["env_file"])
                    if not env_path.is_absolute():
                        env_path = root_dir / env_path
                    env.update(self.load_env_file(env_path))

                self.log(f"🚀 Запуск {service_name} с Python: {python_exe}")

                process = subprocess.Popen(
                    [python_exe, str(script_path)],
                    cwd=str(root_dir) if root_dir.exists() else None,
                    env=env,
                    startupinfo=startupinfo,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0
                )

                # Сохраняем в process_info
                with self.process_lock:
                    self.process_info[process.pid] = service_name

                self.log(f"✅ Запущен {service_name} с PID: {process.pid}")
                self.root.after(0, self.refresh_display)
                return True

            except Exception as e:
                self.log(f"❌ Ошибка: {e}", "error")
                return False
        else:
            self.log(f"❌ Скрипт не найден: {script_path}", "error")
            return False

    def start_service(self, service, check_dependencies=True):
        """Запуск сервиса с проверкой всех зависимостей."""
        service_name = service.get("name")

        # Проверяем не запущен ли уже
        if self.is_service_running(service_name):
            self.log(f"Сервис {service_name} уже запущен")
            return True

        # Проверяем и запускаем все зависимости
        if check_dependencies and self.project_data.get("settings", {}).get("auto_start_dependencies", True):
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
        return self._start_single_service(service)

    def stop_service(self, service):
        """Остановка сервиса."""
        with self.process_lock:
            for pid, name in list(self.process_info.items()):
                if name == service["name"]:
                    try:
                        process = psutil.Process(pid)
                        process.terminate()

                        try:
                            process.wait(timeout=5)
                        except psutil.TimeoutExpired:
                            process.kill()

                        self.log(f"Остановлен {service['name']} (PID: {pid})")
                        del self.process_info[pid]

                    except psutil.NoSuchProcess:
                        del self.process_info[pid]

                    self.root.after(0, self.refresh_display)
                    break

    def restart_service(self, service):
        """Перезапуск сервиса."""

        def restart_thread():
            self.stop_service(service)
            time.sleep(2)
            self.start_service(service)

        thread = threading.Thread(target=restart_thread, daemon=True)
        thread.start()

    def start_all(self):
        """Запуск всех сервисов с учетом иерархии."""
        if not self.project_data:
            return

        services = self.project_data.get("services", [])

        # Находим корневые сервисы (от которых никто не зависит)
        all_deps = set()
        for service in services:
            for dep in service.get("dependencies", []):
                all_deps.add(dep)

        root_services = [s for s in services if s.get("name") not in all_deps]

        self.log(f"🌳 Корневые сервисы: {[s.get('name') for s in root_services]}")

        # Запускаем корневые сервисы (они запустят свои зависимости, но их нет)
        for service in root_services:
            self.start_service(service, check_dependencies=False)

        # Запускаем остальные сервисы (они сами подтянут зависимости)
        for service in services:
            if service not in root_services:
                self.start_service(service, check_dependencies=True)

    def restart_all(self):
        """Перезапуск всех сервисов."""

        def restart_thread():
            self.stop_all()
            time.sleep(3)
            self.start_all()

        thread = threading.Thread(target=restart_thread, daemon=True)
        thread.start()

    def stop_all(self):
        """Остановка всех сервисов."""
        with self.process_lock:
            services_to_stop = list(self.process_info.values())

        for service_name in services_to_stop:
            service = next(
                (s for s in self.project_data.get("services", [])
                 if s.get("name") == service_name),
                {"name": service_name}
            )
            self.stop_service(service)

    def add_service(self):
        """Добавление нового сервиса."""
        self.edit_service_dialog()

    def edit_service(self):
        """Редактирование сервиса."""
        pass

    def edit_service_dialog(self, service=None):
        """Диалог редактирования сервиса."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Редактирование сервиса" if service else "Новый сервис")
        dialog.geometry("600x550")
        dialog.transient(self.root)
        dialog.grab_set()

        fields = [
            ("name", "Имя сервиса*:", service.get("name", "") if service else ""),
            ("script", "Путь к скрипту*:", service.get("script", "") if service else ""),
            ("python_path", "Python интерпретатор:",
             service.get("python_path", "system") if service else "system"),
            ("host", "Хост:", service.get("host", "127.0.0.1") if service else "127.0.0.1"),
            ("port", "Порт:", str(service.get("port", "")) if service else ""),
            ("env_file", "Файл .env:", service.get("env_file", "") if service else ""),
            ("order", "Порядок запуска:", str(service.get("order", "999")) if service else "999"),
        ]

        entries = {}
        for i, (key, label, default) in enumerate(fields):
            tk.Label(dialog, text=label).grid(row=i, column=0, padx=5, pady=5, sticky=tk.W)

            if key == "python_path":
                var = tk.StringVar(value=default)
                combo = ttk.Combobox(dialog, textvariable=var, width=40)
                combo['values'] = self.find_python_interpreters()
                combo.grid(row=i, column=1, padx=5, pady=5)
                entries[key] = var

                def browse_python(var):
                    filename = filedialog.askopenfilename(
                        title="Выберите Python интерпретатор",
                        filetypes=[("Python", "python.exe python3*"), ("All files", "*.*")]
                    )
                    if filename:
                        var.set(filename)

                ttk.Button(dialog, text="Обзор",
                           command=lambda v=var: browse_python(v)).grid(row=i, column=2, padx=5)
            else:
                entry = tk.Entry(dialog, width=40)
                entry.grid(row=i, column=1, padx=5, pady=5)
                entry.insert(0, default)

                if key in ["script", "env_file"]:
                    def browse_file(entry):
                        filename = filedialog.askopenfilename()
                        if filename:
                            entry.delete(0, tk.END)
                            entry.insert(0, filename)

                    ttk.Button(dialog, text="Обзор",
                               command=lambda e=entry: browse_file(e)).grid(row=i, column=2, padx=5)

                entries[key] = entry

        # Зависимости
        tk.Label(dialog, text="Зависимости:").grid(row=len(fields), column=0, padx=5, pady=5, sticky=tk.W)

        deps_frame = tk.Frame(dialog)
        deps_frame.grid(row=len(fields), column=1, padx=5, pady=5, sticky=tk.W)

        deps_listbox = tk.Listbox(deps_frame, height=4, selectmode=tk.MULTIPLE, width=40)
        deps_listbox.pack(side=tk.LEFT)

        scrollbar = ttk.Scrollbar(deps_frame, orient="vertical", command=deps_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        deps_listbox.configure(yscrollcommand=scrollbar.set)

        # Заполнение списка доступных сервисов
        available_services = []
        if self.project_data:
            available_services = [s["name"] for s in self.project_data.get("services", [])
                                  if service is None or s["name"] != service.get("name")]

        for s in available_services:
            deps_listbox.insert(tk.END, s)

        # Выбор текущих зависимостей
        current_deps = service.get("dependencies", []) if service else []
        for i, s in enumerate(available_services):
            if s in current_deps:
                deps_listbox.selection_set(i)

        def save():
            try:
                new_service = {
                    "name": entries["name"].get(),
                    "script": entries["script"].get(),
                    "python_path": entries["python_path"].get(),
                    "host": entries["host"].get(),
                    "port": int(entries["port"].get()) if entries["port"].get().isdigit() else None,
                    "env_file": entries["env_file"].get(),
                    "order": int(entries["order"].get()) if entries["order"].get().isdigit() else 999,
                    "dependencies": [deps_listbox.get(i) for i in deps_listbox.curselection()]
                }

                # Валидация
                if not new_service["name"] or not new_service["script"]:
                    messagebox.showerror("Ошибка", "Имя и путь к скрипту обязательны")
                    return

                if not self.project_data:
                    self.project_data = DEFAULT_CONFIG.copy()

                services = self.project_data.get("services", [])

                if service:
                    for i, s in enumerate(services):
                        if s.get("name") == service.get("name"):
                            services[i] = new_service
                            break
                else:
                    services.append(new_service)

                self.project_data["services"] = services
                self.save_project()
                self.refresh_display()
                self.log(f"Сервис {'обновлен' if service else 'добавлен'}: {new_service['name']}")
                dialog.destroy()

            except Exception as e:
                messagebox.showerror("Ошибка", f"Ошибка сохранения: {e}")

        ttk.Button(dialog, text="Сохранить", command=save).grid(row=len(fields) + 2, column=0, columnspan=3, pady=10)

    def find_python_interpreters(self):
        """Поиск доступных Python интерпретаторов."""
        interpreters = ["system"]

        if self.project_data and "root_dir" in self.project_data:
            root_dir = Path(self.project_data["root_dir"])

            for venv_dir in [".venv", "venv", "env", "virtualenv"]:
                venv_path = root_dir / venv_dir
                if sys.platform == 'win32':
                    python_path = venv_path / "Scripts" / "python.exe"
                else:
                    python_path = venv_path / "bin" / "python3"

                if python_path.exists():
                    interpreters.append(str(python_path))

        return interpreters

    def delete_service(self):
        """Удаление сервиса."""
        pass

    def import_config(self):
        """Импорт конфигурации."""
        filename = filedialog.askopenfilename(
            title="Выберите файл для импорта",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
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
                messagebox.showerror("Ошибка", f"Не удалось импортировать: {e}")

    def export_config(self):
        """Экспорт конфигурации."""
        if not self.project_data:
            return

        filename = filedialog.asksaveasfilename(
            title="Экспорт проекта",
            initialfile=f"{self.project_data.get('name', 'project')}.json",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(self.project_data, f, ensure_ascii=False, indent=2)
                self.log(f"Экспортирован проект: {Path(filename).name}")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось экспортировать: {e}")

    def import_service(self):
        """Импорт сервиса."""
        filename = filedialog.askopenfilename(
            title="Выберите файл сервиса",
            initialdir=SERVICES_DIR,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
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
                messagebox.showerror("Ошибка", f"Не удалось импортировать сервис: {e}")

    def export_service(self):
        """Экспорт сервиса."""
        pass

    def project_settings(self):
        """Настройки проекта."""
        if not self.project_data:
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Настройки проекта")
        dialog.geometry("400x300")
        dialog.transient(self.root)
        dialog.grab_set()

        settings = self.project_data.get("settings", DEFAULT_CONFIG["settings"])

        fields = [
            ("restart_delay", "Задержка перезапуска (сек):", str(settings.get("restart_delay", 3))),
            ("port_check_timeout", "Таймаут проверки порта:", str(settings.get("port_check_timeout", 10))),
        ]

        entries = {}
        for i, (key, label, default) in enumerate(fields):
            tk.Label(dialog, text=label).grid(row=i, column=0, padx=5, pady=5, sticky=tk.W)
            entry = tk.Entry(dialog, width=20)
            entry.grid(row=i, column=1, padx=5, pady=5)
            entry.insert(0, default)
            entries[key] = entry

        auto_var = tk.BooleanVar(value=settings.get("auto_start_dependencies", True))
        tk.Checkbutton(
            dialog,
            text="Автоматически запускать зависимости",
            variable=auto_var
        ).grid(row=len(fields), column=0, columnspan=2, padx=5, pady=5)

        def save():
            try:
                self.project_data["settings"] = {
                    "restart_delay": int(entries["restart_delay"].get()),
                    "port_check_timeout": int(entries["port_check_timeout"].get()),
                    "auto_start_dependencies": auto_var.get()
                }
                self.save_project()
                self.log("Настройки проекта сохранены")
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Ошибка", "Введите корректные числа")

        ttk.Button(dialog, text="Сохранить", command=save).grid(row=len(fields) + 1, column=0, columnspan=2, pady=10)

    def global_settings(self):
        """Глобальные настройки."""
        messagebox.showinfo("Информация", "Глобальные настройки будут доступны в следующей версии")

    def manage_templates(self):
        """Управление шаблонами."""
        messagebox.showinfo("Информация", "Управление шаблонами будет доступно в следующей версии")

    def show_logs(self):
        """Показать окно с логами."""
        log_window = tk.Toplevel(self.root)
        log_window.title("Логи")
        log_window.geometry("600x400")

        log_text = tk.Text(log_window)
        log_text.pack(fill=tk.BOTH, expand=True)

        self.log_text.config(state=tk.NORMAL)
        logs = self.log_text.get(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

        log_text.insert(tk.END, logs)
        log_text.config(state=tk.DISABLED)

    def show_help(self):
        """Показать справку."""
        help_text = """
Универсальный лаунчер сервисов

Основные возможности:
- Управление несколькими проектами
- Запуск/остановка сервисов
- Автоматический запуск зависимостей
- Поддержка индивидуальных Python окружений
- Редактор конфигураций
- Импорт/экспорт проектов

Горячие клавиши:
- Ctrl+N: Новый проект
- Ctrl+O: Открыть проект
- Ctrl+S: Сохранить проект
- F5: Обновить

Подробнее в документации.
        """
        messagebox.showinfo("Справка", help_text)

    def show_about(self):
        """О программе."""
        about_text = f"""
{APP_NAME}
Версия: 1.0.0

Универсальный инструмент для управления микросервисами

Автор: Команда №4
Лицензия: MIT

Директория конфигурации:
{CONFIG_DIR}
        """
        messagebox.showinfo("О программе", about_text)

    def is_port_available(self, host, port):
        """Проверка доступности порта."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s: ##
            try:
                s.bind((host, port))
                return True
            except OSError:
                return False

    def kill_process_on_port(self, port):
        """Убийство процесса на порту."""
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
        """Ожидает освобождения порта с таймаутом."""
        start = time.time()
        while time.time() - start < timeout:
            if self.is_port_available(host, port):
                return True
            time.sleep(1)
        self.log(f"Таймаут ожидания порта {port} ({timeout} сек)")
        return False

    def wait_for_process_start(self, process, service, timeout=10):
        """Ожидает успешного запуска процесса."""
        start = time.time()
        while time.time() - start < timeout:
            if process.poll() is not None:
                return False  # Процесс завершился
            # Проверяем что процесс слушает порт (если указан)
            if service.get("port"):
                if not self.is_port_available(service.get("host", "127.0.0.1"), service["port"]):
                    return True  # Порт занят - процесс запущен
            time.sleep(0.5)
        return True  # Считаем что процесс запущен

    def load_env_file(self, env_path):
        """Загрузка .env файла."""
        env_vars = {}
        if env_path and Path(env_path).exists():
            try:
                with open(env_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            if '=' in line:
                                key, value = line.split('=', 1)
                                env_vars[key.strip()] = value.strip()
            except Exception as e:
                self.log(f"Ошибка загрузки {env_path}: {e}")
        return env_vars

    def update_window_color(self):
        """Обновление цвета окна."""
        with self.process_lock:
            if self.process_info:
                color = COLORS["running"]
            else:
                color = COLORS["stopped"]

        self.root.configure(bg=color)
        for widget in self.root.winfo_children():
            try:
                widget.configure(bg=color)
            except:
                pass

        if self.running:
            self.root.after(500, self.update_window_color)

    def on_closing(self):
        """Обработка закрытия."""
        self.running = False
        self.stop_all()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = UniversalServiceLauncher(root)
    app.update_window_color()
    root.mainloop()