import argparse
import importlib.util
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

HAS_QT = importlib.util.find_spec('PySide6') is not None

if HAS_QT:
    from PySide6.QtCore import QEasingCurve, Property, QPropertyAnimation, QRect, Qt
    from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QFileDialog,
        QFrame,
        QGraphicsDropShadowEffect,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QSizePolicy,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

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
APP_STYLESHEET = """
QWidget {
    background: #07111f;
    color: #e2e8f0;
    font-family: 'Segoe UI', 'Inter', sans-serif;
}
QMainWindow {
    background: #050b16;
}
QFrame#HeroPanel, QFrame#GlassCard, QFrame#GaugeCard {
    background: rgba(15, 23, 42, 0.92);
    border: 1px solid rgba(148, 163, 184, 0.18);
    border-radius: 24px;
}
QFrame#StatCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(15, 23, 42, 0.98),
        stop:1 rgba(30, 41, 59, 0.92));
    border: 1px solid rgba(96, 165, 250, 0.16);
    border-radius: 20px;
}
QFrame#AccentCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(37, 99, 235, 0.28),
        stop:1 rgba(56, 189, 248, 0.12));
    border: 1px solid rgba(96, 165, 250, 0.24);
    border-radius: 20px;
}
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2563eb, stop:1 #38bdf8);
    color: white;
    border: none;
    border-radius: 14px;
    padding: 12px 18px;
    font-weight: 600;
}
QPushButton:hover { background: #3b82f6; }
QPushButton:pressed { background: #1d4ed8; }
QTabWidget::pane {
    border: 1px solid rgba(148, 163, 184, 0.14);
    border-radius: 20px;
    top: -1px;
    background: rgba(15, 23, 42, 0.94);
}
QTabBar::tab {
    background: rgba(15, 23, 42, 0.55);
    border: 1px solid rgba(148, 163, 184, 0.12);
    padding: 12px 18px;
    margin-right: 8px;
    border-top-left-radius: 14px;
    border-top-right-radius: 14px;
    color: #94a3b8;
    font-weight: 600;
}
QTabBar::tab:selected {
    color: white;
    background: rgba(37, 99, 235, 0.26);
    border-color: rgba(96, 165, 250, 0.28);
}
QTableWidget {
    background: transparent;
    alternate-background-color: rgba(15, 23, 42, 0.48);
    gridline-color: rgba(148, 163, 184, 0.10);
    border: none;
    border-radius: 16px;
}
QHeaderView::section {
    background: rgba(15, 23, 42, 0.96);
    color: #cbd5e1;
    padding: 12px;
    border: none;
    border-bottom: 1px solid rgba(148, 163, 184, 0.12);
    font-weight: 700;
}
QTableWidget::item {
    padding: 8px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.08);
}
QTableWidget::item:selected {
    background: rgba(37, 99, 235, 0.35);
}
QPlainTextEdit {
    background: rgba(2, 6, 23, 0.88);
    border: 1px solid rgba(148, 163, 184, 0.12);
    border-radius: 16px;
    padding: 14px;
    selection-background-color: rgba(37, 99, 235, 0.50);
}
QScrollArea { border: none; }
"""
PRIORITY_COLORS = {
    'Crítica': '#ef4444',
    'Alta': '#f97316',
    'Média': '#38bdf8',
    'Baixa': '#10b981',
}
CATEGORY_COLORS = ['#38bdf8', '#22c55e', '#f97316', '#a855f7', '#ef4444', '#facc15', '#14b8a6', '#f472b6']


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
            payload = block[line_match.end('topic'):].strip().replace('\ufeff', '').replace('\x00', '').strip()
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
        return datetime.strptime(match.group(1), '%Y-%m-%d').date() if match else None

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
        timestamp_value = raw_data.get('Timestamp') if raw_data else None
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

            version_inventory.extend(self._extract_versions(record))

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
                active_session = ProgramSession(index=len(sessions) + 1, start=record.timestamp, cut_mode=current_cut_mode)
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
            state_duration_seconds=self._compute_state_durations(state_history),
            recommendations=self._build_recommendations(
                sessions=sessions,
                service_status_history=service_status_history,
                version_inventory=version_inventory,
                category_counts=category_counts,
                source_context_counts=source_context_counts,
                unassigned_errors=unassigned_errors,
            ),
        )

    def _detect_io(self, message: str) -> tuple[str, str, str, bool] | None:
        match = IO_RE.search(message)
        if not match:
            return None
        return match.group(1).title(), match.group(2), match.group(3), match.group(4).lower() == 'on'

    def _detect_state(self, message: str) -> str | None:
        match = STATE_RE.search(message)
        return match.group(1) if match else None

    def _detect_cut_mode(self, message: str) -> str | None:
        match = CUT_MODE_RE.search(message)
        return match.group(1) if match else None

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
            totals[state] += max((next_timestamp - timestamp).total_seconds(), 0.0)
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

        recommendations.extend([
            InsightItem(
                title='Registro de eficiência por programa',
                description='Registrar duração, tempo de arco, eficiência, modo de corte e estados percorridos para comparar produtividade entre execuções.',
                priority='Alta',
                metric=f'{low_efficiency_sessions} programas com eficiência abaixo de 30%.' if sessions else 'Sem programas no log.',
            ),
            InsightItem(
                title='Registro de disponibilidade dos serviços',
                description='Monitorar Phoenix, Managed e Rtos com eventos Online/Offline para detectar reinícios, quedas e sequência de startup da máquina.',
                priority='Alta' if any(status != 'Online' for status in latest_service_status.values()) else 'Média',
                metric=', '.join(f'{service}: {status}' for service, status in latest_service_status.items()) or 'Sem eventos de status.',
            ),
            InsightItem(
                title='Registro de incidentes de segurança',
                description='Consolidar torch collision, fast stop e paradas manuais em uma trilha única de incidentes com horário, origem e sessão impactada.',
                priority='Crítica' if collision_count or stop_count else 'Média',
                metric=f'{collision_count} colisões e {stop_count} eventos de parada/safety.',
            ),
            InsightItem(
                title='Registro de saúde Fieldbus / CAN',
                description='Separar falhas de drive, CAN e EtherCAT para análise de manutenção preditiva e correlação com erros de processo.',
                priority='Alta' if fieldbus_count else 'Média',
                metric=f'{fieldbus_count} eventos técnicos ligados a CAN/Fieldbus.',
            ),
            InsightItem(
                title='Registro de inventário técnico e versões',
                description='Guardar versões de Phoenix, branch, cutchart e softwares instalados para auditoria e rastreabilidade de atualização.',
                priority='Média',
                metric=f'{inventory_count} registros de versão/inventário extraídos do log.',
            ),
            InsightItem(
                title='Registro de erros fora de programa',
                description='Criar uma fila operacional para erros fora da execução de corte, pois indicam falhas de inicialização, comunicação ou hardware.',
                priority='Alta' if external_errors else 'Baixa',
                metric=f'{external_errors} erros ocorreram fora de uma sessão de programa.',
            ),
        ])
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


