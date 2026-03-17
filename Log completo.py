import json
import re
from datetime import datetime
from queue import Queue, Empty
from tkinter import filedialog

import paho.mqtt.client as mqtt
import tkinter as tk
from tkinter import ttk


BROKER = "100.96.164.3"
PORT = 1884
USERNAME = "PhoenixBroker"
PASSWORD = "Broker2022"

LOG_FILE = "mqtt_full_log.txt"

FILTER_TOPICS = {
    "Phoenix/Phoenix/Uptime",
    "Phoenix/Managed/Uptime"
}


def parse_message(payload):
    try:
        data = json.loads(payload)
        msg = data.get("Message")
        if not msg:
            msg = data.get("MessageTemplate", {}).get("Text", "")
        return msg, data
    except:
        return payload, payload


def detect_io(message):

    pattern_named = r"(Output|Input)\s+(\d+),\s*([A-Za-z0-9_\-]+)\s+turned\s+(On|Off)"
    pattern_simple = r"(Output|Input)\s+(\d+)\s+turned\s+(On|Off)"

    m = re.search(pattern_named, message)

    if m:
        io_type = m.group(1)
        number = m.group(2)
        name = m.group(3)
        state = m.group(4).lower() == "on"
        return io_type, f"{number} {name}", state

    m = re.search(pattern_simple, message)

    if m:
        io_type = m.group(1)
        number = m.group(2)
        state = m.group(3).lower() == "on"
        return io_type, f"{number}", state

    return None


def detect_state(message):

    m = re.search(r"Update Cnc State to (\w+)", message)

    if m:
        return m.group(1)

    return None


class MQTTClient:

    def __init__(self, queue):
        self.queue = queue
        self.client = None

    def connect(self, host):

        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except:
                pass

        self.client = mqtt.Client(client_id="phoenix_monitor")

        self.client.username_pw_set(USERNAME, PASSWORD)

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.client.connect(host, PORT, 60)

        self.client.loop_start()

    def on_connect(self, client, userdata, flags, rc):

        client.subscribe("Phoenix/#")

        self.queue.put(("status", "Connected"))

    def on_message(self, client, userdata, msg):

        payload = msg.payload.decode(errors="ignore")

        self.queue.put(
            (
                "msg",
                msg.topic,
                payload,
                datetime.now().strftime("%H:%M:%S")
            )
        )


class LEDIndicator:

    def __init__(self, parent, name):

        frame = tk.Frame(parent)
        frame.pack(anchor="w", pady=2, fill="x")

        self.canvas = tk.Canvas(frame, width=16, height=16, highlightthickness=0)
        self.canvas.pack(side="left", padx=4)

        self.circle = self.canvas.create_oval(2, 2, 14, 14, fill="#440000")

        self.label = tk.Label(frame, text=name, anchor="w")
        self.label.pack(side="left", padx=4)

    def set_state(self, state):

        color = "#00ff00" if state else "#550000"

        self.canvas.itemconfig(self.circle, fill=color)


