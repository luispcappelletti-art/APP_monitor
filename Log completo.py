import tkinter as tk
import paho.mqtt.client as mqtt
import json
import threading
from datetime import datetime

# ===== CONFIGURE AQUI =====
BROKER = "100.96.164.3"   # IP Tailscale da máquina
PORT = 1884              # Porta do broker do Phoenix
USERNAME = "PhoenixBroker"
PASSWORD = "Broker2022"


class PhoenixMonitor:

    def __init__(self, root):
        self.root = root
        self.root.title("Phoenix Monitor - Tempo Real")
        self.root.geometry("1200x700")
        self.root.configure(bg="#1e1e1e")

        self.hide_phoenix_uptime = tk.BooleanVar(value=True)
        self.hide_managed_uptime = tk.BooleanVar(value=True)

        self.create_ui()
        self.start_mqtt()

    # ================= UI =================
    def create_ui(self):

        top = tk.Frame(self.root, bg="#2b2b2b")
        top.pack(fill="x", padx=10, pady=5)

        tk.Checkbutton(
            top,
            text="Ocultar Phoenix/Phoenix/Uptime",
            variable=self.hide_phoenix_uptime,
            bg="#2b2b2b",
            fg="white",
            selectcolor="#1e1e1e"
        ).pack(side="left", padx=10)

        tk.Checkbutton(
            top,
            text="Ocultar Phoenix/Managed/Uptime",
            variable=self.hide_managed_uptime,
            bg="#2b2b2b",
            fg="white",
            selectcolor="#1e1e1e"
        ).pack(side="left", padx=10)

        tk.Button(
            top,
            text="Limpar Log",
            command=self.clear_log,
            bg="#444",
            fg="white"
        ).pack(side="right", padx=10)

        self.log_text = tk.Text(
            self.root,
            bg="#121212",
            fg="white",
            font=("Consolas", 10)
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=5)

    # ================= MQTT =================
    def start_mqtt(self):
        self.client = mqtt.Client()
        self.client.username_pw_set(USERNAME, PASSWORD)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        threading.Thread(target=self.mqtt_loop, daemon=True).start()

    def mqtt_loop(self):
        self.client.connect(BROKER, PORT, 60)
        self.client.loop_forever()

    def on_connect(self, client, userdata, flags, rc):
        self.log("Conectado ao Broker\n")
        client.subscribe("Phoenix/#")

    def on_message(self, client, userdata, msg):

        topic = msg.topic

        # ===== FILTROS =====
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
            except:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            entry = f"{timestamp} - {message}\n"

        except:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"{timestamp} - {payload}\n"

        self.log(entry)

    # ================= UTIL =================
    def log(self, text):
        self.log_text.insert("end", text)
        self.log_text.see("end")

    def clear_log(self):
        self.log_text.delete("1.0", "end")


if __name__ == "__main__":
    root = tk.Tk()
    app = PhoenixMonitor(root)
    root.mainloop()