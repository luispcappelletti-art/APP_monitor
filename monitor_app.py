import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Iterable

RECORD_START_RE = re.compile(r'(?m)^(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<topic>\S+)\s*(?P<payload>.*)$')
IO_RE = re.compile(r'(Output|Input)\s+(\d+),\s*([A-Za-z0-9_\-]+)\s+turned\s+(On|Off)', re.IGNORECASE)
STATE_RE = re.compile(r'Update Cnc State to\s+(\w+)', re.IGNORECASE)
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
        total = timedelta(0)
        for event in self.arc_events:
            total += event.duration
        return total

    @property
    def status(self) -> str:
        return 'Finalizado' if self.end else 'Em andamento'

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
        match = re.search(r'Update Cut Mode to\s+(\w+)', message, re.IGNORECASE)
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


class MonitorApp:
    def __init__(self, root: tk.Tk, initial_path: str | None = None):
        self.root = root
        self.root.title('APP Monitor de Corte')
        self.root.geometry('1600x900')

        self.analysis: LogAnalysis | None = None
        self.summary_vars: dict[str, tk.StringVar] = {}

        self._build()
        if initial_path:
            self.load_file(initial_path)

    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=12)
        top.pack(fill='x')

        ttk.Button(top, text='Abrir log', command=self.pick_file).pack(side='left')
        ttk.Button(top, text='Exportar resumo JSON', command=self.export_summary).pack(side='left', padx=(8, 0))

        self.file_label = ttk.Label(top, text='Nenhum arquivo carregado')
        self.file_label.pack(side='left', padx=(12, 0))

        summary = ttk.LabelFrame(self.root, text='Resumo geral', padding=12)
        summary.pack(fill='x', padx=12, pady=(0, 8))

        cards = [
            ('programs', 'Programas cortados'),
            ('completed', 'Programas finalizados'),
            ('arcs', 'Aberturas de arco'),
            ('arc_time', 'Tempo total de arco'),
            ('errors', 'Erros detectados'),
        ]
        for column, (key, label) in enumerate(cards):
            frame = ttk.Frame(summary, padding=6)
            frame.grid(row=0, column=column, sticky='nsew', padx=8)
            summary.columnconfigure(column, weight=1)
            ttk.Label(frame, text=label).pack(anchor='center')
            variable = tk.StringVar(value='-')
            ttk.Label(frame, textvariable=variable, font=('Arial', 18, 'bold')).pack(anchor='center')
            self.summary_vars[key] = variable

        panes = ttk.Panedwindow(self.root, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=12, pady=(0, 12))

        left = ttk.Frame(panes, padding=8)
        center = ttk.Frame(panes, padding=8)
        right = ttk.Frame(panes, padding=8)
        panes.add(left, weight=3)
        panes.add(center, weight=2)
        panes.add(right, weight=2)

        ttk.Label(left, text='Sessões de corte').pack(anchor='w')
        self.session_table = ttk.Treeview(
            left,
            columns=('idx', 'inicio', 'fim', 'duracao', 'arcos', 'tempo_arco', 'modo', 'status'),
            show='headings',
            height=20,
        )
        headings = {
            'idx': 'Programa',
            'inicio': 'Início',
            'fim': 'Fim',
            'duracao': 'Duração',
            'arcos': 'Aberturas',
            'tempo_arco': 'Tempo arco',
            'modo': 'Modo',
            'status': 'Status',
        }
        widths = {'idx': 80, 'inicio': 150, 'fim': 150, 'duracao': 100, 'arcos': 90, 'tempo_arco': 100, 'modo': 90, 'status': 100}
        for key, text in headings.items():
            self.session_table.heading(key, text=text)
            self.session_table.column(key, width=widths[key], anchor='center')
        self.session_table.pack(fill='both', expand=True)
        self.session_table.bind('<<TreeviewSelect>>', self.on_session_select)

        ttk.Label(center, text='Detalhes do programa selecionado').pack(anchor='w')
        self.details = tk.Text(center, wrap='word')
        self.details.pack(fill='both', expand=True)

        ttk.Label(right, text='Erros encontrados').pack(anchor='w')
        self.error_table = ttk.Treeview(
            right,
            columns=('hora', 'origem', 'mensagem'),
            show='headings',
            height=12,
        )
        self.error_table.heading('hora', text='Horário')
        self.error_table.heading('origem', text='Origem')
        self.error_table.heading('mensagem', text='Mensagem')
        self.error_table.column('hora', width=130, anchor='center')
        self.error_table.column('origem', width=150, anchor='center')
        self.error_table.column('mensagem', width=420, anchor='w')
        self.error_table.pack(fill='both', expand=True)

        ttk.Label(right, text='Estados CNC').pack(anchor='w', pady=(8, 0))
        self.state_table = ttk.Treeview(
            right,
            columns=('hora', 'estado'),
            show='headings',
            height=8,
        )
        self.state_table.heading('hora', text='Horário')
        self.state_table.heading('estado', text='Estado')
        self.state_table.column('hora', width=130, anchor='center')
        self.state_table.column('estado', width=180, anchor='center')
        self.state_table.pack(fill='x', expand=False)

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

        self.file_label.config(text=str(path))
        self._refresh_summary()
        self._refresh_sessions()
        self._refresh_errors()
        self._refresh_states()
        self.details.delete('1.0', 'end')
        self.details.insert('end', 'Selecione um programa na tabela para ver o detalhamento.')

    def export_summary(self) -> None:
        if not self.analysis:
            messagebox.showinfo('Sem análise', 'Carregue um arquivo antes de exportar o resumo.')
            return

        destination = filedialog.asksaveasfilename(
            title='Salvar resumo em JSON',
            defaultextension='.json',
            initialfile=f'{self.analysis.source_path.stem}_resumo.json',
            filetypes=[('JSON', '*.json')],
        )
        if not destination:
            return

        payload = build_summary_payload(self.analysis)
        Path(destination).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        messagebox.showinfo('Exportação concluída', f'Resumo salvo em {destination}')

    def _refresh_summary(self) -> None:
        assert self.analysis is not None
        self.summary_vars['programs'].set(str(self.analysis.total_programs))
        self.summary_vars['completed'].set(str(self.analysis.completed_programs))
        self.summary_vars['arcs'].set(str(self.analysis.total_arc_openings))
        self.summary_vars['arc_time'].set(format_timedelta(self.analysis.total_arc_time))
        self.summary_vars['errors'].set(str(self.analysis.total_errors))

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
                    session.start.strftime('%Y-%m-%d %H:%M:%S'),
                    session.end.strftime('%Y-%m-%d %H:%M:%S') if session.end else '-',
                    format_timedelta(session.duration),
                    session.arc_openings,
                    format_timedelta(session.total_arc_time),
                    session.cut_mode or '-',
                    session.status,
                ),
            )

    def _refresh_errors(self) -> None:
        assert self.analysis is not None
        self.error_table.delete(*self.error_table.get_children())
        rows: list[tuple[datetime, str, str]] = []
        for session in self.analysis.sessions:
            for error in session.errors:
                rows.append((error.timestamp, f'Programa {session.index}', error.message))
        for error in self.analysis.unassigned_errors:
            rows.append((error.timestamp, 'Fora de programa', error.message))
        rows.sort(key=lambda item: item[0])
        for index, (timestamp, origin, message) in enumerate(rows, start=1):
            self.error_table.insert('', 'end', iid=str(index), values=(timestamp.strftime('%Y-%m-%d %H:%M:%S'), origin, message))

    def _refresh_states(self) -> None:
        assert self.analysis is not None
        self.state_table.delete(*self.state_table.get_children())
        for index, (timestamp, state) in enumerate(self.analysis.state_history[-20:], start=1):
            self.state_table.insert('', 'end', iid=str(index), values=(timestamp.strftime('%Y-%m-%d %H:%M:%S'), state))

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
            f'Estados percorridos: {", ".join(session.states) if session.states else "sem estados detectados"}',
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
                lines.append(
                    f'- Arco {arc_index}: {arc.start:%H:%M:%S} -> {arc_end} '
                    f'({format_timedelta(arc.duration)})'
                )
        else:
            lines.append('- Nenhum evento de arco encontrado.')

        self.details.delete('1.0', 'end')
        self.details.insert('end', '\n'.join(lines))