class App:

    def __init__(self, root):

        self.root = root

        self.queue = Queue()

        self.mqtt = MQTTClient(self.queue)

        self.messages = []
        self.received_messages = []

        self.outputs = {}
        self.inputs = {}

        self.cnc_state = tk.StringVar(value="Unknown")

        self.broker = tk.StringVar(value=BROKER)

        self.filter_uptime = tk.BooleanVar(value=True)

        self.build()

        self.loop()

    def build(self):

        top = tk.Frame(self.root)
        top.pack(fill="x")

        tk.Label(top, text="Broker").pack(side="left")

        tk.Entry(top, textvariable=self.broker, width=20).pack(side="left")

        tk.Button(top, text="Connect", command=self.connect).pack(side="left")

        tk.Checkbutton(
            top,
            text="Hide Uptime",
            variable=self.filter_uptime
        ).pack(side="left")

        tk.Button(top, text="Export", command=self.export_messages).pack(side="left", padx=8)

        self.status = tk.Label(top, text="Disconnected")
        self.status.pack(side="right")

        main = tk.PanedWindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True)

        self.main_pane = main

        left = tk.Frame(main)
        main.add(left)

        columns = ("time", "topic", "message")

        self.table = ttk.Treeview(
            left,
            columns=columns,
            show="headings",
            height=30
        )

        self.table.heading("time", text="Time")
        self.table.heading("topic", text="Topic")
        self.table.heading("message", text="Message")

        self.table.column("time", width=80)
        self.table.column("topic", width=260)
        self.table.column("message", width=480)

        self.table.pack(fill="both", expand=True)

        self.table.bind("<<TreeviewSelect>>", self.show_message)

        center = tk.Frame(main)
        main.add(center)

        tk.Label(center, text="Message Details").pack()

        self.details = tk.Text(center, width=60)
        self.details.pack(fill="both", expand=True)

        right = tk.Frame(main, width=300)
        main.add(right)

        state_frame = tk.LabelFrame(right, text="CNC STATE")
        state_frame.pack(fill="x", padx=5, pady=5)

        tk.Label(
            state_frame,
            textvariable=self.cnc_state,
            font=("Arial", 16)
        ).pack()

        outputs_frame = tk.LabelFrame(right, text="OUTPUTS")
        outputs_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.outputs_frame = outputs_frame

        inputs_frame = tk.LabelFrame(right, text="INPUTS")
        inputs_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.inputs_frame = inputs_frame

        self.root.after(100, self.set_equal_panels)

    def set_equal_panels(self):

        self.main_pane.update_idletasks()

        total_width = self.main_pane.winfo_width()

        if total_width <= 0:
            self.root.after(100, self.set_equal_panels)
            return

        one_third = total_width // 3

        self.main_pane.sash_place(0, one_third, 0)
        self.main_pane.sash_place(1, one_third * 2, 0)

    def connect(self):

        self.mqtt.connect(self.broker.get())

    def show_message(self, event):

        sel = self.table.selection()

        if not sel:
            return

        index = int(sel[0])

        topic, payload, ts = self.messages[index]

        self.details.delete("1.0", "end")

        try:
            formatted = json.dumps(json.loads(payload), indent=4)
        except:
            formatted = payload

        self.details.insert("end", formatted)

    def update_led(self, panel, store, name, state):

        if name not in store:

            led = LEDIndicator(panel, name)

            store[name] = led

        store[name].set_state(state)

    def add_message(self, topic, payload, ts):

        parsed_message, _ = parse_message(payload)

        self.received_messages.append((ts, topic, payload, parsed_message))

        if self.filter_uptime.get():
            if topic in FILTER_TOPICS:
                return

        message = parsed_message

        index = len(self.messages)

        self.messages.append((topic, payload, ts))

        self.table.insert(
            "",
            "end",
            iid=index,
            values=(ts, topic, message)
        )

        state = detect_state(message)

        if state:
            self.cnc_state.set(state)

        io = detect_io(message)

        if io:

            io_type, name, state = io

            if io_type == "Output":

                self.update_led(
                    self.outputs_frame,
                    self.outputs,
                    name,
                    state
                )

            else:

                self.update_led(
                    self.inputs_frame,
                    self.inputs,
                    name,
                    state
                )

        with open(LOG_FILE, "a", encoding="utf-8") as f:

            f.write(f"{ts} {topic} {payload}\n")

    def export_messages(self):

        if not self.received_messages:
            self.status.config(text="No messages to export")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        descriptions_path = filedialog.asksaveasfilename(
            title="Save message descriptions",
            defaultextension=".txt",
            initialfile=f"message_descriptions_{timestamp}.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )

        if not descriptions_path:
            return

        full_messages_path = filedialog.asksaveasfilename(
            title="Save full messages (without uptime)",
            defaultextension=".txt",
            initialfile=f"full_messages_without_uptime_{timestamp}.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )

        if not full_messages_path:
            return

        with open(descriptions_path, "w", encoding="utf-8") as descriptions_file:
            for ts, topic, payload, parsed_message in self.received_messages:
                descriptions_file.write(f"{ts} | {topic} | {parsed_message}\n")

        with open(full_messages_path, "w", encoding="utf-8") as full_messages_file:
            for ts, topic, payload, _ in self.received_messages:
                if topic in FILTER_TOPICS:
                    continue
                full_messages_file.write(f"{ts} {topic} {payload}\n")

        self.status.config(text="Export completed")

    def loop(self):

        try:

            while True:

                ev = self.queue.get_nowait()

                if ev[0] == "status":

                    self.status.config(text=ev[1])

                if ev[0] == "msg":

                    topic = ev[1]
                    payload = ev[2]
                    ts = ev[3]

                    self.add_message(topic, payload, ts)

        except Empty:
            pass

        self.root.after(50, self.loop)


root = tk.Tk()

root.title("Phoenix CNC Signal Analyzer")

root.geometry("1600x900")

app = App(root)

root.mainloop()
