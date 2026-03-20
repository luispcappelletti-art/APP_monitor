import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Iterable


RECORD_START_RE = re.compile(r'(?m)^(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<topic>\S+)\s*(?P<payload>.*)$')
IO_RE = re.compile(r'(Output|Input)\s+(\d+),\s*([A-Za-z0-9_\-]+)\s+turned\s+(On|Off)', re.IGNORECASE)
STATE_RE = re.compile(r'Update Cnc State to\s+(\w+)', re.IGNORECASE)
CUT_MODE_RE = re.compile(r'Update Cut Mode to\s+(\w+)', re.IGNORECASE)
ERROR_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r'\berror\b',
        r'\bfault\b',
        r'\balarm\b',
        r'collision',
        r'fast stop',
        r'genericerror',
        r'publish xpr error',
    ]
]
IGNORE_ERROR_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r'add level switch',
        r'found \d+ fault log folders',
        r'pagefaultcount',
        r'parseerrors',
    ]
]

BG = '#0b1220'
SURFACE = '#111827'
SURFACE_ALT = '#172033'
SURFACE_SOFT = '#1f2a44'
CARD = '#111b2f'
TEXT = '#eef2ff'
TEXT_MUTED = '#94a3b8'
ACCENT = '#38bdf8'
ACCENT_2 = '#818cf8'
SUCCESS = '#22c55e'
WARNING = '#f59e0b'
DANGER = '#ef4444'
GRID = '#30415f'
BAR_COLORS = ['#38bdf8', '#818cf8', '#06b6d4', '#22c55e', '#f59e0b', '#fb7185']


@dataclass
class LogRecord:
    sequence: int
    timestamp: datetime
    topic: str
    payload: str
    message: str
    level: str | None
    raw_data: dict[str, Any] | None = None


@dataclass
class ArcEvent:
    start: datetime
    end: datetime | None = None

    @property
    def duration(self) -> timedelta:
        if self.end is None:
            return timedelta(0)
        return self.end - self.start


@dataclass
class ProgramSession:
    index: int
    start: datetime
    end: datetime | None = None
    arc_events: list[ArcEvent] = field(default_factory=list)
    errors: list[LogRecord] = field(default_factory=list)
    warnings: list[LogRecord] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    cut_mode: str | None = None
    events: list[LogRecord] = field(default_factory=list)

    @property
    def duration(self) -> timedelta:
        if self.end is None:
            return timedelta(0)
        return self.end - self.start

    @property
    def arc_openings(self) -> int:
        return len(self.arc_events)

    @property
    def total_arc_time(self) -> timedelta:
        return sum((event.duration for event in self.arc_events), timedelta(0))

    @property
    def utilization_rate(self) -> float:
        total_seconds = self.duration.total_seconds()
        if total_seconds <= 0:
            return 0.0
        return min(100.0, (self.total_arc_time.total_seconds() / total_seconds) * 100)

    @property
    def status(self) -> str:
        return 'Finalizado' if self.end else 'Em andamento'

    @property
    def start_label(self) -> str:
        return self.start.strftime('%Y-%m-%d %H:%M:%S')

    @property
    def end_label(self) -> str:
        return self.end.strftime('%Y-%m-%d %H:%M:%S') if self.end else '-'

    @property
    def error_summary(self) -> str:
        if not self.errors:
            return 'Sem erros'
        counter = Counter(record.message for record in self.errors)
        return '; '.join(f'{message} ({count}x)' for message, count in counter.most_common(3))


@dataclass
class LogAnalysis:
    source_path: Path
    records: list[LogRecord]
    sessions: list[ProgramSession]
    unassigned_errors: list[LogRecord]
    cut_mode_history: list[tuple[datetime, str]]
    state_history: list[tuple[datetime, str]]

    @property
    def total_programs(self) -> int:
        return len(self.sessions)

    @property
    def completed_programs(self) -> int:
        return sum(1 for session in self.sessions if session.end)

    @property
    def total_program_time(self) -> timedelta:
        return sum((session.duration for session in self.sessions), timedelta(0))

    @property
    def total_arc_openings(self) -> int:
        return sum(session.arc_openings for session in self.sessions)

    @property
    def total_arc_time(self) -> timedelta:
        return sum((session.total_arc_time for session in self.sessions), timedelta(0))

    @property
    def total_errors(self) -> int:
        return sum(len(session.errors) for session in self.sessions) + len(self.unassigned_errors)

    @property
    def total_warnings(self) -> int:
        return sum(len(session.warnings) for session in self.sessions)

    @property
    def utilization_rate(self) -> float:
        total_seconds = self.total_program_time.total_seconds()
        if total_seconds <= 0:
            return 0.0
        return min(100.0, (self.total_arc_time.total_seconds() / total_seconds) * 100)

    @property
    def average_program_duration(self) -> timedelta:
        if not self.sessions:
            return timedelta(0)
        return timedelta(seconds=self.total_program_time.total_seconds() / len(self.sessions))

    @property
    def average_arc_openings(self) -> float:
        if not self.sessions:
            return 0.0
        return self.total_arc_openings / len(self.sessions)

    @property
    def modes_counter(self) -> Counter[str]:
        counter: Counter[str] = Counter()
        for session in self.sessions:
            counter.update([session.cut_mode or 'Desconhecido'])
        return counter

    @property
    def error_counter(self) -> Counter[str]:
        counter: Counter[str] = Counter(record.message for record in self.unassigned_errors)
        for session in self.sessions:
            counter.update(record.message for record in session.errors)
        return counter

    @property
    def state_counter(self) -> Counter[str]:
        return Counter(state for _, state in self.state_history)

    @property
    def best_session(self) -> ProgramSession | None:
        if not self.sessions:
            return None
        return max(self.sessions, key=lambda session: (session.utilization_rate, session.total_arc_time, -session.index))

    @property
    def worst_session(self) -> ProgramSession | None:
        if not self.sessions:
            return None
        return min(self.sessions, key=lambda session: (session.utilization_rate, session.total_arc_time, session.index))


