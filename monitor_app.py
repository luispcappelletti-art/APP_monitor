import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Iterable

RECORD_START_RE = re.compile(r'(?m)^(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<topic>\S+)\s*(?P<payload>.*)$')
IO_RE = re.compile(r'(Output|Input)\s+(\d+),\s*([A-Za-z0-9_\-]+)\s+turned\s+(On|Off)', re.IGNORECASE)
STATE_RE = re.compile(r'Update Cnc State to\s+(\w+)', re.IGNORECASE)
CUT_MODE_RE = re.compile(r'Update Cut Mode to\s+(\w+)', re.IGNORECASE)
STATUS_TOPIC_RE = re.compile(r'^(?P<topic_root>.+)/Status$')
VERSION_PATTERNS = [
    re.compile(r'(?P<label>Phoenix version):\s*(?P<value>.+)', re.IGNORECASE),
    re.compile(r'(?P<label>Application version):\s*(?P<value>.+)', re.IGNORECASE),
    re.compile(r'(?P<label>MRT INtime version)\s+(?P<value>.+)', re.IGNORECASE),
    re.compile(r'(?P<label>Build branch name):\s*(?P<value>.+)', re.IGNORECASE),
    re.compile(r'(?P<label>Build branch description):\s*(?P<value>.+)', re.IGNORECASE),
    re.compile(r'Posting cutchart version\s+"(?P<value>.+?)".*', re.IGNORECASE),
    re.compile(r'Found\s+(?P<label>.+?)\s+\[version\s+(?P<value>.+?)\]', re.IGNORECASE),
]
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
CATEGORY_RULES: list[tuple[str, list[re.Pattern[str]]]] = [
    ('Colisão', [re.compile(r'torch_collision|torch collision', re.IGNORECASE)]),
    ('Parada de segurança', [re.compile(r'fast stop|front_panel_stop|stop requested', re.IGNORECASE)]),
    ('Fonte / XPR', [re.compile(r'xpr|cutchart|process', re.IGNORECASE)]),
    ('Fieldbus / CAN', [re.compile(r'can::errorregister|fieldbus|ethercat|faulted drive|wrongwc', re.IGNORECASE)]),
    ('Movimento / homing', [re.compile(r'homing|manualmotion|programmedmotion|returningtostart', re.IGNORECASE)]),
    ('Broker / conectividade', [re.compile(r'connected to|mqtt client|status online|status offline', re.IGNORECASE)]),
    ('Inventário / versão', [re.compile(r'\bversion\b|build branch|working directory|operating system', re.IGNORECASE)]),
]
CARD_COLORS = {
    'blue': '#1d4ed8',
    'green': '#059669',
    'amber': '#d97706',
    'red': '#dc2626',
    'violet': '#7c3aed',
    'slate': '#334155',
}


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
    def status(self) -> str:
        return 'Finalizado' if self.end else 'Em andamento'

    @property
    def arc_efficiency(self) -> float:
        duration_seconds = self.duration.total_seconds()
        if duration_seconds <= 0:
            return 0.0
        return self.total_arc_time.total_seconds() / duration_seconds

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def error_summary(self) -> str:
        if not self.errors:
            return 'Sem erros'
        counter = Counter(record.message for record in self.errors)
        return '; '.join(f'{message} ({count}x)' for message, count in counter.most_common(3))


@dataclass
class ServiceStatusEvent:
    timestamp: datetime
    service: str
    status: str


@dataclass
class VersionEntry:
    label: str
    value: str
    timestamp: datetime


@dataclass
class InsightItem:
    title: str
    description: str
    priority: str
    metric: str


