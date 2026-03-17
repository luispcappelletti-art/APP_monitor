import json
import sqlite3
import threading
from collections import defaultdict, deque
from datetime import datetime
from queue import Empty, Queue

import paho.mqtt.client as mqtt
import tkinter as tk
from tkinter import filedialog, messagebox

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


DEFAULT_BROKER = "100.96.164.3"
PORT = 1884
USERNAME = "PhoenixBroker"
PASSWORD = "Broker2022"
DB_FILE = "mqtt_monitor.db"
MAX_MESSAGES_DB = 500000
ARC_TOPIC = "Phoenix/Cut/ArcOn"


class DatabaseManager:
    def __init__(self, db_path=DB_FILE, max_messages=MAX_MESSAGES_DB):
        self.db_path = db_path
        self.max_messages = max_messages
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    value TEXT,
                    raw_payload TEXT
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_topic ON messages(topic)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)")
            self.conn.commit()

    def insert_message(self, timestamp, topic, value, raw_payload):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                "INSERT INTO messages (timestamp, topic, value, raw_payload) VALUES (?, ?, ?, ?)",
                (timestamp, topic, value, raw_payload),
            )
            self.conn.commit()
            self._trim_if_needed(cur)

    def _trim_if_needed(self, cur):
        cur.execute("SELECT COUNT(*) FROM messages")
        total = cur.fetchone()[0]
        if total > self.max_messages:
            overflow = total - self.max_messages
            cur.execute(
                "DELETE FROM messages WHERE id IN (SELECT id FROM messages ORDER BY id ASC LIMIT ?)",
                (overflow,),
            )
            self.conn.commit()

    def export_csv(self, output_path):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT id, timestamp, topic, value, raw_payload FROM messages ORDER BY id")
            rows = cur.fetchall()

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("id,timestamp,topic,value,raw_payload\n")
            for row in rows:
                escaped = []
                for item in row:
                    cell = "" if item is None else str(item)
                    cell = cell.replace('"', '""')
                    escaped.append(f'"{cell}"')
                f.write(",".join(escaped) + "\n")

    def export_json(self, output_path):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT id, timestamp, topic, value, raw_payload FROM messages ORDER BY id")
            rows = cur.fetchall()

        payload = [
            {
                "id": row[0],
                "timestamp": row[1],
                "topic": row[2],
                "value": row[3],
                "raw_payload": row[4],
            }
            for row in rows
        ]
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def close(self):
        with self.lock:
            self.conn.close()