class LogParser:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def parse(self) -> list[LogRecord]:
        raw_text = self.path.read_text(encoding='utf-8', errors='ignore')
        matches = list(RECORD_START_RE.finditer(raw_text))
        if not matches:
            raise ValueError('Nenhum registro reconhecido no arquivo informado.')

        records: list[LogRecord] = []
        current_date: date | None = self._extract_first_date(raw_text)
        previous_dt: datetime | None = None

        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
            block = raw_text[start:end].strip()
            line_match = RECORD_START_RE.match(block)
            if not line_match:
                continue

            time_text = line_match.group('time')
            topic = line_match.group('topic')
            payload = block[line_match.end('topic'):].strip()
            payload = payload.replace('\ufeff', '').replace('\x00', '').strip()

            message, level, raw_data = self._extract_message(payload)
            explicit_date = self._extract_date(raw_data, payload)
            if explicit_date:
                current_date = explicit_date
            elif current_date is None:
                current_date = date.today()

            timestamp = datetime.combine(current_date, datetime.strptime(time_text, '%H:%M:%S').time())
            if previous_dt and timestamp < previous_dt:
                current_date = current_date + timedelta(days=1)
                timestamp = datetime.combine(current_date, timestamp.time())

            previous_dt = timestamp
            records.append(
                LogRecord(
                    sequence=len(records) + 1,
                    timestamp=timestamp,
                    topic=topic,
                    payload=payload,
                    message=message,
                    level=level,
                    raw_data=raw_data,
                )
            )

        return records

    def _extract_first_date(self, text: str) -> date | None:
        match = re.search(r'\b(\d{4}-\d{2}-\d{2})T', text)
        if not match:
            return None
        return datetime.strptime(match.group(1), '%Y-%m-%d').date()

    def _extract_message(self, payload: str) -> tuple[str, str | None, dict[str, Any] | None]:
        cleaned = payload.strip()
        if cleaned.startswith('{'):
            try:
                data = json.loads(cleaned)
                message = data.get('Message')
                if not message:
                    template = data.get('MessageTemplate')
                    if isinstance(template, dict):
                        message = template.get('Text')
                    elif isinstance(template, str):
                        message = template
                return str(message or cleaned), data.get('Level'), data
            except json.JSONDecodeError:
                pass
        return cleaned, None, None

    def _extract_date(self, raw_data: dict[str, Any] | None, payload: str) -> date | None:
        timestamp_value = None
        if raw_data:
            timestamp_value = raw_data.get('Timestamp')
        if not timestamp_value:
            match = re.search(r'\b(\d{4}-\d{2}-\d{2})T', payload)
            if match:
                timestamp_value = match.group(1)
        if not timestamp_value:
            return None
        try:
            return datetime.fromisoformat(str(timestamp_value).replace('Z', '+00:00')).date()
        except ValueError:
            if isinstance(timestamp_value, str):
                return datetime.strptime(timestamp_value[:10], '%Y-%m-%d').date()
        return None


