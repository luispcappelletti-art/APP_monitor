import json
import threading
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
import tkinter as tk
from tkinter import messagebox

# ===== CONFIGURAÇÃO PADRÃO =====
DEFAULT_BROKER = "100.96.164.3"  # Pode ser IP local ou Tailscale
PORT = 1884
USERNAME = "PhoenixBroker"
PASSWORD = "Broker2022"
LOG_FILE = Path("mqtt_log_completo.txt")
UPTIME_TOPICS = {"Phoenix/Phoenix/Uptime", "Phoenix/Managed/Uptime"}


class PhoenixMonitor:
    def __init__(self, root):
        self.root = root
        self.root.title("Phoenix Monitor - Tempo Real")
        self.root.geometry("1250x730")
        self.root.configure(bg="#1e1e1e")

        self.hide_phoenix_uptime = tk.BooleanVar(value=True)
        self.hide_managed_uptime = tk.BooleanVar(value=True)
        self.broker_var = tk.StringVar(value=DEFAULT_BROKER)
        self.status_var = tk.StringVar(value="Desconectado")
        self.total_messages = 0

        self.client = None
        self.mqtt_thread = None
        self._lock = threading.Lock()

        self.create_ui()
        self.start_mqtt()

    # ================= UI =================
    def create_ui(self):
        top = tk.Frame(self.root, bg="#2b2b2b")
        top.pack(fill="x", padx=10, pady=5)

        tk.Label(top, text="IP / Host da máquina:", bg="#2b2b2b", fg="white").pack(
            side="left", padx=(10, 6)
        )

        ip_entry = tk.Entry(
            top,
            textvariable=self.broker_var,
            bg="#1e1e1e",
            fg="white",
            insertbackground="white",
            width=24,
        )
        ip_entry.pack(side="left")

        tk.Button(
            top,
            text="Aplicar",
            command=self.reconnect_with_new_ip,
            bg="#2f6f3e",
            fg="white",
        ).pack(side="left", padx=8)

        tk.Checkbutton(
            top,
            text="Ocultar Phoenix/Phoenix/Uptime",
            variable=self.hide_phoenix_uptime,
            bg="#2b2b2b",
            fg="white",
            selectcolor="#1e1e1e",
        ).pack(side="left", padx=8)

        tk.Checkbutton(
            top,
            text="Ocultar Phoenix/Managed/Uptime",
            variable=self.hide_managed_uptime,
            bg="#2b2b2b",
            fg="white",
            selectcolor="#1e1e1e",
        ).pack(side="left", padx=8)

        tk.Button(top, text="Limpar Log", command=self.clear_log, bg="#444", fg="white").pack(
            side="right", padx=10
        )

        self.log_text = tk.Text(self.root, bg="#121212", fg="white", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(5, 2))

        status = tk.Frame(self.root, bg="#2b2b2b")
        status.pack(fill="x", padx=10, pady=(2, 8))

        tk.Label(status, textvariable=self.status_var, bg="#2b2b2b", fg="#9edb9e").pack(
            side="left", padx=10
        )
        self.counter_label = tk.Label(status, text="Mensagens: 0", bg="#2b2b2b", fg="white")
        self.counter_label.pack(side="right", padx=10)

    # ================= MQTT =================
    def start_mqtt(self):
        self.connect_client(self.broker_var.get().strip())

    def connect_client(self, broker_host):
        broker_host = broker_host.strip()
        if not broker_host:
            messagebox.showwarning("Host inválido", "Informe um IP/Host válido para conectar.")
            return

        with self._lock:
            self.disconnect_client()

            self.client = mqtt.Client()
            self.client.username_pw_set(USERNAME, PASSWORD)
            self.client.on_connect = self.on_connect
            self.client.on_disconnect = self.on_disconnect
            self.client.on_message = self.on_message

            self.status_var.set(f"Conectando em {broker_host}:{PORT}...")

            self.mqtt_thread = threading.Thread(
                target=self.mqtt_loop,
                args=(broker_host,),
                daemon=True,
            )
            self.mqtt_thread.start()

    def disconnect_client(self):
        if not self.client:
            return

        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def reconnect_with_new_ip(self):
        self.connect_client(self.broker_var.get())

    def mqtt_loop(self, broker_host):
        try:
            self.client.connect(broker_host, PORT, 60)
            self.client.loop_forever()
        except Exception as exc:
            self.root.after(
                0,
                lambda: self.status_var.set(f"Falha de conexão ({broker_host}): {exc}"),
            )

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.root.after(0, lambda: self.status_var.set("Conectado ao broker MQTT"))
            self.log("Conectado ao Broker\n")
            client.subscribe("Phoenix/#")
        else:
            self.root.after(0, lambda: self.status_var.set(f"Conexão recusada (rc={rc})"))

    def on_disconnect(self, client, userdata, rc):
        self.root.after(0, lambda: self.status_var.set("Desconectado"))

    def on_message(self, client, userdata, msg):
        topic = msg.topic

        # Filtros visuais
        if self.hide_phoenix_uptime.get() and topic == "Phoenix/Phoenix/Uptime":
            return

        if self.hide_managed_uptime.get() and topic == "Phoenix/Managed/Uptime":
            return

        payload = msg.payload.decode(errors="ignore")

        try:
            data = json.loads(payload)
            timestamp = data.get("Timestamp", "")
            message = data.get("Message", "")

            if not message:
                return

            try:
                dt = datetime.fromisoformat(timestamp)
                timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            entry = f"[{topic}] {timestamp} - {message}\n"
        except json.JSONDecodeError:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"[{topic}] {timestamp} - {payload}\n"

        self.root.after(0, lambda: self.log(entry))

        if topic not in UPTIME_TOPICS:
            self.write_log_file(entry)

    # ================= UTIL =================
    def log(self, text):
        self.total_messages += 1
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.counter_label.config(text=f"Mensagens: {self.total_messages}")

    def write_log_file(self, text):
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(text)

    def clear_log(self):
        self.total_messages = 0
        self.log_text.delete("1.0", "end")
        self.counter_label.config(text="Mensagens: 0")


if __name__ == "__main__":
    root = tk.Tk()
    app = PhoenixMonitor(root)
    root.mainloop()