class SignalAnalyzer:
    def __init__(self):
        self.topics = {}
        self.current_values = {}
        self.value_history = defaultdict(lambda: deque(maxlen=200))
        self.events = deque(maxlen=1000)
        self.total_messages = 0
        self.detected_signals = set()
        self.connection_start = None
        self.connected = False
        self.lock = threading.Lock()
        self.messages_per_second_window = deque(maxlen=10)
        self.total_messages_since_connect = 0

    @staticmethod
    def detect_type(value_text):
        txt = value_text.strip()
        if txt.lower() in {"true", "false"}:
            return "boolean"
        try:
            if txt.startswith("{") and txt.endswith("}"):
                json.loads(txt)
                return "json"
        except json.JSONDecodeError:
            pass
        try:
            int(txt)
            return "int"
        except ValueError:
            pass
        try:
            float(txt)
            return "float"
        except ValueError:
            pass
        return "string"

    @staticmethod
    def parse_value(value_text, value_type):
        if value_type == "boolean":
            return value_text.strip().lower() == "true"
        if value_type == "int":
            return int(value_text)
        if value_type == "float":
            return float(value_text)
        if value_type == "json":
            try:
                return json.loads(value_text)
            except json.JSONDecodeError:
                return value_text
        return value_text

    def update_connection(self, connected):
        with self.lock:
            self.connected = connected
            if connected:
                self.connection_start = datetime.now()
                self.total_messages_since_connect = 0
                self.messages_per_second_window.clear()

    def register_tick(self, count_last_second):
        with self.lock:
            self.messages_per_second_window.append(count_last_second)

    def register_message(self, topic, value_text, timestamp):
        with self.lock:
            self.total_messages += 1
            self.total_messages_since_connect += 1
            dtype = self.detect_type(value_text)
            parsed = self.parse_value(value_text, dtype)

            if topic not in self.topics:
                self.topics[topic] = {
                    "count": 0,
                    "first_seen": timestamp,
                    "last_seen": timestamp,
                    "type": dtype,
                    "changes": 0,
                }
                self.detected_signals.add(topic)

            topic_data = self.topics[topic]
            topic_data["count"] += 1
            topic_data["last_seen"] = timestamp
            topic_data["type"] = dtype

            old_value = self.current_values.get(topic)
            if old_value is not None and old_value != parsed:
                topic_data["changes"] += 1
                if isinstance(parsed, bool) and isinstance(old_value, bool):
                    self.events.append(
                        {
                            "timestamp": timestamp,
                            "topic": topic,
                            "old": old_value,
                            "new": parsed,
                            "kind": "EVENT DETECTED",
                        }
                    )

            self.current_values[topic] = parsed
            if isinstance(parsed, (int, float)):
                self.value_history[topic].append((timestamp, parsed))

            return dtype, parsed

    def get_activity(self, topic):
        meta = self.topics.get(topic)
        if not meta or meta["count"] <= 1:
            return "LOW ACTIVITY"
        ratio = meta["changes"] / max(meta["count"] - 1, 1)
        if ratio > 0.6:
            return "HIGH ACTIVITY"
        if ratio > 0.2:
            return "MEDIUM ACTIVITY"
        return "LOW ACTIVITY"

    def get_connected_time(self):
        if not self.connected or not self.connection_start:
            return "00:00:00"
        delta = datetime.now() - self.connection_start
        total_sec = int(delta.total_seconds())
        hours = total_sec // 3600
        mins = (total_sec % 3600) // 60
        sec = total_sec % 60
        return f"{hours:02d}:{mins:02d}:{sec:02d}"

    def get_avg_mps(self):
        if not self.messages_per_second_window:
            return 0.0
        return sum(self.messages_per_second_window) / len(self.messages_per_second_window)


class MQTTClient:
    def __init__(self, event_queue, analyzer):
        self.event_queue = event_queue
        self.analyzer = analyzer
        self.client = None
        self.host = None
        self.port = PORT
        self.connected = False

    def connect(self, host, username=USERNAME, password=PASSWORD):
        self.host = host
        if self.client:
            self.disconnect()

        self.client = mqtt.Client()
        self.client.username_pw_set(username, password)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

        try:
            self.client.connect(host, self.port, keepalive=60)
            self.client.loop_start()
            self.event_queue.put({"type": "status", "message": f"Conectando em {host}:{self.port}"})
        except Exception as exc:
            self.event_queue.put({"type": "error", "message": f"Falha de conexão: {exc}"})

    def disconnect(self):
        if not self.client:
            return
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            self.analyzer.update_connection(True)
            client.subscribe("Phoenix/#")
            self.event_queue.put({"type": "connected", "message": "Conectado ao broker MQTT"})
        else:
            self.event_queue.put({"type": "error", "message": f"Conexão recusada (rc={rc})"})

    def on_disconnect(self, client, userdata, rc):
        self.connected = False
        self.analyzer.update_connection(False)
        self.event_queue.put({"type": "disconnected", "message": "Desconectado. Tentando reconectar..."})

    def on_message(self, client, userdata, msg):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = msg.payload.decode(errors="ignore")
        self.event_queue.put(
            {
                "type": "message",
                "topic": msg.topic,
                "payload": payload,
                "timestamp": ts,
                "raw_payload": payload,
            }
        )


class PhoenixMonitorUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Phoenix CNC MQTT Monitor Pro")
        self.root.geometry("1500x900")
        self.root.configure(bg="#1e1e1e")

        self.db = DatabaseManager()
        self.analyzer = SignalAnalyzer()
        self.queue = Queue(maxsize=10000)
        self.mqtt_client = MQTTClient(self.queue, self.analyzer)

        self.broker_var = tk.StringVar(value=DEFAULT_BROKER)
        self.status_var = tk.StringVar(value="Status: Desconectado")
        self.filter_var = tk.StringVar(value="")
        self.record_arcon_only = tk.BooleanVar(value=False)
        self.pause_log_var = tk.BooleanVar(value=False)

        self.msg_count_last_second = 0

        self.topic_rows = {}
        self.current_rows = {}

        self._build_ui()
        self._bind_events()
        self._ui_loop()
        self._metrics_tick()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        top = tk.Frame(self.root, bg="#2b2b2b")
        top.pack(fill="x", padx=8, pady=8)

        tk.Label(top, text="Broker IP:", bg="#2b2b2b", fg="white").pack(side="left", padx=(10, 5))
        tk.Entry(top, textvariable=self.broker_var, bg="#1e1e1e", fg="white", insertbackground="white", width=18).pack(side="left")
        tk.Button(top, text="Conectar", bg="#2f6f3e", fg="white", command=self.connect).pack(side="left", padx=6)
        tk.Button(top, text="Limpar Log", bg="#555", fg="white", command=self.clear_log).pack(side="left", padx=6)
        tk.Button(top, text="Export Data", bg="#444", fg="white", command=self.export_data).pack(side="left", padx=6)
        tk.Button(top, text="Plot Signal", bg="#345", fg="white", command=self.plot_selected_signal).pack(side="left", padx=6)
        tk.Checkbutton(
            top,
            text="Record Only When ArcOn",
            variable=self.record_arcon_only,
            bg="#2b2b2b",
            fg="white",
            selectcolor="#1e1e1e",
            activebackground="#2b2b2b",
            activeforeground="white",
        ).pack(side="left", padx=10)
        tk.Checkbutton(
            top,
            text="Pause Log",
            variable=self.pause_log_var,
            bg="#2b2b2b",
            fg="white",
            selectcolor="#1e1e1e",
            activebackground="#2b2b2b",
            activeforeground="white",
        ).pack(side="left")

        mid = tk.Frame(self.root, bg="#1e1e1e")
        mid.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left = tk.Frame(mid, bg="#2b2b2b", width=430)
        left.pack(side="left", fill="y", padx=(0, 6))
        left.pack_propagate(False)

        center = tk.Frame(mid, bg="#2b2b2b")
        center.pack(side="left", fill="both", expand=True, padx=(0, 6))

        right = tk.Frame(mid, bg="#2b2b2b", width=380)
        right.pack(side="left", fill="y")
        right.pack_propagate(False)

        tk.Label(left, text="TOPICS DISCOVERED", bg="#2b2b2b", fg="white", font=("Arial", 11, "bold")).pack(anchor="w", padx=8, pady=(8, 4))
        tk.Entry(left, textvariable=self.filter_var, bg="#1e1e1e", fg="white", insertbackground="white").pack(fill="x", padx=8, pady=(0, 6))

        self.topic_list = tk.Listbox(left, bg="#121212", fg="white", selectbackground="#345", font=("Consolas", 9))
        self.topic_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        tk.Label(center, text="REAL-TIME LOG", bg="#2b2b2b", fg="white", font=("Arial", 11, "bold")).pack(anchor="w", padx=8, pady=(8, 4))
        self.log_text = tk.Text(center, bg="#121212", fg="white", insertbackground="white", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        tk.Label(right, text="CURRENT VALUES", bg="#2b2b2b", fg="white", font=("Arial", 11, "bold")).pack(anchor="w", padx=8, pady=(8, 4))
        self.current_list = tk.Listbox(right, bg="#121212", fg="white", selectbackground="#345", font=("Consolas", 9))
        self.current_list.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        tk.Label(right, text="STATISTICS", bg="#2b2b2b", fg="white", font=("Arial", 11, "bold")).pack(anchor="w", padx=8, pady=(4, 2))
        self.stats_label = tk.Label(right, bg="#2b2b2b", fg="white", justify="left", anchor="w")
        self.stats_label.pack(fill="x", padx=8, pady=(0, 8))

        bottom = tk.Frame(self.root, bg="#2b2b2b", height=200)
        bottom.pack(fill="x", padx=8, pady=(0, 8))

        tk.Label(bottom, text="EVENT LOG", bg="#2b2b2b", fg="white", font=("Arial", 11, "bold")).pack(anchor="w", padx=8, pady=(8, 4))
        self.event_text = tk.Text(bottom, height=8, bg="#121212", fg="#9eff9e", insertbackground="white", font=("Consolas", 9))
        self.event_text.pack(fill="x", padx=8, pady=(0, 8))

        footer = tk.Frame(self.root, bg="#2b2b2b")
        footer.pack(fill="x", padx=8, pady=(0, 8))

        self.indicator = tk.Label(footer, text="●", fg="#ff4d4d", bg="#2b2b2b", font=("Arial", 13, "bold"))
        self.indicator.pack(side="left", padx=(10, 6))
        tk.Label(footer, textvariable=self.status_var, bg="#2b2b2b", fg="white").pack(side="left")

        self.footer_metrics = tk.Label(footer, bg="#2b2b2b", fg="white")
        self.footer_metrics.pack(side="right", padx=10)

    def _bind_events(self):
        self.filter_var.trace_add("write", lambda *_: self.refresh_topic_panel())

    def connect(self):
        host = self.broker_var.get().strip()
        if not host:
            messagebox.showwarning("Broker inválido", "Informe um IP/Host válido")
            return
        self.mqtt_client.connect(host)

    def clear_log(self):
        self.log_text.delete("1.0", "end")

    def export_data(self):
        out = filedialog.asksaveasfilename(
            title="Exportar dados",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("JSON", "*.json")],
        )
        if not out:
            return

        if out.lower().endswith(".json"):
            self.db.export_json(out)
        else:
            self.db.export_csv(out)
        messagebox.showinfo("Exportação", f"Dados exportados para:\n{out}")

    def plot_selected_signal(self):
        selected = self.topic_list.curselection()
        if not selected:
            messagebox.showwarning("Seleção", "Selecione um tópico no painel TOPICS DISCOVERED")
            return

        topic_line = self.topic_list.get(selected[0])
        topic = topic_line.split(" | ")[0]
        dtype = self.analyzer.topics.get(topic, {}).get("type")
        if dtype not in {"int", "float"}:
            messagebox.showwarning("Tópico inválido", "Selecione um tópico numérico (int/float)")
            return

        fig, ax = plt.subplots()
        fig.canvas.manager.set_window_title(f"Plot Signal - {topic}")

        def update(_frame):
            points = list(self.analyzer.value_history.get(topic, []))
            ax.clear()
            if not points:
                ax.set_title(f"{topic} (sem dados)")
                return
            y = [p[1] for p in points]
            x = list(range(len(y)))
            ax.plot(x, y, color="#00bfff", linewidth=1.5)
            ax.set_title(f"{topic} - últimos {len(y)} valores")
            ax.set_facecolor("#111111")
            fig.patch.set_facecolor("#1e1e1e")
            ax.grid(True, alpha=0.2)

        FuncAnimation(fig, update, interval=500)
        plt.show()

    def _ui_loop(self):
        processed = 0
        while processed < 1000:
            try:
                item = self.queue.get_nowait()
            except Empty:
                break
            self._process_event(item)
            processed += 1

        self.refresh_topic_panel()
        self.refresh_current_values()
        self.refresh_statistics()
        self.root.after(80, self._ui_loop)

    def _metrics_tick(self):
        self.analyzer.register_tick(self.msg_count_last_second)
        self.msg_count_last_second = 0

        status = "Conectado" if self.analyzer.connected else "Desconectado"
        self.indicator.config(fg="#6df06d" if self.analyzer.connected else "#ff4d4d")
        self.footer_metrics.config(
            text=(
                f"Status: {status} | Mensagens totais: {self.analyzer.total_messages} | "
                f"Mensagens/s: {self.analyzer.messages_per_second_window[-1] if self.analyzer.messages_per_second_window else 0} | "
                f"Tempo conectado: {self.analyzer.get_connected_time()}"
            )
        )
        self.root.after(1000, self._metrics_tick)

    def _process_event(self, event):
        etype = event.get("type")
        if etype == "status":
            self.status_var.set(event["message"])
        elif etype == "connected":
            self.status_var.set(event["message"])
            self._append_log("[INFO] Conectado ao broker MQTT\n", color="#9eff9e")
        elif etype == "disconnected":
            self.status_var.set(event["message"])
            self._append_log("[WARN] Desconectado. Reconexão automática ativa.\n", color="#ffb347")
        elif etype == "error":
            self.status_var.set(event["message"])
            self._append_log(f"[ERROR] {event['message']}\n", color="#ff7070")
        elif etype == "message":
            self.msg_count_last_second += 1
            self._handle_message(event)

    def _handle_message(self, event):
        topic = event["topic"]
        payload = event["payload"]
        ts = event["timestamp"]
        raw_payload = event["raw_payload"]

        dtype, parsed = self.analyzer.register_message(topic, payload, ts)

        show_payload = payload
        if dtype == "json":
            try:
                show_payload = json.dumps(json.loads(payload), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass

        if not self.pause_log_var.get():
            self._append_log(f"[{ts}] {topic} -> {show_payload}\n")

        if self.record_arcon_only.get():
            arc_value = self.analyzer.current_values.get(ARC_TOPIC)
            if not isinstance(arc_value, bool) or not arc_value:
                return

        value_for_db = str(parsed) if not isinstance(parsed, (dict, list)) else json.dumps(parsed, ensure_ascii=False)
        self.db.insert_message(ts, topic, value_for_db, raw_payload)

        if self.analyzer.events:
            latest = self.analyzer.events[-1]
            if latest["timestamp"] == ts and latest["topic"] == topic:
                self._append_event(latest)

    def _append_log(self, text, color="white"):
        tag = f"tag_{color}"
        if not self.log_text.tag_cget(tag, "foreground"):
            self.log_text.tag_configure(tag, foreground=color)
        self.log_text.insert("end", text, tag)
        self.log_text.see("end")

    def _append_event(self, event):
        line = (
            f"EVENT DETECTED | {event['timestamp']} | {event['topic']} | "
            f"{event['old']} -> {event['new']}\n"
        )
        self.event_text.insert("end", line)
        self.event_text.see("end")

    def refresh_topic_panel(self):
        flt = self.filter_var.get().strip().lower()
        entries = []
        for topic, meta in sorted(self.analyzer.topics.items()):
            if flt and flt not in topic.lower():
                continue
            activity = self.analyzer.get_activity(topic)
            entry = (
                f"{topic} | Msg:{meta['count']} | Tipo:{meta['type']} | "
                f"Mudanças:{meta['changes']} | {activity}"
            )
            entries.append(entry)

        current = self.topic_list.get(0, "end")
        if tuple(entries) != current:
            self.topic_list.delete(0, "end")
            for line in entries:
                self.topic_list.insert("end", line)

    def refresh_current_values(self):
        entries = []
        for topic, value in sorted(self.analyzer.current_values.items()):
            if isinstance(value, bool):
                v = "TRUE" if value else "FALSE"
            elif isinstance(value, (dict, list)):
                v = json.dumps(value, ensure_ascii=False)
            else:
                v = str(value)
            entries.append(f"{topic} -> {v}")

        current = self.current_list.get(0, "end")
        if tuple(entries) != current:
            self.current_list.delete(0, "end")
            for line in entries:
                self.current_list.insert("end", line)

    def refresh_statistics(self):
        text = (
            f"Total topics: {len(self.analyzer.topics)}\n"
            f"Total messages: {self.analyzer.total_messages}\n"
            f"Average messages/sec: {self.analyzer.get_avg_mps():.2f}\n"
            f"Signals detected: {len(self.analyzer.detected_signals)}"
        )
        self.stats_label.config(text=text)

    def on_close(self):
        self.mqtt_client.disconnect()
        self.db.close()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = PhoenixMonitorUI(root)
    root.mainloop()