class MonitorAnalyzer:
    def __init__(self, records: Iterable[LogRecord], source_path: str | Path):
        self.records = list(records)
        self.source_path = Path(source_path)

    def analyze(self) -> LogAnalysis:
        sessions: list[ProgramSession] = []
        unassigned_errors: list[LogRecord] = []
        cut_mode_history: list[tuple[datetime, str]] = []
        state_history: list[tuple[datetime, str]] = []
        active_session: ProgramSession | None = None
        active_arc: ArcEvent | None = None
        current_cut_mode: str | None = None

        for record in self.records:
            if cut_mode := self._detect_cut_mode(record.message):
                current_cut_mode = cut_mode
                cut_mode_history.append((record.timestamp, cut_mode))
                if active_session and active_session.cut_mode is None:
                    active_session.cut_mode = cut_mode

            if state := self._detect_state(record.message):
                state_history.append((record.timestamp, state))
                if active_session and (not active_session.states or active_session.states[-1] != state):
                    active_session.states.append(state)

            io_signal = self._detect_io(record.message)
            if io_signal == ('Output', '6', 'Program_Running', True):
                if active_session and active_session.end is None:
                    active_session.end = record.timestamp
                    if active_arc and active_arc.end is None:
                        active_arc.end = record.timestamp
                        active_session.arc_events.append(active_arc)
                        active_arc = None
                active_session = ProgramSession(
                    index=len(sessions) + 1,
                    start=record.timestamp,
                    cut_mode=current_cut_mode,
                )
                sessions.append(active_session)
                active_session.events.append(record)
                continue

            if active_session:
                active_session.events.append(record)

            if io_signal == ('Output', '6', 'Program_Running', False):
                if active_session:
                    active_session.end = record.timestamp
                    if active_arc and active_arc.end is None:
                        active_arc.end = record.timestamp
                        active_session.arc_events.append(active_arc)
                        active_arc = None
                    active_session = None
                continue

            if io_signal == ('Output', '1', 'Cut_Control', True):
                if active_session and active_arc is None:
                    active_arc = ArcEvent(start=record.timestamp)
                continue

            if io_signal == ('Output', '1', 'Cut_Control', False):
                if active_session and active_arc:
                    active_arc.end = record.timestamp
                    active_session.arc_events.append(active_arc)
                    active_arc = None
                continue

            if self._is_error(record):
                if active_session:
                    active_session.errors.append(record)
                else:
                    unassigned_errors.append(record)
                continue

            if self._is_warning(record) and active_session:
                active_session.warnings.append(record)

        if active_session and active_arc and active_arc.end is None:
            active_arc.end = active_session.end or self.records[-1].timestamp
            active_session.arc_events.append(active_arc)

        return LogAnalysis(
            source_path=self.source_path,
            records=self.records,
            sessions=sessions,
            unassigned_errors=unassigned_errors,
            cut_mode_history=cut_mode_history,
            state_history=state_history,
        )

    def _detect_io(self, message: str) -> tuple[str, str, str, bool] | None:
        match = IO_RE.search(message)
        if not match:
            return None
        return match.group(1).title(), match.group(2), match.group(3), match.group(4).lower() == 'on'

    def _detect_state(self, message: str) -> str | None:
        match = STATE_RE.search(message)
        if match:
            return match.group(1)
        return None

    def _detect_cut_mode(self, message: str) -> str | None:
        match = CUT_MODE_RE.search(message)
        if match:
            return match.group(1)
        return None

    def _is_error(self, record: LogRecord) -> bool:
        if any(pattern.search(record.message) for pattern in IGNORE_ERROR_PATTERNS):
            return False
        if record.level and record.level.lower() in {'error', 'fatal', 'critical'}:
            return True
        return any(pattern.search(record.message) for pattern in ERROR_PATTERNS)

    def _is_warning(self, record: LogRecord) -> bool:
        return bool(record.level and record.level.lower() == 'warning')


class MetricCard(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str, accent: str):
        super().__init__(parent, bg=CARD, highlightbackground=SURFACE_SOFT, highlightthickness=1)
        self.title_label = tk.Label(self, text=title, fg=TEXT_MUTED, bg=CARD, font=('Segoe UI', 10, 'bold'))
        self.title_label.pack(anchor='w', padx=16, pady=(12, 4))
        self.value_label = tk.Label(self, text='-', fg=TEXT, bg=CARD, font=('Segoe UI', 24, 'bold'))
        self.value_label.pack(anchor='w', padx=16)
        self.footer_label = tk.Label(self, text='', fg=accent, bg=CARD, font=('Segoe UI', 10))
        self.footer_label.pack(anchor='w', padx=16, pady=(4, 12))

    def set_value(self, value: str, footer: str = '') -> None:
        self.value_label.config(text=value)
        self.footer_label.config(text=footer)


class ChartCard(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str, subtitle: str = ''):
        super().__init__(parent, bg=CARD, highlightbackground=SURFACE_SOFT, highlightthickness=1)
        tk.Label(self, text=title, fg=TEXT, bg=CARD, font=('Segoe UI', 12, 'bold')).pack(anchor='w', padx=16, pady=(14, 0))
        tk.Label(self, text=subtitle, fg=TEXT_MUTED, bg=CARD, font=('Segoe UI', 9)).pack(anchor='w', padx=16, pady=(2, 10))


class SimpleBarChart(tk.Canvas):
    def __init__(self, parent: tk.Misc, height: int = 260):
        super().__init__(parent, bg=CARD, highlightthickness=0, height=height)
        self.items: list[tuple[str, float]] = []
        self.max_value = 0.0
        self.value_formatter: Callable[[float], str] = lambda value: f'{value:.0f}'
        self.bind('<Configure>', lambda _event: self.redraw())

    def set_data(
        self,
        items: list[tuple[str, float]],
        *,
        max_value: float | None = None,
        value_formatter: Callable[[float], str] | None = None,
    ) -> None:
        self.items = items
        self.max_value = max_value if max_value is not None else max((value for _, value in items), default=1)
        self.value_formatter = value_formatter or (lambda value: f'{value:.0f}')
        self.redraw()

    def redraw(self) -> None:
        self.delete('all')
        width = max(self.winfo_width(), 320)
        height = max(self.winfo_height(), 220)
        left, top, right, bottom = 48, 18, width - 18, height - 42
        chart_height = bottom - top
        chart_width = right - left

        self.create_line(left, bottom, right, bottom, fill=GRID, width=1)
        self.create_line(left, top, left, bottom, fill=GRID, width=1)

        if not self.items:
            self.create_text(width / 2, height / 2, text='Sem dados para exibir', fill=TEXT_MUTED, font=('Segoe UI', 11))
            return

        max_value = self.max_value or 1
        for step in range(1, 5):
            y = bottom - (chart_height * step / 4)
            self.create_line(left, y, right, y, fill=GRID, width=1, dash=(2, 4))
            value = max_value * step / 4
            self.create_text(left - 10, y, text=self.value_formatter(value), fill=TEXT_MUTED, font=('Segoe UI', 8), anchor='e')

        gap = 14
        bar_width = max(28, (chart_width - gap * (len(self.items) - 1)) / max(len(self.items), 1))
        if len(self.items) * (bar_width + gap) > chart_width:
            bar_width = max(20, chart_width / max(len(self.items) * 1.25, 1))
            gap = max(6, bar_width * 0.25)

        for index, (label, value) in enumerate(self.items):
            color = BAR_COLORS[index % len(BAR_COLORS)]
            x0 = left + index * (bar_width + gap)
            x1 = x0 + bar_width
            ratio = 0 if max_value == 0 else value / max_value
            y1 = bottom
            y0 = y1 - (chart_height * ratio)
            self.create_rectangle(x0, y0, x1, y1, fill=color, outline='')
            self.create_text((x0 + x1) / 2, y0 - 10, text=self.value_formatter(value), fill=TEXT, font=('Segoe UI', 8))
            self.create_text((x0 + x1) / 2, bottom + 14, text=label, fill=TEXT_MUTED, font=('Segoe UI', 8), angle=0)