@dataclass
class LogAnalysis:
    source_path: Path
    records: list[LogRecord]
    sessions: list[ProgramSession]
    unassigned_errors: list[LogRecord]
    cut_mode_history: list[tuple[datetime, str]]
    state_history: list[tuple[datetime, str]]
    service_status_history: list[ServiceStatusEvent]
    version_inventory: list[VersionEntry]
    source_context_counts: Counter[str]
    topic_counts: Counter[str]
    category_counts: Counter[str]
    state_duration_seconds: dict[str, float]
    recommendations: list[InsightItem]

    @property
    def total_programs(self) -> int:
        return len(self.sessions)

    @property
    def completed_programs(self) -> int:
        return sum(1 for session in self.sessions if session.end)

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
    def total_runtime(self) -> timedelta:
        if not self.records:
            return timedelta(0)
        return self.records[-1].timestamp - self.records[0].timestamp

    @property
    def average_session_duration(self) -> timedelta:
        if not self.sessions:
            return timedelta(0)
        total = sum((session.duration for session in self.sessions), timedelta(0))
        return total / len(self.sessions)

    @property
    def arc_efficiency(self) -> float:
        programmed_time = sum((session.duration for session in self.sessions), timedelta(0)).total_seconds()
        if programmed_time <= 0:
            return 0.0
        return self.total_arc_time.total_seconds() / programmed_time

    @property
    def health_score(self) -> int:
        score = 100
        score -= min(self.total_errors * 4, 36)
        score -= min(self.total_warnings * 2, 10)
        critical_events = self.category_counts.get('Colisão', 0) + self.category_counts.get('Parada de segurança', 0)
        score -= min(critical_events * 5, 20)
        if self.completed_programs < self.total_programs:
            score -= 8
        return max(score, 12)

    @property
    def service_status_summary(self) -> dict[str, str]:
        latest: dict[str, str] = {}
        for event in self.service_status_history:
            latest[event.service] = event.status
        return latest


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
        service_status_history: list[ServiceStatusEvent] = []
        version_inventory: list[VersionEntry] = []
        source_context_counts: Counter[str] = Counter()
        topic_counts: Counter[str] = Counter()
        category_counts: Counter[str] = Counter()
        active_session: ProgramSession | None = None
        active_arc: ArcEvent | None = None
        current_cut_mode: str | None = None

        for record in self.records:
            topic_counts[record.topic] += 1
            category_counts[self._categorize_record(record)] += 1

            if source_context := self._extract_source_context(record):
                source_context_counts[source_context] += 1

            for version in self._extract_versions(record):
                version_inventory.append(version)

            if status_event := self._detect_service_status(record):
                service_status_history.append(status_event)

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
                    active_session.events.append(record)
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

        state_duration_seconds = self._compute_state_durations(state_history)
        recommendations = self._build_recommendations(
            sessions=sessions,
            service_status_history=service_status_history,
            version_inventory=version_inventory,
            category_counts=category_counts,
            source_context_counts=source_context_counts,
            unassigned_errors=unassigned_errors,
        )

        return LogAnalysis(
            source_path=self.source_path,
            records=self.records,
            sessions=sessions,
            unassigned_errors=unassigned_errors,
            cut_mode_history=cut_mode_history,
            state_history=state_history,
            service_status_history=service_status_history,
            version_inventory=version_inventory,
            source_context_counts=source_context_counts,
            topic_counts=topic_counts,
            category_counts=category_counts,
            state_duration_seconds=state_duration_seconds,
            recommendations=recommendations,
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

    def _detect_service_status(self, record: LogRecord) -> ServiceStatusEvent | None:
        match = STATUS_TOPIC_RE.match(record.topic)
        if not match:
            return None
        status = record.message.strip().title()
        if status not in {'Online', 'Offline'}:
            return None
        service_name = match.group('topic_root').split('/')[-1]
        return ServiceStatusEvent(timestamp=record.timestamp, service=service_name, status=status)

    def _extract_source_context(self, record: LogRecord) -> str | None:
        if not record.raw_data:
            return None
        properties = record.raw_data.get('Properties') or {}
        source_context = properties.get('SourceContext')
        if isinstance(source_context, dict):
            value = source_context.get('Value')
            if value:
                return str(value)
        return None

    def _extract_versions(self, record: LogRecord) -> list[VersionEntry]:
        versions: list[VersionEntry] = []
        for pattern in VERSION_PATTERNS:
            match = pattern.search(record.message)
            if not match:
                continue
            label = match.groupdict().get('label') or 'Cutchart version'
            value = match.groupdict().get('value')
            if value:
                versions.append(VersionEntry(label=label.strip(), value=value.strip(), timestamp=record.timestamp))
        return versions

    def _categorize_record(self, record: LogRecord) -> str:
        for category, patterns in CATEGORY_RULES:
            if any(pattern.search(record.message) or pattern.search(record.topic) for pattern in patterns):
                return category
        if self._is_error(record):
            return 'Erros diversos'
        return 'Operação geral'

    def _is_error(self, record: LogRecord) -> bool:
        if any(pattern.search(record.message) for pattern in IGNORE_ERROR_PATTERNS):
            return False
        if record.level and record.level.lower() in {'error', 'fatal', 'critical'}:
            return True
        return any(pattern.search(record.message) for pattern in ERROR_PATTERNS)

    def _is_warning(self, record: LogRecord) -> bool:
        return bool(record.level and record.level.lower() == 'warning')

    def _compute_state_durations(self, history: list[tuple[datetime, str]]) -> dict[str, float]:
        totals: defaultdict[str, float] = defaultdict(float)
        if not history:
            return {}
        for index, (timestamp, state) in enumerate(history):
            next_timestamp = history[index + 1][0] if index + 1 < len(history) else self.records[-1].timestamp
            delta = max((next_timestamp - timestamp).total_seconds(), 0.0)
            totals[state] += delta
        return dict(totals)

    def _build_recommendations(
        self,
        sessions: list[ProgramSession],
        service_status_history: list[ServiceStatusEvent],
        version_inventory: list[VersionEntry],
        category_counts: Counter[str],
        source_context_counts: Counter[str],
        unassigned_errors: list[LogRecord],
    ) -> list[InsightItem]:
        recommendations: list[InsightItem] = []
        collision_count = category_counts.get('Colisão', 0)
        stop_count = category_counts.get('Parada de segurança', 0)
        fieldbus_count = category_counts.get('Fieldbus / CAN', 0)
        inventory_count = len(version_inventory)
        external_errors = len(unassigned_errors)
        low_efficiency_sessions = sum(1 for session in sessions if session.duration and session.arc_efficiency < 0.3)
        latest_service_status = {event.service: event.status for event in service_status_history}

        recommendations.append(
            InsightItem(
                title='Registro de eficiência por programa',
                description='Registrar duração, tempo de arco, eficiência, modo de corte e estados percorridos para comparar produtividade entre execuções.',
                priority='Alta',
                metric=f'{low_efficiency_sessions} programas com eficiência abaixo de 30%.' if sessions else 'Sem programas no log.',
            )
        )
        recommendations.append(
            InsightItem(
                title='Registro de disponibilidade dos serviços',
                description='Monitorar Phoenix, Managed e Rtos com eventos Online/Offline para detectar reinícios, quedas e sequência de startup da máquina.',
                priority='Alta' if any(status != 'Online' for status in latest_service_status.values()) else 'Média',
                metric=', '.join(f'{service}: {status}' for service, status in latest_service_status.items()) or 'Sem eventos de status.',
            )
        )
        recommendations.append(
            InsightItem(
                title='Registro de incidentes de segurança',
                description='Consolidar torch collision, fast stop e paradas manuais em uma trilha única de incidentes com horário, origem e sessão impactada.',
                priority='Crítica' if collision_count or stop_count else 'Média',
                metric=f'{collision_count} colisões e {stop_count} eventos de parada/safety.',
            )
        )
        recommendations.append(
            InsightItem(
                title='Registro de saúde Fieldbus / CAN',
                description='Separar falhas de drive, CAN e EtherCAT para análise de manutenção preditiva e correlação com erros de processo.',
                priority='Alta' if fieldbus_count else 'Média',
                metric=f'{fieldbus_count} eventos técnicos ligados a CAN/Fieldbus.',
            )
        )
        recommendations.append(
            InsightItem(
                title='Registro de inventário técnico e versões',
                description='Guardar versões de Phoenix, branch, cutchart e softwares instalados para auditoria e rastreabilidade de atualização.',
                priority='Média',
                metric=f'{inventory_count} registros de versão/inventário extraídos do log.',
            )
        )
        recommendations.append(
            InsightItem(
                title='Registro de erros fora de programa',
                description='Criar uma fila operacional para erros fora da execução de corte, pois indicam falhas de inicialização, comunicação ou hardware.',
                priority='Alta' if external_errors else 'Baixa',
                metric=f'{external_errors} erros ocorreram fora de uma sessão de programa.',
            )
        )
        if source_context_counts:
            top_context, top_count = source_context_counts.most_common(1)[0]
            recommendations.append(
                InsightItem(
                    title='Registro por origem técnica',
                    description='Agrupar eventos pelo SourceContext do log para revelar qual módulo concentra mais atividade ou ruído operacional.',
                    priority='Média',
                    metric=f'Origem com maior volume: {top_context} ({top_count} eventos).',
                )
            )
        return recommendations


class DashboardCharts:
    def __init__(self, parent: ttk.Frame):
        self.parent = parent
        self.session_canvas = tk.Canvas(parent, height=260, bg='white', highlightthickness=0)
        self.state_canvas = tk.Canvas(parent, height=260, bg='white', highlightthickness=0)
        self.category_canvas = tk.Canvas(parent, height=260, bg='white', highlightthickness=0)
        self.health_canvas = tk.Canvas(parent, height=220, bg='white', highlightthickness=0)

    def pack(self) -> None:
        self.session_canvas.pack(fill='x', padx=4, pady=(0, 10))
        self.state_canvas.pack(fill='x', padx=4, pady=(0, 10))
        self.category_canvas.pack(fill='x', padx=4, pady=(0, 10))
        self.health_canvas.pack(fill='x', padx=4)


class MonitorApp:
    def __init__(self, root: tk.Tk, initial_path: str | None = None):
        self.root = root
        self.root.title('APP Monitor | Dashboard de Produção Phoenix')
        self.root.geometry('1680x980')
        self.root.configure(bg='#eef2ff')

        self.analysis: LogAnalysis | None = None
        self.summary_vars: dict[str, tk.StringVar] = {}
        self.hero_vars: dict[str, tk.StringVar] = {}

        self._configure_styles()
        self._build()
        if initial_path:
            self.load_file(initial_path)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', background='#eef2ff', foreground='#0f172a')
        style.configure('App.TFrame', background='#eef2ff')
        style.configure('Surface.TFrame', background='white')
        style.configure('Surface.TLabelframe', background='white', bordercolor='#dbeafe', relief='solid')
        style.configure('Surface.TLabelframe.Label', background='white', foreground='#0f172a', font=('Segoe UI', 11, 'bold'))
        style.configure('Title.TLabel', background='#eef2ff', foreground='#0f172a', font=('Segoe UI', 20, 'bold'))
        style.configure('Subtitle.TLabel', background='#eef2ff', foreground='#475569', font=('Segoe UI', 10))
        style.configure('CardValue.TLabel', background='white', foreground='#0f172a', font=('Segoe UI', 22, 'bold'))
        style.configure('CardLabel.TLabel', background='white', foreground='#64748b', font=('Segoe UI', 10))
        style.configure('SectionTitle.TLabel', background='white', foreground='#0f172a', font=('Segoe UI', 12, 'bold'))
        style.configure('Treeview', rowheight=28, fieldbackground='white', background='white', foreground='#0f172a')
        style.configure('Treeview.Heading', background='#e2e8f0', foreground='#0f172a', font=('Segoe UI', 10, 'bold'))
        style.map('Treeview', background=[('selected', '#dbeafe')], foreground=[('selected', '#0f172a')])
        style.configure('TNotebook', background='#eef2ff', tabmargins=[0, 0, 0, 0])
        style.configure('TNotebook.Tab', padding=(16, 10), font=('Segoe UI', 10, 'bold'))

    def _build(self) -> None:
        main = ttk.Frame(self.root, style='App.TFrame', padding=14)
        main.pack(fill='both', expand=True)

        header = ttk.Frame(main, style='App.TFrame')
        header.pack(fill='x', pady=(0, 12))

        title_block = ttk.Frame(header, style='App.TFrame')
        title_block.pack(side='left', fill='x', expand=True)
        ttk.Label(title_block, text='Dashboard Operacional Phoenix', style='Title.TLabel').pack(anchor='w')
        ttk.Label(
            title_block,
            text='Visão executiva + análise técnica do log com métricas de produção, saúde do sistema e registros recomendados.',
            style='Subtitle.TLabel',
        ).pack(anchor='w', pady=(2, 0))

        actions = ttk.Frame(header, style='App.TFrame')
        actions.pack(side='right')
        ttk.Button(actions, text='Abrir log', command=self.pick_file).pack(side='left')
        ttk.Button(actions, text='Exportar resumo JSON', command=self.export_summary).pack(side='left', padx=(8, 0))

        hero = ttk.Frame(main, style='App.TFrame')
        hero.pack(fill='x', pady=(0, 10))
        hero_cards = [
            ('health', 'Score operacional', 'slate'),
            ('runtime', 'Janela analisada', 'blue'),
            ('efficiency', 'Eficiência de arco', 'green'),
            ('services', 'Serviços online', 'violet'),
        ]
        for idx, (key, label, color) in enumerate(hero_cards):
            card = tk.Frame(hero, bg='white', bd=0, highlightthickness=1, highlightbackground='#dbeafe')
            card.grid(row=0, column=idx, sticky='nsew', padx=6)
            hero.columnconfigure(idx, weight=1)
            tk.Label(card, text=label, bg='white', fg='#64748b', font=('Segoe UI', 10)).pack(anchor='w', padx=16, pady=(14, 4))
            var = tk.StringVar(value='-')
            tk.Label(card, textvariable=var, bg='white', fg=CARD_COLORS[color], font=('Segoe UI', 20, 'bold')).pack(anchor='w', padx=16, pady=(0, 12))
            self.hero_vars[key] = var

        summary = ttk.LabelFrame(main, text='KPIs principais', style='Surface.TLabelframe', padding=10)
        summary.pack(fill='x', pady=(0, 10))
        cards = [
            ('programs', 'Programas detectados'),
            ('completed', 'Programas finalizados'),
            ('arcs', 'Aberturas de arco'),
            ('arc_time', 'Tempo total de arco'),
            ('errors', 'Erros detectados'),
            ('warnings', 'Warnings'),
        ]
        for column, (key, label) in enumerate(cards):
            frame = ttk.Frame(summary, style='Surface.TFrame', padding=8)
            frame.grid(row=0, column=column, sticky='nsew', padx=6)
            summary.columnconfigure(column, weight=1)
            ttk.Label(frame, text=label, style='CardLabel.TLabel').pack(anchor='w')
            variable = tk.StringVar(value='-')
            ttk.Label(frame, textvariable=variable, style='CardValue.TLabel').pack(anchor='w', pady=(4, 0))
            self.summary_vars[key] = variable

        self.file_label = ttk.Label(main, text='Nenhum arquivo carregado', style='Subtitle.TLabel')
        self.file_label.pack(anchor='w', pady=(0, 10))

        notebook = ttk.Notebook(main)
        notebook.pack(fill='both', expand=True)

        self.tab_overview = ttk.Frame(notebook, style='App.TFrame', padding=4)
        self.tab_charts = ttk.Frame(notebook, style='App.TFrame', padding=4)
        self.tab_programs = ttk.Frame(notebook, style='App.TFrame', padding=4)
        self.tab_events = ttk.Frame(notebook, style='App.TFrame', padding=4)
        notebook.add(self.tab_overview, text='Visão geral')
        notebook.add(self.tab_charts, text='Gráficos')
        notebook.add(self.tab_programs, text='Programas')
        notebook.add(self.tab_events, text='Eventos & ativos')

        self._build_overview_tab()
        self._build_charts_tab()
        self._build_programs_tab()
        self._build_events_tab()

    def _build_overview_tab(self) -> None:
        top = ttk.Frame(self.tab_overview, style='App.TFrame')
        top.pack(fill='both', expand=True)

        left = ttk.LabelFrame(top, text='Radar operacional', style='Surface.TLabelframe', padding=10)
        left.pack(side='left', fill='both', expand=True, padx=(0, 6))
        ttk.Label(left, text='Resumo executivo do log analisado', style='SectionTitle.TLabel').pack(anchor='w', pady=(0, 8))
        self.executive_text = tk.Text(left, height=18, wrap='word', bg='white', fg='#0f172a', relief='flat', font=('Segoe UI', 10))
        self.executive_text.pack(fill='both', expand=True)

        right = ttk.LabelFrame(top, text='Registros recomendados', style='Surface.TLabelframe', padding=10)
        right.pack(side='left', fill='both', expand=True, padx=(6, 0))
        self.recommendation_table = ttk.Treeview(right, columns=('prioridade', 'titulo', 'metrica'), show='headings', height=10)
        self.recommendation_table.heading('prioridade', text='Prioridade')
        self.recommendation_table.heading('titulo', text='Registro sugerido')
        self.recommendation_table.heading('metrica', text='Base no log')
        self.recommendation_table.column('prioridade', width=110, anchor='center')
        self.recommendation_table.column('titulo', width=280, anchor='w')
        self.recommendation_table.column('metrica', width=350, anchor='w')
        self.recommendation_table.pack(fill='both', expand=True)
        self.recommendation_table.bind('<<TreeviewSelect>>', self.on_recommendation_select)

        self.recommendation_details = tk.Text(right, height=8, wrap='word', bg='white', fg='#0f172a', relief='flat', font=('Segoe UI', 10))
        self.recommendation_details.pack(fill='x', pady=(10, 0))

    def _build_charts_tab(self) -> None:
        frame = ttk.Frame(self.tab_charts, style='App.TFrame')
        frame.pack(fill='both', expand=True)
        left = ttk.LabelFrame(frame, text='Sessões e eficiência', style='Surface.TLabelframe', padding=10)
        left.pack(side='left', fill='both', expand=True, padx=(0, 6))
        right = ttk.LabelFrame(frame, text='Distribuições e score', style='Surface.TLabelframe', padding=10)
        right.pack(side='left', fill='both', expand=True, padx=(6, 0))

        self.charts_left = DashboardCharts(left)
        self.charts_left.session_canvas.config(height=250)
        self.charts_left.state_canvas.config(height=250)
        self.charts_left.pack()

        self.charts_right = DashboardCharts(right)
        self.charts_right.session_canvas.destroy()
        self.charts_right.state_canvas.destroy()
        self.charts_right.session_canvas = self.charts_right.category_canvas
        self.charts_right.state_canvas = self.charts_right.health_canvas
        self.charts_right.category_canvas = tk.Canvas(right, height=250, bg='white', highlightthickness=0)
        self.charts_right.health_canvas = tk.Canvas(right, height=250, bg='white', highlightthickness=0)
        self.charts_right.session_canvas.pack(fill='x', padx=4, pady=(0, 10))
        self.charts_right.state_canvas.pack(fill='x', padx=4, pady=(0, 10))
        self.charts_right.category_canvas.pack_forget()
        self.charts_right.health_canvas.pack_forget()

    def _build_programs_tab(self) -> None:
        frame = ttk.Frame(self.tab_programs, style='App.TFrame')
        frame.pack(fill='both', expand=True)

        left = ttk.LabelFrame(frame, text='Programas detectados', style='Surface.TLabelframe', padding=10)
        left.pack(side='left', fill='both', expand=True, padx=(0, 6))
        center = ttk.LabelFrame(frame, text='Detalhamento da sessão', style='Surface.TLabelframe', padding=10)
        center.pack(side='left', fill='both', expand=True, padx=6)
        right = ttk.LabelFrame(frame, text='Eventos da sessão', style='Surface.TLabelframe', padding=10)
        right.pack(side='left', fill='both', expand=True, padx=(6, 0))

        self.session_table = ttk.Treeview(
            left,
            columns=('idx', 'inicio', 'duracao', 'modo', 'arcos', 'eficiencia', 'status', 'erros'),
            show='headings',
            height=18,
        )
        for key, text, width in [
            ('idx', 'Programa', 80),
            ('inicio', 'Início', 150),
            ('duracao', 'Duração', 110),
            ('modo', 'Modo', 90),
            ('arcos', 'Arcos', 70),
            ('eficiencia', 'Eficiência', 100),
            ('status', 'Status', 110),
            ('erros', 'Erros', 70),
        ]:
            self.session_table.heading(key, text=text)
            self.session_table.column(key, width=width, anchor='center')
        self.session_table.pack(fill='both', expand=True)
        self.session_table.bind('<<TreeviewSelect>>', self.on_session_select)

        self.details = tk.Text(center, wrap='word', bg='white', fg='#0f172a', relief='flat', font=('Segoe UI', 10))
        self.details.pack(fill='both', expand=True)

        self.session_event_table = ttk.Treeview(
            right,
            columns=('hora', 'categoria', 'mensagem'),
            show='headings',
            height=18,
        )
        self.session_event_table.heading('hora', text='Horário')
        self.session_event_table.heading('categoria', text='Categoria')
        self.session_event_table.heading('mensagem', text='Mensagem')
        self.session_event_table.column('hora', width=90, anchor='center')
        self.session_event_table.column('categoria', width=130, anchor='center')
        self.session_event_table.column('mensagem', width=350, anchor='w')
        self.session_event_table.pack(fill='both', expand=True)

    def _build_events_tab(self) -> None:
        frame = ttk.Frame(self.tab_events, style='App.TFrame')
        frame.pack(fill='both', expand=True)

        upper = ttk.Frame(frame, style='App.TFrame')
        upper.pack(fill='both', expand=True, pady=(0, 8))
        lower = ttk.Frame(frame, style='App.TFrame')
        lower.pack(fill='both', expand=True)

        incidents = ttk.LabelFrame(upper, text='Incidentes / erros', style='Surface.TLabelframe', padding=10)
        incidents.pack(side='left', fill='both', expand=True, padx=(0, 6))
        services = ttk.LabelFrame(upper, text='Serviços e estados', style='Surface.TLabelframe', padding=10)
        services.pack(side='left', fill='both', expand=True, padx=(6, 0))
        versions = ttk.LabelFrame(lower, text='Inventário e versões', style='Surface.TLabelframe', padding=10)
        versions.pack(side='left', fill='both', expand=True, padx=(0, 6))
        topics = ttk.LabelFrame(lower, text='Top tópicos / módulos', style='Surface.TLabelframe', padding=10)
        topics.pack(side='left', fill='both', expand=True, padx=(6, 0))

        self.error_table = ttk.Treeview(incidents, columns=('hora', 'origem', 'categoria', 'mensagem'), show='headings', height=12)
        for key, text, width, anchor in [
            ('hora', 'Horário', 135, 'center'),
            ('origem', 'Origem', 120, 'center'),
            ('categoria', 'Categoria', 140, 'center'),
            ('mensagem', 'Mensagem', 420, 'w'),
        ]:
            self.error_table.heading(key, text=text)
            self.error_table.column(key, width=width, anchor=anchor)
        self.error_table.pack(fill='both', expand=True)

        self.state_table = ttk.Treeview(services, columns=('hora', 'tipo', 'valor'), show='headings', height=12)
        for key, text, width in [('hora', 'Horário', 130), ('tipo', 'Tipo', 150), ('valor', 'Valor', 220)]:
            self.state_table.heading(key, text=text)
            self.state_table.column(key, width=width, anchor='center')
        self.state_table.pack(fill='both', expand=True)

        self.version_table = ttk.Treeview(versions, columns=('hora', 'item', 'versao'), show='headings', height=12)
        for key, text, width in [('hora', 'Horário', 120), ('item', 'Item', 260), ('versao', 'Versão / valor', 260)]:
            self.version_table.heading(key, text=text)
            self.version_table.column(key, width=width, anchor='w')
        self.version_table.pack(fill='both', expand=True)

        self.topic_table = ttk.Treeview(topics, columns=('tipo', 'nome', 'volume'), show='headings', height=12)
        for key, text, width in [('tipo', 'Tipo', 120), ('nome', 'Nome', 290), ('volume', 'Volume', 90)]:
            self.topic_table.heading(key, text=text)
            self.topic_table.column(key, width=width, anchor='center' if key != 'nome' else 'w')
        self.topic_table.pack(fill='both', expand=True)

    def pick_file(self) -> None:
        selected = filedialog.askopenfilename(
            title='Selecione o arquivo de log',
            filetypes=[('Arquivos de log', '*.txt *.log'), ('Todos os arquivos', '*.*')],
        )
        if selected:
            self.load_file(selected)

    def load_file(self, path: str) -> None:
        try:
            records = LogParser(path).parse()
            self.analysis = MonitorAnalyzer(records, path).analyze()
        except Exception as exc:
            messagebox.showerror('Falha ao analisar o log', str(exc))
            return

        self.file_label.config(text=f'Arquivo carregado: {path}')
        self._refresh_summary()
        self._refresh_overview()
        self._refresh_sessions()
        self._refresh_errors()
        self._refresh_states()
        self._refresh_versions_and_topics()
        self._refresh_charts()
        self.details.delete('1.0', 'end')
        self.details.insert('end', 'Selecione um programa para explorar eventos, erros, eficiência e sequência operacional.')
        self.recommendation_details.delete('1.0', 'end')
        self.recommendation_details.insert('end', 'Selecione um registro sugerido para ver o racional baseado no log.')

    def export_summary(self) -> None:
        if not self.analysis:
            messagebox.showinfo('Sem análise', 'Carregue um arquivo antes de exportar o resumo.')
            return

        destination = filedialog.asksaveasfilename(
            title='Salvar resumo em JSON',
            defaultextension='.json',
            initialfile=f'{self.analysis.source_path.stem}_dashboard_resumo.json',
            filetypes=[('JSON', '*.json')],
        )
        if not destination:
            return

        payload = build_summary_payload(self.analysis)
        Path(destination).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        messagebox.showinfo('Exportação concluída', f'Resumo salvo em {destination}')

    def _refresh_summary(self) -> None:
        assert self.analysis is not None
        analysis = self.analysis
        self.summary_vars['programs'].set(str(analysis.total_programs))
        self.summary_vars['completed'].set(str(analysis.completed_programs))
        self.summary_vars['arcs'].set(str(analysis.total_arc_openings))
        self.summary_vars['arc_time'].set(format_timedelta(analysis.total_arc_time))
        self.summary_vars['errors'].set(str(analysis.total_errors))
        self.summary_vars['warnings'].set(str(analysis.total_warnings))

        self.hero_vars['health'].set(f'{analysis.health_score}/100')
        self.hero_vars['runtime'].set(format_timedelta(analysis.total_runtime))
        self.hero_vars['efficiency'].set(f'{analysis.arc_efficiency * 100:.1f}%')
        services_online = sum(1 for status in analysis.service_status_summary.values() if status == 'Online')
        services_total = max(len(analysis.service_status_summary), 1)
        self.hero_vars['services'].set(f'{services_online}/{services_total}')

    def _refresh_overview(self) -> None:
        assert self.analysis is not None
        analysis = self.analysis
        executive_lines = [
            'Resumo executivo',
            f'- Janela analisada: {analysis.records[0].timestamp:%d/%m/%Y %H:%M:%S} até {analysis.records[-1].timestamp:%d/%m/%Y %H:%M:%S}.',
            f'- {analysis.total_programs} programas identificados, sendo {analysis.completed_programs} finalizados.',
            f'- Tempo total de arco: {format_timedelta(analysis.total_arc_time)} com eficiência média de {analysis.arc_efficiency * 100:.1f}%.',
            f'- {analysis.total_errors} erros e {analysis.total_warnings} warnings foram encontrados no período.',
            f'- Categorias mais presentes: {format_counter(analysis.category_counts, 4)}.',
            f'- Principais módulos de origem: {format_counter(analysis.source_context_counts, 4)}.',
            f'- Serviços monitorados: {", ".join(f"{k}={v}" for k, v in analysis.service_status_summary.items()) or "nenhum status disponível"}.',
            '',
            'Leituras de negócio',
        ]
        if analysis.sessions:
            longest = max(analysis.sessions, key=lambda item: item.duration)
            best_efficiency = max(analysis.sessions, key=lambda item: item.arc_efficiency)
            executive_lines.extend([
                f'- Programa mais longo: #{longest.index} com {format_timedelta(longest.duration)}.',
                f'- Programa com melhor uso de arco: #{best_efficiency.index} com {best_efficiency.arc_efficiency * 100:.1f}% de eficiência.',
            ])
        if analysis.category_counts.get('Colisão', 0):
            executive_lines.append('- O log contém evento de colisão, então vale destacar incidentes de segurança no dashboard operacional.')
        if analysis.category_counts.get('Fieldbus / CAN', 0):
            executive_lines.append('- Há sinais de Fieldbus/CAN no log, sugerindo acompanhamento técnico separado para manutenção.')
        if analysis.version_inventory:
            executive_lines.append('- O log traz dados de versão e inventário suficientes para uma trilha de rastreabilidade técnica.')

        self.executive_text.delete('1.0', 'end')
        self.executive_text.insert('end', '\n'.join(executive_lines))

        self.recommendation_table.delete(*self.recommendation_table.get_children())
        for index, item in enumerate(analysis.recommendations, start=1):
            self.recommendation_table.insert('', 'end', iid=str(index), values=(item.priority, item.title, item.metric))

    def _refresh_sessions(self) -> None:
        assert self.analysis is not None
        self.session_table.delete(*self.session_table.get_children())
        for session in self.analysis.sessions:
            self.session_table.insert(
                '',
                'end',
                iid=str(session.index),
                values=(
                    session.index,
                    session.start.strftime('%H:%M:%S'),
                    format_timedelta(session.duration),
                    session.cut_mode or '-',
                    session.arc_openings,
                    f'{session.arc_efficiency * 100:.1f}%',
                    session.status,
                    len(session.errors),
                ),
            )

    def _refresh_errors(self) -> None:
        assert self.analysis is not None
        self.error_table.delete(*self.error_table.get_children())
        rows: list[tuple[datetime, str, str, str]] = []
        for session in self.analysis.sessions:
            for error in session.errors:
                rows.append((error.timestamp, f'Prog. {session.index}', self._categorize(error), error.message))
        for error in self.analysis.unassigned_errors:
            rows.append((error.timestamp, 'Fora prog.', self._categorize(error), error.message))
        rows.sort(key=lambda item: item[0])
        for index, (timestamp, origin, category, message) in enumerate(rows, start=1):
            self.error_table.insert('', 'end', iid=str(index), values=(timestamp.strftime('%Y-%m-%d %H:%M:%S'), origin, category, message))

    def _refresh_states(self) -> None:
        assert self.analysis is not None
        self.state_table.delete(*self.state_table.get_children())
        rows: list[tuple[datetime, str, str]] = []
        for event in self.analysis.service_status_history:
            rows.append((event.timestamp, 'Serviço', f'{event.service}: {event.status}'))
        for timestamp, state in self.analysis.state_history[-30:]:
            rows.append((timestamp, 'CNC State', state))
        rows.sort(key=lambda item: item[0])
        for index, (timestamp, row_type, value) in enumerate(rows, start=1):
            self.state_table.insert('', 'end', iid=str(index), values=(timestamp.strftime('%Y-%m-%d %H:%M:%S'), row_type, value))

    def _refresh_versions_and_topics(self) -> None:
        assert self.analysis is not None
        self.version_table.delete(*self.version_table.get_children())
        dedup: set[tuple[str, str]] = set()
        for index, entry in enumerate(self.analysis.version_inventory, start=1):
            key = (entry.label, entry.value)
            if key in dedup:
                continue
            dedup.add(key)
            self.version_table.insert('', 'end', iid=str(index), values=(entry.timestamp.strftime('%H:%M:%S'), entry.label, entry.value))
            if len(dedup) >= 25:
                break

        self.topic_table.delete(*self.topic_table.get_children())
        row_index = 1
        for name, count in self.analysis.topic_counts.most_common(8):
            self.topic_table.insert('', 'end', iid=f'topic-{row_index}', values=('Tópico', name, count))
            row_index += 1
        for name, count in self.analysis.source_context_counts.most_common(8):
            self.topic_table.insert('', 'end', iid=f'ctx-{row_index}', values=('Módulo', name, count))
            row_index += 1

    def _refresh_charts(self) -> None:
        assert self.analysis is not None
        session_labels = [f'P{session.index}' for session in self.analysis.sessions]
        session_values = [session.duration.total_seconds() / 60 for session in self.analysis.sessions]
        efficiency_values = [session.arc_efficiency * 100 for session in self.analysis.sessions]
        state_items = sorted(self.analysis.state_duration_seconds.items(), key=lambda item: item[1], reverse=True)[:8]
        category_items = self.analysis.category_counts.most_common(8)
        self._draw_dual_bar_chart(
            self.charts_left.session_canvas,
            'Duração vs eficiência por programa',
            session_labels,
            session_values,
            efficiency_values,
            '#1d4ed8',
            '#10b981',
            'min',
            '%',
        )
        self._draw_single_bar_chart(
            self.charts_left.state_canvas,
            'Tempo estimado por estado CNC',
            [label for label, _ in state_items],
            [value / 60 for _, value in state_items],
            '#7c3aed',
            'min',
        )
        self._draw_single_bar_chart(
            self.charts_right.session_canvas,
            'Distribuição por categoria de evento',
            [label for label, _ in category_items],
            [float(value) for _, value in category_items],
            '#f97316',
            'evt',
        )
        self._draw_health_panel(self.charts_right.state_canvas, self.analysis)

    def _draw_single_bar_chart(self, canvas: tk.Canvas, title: str, labels: list[str], values: list[float], color: str, unit: str) -> None:
        self._clear_canvas(canvas)
        width = max(canvas.winfo_width(), 620)
        height = int(canvas.cget('height'))
        canvas.config(scrollregion=(0, 0, width, height))
        canvas.create_text(18, 20, text=title, anchor='w', fill='#0f172a', font=('Segoe UI', 12, 'bold'))
        if not labels:
            canvas.create_text(width / 2, height / 2, text='Sem dados suficientes para este gráfico.', fill='#64748b', font=('Segoe UI', 11))
            return
        left, top, bottom = 48, 52, height - 36
        usable_width = width - left - 40
        step = usable_width / max(len(labels), 1)
        max_value = max(values) if any(values) else 1.0
        for idx, (label, value) in enumerate(zip(labels, values)):
            x0 = left + idx * step + 8
            x1 = x0 + step - 22
            y1 = bottom
            ratio = 0 if max_value == 0 else value / max_value
            y0 = y1 - max(ratio * (bottom - top), 4)
            canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline='')
            canvas.create_text((x0 + x1) / 2, y0 - 10, text=f'{value:.1f} {unit}', fill='#334155', font=('Segoe UI', 9))
            canvas.create_text((x0 + x1) / 2, y1 + 14, text=label[:16], fill='#334155', font=('Segoe UI', 9))
        canvas.create_line(left, bottom, width - 24, bottom, fill='#cbd5e1')

    def _draw_dual_bar_chart(
        self,
        canvas: tk.Canvas,
        title: str,
        labels: list[str],
        series_a: list[float],
        series_b: list[float],
        color_a: str,
        color_b: str,
        unit_a: str,
        unit_b: str,
    ) -> None:
        self._clear_canvas(canvas)
        width = max(canvas.winfo_width(), 620)
        height = int(canvas.cget('height'))
        canvas.create_text(18, 20, text=title, anchor='w', fill='#0f172a', font=('Segoe UI', 12, 'bold'))
        canvas.create_rectangle(width - 220, 10, width - 206, 24, fill=color_a, outline='')
        canvas.create_text(width - 200, 17, text=f'Duração ({unit_a})', anchor='w', fill='#334155', font=('Segoe UI', 9))
        canvas.create_rectangle(width - 110, 10, width - 96, 24, fill=color_b, outline='')
        canvas.create_text(width - 90, 17, text=f'Eficiência ({unit_b})', anchor='w', fill='#334155', font=('Segoe UI', 9))
        if not labels:
            canvas.create_text(width / 2, height / 2, text='Sem sessões para comparar.', fill='#64748b', font=('Segoe UI', 11))
            return
        left, top, bottom = 48, 52, height - 36
        usable_width = width - left - 40
        step = usable_width / max(len(labels), 1)
        max_a = max(series_a) if any(series_a) else 1.0
        max_b = max(series_b) if any(series_b) else 1.0
        for idx, label in enumerate(labels):
            group_left = left + idx * step + 12
            bar_width = max((step - 28) / 2, 12)
            a_x0 = group_left
            a_x1 = group_left + bar_width
            b_x0 = a_x1 + 6
            b_x1 = b_x0 + bar_width
            a_ratio = 0 if max_a == 0 else series_a[idx] / max_a
            b_ratio = 0 if max_b == 0 else series_b[idx] / max_b
            a_y0 = bottom - max(a_ratio * (bottom - top), 4)
            b_y0 = bottom - max(b_ratio * (bottom - top), 4)
            canvas.create_rectangle(a_x0, a_y0, a_x1, bottom, fill=color_a, outline='')
            canvas.create_rectangle(b_x0, b_y0, b_x1, bottom, fill=color_b, outline='')
            canvas.create_text((a_x0 + a_x1) / 2, a_y0 - 10, text=f'{series_a[idx]:.1f}', fill='#334155', font=('Segoe UI', 8))
            canvas.create_text((b_x0 + b_x1) / 2, b_y0 - 10, text=f'{series_b[idx]:.1f}', fill='#334155', font=('Segoe UI', 8))
            canvas.create_text((a_x0 + b_x1) / 2, bottom + 14, text=label, fill='#334155', font=('Segoe UI', 9))
        canvas.create_line(left, bottom, width - 24, bottom, fill='#cbd5e1')

    def _draw_health_panel(self, canvas: tk.Canvas, analysis: LogAnalysis) -> None:
        self._clear_canvas(canvas)
        width = max(canvas.winfo_width(), 620)
        height = int(canvas.cget('height'))
        score = analysis.health_score
        canvas.create_text(18, 20, text='Score operacional e sinais de atenção', anchor='w', fill='#0f172a', font=('Segoe UI', 12, 'bold'))
        center_x, center_y = 120, 130
        radius = 70
        canvas.create_oval(center_x - radius, center_y - radius, center_x + radius, center_y + radius, outline='#e2e8f0', width=16)
        extent = 360 * score / 100
        color = '#10b981' if score >= 80 else '#f59e0b' if score >= 60 else '#ef4444'
        canvas.create_arc(
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
            start=90,
            extent=-extent,
            style='arc',
            outline=color,
            width=16,
        )
        canvas.create_text(center_x, center_y - 10, text=str(score), fill='#0f172a', font=('Segoe UI', 28, 'bold'))
        canvas.create_text(center_x, center_y + 18, text='de 100', fill='#64748b', font=('Segoe UI', 10))

        signals = [
            ('Erros', analysis.total_errors, '#dc2626'),
            ('Warnings', analysis.total_warnings, '#d97706'),
            ('Colisões', analysis.category_counts.get('Colisão', 0), '#7c2d12'),
            ('Fieldbus/CAN', analysis.category_counts.get('Fieldbus / CAN', 0), '#7c3aed'),
            ('Eficiência', f'{analysis.arc_efficiency * 100:.1f}%', '#059669'),
        ]
        start_x = 250
        for idx, (label, value, dot_color) in enumerate(signals):
            y = 60 + idx * 34
            canvas.create_oval(start_x, y, start_x + 10, y + 10, fill=dot_color, outline='')
            canvas.create_text(start_x + 20, y + 5, text=label, anchor='w', fill='#334155', font=('Segoe UI', 10, 'bold'))
            canvas.create_text(start_x + 150, y + 5, text=str(value), anchor='w', fill='#0f172a', font=('Segoe UI', 10))

    def _clear_canvas(self, canvas: tk.Canvas) -> None:
        canvas.delete('all')
        canvas.update_idletasks()

    def on_recommendation_select(self, _event: object) -> None:
        if not self.analysis:
            return
        selection = self.recommendation_table.selection()
        if not selection:
            return
        item = self.analysis.recommendations[int(selection[0]) - 1]
        lines = [
            item.title,
            f'Prioridade: {item.priority}',
            f'Métrica gatilho: {item.metric}',
            '',
            item.description,
        ]
        self.recommendation_details.delete('1.0', 'end')
        self.recommendation_details.insert('end', '\n'.join(lines))

    def on_session_select(self, _event: object) -> None:
        if not self.analysis:
            return
        selection = self.session_table.selection()
        if not selection:
            return
        index = int(selection[0])
        session = next((item for item in self.analysis.sessions if item.index == index), None)
        if not session:
            return

        lines = [
            f'Programa {session.index}',
            f'Início: {session.start:%Y-%m-%d %H:%M:%S}',
            f'Fim: {session.end:%Y-%m-%d %H:%M:%S}' if session.end else 'Fim: em andamento',
            f'Duração total: {format_timedelta(session.duration)}',
            f'Modo de corte: {session.cut_mode or "não identificado"}',
            f'Aberturas de arco: {session.arc_openings}',
            f'Tempo total de arco: {format_timedelta(session.total_arc_time)}',
            f'Eficiência estimada de arco: {session.arc_efficiency * 100:.1f}%',
            f'Estados percorridos: {", ".join(session.states) if session.states else "sem estados detectados"}',
            f'Eventos da sessão: {session.event_count}',
            '',
            'Erros nesta sessão:',
        ]
        if session.errors:
            for record in session.errors:
                lines.append(f'- {record.timestamp:%H:%M:%S} | {record.message}')
        else:
            lines.append('- Nenhum erro detectado.')

        lines.extend(['', 'Aberturas de arco:'])
        if session.arc_events:
            for arc_index, arc in enumerate(session.arc_events, start=1):
                arc_end = arc.end.strftime('%H:%M:%S') if arc.end else 'aberto'
                lines.append(f'- Arco {arc_index}: {arc.start:%H:%M:%S} -> {arc_end} ({format_timedelta(arc.duration)})')
        else:
            lines.append('- Nenhum evento de arco encontrado.')

        self.details.delete('1.0', 'end')
        self.details.insert('end', '\n'.join(lines))

        self.session_event_table.delete(*self.session_event_table.get_children())
        for row_index, event in enumerate(session.events[-40:], start=1):
            self.session_event_table.insert(
                '',
                'end',
                iid=str(row_index),
                values=(event.timestamp.strftime('%H:%M:%S'), self._categorize(event), event.message[:180]),
            )

    def _categorize(self, record: LogRecord) -> str:
        for category, patterns in CATEGORY_RULES:
            if any(pattern.search(record.message) or pattern.search(record.topic) for pattern in patterns):
                return category
        return 'Erros diversos' if any(pattern.search(record.message) for pattern in ERROR_PATTERNS) else 'Operação geral'