if HAS_QT:

    class GlassFrame(QFrame):
        def __init__(self, object_name: str = 'GlassCard'):
            super().__init__()
            self.setObjectName(object_name)
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(32)
            effect.setOffset(0, 14)
            effect.setColor(QColor(0, 0, 0, 110))
            self.setGraphicsEffect(effect)


    class AnimatedGauge(QWidget):
        def __init__(self, title: str):
            super().__init__()
            self.title = title
            self._value = 0.0
            self.subtitle = 'Sem análise carregada'
            self.setMinimumHeight(250)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        def getValue(self) -> float:
            return self._value

        def setValue(self, value: float) -> None:
            self._value = value
            self.update()

        value = Property(float, getValue, setValue)

        def animate_to(self, value: float, subtitle: str) -> None:
            self.subtitle = subtitle
            animation = QPropertyAnimation(self, b'value')
            animation.setDuration(900)
            animation.setStartValue(self._value)
            animation.setEndValue(max(0.0, min(value, 100.0)))
            animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.start()
            self._animation = animation

        def paintEvent(self, _event) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            rect = self.rect().adjusted(20, 16, -20, -16)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#0f172a'))
            painter.drawRoundedRect(rect, 22, 22)

            painter.setPen(QColor('#f8fafc'))
            painter.setFont(QFont('Segoe UI', 11, QFont.Bold))
            painter.drawText(rect.adjusted(18, 12, -18, 0), self.title)

            arc_rect = QRect(rect.left() + 34, rect.top() + 48, 160, 160)
            base_pen = QPen(QColor('#1e293b'), 16)
            base_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(base_pen)
            painter.drawArc(arc_rect, 0, 360 * 16)

            color = QColor('#10b981' if self._value >= 80 else '#f59e0b' if self._value >= 60 else '#ef4444')
            active_pen = QPen(color, 16)
            active_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(active_pen)
            painter.drawArc(arc_rect, 90 * 16, -int(self._value / 100 * 360 * 16))

            painter.setPen(QColor('#ffffff'))
            painter.setFont(QFont('Segoe UI', 26, QFont.Bold))
            painter.drawText(arc_rect, Qt.AlignCenter, f'{self._value:.0f}')

            painter.setPen(QColor('#94a3b8'))
            painter.setFont(QFont('Segoe UI', 10))
            painter.drawText(arc_rect.adjusted(0, 54, 0, 0), Qt.AlignCenter, 'score')

            subtitle_rect = QRect(rect.left() + 218, rect.top() + 64, rect.width() - 240, 120)
            painter.setPen(QColor('#e2e8f0'))
            painter.setFont(QFont('Segoe UI', 11, QFont.Bold))
            painter.drawText(subtitle_rect.adjusted(0, 0, 0, -62), Qt.TextWordWrap, self.subtitle)
            painter.setPen(QColor('#94a3b8'))
            painter.setFont(QFont('Segoe UI', 10))
            painter.drawText(subtitle_rect.adjusted(0, 50, 0, 0), Qt.TextWordWrap, 'Quanto mais perto de 100, melhor a saúde operacional do período analisado.')


    class MiniBarChart(QWidget):
        def __init__(self, title: str, unit: str):
            super().__init__()
            self.title = title
            self.unit = unit
            self.series: list[tuple[str, float, str]] = []
            self.setMinimumHeight(280)

        def set_series(self, series: list[tuple[str, float, str]]) -> None:
            self.series = series
            self.update()

        def paintEvent(self, _event) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
            rect = self.rect().adjusted(16, 16, -16, -16)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(15, 23, 42, 220))
            painter.drawRoundedRect(rect, 20, 20)

            painter.setPen(QColor('#f8fafc'))
            painter.setFont(QFont('Segoe UI', 11, QFont.Bold))
            painter.drawText(rect.adjusted(18, 14, -18, 0), self.title)
            if not self.series:
                painter.setPen(QColor('#94a3b8'))
                painter.setFont(QFont('Segoe UI', 10))
                painter.drawText(rect, Qt.AlignCenter, 'Sem dados suficientes para o gráfico.')
                return

            chart = rect.adjusted(28, 52, -22, -26)
            max_value = max(value for _, value, _ in self.series) or 1.0
            step = chart.width() / max(len(self.series), 1)
            bottom = chart.bottom() - 24
            height = chart.height() - 50

            axis_pen = QPen(QColor('#334155'), 1)
            painter.setPen(axis_pen)
            painter.drawLine(chart.left(), bottom, chart.right(), bottom)

            for index, (label, value, color) in enumerate(self.series):
                bar_width = max(step - 22, 18)
                x = chart.left() + index * step + 8
                ratio = 0 if max_value == 0 else value / max_value
                bar_height = max(8, ratio * height)
                y = bottom - bar_height
                grad = QLinearGradient(x, y, x, bottom)
                grad.setColorAt(0, QColor(color).lighter(125))
                grad.setColorAt(1, QColor(color))
                painter.setPen(Qt.NoPen)
                painter.setBrush(grad)
                painter.drawRoundedRect(x, y, bar_width, bar_height, 10, 10)
                painter.setPen(QColor('#cbd5e1'))
                painter.setFont(QFont('Segoe UI', 9, QFont.Bold))
                painter.drawText(QRect(int(x), int(y - 26), int(bar_width), 18), Qt.AlignCenter, f'{value:.1f} {self.unit}')
                painter.setFont(QFont('Segoe UI', 9))
                painter.setPen(QColor('#94a3b8'))
                painter.drawText(QRect(int(x - 6), int(bottom + 6), int(bar_width + 12), 32), Qt.AlignHCenter | Qt.TextWordWrap, label[:18])


    class DonutChart(QWidget):
        def __init__(self, title: str):
            super().__init__()
            self.title = title
            self.items: list[tuple[str, float, str]] = []
            self.setMinimumHeight(280)

        def set_items(self, items: list[tuple[str, float, str]]) -> None:
            self.items = items
            self.update()

        def paintEvent(self, _event) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            rect = self.rect().adjusted(16, 16, -16, -16)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(15, 23, 42, 220))
            painter.drawRoundedRect(rect, 20, 20)

            painter.setPen(QColor('#f8fafc'))
            painter.setFont(QFont('Segoe UI', 11, QFont.Bold))
            painter.drawText(rect.adjusted(18, 14, -18, 0), self.title)
            if not self.items:
                painter.setPen(QColor('#94a3b8'))
                painter.setFont(QFont('Segoe UI', 10))
                painter.drawText(rect, Qt.AlignCenter, 'Sem categorias suficientes para desenhar.')
                return

            total = sum(value for _, value, _ in self.items) or 1.0
            donut = QRect(rect.left() + 22, rect.top() + 56, 150, 150)
            start = 90 * 16
            for label, value, color in self.items:
                span = -int(value / total * 360 * 16)
                pen = QPen(QColor(color), 18)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
                painter.drawArc(donut, start, span)
                start += span

            center_path = QPainterPath()
            center_path.addEllipse(donut.adjusted(34, 34, -34, -34))
            painter.fillPath(center_path, QColor('#020617'))
            painter.setPen(QColor('#ffffff'))
            painter.setFont(QFont('Segoe UI', 20, QFont.Bold))
            painter.drawText(donut, Qt.AlignCenter, str(int(total)))
            painter.setPen(QColor('#94a3b8'))
            painter.setFont(QFont('Segoe UI', 9))
            painter.drawText(donut.adjusted(0, 54, 0, 0), Qt.AlignCenter, 'eventos')

            legend_x = donut.right() + 28
            for index, (label, value, color) in enumerate(self.items[:6]):
                y = rect.top() + 68 + index * 28
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(color))
                painter.drawEllipse(legend_x, y, 12, 12)
                painter.setPen(QColor('#e2e8f0'))
                painter.setFont(QFont('Segoe UI', 9, QFont.Bold))
                painter.drawText(legend_x + 20, y + 10, f'{label}')
                painter.setPen(QColor('#94a3b8'))
                painter.drawText(legend_x + 150, y + 10, f'{value:.0f}')


    class TrendLines(QWidget):
        def __init__(self, title: str):
            super().__init__()
            self.title = title
            self.primary: list[float] = []
            self.secondary: list[float] = []
            self.labels: list[str] = []
            self.setMinimumHeight(280)

        def set_data(self, labels: list[str], primary: list[float], secondary: list[float]) -> None:
            self.labels = labels
            self.primary = primary
            self.secondary = secondary
            self.update()

        def paintEvent(self, _event) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            rect = self.rect().adjusted(16, 16, -16, -16)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(15, 23, 42, 220))
            painter.drawRoundedRect(rect, 20, 20)

            painter.setPen(QColor('#f8fafc'))
            painter.setFont(QFont('Segoe UI', 11, QFont.Bold))
            painter.drawText(rect.adjusted(18, 14, -18, 0), self.title)

            if not self.labels:
                painter.setPen(QColor('#94a3b8'))
                painter.setFont(QFont('Segoe UI', 10))
                painter.drawText(rect, Qt.AlignCenter, 'Sem sessões suficientes para desenhar a tendência.')
                return

            chart = rect.adjusted(24, 54, -24, -28)
            bottom = chart.bottom() - 24
            left = chart.left() + 8
            right = chart.right()
            painter.setPen(QPen(QColor('#334155'), 1))
            for step in range(4):
                y = chart.top() + step * (chart.height() - 24) / 3
                painter.drawLine(left, int(y), right, int(y))

            all_values = self.primary + self.secondary
            max_value = max(all_values) if all_values else 1.0
            points_a = self._build_points(chart, self.primary, max_value)
            points_b = self._build_points(chart, self.secondary, max_value)

            self._draw_curve(painter, points_a, QColor('#38bdf8'))
            self._draw_curve(painter, points_b, QColor('#22c55e'))
            for idx, label in enumerate(self.labels):
                x = int(chart.left() + idx * (chart.width() / max(len(self.labels) - 1, 1))) if len(self.labels) > 1 else chart.center().x()
                painter.setPen(QColor('#94a3b8'))
                painter.setFont(QFont('Segoe UI', 8))
                painter.drawText(QRect(x - 24, bottom + 8, 48, 22), Qt.AlignCenter, label)

            painter.setPen(QColor('#38bdf8'))
            painter.setFont(QFont('Segoe UI', 9, QFont.Bold))
            painter.drawText(rect.right() - 148, rect.top() + 24, '● duração (min)')
            painter.setPen(QColor('#22c55e'))
            painter.drawText(rect.right() - 148, rect.top() + 46, '● eficiência (%)')

        def _build_points(self, rect: QRect, values: list[float], max_value: float) -> list[tuple[float, float]]:
            if not values:
                return []
            if len(values) == 1:
                return [(rect.center().x(), rect.bottom() - 24 - (values[0] / max_value * (rect.height() - 56)))]
            points = []
            for index, value in enumerate(values):
                x = rect.left() + index * (rect.width() / (len(values) - 1))
                y = rect.bottom() - 24 - (0 if max_value == 0 else value / max_value * (rect.height() - 56))
                points.append((x, y))
            return points

        def _draw_curve(self, painter: QPainter, points: list[tuple[float, float]], color: QColor) -> None:
            if not points:
                return
            pen = QPen(color, 3)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            painter.drawPath(path)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            for x, y in points:
                painter.drawEllipse(int(x - 4), int(y - 4), 8, 8)


    class StatCard(GlassFrame):
        def __init__(self, title: str, accent: str):
            super().__init__('StatCard')
            self.value_label = QLabel('—')
            self.value_label.setStyleSheet('font-size: 30px; font-weight: 800; color: white; background: transparent;')
            self.title_label = QLabel(title)
            self.title_label.setStyleSheet('font-size: 12px; color: #94a3b8; background: transparent;')
            self.caption_label = QLabel('Aguardando arquivo...')
            self.caption_label.setWordWrap(True)
            self.caption_label.setStyleSheet('font-size: 11px; color: #cbd5e1; background: transparent;')
            dot = QLabel()
            dot.setFixedSize(12, 12)
            dot.setStyleSheet(f'background: {accent}; border-radius: 6px;')

            layout = QVBoxLayout(self)
            layout.setContentsMargins(18, 16, 18, 16)
            layout.setSpacing(8)
            head = QHBoxLayout()
            head.addWidget(dot)
            head.addWidget(self.title_label)
            head.addStretch(1)
            layout.addLayout(head)
            layout.addWidget(self.value_label)
            layout.addWidget(self.caption_label)

        def update_content(self, value: str, caption: str) -> None:
            self.value_label.setText(value)
            self.caption_label.setText(caption)


    class MonitorMainWindow(QMainWindow):
        def __init__(self, initial_path: str | None = None):
            super().__init__()
            self.analysis: LogAnalysis | None = None
            self.setWindowTitle('APP Monitor Next | Phoenix Command Center')
            self.resize(1680, 1040)
            self.setStyleSheet(APP_STYLESHEET)
            self._build_ui()
            if initial_path:
                self.load_file(initial_path)

        def _build_ui(self) -> None:
            container = QWidget()
            self.setCentralWidget(container)
            root = QVBoxLayout(container)
            root.setContentsMargins(20, 20, 20, 20)
            root.setSpacing(18)

            hero = GlassFrame('HeroPanel')
            hero_layout = QHBoxLayout(hero)
            hero_layout.setContentsMargins(26, 26, 26, 26)
            hero_layout.setSpacing(18)

            info_col = QVBoxLayout()
            title = QLabel('APP Monitor • visual repaginado em Qt')
            title.setStyleSheet('font-size: 30px; font-weight: 800; color: white; background: transparent;')
            subtitle = QLabel('Nova cabine de comando com visual dark, painéis glassmorphism, gráficos customizados e base pronta para animações mais ricas.')
            subtitle.setWordWrap(True)
            subtitle.setStyleSheet('font-size: 13px; color: #94a3b8; background: transparent;')
            self.file_label = QLabel('Nenhum log carregado.')
            self.file_label.setWordWrap(True)
            self.file_label.setStyleSheet('font-size: 12px; color: #e2e8f0; background: transparent;')

            button_row = QHBoxLayout()
            open_button = QPushButton('Abrir log')
            open_button.clicked.connect(self.choose_file)
            export_button = QPushButton('Exportar JSON')
            export_button.clicked.connect(self.export_summary)
            button_row.addWidget(open_button)
            button_row.addWidget(export_button)
            button_row.addStretch(1)

            info_col.addWidget(title)
            info_col.addWidget(subtitle)
            info_col.addSpacing(10)
            info_col.addLayout(button_row)
            info_col.addWidget(self.file_label)
            hero_layout.addLayout(info_col, 3)

            accent = GlassFrame('AccentCard')
            accent_layout = QVBoxLayout(accent)
            accent_layout.setContentsMargins(22, 20, 22, 20)
            accent_layout.setSpacing(10)
            accent_tag = QLabel('Resumo instantâneo')
            accent_tag.setStyleSheet('font-size: 11px; color: #93c5fd; font-weight: 700; background: transparent;')
            self.hero_badge = QLabel('Pronto para analisar produção, estados e incidentes.')
            self.hero_badge.setWordWrap(True)
            self.hero_badge.setStyleSheet('font-size: 16px; font-weight: 700; color: white; background: transparent;')
            self.hero_meta = QLabel('Carregue um log para preencher as visões executiva, técnica e operacional.')
            self.hero_meta.setWordWrap(True)
            self.hero_meta.setStyleSheet('font-size: 11px; color: #cbd5e1; background: transparent;')
            accent_layout.addWidget(accent_tag)
            accent_layout.addWidget(self.hero_badge)
            accent_layout.addWidget(self.hero_meta)
            accent_layout.addStretch(1)
            hero_layout.addWidget(accent, 2)
            root.addWidget(hero)

            stat_grid = QGridLayout()
            stat_grid.setHorizontalSpacing(16)
            stat_grid.setVerticalSpacing(16)
            self.stat_cards = [
                StatCard('Programas detectados', '#38bdf8'),
                StatCard('Eficiência média', '#22c55e'),
                StatCard('Erros / alertas', '#f97316'),
                StatCard('Serviços online', '#a855f7'),
            ]
            for idx, card in enumerate(self.stat_cards):
                stat_grid.addWidget(card, 0, idx)
            root.addLayout(stat_grid)

            self.tabs = QTabWidget()
            root.addWidget(self.tabs, 1)

            self.overview_tab = self._build_overview_tab()
            self.sessions_tab = self._build_sessions_tab()
            self.alerts_tab = self._build_alerts_tab()
            self.deep_tab = self._build_deep_tab()

            self.tabs.addTab(self.overview_tab, 'Visão geral')
            self.tabs.addTab(self.sessions_tab, 'Programas')
            self.tabs.addTab(self.alerts_tab, 'Alertas e timeline')
            self.tabs.addTab(self.deep_tab, 'Inventário técnico')

        def _build_overview_tab(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(16)

            top = QHBoxLayout()
            gauge_card = GlassFrame('GaugeCard')
            gauge_layout = QVBoxLayout(gauge_card)
            gauge_layout.setContentsMargins(10, 10, 10, 10)
            self.gauge = AnimatedGauge('Pulse operacional')
            gauge_layout.addWidget(self.gauge)

            executive_card = GlassFrame()
            exec_layout = QVBoxLayout(executive_card)
            exec_layout.setContentsMargins(18, 18, 18, 18)
            exec_title = QLabel('Resumo executivo')
            exec_title.setStyleSheet('font-size: 16px; font-weight: 800; color: white; background: transparent;')
            self.executive_text = QPlainTextEdit()
            self.executive_text.setReadOnly(True)
            exec_layout.addWidget(exec_title)
            exec_layout.addWidget(self.executive_text)

            top.addWidget(gauge_card, 2)
            top.addWidget(executive_card, 3)
            layout.addLayout(top)

            chart_row = QHBoxLayout()
            self.trend_chart = TrendLines('Duração x eficiência por programa')
            self.state_chart = MiniBarChart('Tempo por estado CNC', 'min')
            self.category_chart = DonutChart('Mix de categorias')
            chart_row.addWidget(self.trend_chart, 2)
            chart_row.addWidget(self.state_chart, 2)
            chart_row.addWidget(self.category_chart, 2)
            layout.addLayout(chart_row)
            return page

        def _build_sessions_tab(self) -> QWidget:
            page = QWidget()
            layout = QHBoxLayout(page)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(16)

            session_card = GlassFrame()
            session_layout = QVBoxLayout(session_card)
            session_layout.setContentsMargins(18, 18, 18, 18)
            label = QLabel('Programas detectados')
            label.setStyleSheet('font-size: 16px; font-weight: 800; color: white; background: transparent;')
            self.session_table = self._create_table(['#', 'Início', 'Duração', 'Modo', 'Arcos', 'Eficiência', 'Status', 'Erros'])
            self.session_table.itemSelectionChanged.connect(self.on_session_selected)
            session_layout.addWidget(label)
            session_layout.addWidget(self.session_table)

            details_col = QVBoxLayout()
            detail_card = GlassFrame()
            detail_layout = QVBoxLayout(detail_card)
            detail_layout.setContentsMargins(18, 18, 18, 18)
            detail_title = QLabel('Painel do programa')
            detail_title.setStyleSheet('font-size: 16px; font-weight: 800; color: white; background: transparent;')
            self.session_details = QPlainTextEdit()
            self.session_details.setReadOnly(True)
            detail_layout.addWidget(detail_title)
            detail_layout.addWidget(self.session_details)

            event_card = GlassFrame()
            event_layout = QVBoxLayout(event_card)
            event_layout.setContentsMargins(18, 18, 18, 18)
            event_title = QLabel('Últimos eventos da sessão')
            event_title.setStyleSheet('font-size: 16px; font-weight: 800; color: white; background: transparent;')
            self.session_events = self._create_table(['Horário', 'Categoria', 'Mensagem'])
            event_layout.addWidget(event_title)
            event_layout.addWidget(self.session_events)

            details_col.addWidget(detail_card, 1)
            details_col.addWidget(event_card, 1)
            layout.addWidget(session_card, 3)
            layout.addLayout(details_col, 2)
            return page

        def _build_alerts_tab(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(16)

            upper = QHBoxLayout()
            recommendations = GlassFrame()
            rec_layout = QVBoxLayout(recommendations)
            rec_layout.setContentsMargins(18, 18, 18, 18)
            rec_title = QLabel('Ações recomendadas')
            rec_title.setStyleSheet('font-size: 16px; font-weight: 800; color: white; background: transparent;')
            self.recommendations_table = self._create_table(['Prioridade', 'Título', 'Métrica'])
            self.recommendations_table.itemSelectionChanged.connect(self.on_recommendation_selected)
            self.recommendation_details = QPlainTextEdit()
            self.recommendation_details.setReadOnly(True)
            rec_layout.addWidget(rec_title)
            rec_layout.addWidget(self.recommendations_table)
            rec_layout.addWidget(self.recommendation_details)

            errors = GlassFrame()
            err_layout = QVBoxLayout(errors)
            err_layout.setContentsMargins(18, 18, 18, 18)
            err_title = QLabel('Falhas e incidentes')
            err_title.setStyleSheet('font-size: 16px; font-weight: 800; color: white; background: transparent;')
            self.error_table = self._create_table(['Horário', 'Origem', 'Categoria', 'Mensagem'])
            err_layout.addWidget(err_title)
            err_layout.addWidget(self.error_table)

            upper.addWidget(recommendations, 2)
            upper.addWidget(errors, 3)
            layout.addLayout(upper)

            lower = GlassFrame()
            lower_layout = QVBoxLayout(lower)
            lower_layout.setContentsMargins(18, 18, 18, 18)
            lower_title = QLabel('Timeline de estados e serviços')
            lower_title.setStyleSheet('font-size: 16px; font-weight: 800; color: white; background: transparent;')
            self.timeline_table = self._create_table(['Horário', 'Tipo', 'Valor'])
            lower_layout.addWidget(lower_title)
            lower_layout.addWidget(self.timeline_table)
            layout.addWidget(lower, 2)
            return page

        def _build_deep_tab(self) -> QWidget:
            page = QWidget()
            layout = QHBoxLayout(page)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(16)

            left = GlassFrame()
            left_layout = QVBoxLayout(left)
            left_layout.setContentsMargins(18, 18, 18, 18)
            title = QLabel('Inventário e versões')
            title.setStyleSheet('font-size: 16px; font-weight: 800; color: white; background: transparent;')
            self.version_table = self._create_table(['Horário', 'Item', 'Valor'])
            left_layout.addWidget(title)
            left_layout.addWidget(self.version_table)

            right = QVBoxLayout()
            top = GlassFrame()
            top_layout = QVBoxLayout(top)
            top_layout.setContentsMargins(18, 18, 18, 18)
            top_title = QLabel('Top tópicos e módulos')
            top_title.setStyleSheet('font-size: 16px; font-weight: 800; color: white; background: transparent;')
            self.topic_table = self._create_table(['Tipo', 'Nome', 'Ocorrências'])
            top_layout.addWidget(top_title)
            top_layout.addWidget(self.topic_table)

            extra = GlassFrame()
            extra_layout = QVBoxLayout(extra)
            extra_layout.setContentsMargins(18, 18, 18, 18)
            extra_title = QLabel('Highlights técnicos')
            extra_title.setStyleSheet('font-size: 16px; font-weight: 800; color: white; background: transparent;')
            self.highlights_text = QPlainTextEdit()
            self.highlights_text.setReadOnly(True)
            extra_layout.addWidget(extra_title)
            extra_layout.addWidget(self.highlights_text)

            right.addWidget(top, 1)
            right.addWidget(extra, 1)
            layout.addWidget(left, 2)
            layout.addLayout(right, 2)
            return page

        def _create_table(self, headers: list[str]) -> QTableWidget:
            table = QTableWidget(0, len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setSelectionMode(QTableWidget.SingleSelection)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            table.horizontalHeader().setMinimumSectionSize(80)
            return table

        def choose_file(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, 'Selecionar log', str(Path.cwd()), 'Logs (*.txt *.log *.json);;Todos (*.*)')
            if path:
                self.load_file(path)

        def load_file(self, path: str) -> None:
            try:
                records = LogParser(path).parse()
                analysis = MonitorAnalyzer(records, path).analyze()
            except Exception as exc:
                QMessageBox.critical(self, 'Erro ao carregar', str(exc))
                return

            self.analysis = analysis
            self.file_label.setText(f'Arquivo ativo: {analysis.source_path}')
            self.hero_badge.setText(f'{analysis.total_programs} programas • {analysis.total_errors} erros • score {analysis.health_score}/100')
            self.hero_meta.setText(
                f'Janela: {analysis.records[0].timestamp:%d/%m/%Y %H:%M:%S} → {analysis.records[-1].timestamp:%d/%m/%Y %H:%M:%S} | '
                f'Tempo de arco: {format_timedelta(analysis.total_arc_time)}'
            )

            services_total = max(len(analysis.service_status_summary), 1)
            services_online = sum(1 for status in analysis.service_status_summary.values() if status == 'Online')
            self.stat_cards[0].update_content(str(analysis.total_programs), f'{analysis.completed_programs} concluídos e {analysis.total_arc_openings} aberturas de arco no período.')
            self.stat_cards[1].update_content(f'{analysis.arc_efficiency * 100:.1f}%', f'Duração média por programa: {format_timedelta(analysis.average_session_duration)}.')
            self.stat_cards[2].update_content(f'{analysis.total_errors}/{analysis.total_warnings}', 'Primeiro número = erros. Segundo número = warnings associados às sessões.')
            self.stat_cards[3].update_content(f'{services_online}/{services_total}', format_services_line(analysis.service_status_summary))

            self._refresh_overview(analysis)
            self._refresh_sessions(analysis)
            self._refresh_alerts(analysis)
            self._refresh_deep(analysis)

        def _refresh_overview(self, analysis: LogAnalysis) -> None:
            executive_lines = [
                'Resumo executivo',
                f'• Janela analisada: {analysis.records[0].timestamp:%d/%m/%Y %H:%M:%S} até {analysis.records[-1].timestamp:%d/%m/%Y %H:%M:%S}.',
                f'• {analysis.total_programs} programas identificados, com {analysis.completed_programs} finalizados.',
                f'• Tempo total de arco: {format_timedelta(analysis.total_arc_time)} e eficiência média de {analysis.arc_efficiency * 100:.1f}%.',
                f'• Foram detectados {analysis.total_errors} erros e {analysis.total_warnings} warnings.',
                f'• Mix de categorias: {format_counter(analysis.category_counts, 5)}.',
                f'• Serviços monitorados: {format_services_line(analysis.service_status_summary)}.',
                '',
                'Leituras de negócio',
            ]
            if analysis.sessions:
                longest = max(analysis.sessions, key=lambda item: item.duration)
                best = max(analysis.sessions, key=lambda item: item.arc_efficiency)
                executive_lines.append(f'• Programa mais longo: #{longest.index} com {format_timedelta(longest.duration)}.')
                executive_lines.append(f'• Melhor uso de arco: programa #{best.index} com {best.arc_efficiency * 100:.1f}% de eficiência.')
            if analysis.category_counts.get('Colisão', 0):
                executive_lines.append('• Há registro de colisão no período, então segurança operacional deve ganhar destaque.')
            if analysis.category_counts.get('Fieldbus / CAN', 0):
                executive_lines.append('• O log mostra sintomas de Fieldbus/CAN, sugerindo rotina separada para manutenção preditiva.')
            if analysis.version_inventory:
                executive_lines.append('• Existe inventário técnico suficiente para auditoria de software e versões instaladas.')
            self.executive_text.setPlainText('\n'.join(executive_lines))

            score_summary = (
                'Fluxo limpo e controlado.'
                if analysis.health_score >= 80
                else 'Há sinais de atrito operacional, mas com controle.'
                if analysis.health_score >= 60
                else 'Atenção: o período mostra criticidade operacional relevante.'
            )
            self.gauge.animate_to(analysis.health_score, score_summary)

            labels = [f'P{session.index}' for session in analysis.sessions]
            self.trend_chart.set_data(
                labels,
                [session.duration.total_seconds() / 60 for session in analysis.sessions],
                [session.arc_efficiency * 100 for session in analysis.sessions],
            )
            state_items = sorted(analysis.state_duration_seconds.items(), key=lambda item: item[1], reverse=True)[:6]
            self.state_chart.set_series([
                (label, value / 60, CATEGORY_COLORS[index % len(CATEGORY_COLORS)])
                for index, (label, value) in enumerate(state_items)
            ])
            category_items = analysis.category_counts.most_common(6)
            self.category_chart.set_items([
                (label, float(value), CATEGORY_COLORS[index % len(CATEGORY_COLORS)])
                for index, (label, value) in enumerate(category_items)
            ])

        def _refresh_sessions(self, analysis: LogAnalysis) -> None:
            self._fill_table(
                self.session_table,
                [
                    [
                        str(session.index),
                        session.start.strftime('%H:%M:%S'),
                        format_timedelta(session.duration),
                        session.cut_mode or '-',
                        str(session.arc_openings),
                        f'{session.arc_efficiency * 100:.1f}%',
                        session.status,
                        str(len(session.errors)),
                    ]
                    for session in analysis.sessions
                ],
            )
            self.session_details.setPlainText('Selecione um programa para ver os detalhes operacionais.')
            self._fill_table(self.session_events, [])
            if analysis.sessions:
                self.session_table.selectRow(0)
                self.on_session_selected()

        def _refresh_alerts(self, analysis: LogAnalysis) -> None:
            self._fill_table(
                self.recommendations_table,
                [[item.priority, item.title, item.metric] for item in analysis.recommendations],
                color_column=0,
            )
            rows: list[list[str]] = []
            for session in analysis.sessions:
                for error in session.errors:
                    rows.append([error.timestamp.strftime('%Y-%m-%d %H:%M:%S'), f'Prog. {session.index}', self._categorize(error), error.message])
            for error in analysis.unassigned_errors:
                rows.append([error.timestamp.strftime('%Y-%m-%d %H:%M:%S'), 'Fora prog.', self._categorize(error), error.message])
            rows.sort(key=lambda row: row[0])
            self._fill_table(self.error_table, rows)

            timeline_rows = [[event.timestamp.strftime('%Y-%m-%d %H:%M:%S'), 'Serviço', f'{event.service}: {event.status}'] for event in analysis.service_status_history]
            timeline_rows.extend([[timestamp.strftime('%Y-%m-%d %H:%M:%S'), 'CNC State', state] for timestamp, state in analysis.state_history[-40:]])
            timeline_rows.sort(key=lambda row: row[0])
            self._fill_table(self.timeline_table, timeline_rows)
            self.recommendation_details.setPlainText('Selecione uma recomendação para abrir a explicação e a métrica gatilho.')
            if analysis.recommendations:
                self.recommendations_table.selectRow(0)
                self.on_recommendation_selected()

        def _refresh_deep(self, analysis: LogAnalysis) -> None:
            seen: set[tuple[str, str]] = set()
            version_rows: list[list[str]] = []
            for entry in analysis.version_inventory:
                key = (entry.label, entry.value)
                if key in seen:
                    continue
                seen.add(key)
                version_rows.append([entry.timestamp.strftime('%H:%M:%S'), entry.label, entry.value])
                if len(version_rows) >= 30:
                    break
            self._fill_table(self.version_table, version_rows)

            topic_rows = [[ 'Tópico', name, str(count)] for name, count in analysis.topic_counts.most_common(8)]
            topic_rows.extend([[ 'Módulo', name, str(count)] for name, count in analysis.source_context_counts.most_common(8)])
            self._fill_table(self.topic_table, topic_rows)

            highlights = [
                f'• Top categorias: {format_counter(analysis.category_counts, 6)}.',
                f'• Top módulos: {format_counter(analysis.source_context_counts, 6)}.',
                f'• Serviços finais: {format_services_line(analysis.service_status_summary)}.',
                f'• Inventário identificado: {len(version_rows)} itens únicos.',
            ]
            if analysis.version_inventory:
                latest = analysis.version_inventory[-1]
                highlights.append(f'• Última versão vista no log: {latest.label} = {latest.value}.')
            self.highlights_text.setPlainText('\n'.join(highlights))

        def on_session_selected(self) -> None:
            if not self.analysis:
                return
            row = self.session_table.currentRow()
            if row < 0 or row >= len(self.analysis.sessions):
                return
            session = self.analysis.sessions[row]
            lines = [
                f'Programa {session.index}',
                f'Início: {session.start:%Y-%m-%d %H:%M:%S}',
                f'Fim: {session.end:%Y-%m-%d %H:%M:%S}' if session.end else 'Fim: em andamento',
                f'Duração total: {format_timedelta(session.duration)}',
                f'Modo de corte: {session.cut_mode or "não identificado"}',
                f'Aberturas de arco: {session.arc_openings}',
                f'Tempo total de arco: {format_timedelta(session.total_arc_time)}',
                f'Eficiência estimada: {session.arc_efficiency * 100:.1f}%',
                f'Estados percorridos: {", ".join(session.states) if session.states else "sem estados detectados"}',
                f'Eventos coletados: {session.event_count}',
                '',
                'Erros nesta sessão:',
            ]
            if session.errors:
                lines.extend([f'• {record.timestamp:%H:%M:%S} | {record.message}' for record in session.errors])
            else:
                lines.append('• Nenhum erro detectado nesta janela.')
            self.session_details.setPlainText('\n'.join(lines))
            self._fill_table(
                self.session_events,
                [[event.timestamp.strftime('%H:%M:%S'), self._categorize(event), event.message[:180]] for event in session.events[-40:]],
            )

        def on_recommendation_selected(self) -> None:
            if not self.analysis:
                return
            row = self.recommendations_table.currentRow()
            if row < 0 or row >= len(self.analysis.recommendations):
                return
            item = self.analysis.recommendations[row]
            self.recommendation_details.setPlainText(
                f'{item.title}\nPrioridade: {item.priority}\nMétrica gatilho: {item.metric}\n\n{item.description}'
            )

        def export_summary(self) -> None:
            if not self.analysis:
                QMessageBox.information(self, 'Sem dados', 'Carregue um arquivo antes de exportar o resumo.')
                return
            path, _ = QFileDialog.getSaveFileName(self, 'Salvar resumo', str(self.analysis.source_path.with_suffix('.summary.json')), 'JSON (*.json)')
            if not path:
                return
            Path(path).write_text(json.dumps(build_summary_payload(self.analysis), indent=2, ensure_ascii=False), encoding='utf-8')
            QMessageBox.information(self, 'Exportação concluída', f'Resumo salvo em:\n{path}')

        def _fill_table(self, table: QTableWidget, rows: list[list[str]], color_column: int | None = None) -> None:
            table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for col_index, value in enumerate(row):
                    item = QTableWidgetItem(value)
                    if color_column is not None and col_index == color_column:
                        item.setForeground(QColor(PRIORITY_COLORS.get(value, '#e2e8f0')))
                    table.setItem(row_index, col_index, item)
            table.clearSelection()

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


def format_services_line(statuses: dict[str, str]) -> str:
    return ', '.join(f'{name}={status}' for name, status in statuses.items()) or 'nenhum status disponível'


def print_cli_summary(analysis: LogAnalysis) -> None:
    print(json.dumps(build_summary_payload(analysis), indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description='Monitor de corte para logs Phoenix.')
    parser.add_argument('logfile', nargs='?', help='Arquivo de log a ser analisado.')
    parser.add_argument('--summary', action='store_true', help='Imprime o resumo JSON no terminal e encerra.')
    args = parser.parse_args()

    if args.summary:
        if not args.logfile:
            raise SystemExit('Informe o caminho do log ao usar --summary.')
        analysis = MonitorAnalyzer(LogParser(args.logfile).parse(), args.logfile).analyze()
        print_cli_summary(analysis)
        return

    if not HAS_QT:
        raise SystemExit(
            'A interface gráfica agora usa PySide6. Instale com: python3 -m pip install PySide6'
        )

    app = QApplication(sys.argv)
    window = MonitorMainWindow(initial_path=args.logfile)
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