class ArcTimeline(tk.Canvas):
    def __init__(self, parent: tk.Misc, height: int = 150):
        super().__init__(parent, bg=CARD, highlightthickness=0, height=height)
        self.session: ProgramSession | None = None
        self.bind('<Configure>', lambda _event: self.redraw())

    def set_session(self, session: ProgramSession | None) -> None:
        self.session = session
        self.redraw()

    def redraw(self) -> None:
        self.delete('all')
        width = max(self.winfo_width(), 300)
        height = max(self.winfo_height(), 120)
        left, right = 32, width - 24
        center_y = height / 2

        self.create_text(left, 18, text='Timeline do programa', fill=TEXT, font=('Segoe UI', 11, 'bold'), anchor='w')
        self.create_line(left, center_y, right, center_y, fill=GRID, width=8)

        if not self.session or self.session.duration.total_seconds() <= 0:
            self.create_text(width / 2, center_y, text='Selecione um programa para ver a linha do tempo dos arcos.', fill=TEXT_MUTED, font=('Segoe UI', 10))
            return

        total_seconds = self.session.duration.total_seconds()
        self.create_text(left, center_y + 26, text=self.session.start.strftime('%H:%M:%S'), fill=TEXT_MUTED, font=('Segoe UI', 8), anchor='w')
        self.create_text(right, center_y + 26, text=self.session.end.strftime('%H:%M:%S') if self.session.end else '-', fill=TEXT_MUTED, font=('Segoe UI', 8), anchor='e')

        for arc in self.session.arc_events:
            start_offset = max((arc.start - self.session.start).total_seconds(), 0)
            end_dt = arc.end or self.session.end or arc.start
            end_offset = min((end_dt - self.session.start).total_seconds(), total_seconds)
            x0 = left + ((right - left) * start_offset / total_seconds)
            x1 = left + ((right - left) * end_offset / total_seconds)
            self.create_line(x0, center_y, x1, center_y, fill=ACCENT, width=10)
            self.create_oval(x0 - 4, center_y - 4, x0 + 4, center_y + 4, fill=ACCENT_2, outline='')

        self.create_text(left, height - 18, text=f'Utilização: {self.session.utilization_rate:.1f}%   •   Aberturas de arco: {self.session.arc_openings}', fill=TEXT_MUTED, font=('Segoe UI', 9), anchor='w')