def build_summary_payload(analysis: LogAnalysis) -> dict[str, Any]:
    error_counter = Counter()
    for session in analysis.sessions:
        error_counter.update(record.message for record in session.errors)
    error_counter.update(record.message for record in analysis.unassigned_errors)

    return {
        'arquivo': str(analysis.source_path),
        'resumo': {
            'programas_detectados': analysis.total_programs,
            'programas_finalizados': analysis.completed_programs,
            'aberturas_de_arco': analysis.total_arc_openings,
            'tempo_total_de_arco': format_timedelta(analysis.total_arc_time),
            'eficiencia_media_de_arco': round(analysis.arc_efficiency * 100, 2),
            'erros_detectados': analysis.total_errors,
            'warnings_detectados': analysis.total_warnings,
            'janela_analisada': format_timedelta(analysis.total_runtime),
            'score_operacional': analysis.health_score,
        },
        'servicos': analysis.service_status_summary,
        'estados_cnc': [
            {'estado': state, 'duracao_estimada_segundos': round(seconds, 1)}
            for state, seconds in sorted(analysis.state_duration_seconds.items(), key=lambda item: item[1], reverse=True)
        ],
        'programas': [
            {
                'programa': session.index,
                'inicio': session.start.isoformat(sep=' '),
                'fim': session.end.isoformat(sep=' ') if session.end else None,
                'duracao': format_timedelta(session.duration),
                'modo_corte': session.cut_mode,
                'aberturas_de_arco': session.arc_openings,
                'tempo_total_de_arco': format_timedelta(session.total_arc_time),
                'eficiencia_percentual': round(session.arc_efficiency * 100, 2),
                'estados': session.states,
                'eventos': session.event_count,
                'erros': [record.message for record in session.errors],
            }
            for session in analysis.sessions
        ],
        'registros_recomendados': [
            {
                'titulo': item.title,
                'prioridade': item.priority,
                'metrica': item.metric,
                'descricao': item.description,
            }
            for item in analysis.recommendations
        ],
        'top_categorias': [{'categoria': category, 'ocorrencias': count} for category, count in analysis.category_counts.most_common(10)],
        'top_origens': [{'origem': name, 'ocorrencias': count} for name, count in analysis.source_context_counts.most_common(10)],
        'top_erros': [{'mensagem': message, 'ocorrencias': count} for message, count in error_counter.most_common(10)],
        'inventario_versoes': [
            {'item': entry.label, 'valor': entry.value, 'horario': entry.timestamp.isoformat(sep=' ')}
            for entry in analysis.version_inventory[:50]
        ],
    }


def format_timedelta(delta: timedelta) -> str:
    total_seconds = max(int(delta.total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def format_counter(counter: Counter[str], limit: int) -> str:
    if not counter:
        return 'sem dados'
    return ', '.join(f'{name} ({count})' for name, count in counter.most_common(limit))


def print_cli_summary(analysis: LogAnalysis) -> None:
    payload = build_summary_payload(analysis)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description='Monitor de corte para logs Phoenix.')
    parser.add_argument('logfile', nargs='?', help='Arquivo de log a ser analisado.')
    parser.add_argument('--summary', action='store_true', help='Imprime o resumo JSON no terminal e encerra.')
    args = parser.parse_args()

    if args.summary:
        if not args.logfile:
            raise SystemExit('Informe o caminho do log ao usar --summary.')
        records = LogParser(args.logfile).parse()
        analysis = MonitorAnalyzer(records, args.logfile).analyze()
        print_cli_summary(analysis)
        return

    root = tk.Tk()
    MonitorApp(root, initial_path=args.logfile)
    root.mainloop()


if __name__ == '__main__':
    main()