def build_summary_payload(analysis: LogAnalysis) -> dict[str, Any]:
    error_counter = Counter()
    for session in analysis.sessions:
        error_counter.update(record.message for record in session.errors)
    error_counter.update(record.message for record in analysis.unassigned_errors)

    return {
        'arquivo': str(analysis.source_path),
        'resumo': {
            'programas_cortados': analysis.total_programs,
            'programas_finalizados': analysis.completed_programs,
            'aberturas_de_arco': analysis.total_arc_openings,
            'tempo_total_de_arco': format_timedelta(analysis.total_arc_time),
            'erros_detectados': analysis.total_errors,
        },
        'programas': [
            {
                'programa': session.index,
                'inicio': session.start.isoformat(sep=' '),
                'fim': session.end.isoformat(sep=' ') if session.end else None,
                'duracao': format_timedelta(session.duration),
                'modo_corte': session.cut_mode,
                'aberturas_de_arco': session.arc_openings,
                'tempo_total_de_arco': format_timedelta(session.total_arc_time),
                'estados': session.states,
                'erros': [record.message for record in session.errors],
            }
            for session in analysis.sessions
        ],
        'top_erros': [{'mensagem': message, 'ocorrencias': count} for message, count in error_counter.most_common(10)],
    }


def format_timedelta(delta: timedelta) -> str:
    total_seconds = max(int(delta.total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


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