class MonitorDashboardApp:
    def __init__(self, root: tk.Tk, initial_path: str | None = None):
        self.root = root
        self.analysis: LogAnalysis | None = None
        self.metric_cards: dict[str, MetricCard] = {}
        self.program_lookup: dict[int, ProgramSession] = {}
        self.selected_session: ProgramSession | None = None

        self._configure_root()
        self._configure_styles()
        self._build_layout()

        if initial_path:
            self.load_file(initial_path)

    def _configure_root(self) -> None:
        self.root.title('APP Monitor • Dashboard Phoenix')
        self.root.geometry('1720x980')
        self.root.configure(bg=BG)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', background=SURFACE_ALT, foreground=TEXT_MUTED, padding=(16, 8), borderwidth=0)
        style.map('TNotebook.Tab', background=[('selected', CARD)], foreground=[('selected', TEXT)])
        style.configure('Treeview', background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=30, bordercolor=SURFACE_SOFT)
        style.map('Treeview', background=[('selected', SURFACE_SOFT)], foreground=[('selected', TEXT)])
        style.configure('Treeview.Heading', background=SURFACE_ALT, foreground=TEXT, relief='flat', font=('Segoe UI', 10, 'bold'))
        style.configure('TScrollbar', troughcolor=SURFACE_ALT, background=SURFACE_SOFT, arrowcolor=TEXT)
        style.configure('Accent.TButton', background=ACCENT, foreground=BG, borderwidth=0, focusthickness=0, padding=(14, 10), font=('Segoe UI', 10, 'bold'))
        style.map('Accent.TButton', background=[('active', '#7dd3fc')])
        style.configure('Ghost.TButton', background=SURFACE_ALT, foreground=TEXT, borderwidth=0, focusthickness=0, padding=(14, 10), font=('Segoe UI', 10, 'bold'))
        style.map('Ghost.TButton', background=[('active', SURFACE_SOFT)])

    def _build_layout(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill='x', padx=18, pady=(18, 10))

        brand = tk.Frame(header, bg=BG)
        brand.pack(side='left', fill='x', expand=True)
        tk.Label(brand, text='Phoenix Cutting Dashboard', fg=TEXT, bg=BG, font=('Segoe UI', 26, 'bold')).pack(anchor='w')
        tk.Label(
            brand,
            text='Dashboard executivo para monitorar programas cortados, utilização, arcos, falhas e comportamento do processo.',
            fg=TEXT_MUTED,
            bg=BG,
            font=('Segoe UI', 11),
        ).pack(anchor='w', pady=(4, 0))

        actions = tk.Frame(header, bg=BG)
        actions.pack(side='right')
        ttk.Button(actions, text='Abrir log', command=self.pick_file, style='Accent.TButton').pack(side='left', padx=(0, 8))
        ttk.Button(actions, text='Exportar JSON', command=self.export_summary, style='Ghost.TButton').pack(side='left')

        hero = tk.Frame(self.root, bg=SURFACE_ALT, highlightbackground=SURFACE_SOFT, highlightthickness=1)
        hero.pack(fill='x', padx=18, pady=(0, 12))
        self.file_label = tk.Label(hero, text='Nenhum arquivo carregado', fg=TEXT, bg=SURFACE_ALT, font=('Segoe UI', 11, 'bold'))
        self.file_label.pack(anchor='w', padx=16, pady=(12, 2))
        self.file_meta_label = tk.Label(hero, text='Abra um log para montar o dashboard completo.', fg=TEXT_MUTED, bg=SURFACE_ALT, font=('Segoe UI', 10))
        self.file_meta_label.pack(anchor='w', padx=16, pady=(0, 12))

        cards_row = tk.Frame(self.root, bg=BG)
        cards_row.pack(fill='x', padx=18, pady=(0, 12))
        metrics = [
            ('programs', 'Programas', ACCENT),
            ('utilization', 'Utilização', SUCCESS),
            ('arc_time', 'Tempo de arco', ACCENT_2),
            ('errors', 'Erros', DANGER),
            ('avg_duration', 'Média por programa', WARNING),
        ]
        for index, (key, title, accent) in enumerate(metrics):
            card = MetricCard(cards_row, title, accent)
            card.grid(row=0, column=index, sticky='nsew', padx=(0 if index == 0 else 10, 0))
            cards_row.columnconfigure(index, weight=1)
            self.metric_cards[key] = card

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=18, pady=(0, 18))

        self.dashboard_tab = tk.Frame(self.notebook, bg=BG)
        self.programs_tab = tk.Frame(self.notebook, bg=BG)
        self.errors_tab = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.dashboard_tab, text='Dashboard')
        self.notebook.add(self.programs_tab, text='Programas')
        self.notebook.add(self.errors_tab, text='Erros & Eventos')

        self._build_dashboard_tab()
        self._build_programs_tab()
        self._build_errors_tab()

    def _build_dashboard_tab(self) -> None:
        top = tk.Frame(self.dashboard_tab, bg=BG)
        top.pack(fill='both', expand=True)
        top.grid_columnconfigure(0, weight=2)
        top.grid_columnconfigure(1, weight=1)

        left_col = tk.Frame(top, bg=BG)
        right_col = tk.Frame(top, bg=BG)
        left_col.grid(row=0, column=0, sticky='nsew', padx=(0, 10), pady=(0, 10))
        right_col.grid(row=0, column=1, sticky='nsew', pady=(0, 10))

        utilization_card = ChartCard(left_col, 'Gráfico de utilização por programa', 'Percentual de tempo de arco sobre o tempo total de cada execução.')
        utilization_card.pack(fill='both', expand=True)
        self.utilization_chart = SimpleBarChart(utilization_card, height=300)
        self.utilization_chart.pack(fill='both', expand=True, padx=12, pady=(0, 12))

        arcs_card = ChartCard(left_col, 'Aberturas de arco por programa', 'Volume de ciclos de corte detectados em cada execução.')
        arcs_card.pack(fill='both', expand=True, pady=(10, 0))
        self.arc_chart = SimpleBarChart(arcs_card, height=260)
        self.arc_chart.pack(fill='both', expand=True, padx=12, pady=(0, 12))

        top_errors_card = ChartCard(right_col, 'Top erros', 'Ranking das mensagens de erro mais frequentes no período analisado.')
        top_errors_card.pack(fill='both', expand=True)
        self.error_chart = SimpleBarChart(top_errors_card, height=260)
        self.error_chart.pack(fill='both', expand=True, padx=12, pady=(0, 12))

        insights_card = ChartCard(right_col, 'Insights executivos', 'Resumo visual dos melhores e piores cenários do log analisado.')
        insights_card.pack(fill='both', expand=True, pady=(10, 0))
        self.insights_text = tk.Text(insights_card, bg=CARD, fg=TEXT, relief='flat', height=14, wrap='word', insertbackground=TEXT)
        self.insights_text.pack(fill='both', expand=True, padx=12, pady=(0, 12))

    def _build_programs_tab(self) -> None:
        top = tk.Frame(self.programs_tab, bg=BG)
        top.pack(fill='both', expand=True)
        top.grid_columnconfigure(0, weight=3)
        top.grid_columnconfigure(1, weight=2)
        top.grid_rowconfigure(0, weight=1)

        table_card = ChartCard(top, 'Execuções identificadas', 'Cada linha representa um programa cortado detectado pelo monitor.')
        table_card.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        detail_card = ChartCard(top, 'Detalhamento visual do programa', 'Linha do tempo, estados e eventos da sessão selecionada.')
        detail_card.grid(row=0, column=1, sticky='nsew')

        table_container = tk.Frame(table_card, bg=CARD)
        table_container.pack(fill='both', expand=True, padx=12, pady=(0, 12))
        self.program_table = ttk.Treeview(
            table_container,
            columns=('programa', 'inicio', 'fim', 'duracao', 'modo', 'arcos', 'utilizacao', 'erros', 'status'),
            show='headings',
        )
        headings = {
            'programa': 'Programa',
            'inicio': 'Início',
            'fim': 'Fim',
            'duracao': 'Duração',
            'modo': 'Modo',
            'arcos': 'Arcos',
            'utilizacao': 'Utilização',
            'erros': 'Erros',
            'status': 'Status',
        }
        widths = {'programa': 90, 'inicio': 155, 'fim': 155, 'duracao': 95, 'modo': 90, 'arcos': 70, 'utilizacao': 95, 'erros': 65, 'status': 95}
        for key, title in headings.items():
            self.program_table.heading(key, text=title)
            self.program_table.column(key, width=widths[key], anchor='center')
        self.program_table.pack(side='left', fill='both', expand=True)
        program_scroll = ttk.Scrollbar(table_container, orient='vertical', command=self.program_table.yview)
        program_scroll.pack(side='right', fill='y')
        self.program_table.configure(yscrollcommand=program_scroll.set)
        self.program_table.bind('<<TreeviewSelect>>', self.on_program_select)

        self.timeline = ArcTimeline(detail_card)
        self.timeline.pack(fill='x', padx=12, pady=(0, 8))
        self.program_details = tk.Text(detail_card, bg=CARD, fg=TEXT, relief='flat', wrap='word', insertbackground=TEXT)
        self.program_details.pack(fill='both', expand=True, padx=12, pady=(0, 12))

    def _build_errors_tab(self) -> None:
        wrapper = tk.Frame(self.errors_tab, bg=BG)
        wrapper.pack(fill='both', expand=True)
        wrapper.grid_columnconfigure(0, weight=1)
        wrapper.grid_columnconfigure(1, weight=1)

        top_errors_card = ChartCard(wrapper, 'Resumo de falhas', 'Totais consolidados das principais mensagens detectadas.')
        top_errors_card.grid(row=0, column=0, sticky='nsew', padx=(0, 10), pady=(0, 10))
        raw_errors_card = ChartCard(wrapper, 'Eventos de erro', 'Lista cronológica dos erros e anomalias encontrados.')
        raw_errors_card.grid(row=0, column=1, sticky='nsew', pady=(0, 10))

        self.error_summary_table = ttk.Treeview(top_errors_card, columns=('mensagem', 'ocorrencias'), show='headings', height=16)
        self.error_summary_table.heading('mensagem', text='Mensagem')
        self.error_summary_table.heading('ocorrencias', text='Ocorrências')
        self.error_summary_table.column('mensagem', width=430, anchor='w')
        self.error_summary_table.column('ocorrencias', width=100, anchor='center')
        self.error_summary_table.pack(fill='both', expand=True, padx=12, pady=(0, 12))

        self.error_events_table = ttk.Treeview(raw_errors_card, columns=('hora', 'origem', 'mensagem'), show='headings', height=16)
        self.error_events_table.heading('hora', text='Horário')
        self.error_events_table.heading('origem', text='Origem')
        self.error_events_table.heading('mensagem', text='Mensagem')
        self.error_events_table.column('hora', width=150, anchor='center')
        self.error_events_table.column('origem', width=130, anchor='center')
        self.error_events_table.column('mensagem', width=430, anchor='w')
        self.error_events_table.pack(fill='both', expand=True, padx=12, pady=(0, 12))

        state_card = ChartCard(wrapper, 'Estados CNC mais frequentes', 'Distribuição dos estados observados ao longo do período.')
        state_card.grid(row=1, column=0, columnspan=2, sticky='nsew')
        self.state_chart = SimpleBarChart(state_card, height=220)
        self.state_chart.pack(fill='both', expand=True, padx=12, pady=(0, 12))

    def pick_file(self) -> None:
        path = filedialog.askopenfilename(
            title='Selecione o arquivo de log',
            filetypes=[('Logs', '*.txt *.log'), ('Todos os arquivos', '*.*')],
        )
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> None:
        try:
            records = LogParser(path).parse()
            analysis = MonitorAnalyzer(records, path).analyze()
        except Exception as exc:
            messagebox.showerror('Falha ao analisar o log', str(exc))
            return

        self.analysis = analysis
        self.file_label.config(text=str(analysis.source_path))
        first_ts = analysis.records[0].timestamp.strftime('%Y-%m-%d %H:%M:%S') if analysis.records else '-'
        last_ts = analysis.records[-1].timestamp.strftime('%Y-%m-%d %H:%M:%S') if analysis.records else '-'
        self.file_meta_label.config(
            text=f'{len(analysis.records)} eventos lidos • Janela analisada: {first_ts} até {last_ts} • Utilização total: {analysis.utilization_rate:.1f}%'
        )
        self._refresh_metrics()
        self._refresh_dashboard_charts()
        self._refresh_program_table()
        self._refresh_error_tables()
        self._select_default_program()

    def export_summary(self) -> None:
        if not self.analysis:
            messagebox.showinfo('Sem análise', 'Carregue um log antes de exportar.')
            return
        destination = filedialog.asksaveasfilename(
            title='Salvar resumo JSON',
            defaultextension='.json',
            initialfile=f'{self.analysis.source_path.stem}_dashboard.json',
            filetypes=[('JSON', '*.json')],
        )
        if not destination:
            return
        payload = build_summary_payload(self.analysis)
        Path(destination).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        messagebox.showinfo('Exportação concluída', f'Resumo salvo em {destination}')

    def _refresh_metrics(self) -> None:
        assert self.analysis is not None
        self.metric_cards['programs'].set_value(str(self.analysis.total_programs), f'{self.analysis.completed_programs} finalizados')
        self.metric_cards['utilization'].set_value(f'{self.analysis.utilization_rate:.1f}%', f'{self.analysis.total_arc_openings} aberturas de arco')
        self.metric_cards['arc_time'].set_value(format_timedelta(self.analysis.total_arc_time), f'Modo dominante: {self.analysis.modes_counter.most_common(1)[0][0] if self.analysis.modes_counter else "-"}')
        self.metric_cards['errors'].set_value(str(self.analysis.total_errors), f'{self.analysis.total_warnings} warnings em sessões')
        self.metric_cards['avg_duration'].set_value(format_timedelta(self.analysis.average_program_duration), f'{self.analysis.average_arc_openings:.1f} arcos por programa')

    def _refresh_dashboard_charts(self) -> None:
        assert self.analysis is not None
        utilization_items = [(f'P{session.index}', session.utilization_rate) for session in self.analysis.sessions]
        self.utilization_chart.set_data(utilization_items, max_value=100, value_formatter=lambda value: f'{value:.0f}%')

        arc_items = [(f'P{session.index}', float(session.arc_openings)) for session in self.analysis.sessions]
        self.arc_chart.set_data(arc_items, value_formatter=lambda value: f'{int(value)}')

        top_errors = self.analysis.error_counter.most_common(6)
        error_items = [(truncate(f'E{index + 1}', 8), float(count)) for index, (_, count) in enumerate(top_errors)]
        self.error_chart.set_data(error_items, value_formatter=lambda value: f'{int(value)}')

        state_items = [(state, float(count)) for state, count in self.analysis.state_counter.most_common(8)]
        self.state_chart.set_data(state_items, value_formatter=lambda value: f'{int(value)}')

        self.insights_text.delete('1.0', 'end')
        self.insights_text.insert('end', self._build_insights_text())

    def _build_insights_text(self) -> str:
        assert self.analysis is not None
        insights: list[str] = []
        best = self.analysis.best_session
        worst = self.analysis.worst_session
        dominant_mode = self.analysis.modes_counter.most_common(1)[0][0] if self.analysis.modes_counter else 'N/D'
        top_error = self.analysis.error_counter.most_common(1)[0] if self.analysis.error_counter else None

        insights.append('• VISÃO GERAL')
        insights.append(f'  - O arquivo analisado contém {self.analysis.total_programs} programas e {len(self.analysis.records)} eventos úteis.')
        insights.append(f'  - A utilização consolidada do processo ficou em {self.analysis.utilization_rate:.1f}%, com {format_timedelta(self.analysis.total_arc_time)} de arco ativo.')
        insights.append(f'  - O modo de corte dominante foi {dominant_mode}.')
        insights.append('')

        insights.append('• PERFORMANCE')
        if best:
            insights.append(f'  - Melhor utilização: Programa {best.index} com {best.utilization_rate:.1f}% e {best.arc_openings} aberturas de arco.')
        if worst:
            insights.append(f'  - Menor utilização: Programa {worst.index} com {worst.utilization_rate:.1f}% e duração {format_timedelta(worst.duration)}.')
        insights.append(f'  - Duração média por programa: {format_timedelta(self.analysis.average_program_duration)}.')
        insights.append('')

        insights.append('• QUALIDADE / FALHAS')
        if top_error:
            insights.append(f'  - Erro mais recorrente: {top_error[0]} ({top_error[1]}x).')
        else:
            insights.append('  - Nenhum erro detectado no período.')
        if self.analysis.unassigned_errors:
            insights.append(f'  - Existem {len(self.analysis.unassigned_errors)} erros fora de sessão de programa.')
        insights.append('')

        insights.append('• LEITURA RÁPIDA')
        insights.append('  - Use a aba “Programas” para abrir a timeline visual de arco por execução.')
        insights.append('  - Use a aba “Erros & Eventos” para investigar falhas e estados dominantes.')
        return '\n'.join(insights)

    def _refresh_program_table(self) -> None:
        assert self.analysis is not None
        self.program_lookup = {session.index: session for session in self.analysis.sessions}
        self.program_table.delete(*self.program_table.get_children())
        for session in self.analysis.sessions:
            self.program_table.insert(
                '',
                'end',
                iid=str(session.index),
                values=(
                    session.index,
                    session.start_label,
                    session.end_label,
                    format_timedelta(session.duration),
                    session.cut_mode or '-',
                    session.arc_openings,
                    f'{session.utilization_rate:.1f}%',
                    len(session.errors),
                    session.status,
                ),
            )

    def _refresh_error_tables(self) -> None:
        assert self.analysis is not None
        self.error_summary_table.delete(*self.error_summary_table.get_children())
        for index, (message, count) in enumerate(self.analysis.error_counter.most_common(20), start=1):
            self.error_summary_table.insert('', 'end', iid=f's{index}', values=(message, count))

        self.error_events_table.delete(*self.error_events_table.get_children())
        rows: list[tuple[datetime, str, str]] = []
        for session in self.analysis.sessions:
            for record in session.errors:
                rows.append((record.timestamp, f'Programa {session.index}', record.message))
        for record in self.analysis.unassigned_errors:
            rows.append((record.timestamp, 'Fora sessão', record.message))
        rows.sort(key=lambda item: item[0])
        for index, (timestamp, origin, message) in enumerate(rows, start=1):
            self.error_events_table.insert('', 'end', iid=f'e{index}', values=(timestamp.strftime('%Y-%m-%d %H:%M:%S'), origin, message))

    def _select_default_program(self) -> None:
        if not self.analysis or not self.analysis.sessions:
            self.selected_session = None
            self.timeline.set_session(None)
            self.program_details.delete('1.0', 'end')
            self.program_details.insert('end', 'Nenhum programa detectado no arquivo carregado.')
            return
        first = self.analysis.sessions[0]
        self.program_table.selection_set(str(first.index))
        self.program_table.focus(str(first.index))
        self._show_program(first)

    def on_program_select(self, _event: object) -> None:
        selection = self.program_table.selection()
        if not selection:
            return
        session = self.program_lookup.get(int(selection[0]))
        if session:
            self._show_program(session)

    def _show_program(self, session: ProgramSession) -> None:
        self.selected_session = session
        self.timeline.set_session(session)
        self.program_details.delete('1.0', 'end')
        self.program_details.insert('end', self._build_program_details(session))

    def _build_program_details(self, session: ProgramSession) -> str:
        lines = [
            f'Programa {session.index}',
            f'Início: {session.start_label}',
            f'Fim: {session.end_label}',
            f'Duração total: {format_timedelta(session.duration)}',
            f'Modo de corte: {session.cut_mode or "não identificado"}',
            f'Utilização: {session.utilization_rate:.1f}%',
            f'Aberturas de arco: {session.arc_openings}',
            f'Tempo total de arco: {format_timedelta(session.total_arc_time)}',
            f'Erros nesta sessão: {len(session.errors)}',
            f'Estados: {", ".join(session.states) if session.states else "sem estados detectados"}',
            '',
            'Arcos detectados:',
        ]
        if session.arc_events:
            for arc_index, arc in enumerate(session.arc_events, start=1):
                arc_end = arc.end.strftime('%H:%M:%S') if arc.end else 'aberto'
                lines.append(f'  - Arco {arc_index}: {arc.start:%H:%M:%S} → {arc_end} ({format_timedelta(arc.duration)})')
        else:
            lines.append('  - Nenhum arco detectado.')

        lines.append('')
        lines.append('Erros detectados:')
        if session.errors:
            for record in session.errors[:20]:
                lines.append(f'  - {record.timestamp:%H:%M:%S} | {record.message}')
            if len(session.errors) > 20:
                lines.append(f'  - ... e mais {len(session.errors) - 20} erro(s).')
        else:
            lines.append('  - Nenhum erro detectado nesta sessão.')
        return '\n'.join(lines)


def format_timedelta(delta: timedelta) -> str:
    total_seconds = max(int(delta.total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + '…'


def build_summary_payload(analysis: LogAnalysis) -> dict[str, Any]:
    return {
        'arquivo': str(analysis.source_path),
        'resumo': {
            'programas_cortados': analysis.total_programs,
            'programas_finalizados': analysis.completed_programs,
            'tempo_total_programas': format_timedelta(analysis.total_program_time),
            'aberturas_de_arco': analysis.total_arc_openings,
            'tempo_total_de_arco': format_timedelta(analysis.total_arc_time),
            'utilizacao_percentual': round(analysis.utilization_rate, 2),
            'erros_detectados': analysis.total_errors,
            'warnings_detectados': analysis.total_warnings,
            'duracao_media_programa': format_timedelta(analysis.average_program_duration),
        },
        'programas': [
            {
                'programa': session.index,
                'inicio': session.start_label,
                'fim': session.end_label,
                'duracao': format_timedelta(session.duration),
                'modo_corte': session.cut_mode,
                'aberturas_de_arco': session.arc_openings,
                'tempo_total_de_arco': format_timedelta(session.total_arc_time),
                'utilizacao_percentual': round(session.utilization_rate, 2),
                'estados': session.states,
                'erros': [record.message for record in session.errors],
            }
            for session in analysis.sessions
        ],
        'top_erros': [{'mensagem': message, 'ocorrencias': count} for message, count in analysis.error_counter.most_common(15)],
        'modos_corte': [{'modo': mode, 'ocorrencias': count} for mode, count in analysis.modes_counter.most_common()],
        'estados_cnc': [{'estado': state, 'ocorrencias': count} for state, count in analysis.state_counter.most_common()],
    }


def print_cli_summary(analysis: LogAnalysis) -> None:
    print(json.dumps(build_summary_payload(analysis), indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description='Dashboard profissional para monitoramento de logs Phoenix.')
    parser.add_argument('logfile', nargs='?', help='Arquivo de log a ser analisado.')
    parser.add_argument('--summary', action='store_true', help='Imprime um resumo JSON no terminal e encerra.')
    args = parser.parse_args()

    if args.summary:
        if not args.logfile:
            raise SystemExit('Informe o caminho do log ao usar --summary.')
        records = LogParser(args.logfile).parse()
        analysis = MonitorAnalyzer(records, args.logfile).analyze()
        print_cli_summary(analysis)
        return

    root = tk.Tk()
    MonitorDashboardApp(root, initial_path=args.logfile)
    root.mainloop()


if __name__ == '__main__':
    main()
