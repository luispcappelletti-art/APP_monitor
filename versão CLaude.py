import customtkinter as ctk
from tkinter import ttk
import tkinter as tk
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import paho.mqtt.client as mqtt
import json
import threading
import re
import time
from datetime import datetime, timedelta
from collections import deque, defaultdict
import os
from tkcalendar import DateEntry

# Configurações MQTT
BROKER = "127.0.0.1"
PORT = 1884
USERNAME = "PhoenixBroker"
PASSWORD = "Broker2022"

# Arquivos de persistência (mantidos iguais)
ARQ_HIST = "historico_cortes.json"
ARQ_PROC = "processos_config.json"
ARQ_EXEC = "execucao_ativa.json"
ARQ_ESTADO = "estado_maquina.json"
ARQ_TURNO = "config_turno.json"

# Configuração do tema
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
plt.style.use('dark_background')


class PhoenixMESPro:
    """Sistema MES Industrial com interface profissional"""

    def __init__(self, root):
        self.root = root
        self.root.title("Phoenix MES Industrial Pro")
        self.root.geometry("1920x1080")

        # Dados de processos
        self.processos = self.carregar_json(ARQ_PROC)

        # Estado da máquina
        self.reset_tempos()
        self.programa = None
        self.origem = None
        self.process_id = None
        self.linhas_eia = 0

        # Métricas para dashboard
        self.historico_estados = deque(maxlen=100)
        self.tempo_por_estado = defaultdict(float)
        self.perfuracoes_historico = deque(maxlen=60)
        self.eficiencia_horaria = deque(maxlen=24)

        # Análise de eficiência
        self.tempo_total_operacao = 0
        self.tempo_total_ocioso = 0
        self.inicio_sessao = datetime.now()

        # Filtros de período
        self.data_inicio_filtro = None
        self.data_fim_filtro = None

        # Configuração de turno
        self.config_turno = self.carregar_config_turno()

        # UI
        self.criar_interface()

        # Inicialização
        self.carregar_estado_maquina()
        self.carregar_historico()
        self.recuperar_execucao()
        self.iniciar_mqtt()
        self.loop_tempo()
        self.atualizar_dashboard()

    # ==================== JSON (mantido igual) ====================

    def carregar_json(self, arquivo):
        """Carrega dados de arquivo JSON"""
        if os.path.exists(arquivo):
            try:
                with open(arquivo, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return {} if arquivo != ARQ_HIST else []
        return {} if arquivo != ARQ_HIST else []

    def salvar_json(self, arquivo, dados):
        """Salva dados em arquivo JSON"""
        with open(arquivo, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=4, ensure_ascii=False)

    def carregar_config_turno(self):
        """Carrega configuração de turno para cálculo de disponibilidade."""
        padrao = {
            "inicio": "07:30",
            "fim": "17:30",
            "almoco_inicio": "12:00",
            "almoco_fim": "13:00"
        }
        dados = self.carregar_json(ARQ_TURNO)
        if not isinstance(dados, dict):
            return padrao

        for chave, valor in padrao.items():
            if chave not in dados or not isinstance(dados.get(chave), str):
                dados[chave] = valor
        return dados

    # ==================== RESET ====================

    def reset_tempos(self):
        """Reseta contadores de tempo"""
        self.perfuracoes = 0
        self.program_running = False
        self.estado = "IDLE"
        self.estado_inicio = None
        self.tempo_corte = 0
        self.tempo_traverse = 0
        self.tempo_pausa = 0

    def reset_programa_completo(self):
        """Reseta todos os dados do programa"""
        self.programa = None
        self.origem = None
        self.process_id = None
        self.linhas_eia = 0
        self.reset_tempos()

    # ==================== ESTADO MÁQUINA (mantido igual) ====================

    def salvar_estado_maquina(self):
        """Salva estado atual da máquina"""
        dados = {
            "programa": self.programa,
            "origem": self.origem,
            "processo_id": self.process_id,
            "linhas_eia": self.linhas_eia
        }
        self.salvar_json(ARQ_ESTADO, dados)

    def carregar_estado_maquina(self):
        """Carrega estado salvo da máquina"""
        dados = self.carregar_json(ARQ_ESTADO)
        if not dados:
            return

        self.programa = dados.get("programa")
        self.origem = dados.get("origem")
        self.process_id = dados.get("processo_id")
        self.linhas_eia = dados.get("linhas_eia", 0)

        if self.programa:
            self.atualizar_info_programa()

    # ==================== EXECUÇÃO ATIVA (mantido igual) ====================

    def salvar_execucao(self):
        """Salva execução ativa"""
        if not self.program_running:
            return

        dados = {
            "programa": self.programa,
            "origem": self.origem,
            "processo_id": self.process_id,
            "linhas_eia": self.linhas_eia,
            "perfuracoes": self.perfuracoes,
            "estado": self.estado,
            "tempo_corte": self.tempo_corte,
            "tempo_traverse": self.tempo_traverse,
            "tempo_pausa": self.tempo_pausa,
            "estado_inicio": self.estado_inicio
        }
        self.salvar_json(ARQ_EXEC, dados)

    def recuperar_execucao(self):
        """Recupera execução interrompida"""
        dados = self.carregar_json(ARQ_EXEC)
        if not dados:
            return

        self.programa = dados.get("programa")
        self.origem = dados.get("origem")
        self.process_id = dados.get("processo_id")
        self.linhas_eia = dados.get("linhas_eia", 0)
        self.perfuracoes = dados.get("perfuracoes", 0)
        self.estado = dados.get("estado", "IDLE")
        self.tempo_corte = dados.get("tempo_corte", 0)
        self.tempo_traverse = dados.get("tempo_traverse", 0)
        self.tempo_pausa = dados.get("tempo_pausa", 0)
        self.estado_inicio = dados.get("estado_inicio")
        self.program_running = True

        self.atualizar_info_programa()

    def limpar_execucao(self):
        """Remove arquivo de execução ativa"""
        if os.path.exists(ARQ_EXEC):
            os.remove(ARQ_EXEC)

    # ==================== INTERFACE ====================

    def criar_interface(self):
        """Cria interface moderna e profissional"""

        # Container principal
        main_container = ctk.CTkFrame(self.root, fg_color="#0a0a0a")
        main_container.pack(fill="both", expand=True)

        # ========== HEADER ==========
        header = ctk.CTkFrame(main_container, height=100, fg_color="#1a1a2e")
        header.pack(fill="x", padx=10, pady=10)
        header.pack_propagate(False)

        # Logo e título
        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(side="left", padx=30, pady=15)

        ctk.CTkLabel(
            title_frame,
            text="⚙️ PHOENIX MES INDUSTRIAL",
            font=ctk.CTkFont(size=32, weight="bold"),
            text_color="#00d9ff"
        ).pack(anchor="w")

        self.mqtt_status = ctk.CTkLabel(
            title_frame,
            text="● MQTT: Desconectado",
            font=ctk.CTkFont(size=13),
            text_color="#ff4444"
        )
        self.mqtt_status.pack(anchor="w")

        # Status da máquina (grande)
        status_frame = ctk.CTkFrame(header, fg_color="#16213e", corner_radius=15)
        status_frame.pack(side="right", padx=30, pady=15)

        self.status_display = ctk.CTkLabel(
            status_frame,
            text="IDLE",
            font=ctk.CTkFont(size=48, weight="bold"),
            text_color="#ffffff",
            width=250,
            height=70
        )
        self.status_display.pack(padx=20, pady=10)

        # ========== TABS ==========
        self.tabview = ctk.CTkTabview(main_container, fg_color="#16213e", corner_radius=10)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=10)

        # Tabs
        self.tab_dashboard = self.tabview.add("🎯 Dashboard")
        self.tab_eficiencia = self.tabview.add("📊 Análise de Eficiência")
        self.tab_historico = self.tabview.add("📜 Histórico de Produção")
        self.tab_processos = self.tabview.add("⚙️ Configuração de Processos")

        self.criar_dashboard()
        self.criar_eficiencia()
        self.criar_historico()
        self.criar_configuracao()

    def criar_dashboard(self):
        """Cria aba do dashboard com métricas e gráficos"""

        # Layout em 2 colunas
        left_panel = ctk.CTkFrame(self.tab_dashboard, fg_color="transparent")
        left_panel.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        right_panel = ctk.CTkFrame(self.tab_dashboard, fg_color="transparent", width=400)
        right_panel.pack(side="right", fill="both", padx=10, pady=10)
        right_panel.pack_propagate(False)

        # ===== PROGRAMA ATUAL =====
        programa_frame = ctk.CTkFrame(left_panel, fg_color="#1a1a2e")
        programa_frame.pack(fill="x", pady=10)

        ctk.CTkLabel(
            programa_frame,
            text="📋 PROGRAMA ATIVO",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=10)

        self.programa_label = ctk.CTkLabel(
            programa_frame,
            text="Nenhum programa carregado",
            font=ctk.CTkFont(size=16),
            text_color="#ffffff"
        )
        self.programa_label.pack(pady=5)

        self.origem_label = ctk.CTkLabel(
            programa_frame,
            text="",
            font=ctk.CTkFont(size=14),
            text_color="#888888"
        )
        self.origem_label.pack(pady=5)

        self.processo_label = ctk.CTkLabel(
            programa_frame,
            text="",
            font=ctk.CTkFont(size=14),
            text_color="#888888"
        )
        self.processo_label.pack(pady=(0, 10))

        # ===== MÉTRICAS PRINCIPAIS =====
        metrics_frame = ctk.CTkFrame(left_panel, fg_color="#1a1a2e")
        metrics_frame.pack(fill="x", pady=10)

        ctk.CTkLabel(
            metrics_frame,
            text="⏱️ TEMPOS DE EXECUÇÃO",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=10)

        # Grid de métricas
        metrics_grid = ctk.CTkFrame(metrics_frame, fg_color="transparent")
        metrics_grid.pack(fill="x", padx=20, pady=10)

        # Tempo de Corte
        self.metric_corte = self.criar_metric_card(
            metrics_grid, "🔴 TEMPO DE CORTE", "00:00:00", "#e63946", 0, 0
        )

        # Tempo de Deslocamento
        self.metric_traverse = self.criar_metric_card(
            metrics_grid, "🔵 DESLOCAMENTO", "00:00:00", "#1d3557", 0, 1
        )

        # Tempo de Pausa
        self.metric_pausa = self.criar_metric_card(
            metrics_grid, "🟡 PAUSAS", "00:00:00", "#f77f00", 1, 0
        )

        # Tempo Total
        self.metric_total = self.criar_metric_card(
            metrics_grid, "⏰ TEMPO TOTAL", "00:00:00", "#06ffa5", 1, 1
        )

        # Perfurações
        perfuracoes_frame = ctk.CTkFrame(metrics_frame, fg_color="#0d1b2a", corner_radius=10)
        perfuracoes_frame.pack(fill="x", padx=20, pady=(10, 20))

        ctk.CTkLabel(
            perfuracoes_frame,
            text="🔨 PERFURAÇÕES",
            font=ctk.CTkFont(size=14),
            text_color="#888888"
        ).pack(pady=(15, 5))

        self.perfuracoes_label = ctk.CTkLabel(
            perfuracoes_frame,
            text="0",
            font=ctk.CTkFont(size=42, weight="bold"),
            text_color="#00d9ff"
        )
        self.perfuracoes_label.pack(pady=(5, 15))

        # ===== GRÁFICOS =====
        # Gráfico de distribuição de tempo
        graph_frame1 = ctk.CTkFrame(left_panel, fg_color="#1a1a2e")
        graph_frame1.pack(fill="both", expand=True, pady=10)

        ctk.CTkLabel(
            graph_frame1,
            text="📊 DISTRIBUIÇÃO DE TEMPO",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=10)

        self.fig1, self.ax1 = plt.subplots(figsize=(8, 4), facecolor='#1a1a2e')
        self.ax1.set_facecolor('#0d1b2a')
        self.canvas1 = FigureCanvasTkAgg(self.fig1, graph_frame1)
        self.canvas1.get_tk_widget().pack(fill="both", expand=True, padx=15, pady=15)

        # ===== PAINEL DIREITO - ESTATÍSTICAS =====

        # Estatísticas de hoje
        stats_frame = ctk.CTkFrame(right_panel, fg_color="#1a1a2e")
        stats_frame.pack(fill="both", expand=True, pady=10)

        ctk.CTkLabel(
            stats_frame,
            text="📈 ESTATÍSTICAS DE HOJE",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=15)

        self.stats_text = ctk.CTkTextbox(
            stats_frame,
            fg_color="#0d1b2a",
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="none"
        )
        self.stats_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Últimos eventos
        eventos_frame = ctk.CTkFrame(right_panel, fg_color="#1a1a2e")
        eventos_frame.pack(fill="both", expand=True, pady=10)

        ctk.CTkLabel(
            eventos_frame,
            text="🔔 EVENTOS RECENTES",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=15)

        self.eventos_text = ctk.CTkTextbox(
            eventos_frame,
            fg_color="#0d1b2a",
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word"
        )
        self.eventos_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

    def criar_eficiencia(self):
        """Cria aba de análise de eficiência com métricas avançadas"""

        # Container principal
        main_container = ctk.CTkFrame(self.tab_eficiencia, fg_color="transparent")
        main_container.pack(fill="both", expand=True, padx=10, pady=10)

        # ===== FILTROS DE PERÍODO =====
        filtro_frame = ctk.CTkFrame(main_container, fg_color="#1a1a2e", height=120)
        filtro_frame.pack(fill="x", pady=(0, 10))
        filtro_frame.pack_propagate(False)

        ctk.CTkLabel(
            filtro_frame,
            text="📅 FILTROS DE PERÍODO",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=10)

        filtro_controls = ctk.CTkFrame(filtro_frame, fg_color="transparent")
        filtro_controls.pack(pady=10)

        # Data início
        ctk.CTkLabel(
            filtro_controls,
            text="Data Início:",
            font=ctk.CTkFont(size=13)
        ).grid(row=0, column=0, padx=10, pady=5)

        self.data_inicio = DateEntry(
            filtro_controls,
            width=15,
            background='#1a1a2e',
            foreground='white',
            borderwidth=2,
            font=("Arial", 11),
            date_pattern='dd/mm/yyyy'
        )
        self.data_inicio.grid(row=0, column=1, padx=10, pady=5)

        # Data fim
        ctk.CTkLabel(
            filtro_controls,
            text="Data Fim:",
            font=ctk.CTkFont(size=13)
        ).grid(row=0, column=2, padx=10, pady=5)

        self.data_fim = DateEntry(
            filtro_controls,
            width=15,
            background='#1a1a2e',
            foreground='white',
            borderwidth=2,
            font=("Arial", 11),
            date_pattern='dd/mm/yyyy'
        )
        self.data_fim.grid(row=0, column=3, padx=10, pady=5)

        # Botões de filtro rápido
        btn_frame = ctk.CTkFrame(filtro_controls, fg_color="transparent")
        btn_frame.grid(row=0, column=4, columnspan=4, padx=20)

        ctk.CTkButton(
            btn_frame,
            text="Hoje",
            command=self.filtro_hoje,
            fg_color="#1d3557",
            hover_color="#14213d",
            width=80,
            height=30
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            btn_frame,
            text="Esta Semana",
            command=self.filtro_semana,
            fg_color="#1d3557",
            hover_color="#14213d",
            width=100,
            height=30
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            btn_frame,
            text="Este Mês",
            command=self.filtro_mes,
            fg_color="#1d3557",
            hover_color="#14213d",
            width=80,
            height=30
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            btn_frame,
            text="🔍 Aplicar Filtro",
            command=self.aplicar_filtro_eficiencia,
            fg_color="#06ffa5",
            hover_color="#05d98a",
            text_color="#000000",
            font=ctk.CTkFont(weight="bold"),
            width=120,
            height=30
        ).pack(side="left", padx=10)

        # Configuração de turno
        ctk.CTkLabel(
            filtro_controls,
            text="Turno:",
            font=ctk.CTkFont(size=13)
        ).grid(row=1, column=0, padx=10, pady=5, sticky="e")

        self.turno_inicio_entry = ctk.CTkEntry(filtro_controls, width=75)
        self.turno_inicio_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")

        ctk.CTkLabel(
            filtro_controls,
            text="até",
            font=ctk.CTkFont(size=12)
        ).grid(row=1, column=1, padx=(85, 0), pady=5, sticky="w")

        self.turno_fim_entry = ctk.CTkEntry(filtro_controls, width=75)
        self.turno_fim_entry.grid(row=1, column=2, padx=5, pady=5, sticky="w")

        ctk.CTkLabel(
            filtro_controls,
            text="Almoço:",
            font=ctk.CTkFont(size=13)
        ).grid(row=1, column=3, padx=10, pady=5, sticky="e")

        self.almoco_inicio_entry = ctk.CTkEntry(filtro_controls, width=75)
        self.almoco_inicio_entry.grid(row=1, column=4, padx=5, pady=5, sticky="w")

        ctk.CTkLabel(
            filtro_controls,
            text="até",
            font=ctk.CTkFont(size=12)
        ).grid(row=1, column=4, padx=(85, 0), pady=5, sticky="w")

        self.almoco_fim_entry = ctk.CTkEntry(filtro_controls, width=75)
        self.almoco_fim_entry.grid(row=1, column=5, padx=5, pady=5, sticky="w")

        ctk.CTkButton(
            filtro_controls,
            text="💾 Salvar Turno",
            command=self.salvar_config_turno,
            fg_color="#1d3557",
            hover_color="#14213d",
            width=130,
            height=30
        ).grid(row=1, column=6, padx=15, pady=5)

        self.turno_inicio_entry.insert(0, self.config_turno.get("inicio", "07:30"))
        self.turno_fim_entry.insert(0, self.config_turno.get("fim", "17:30"))
        self.almoco_inicio_entry.insert(0, self.config_turno.get("almoco_inicio", "12:00"))
        self.almoco_fim_entry.insert(0, self.config_turno.get("almoco_fim", "13:00"))

        # ===== LAYOUT EM 2 COLUNAS =====
        content_frame = ctk.CTkFrame(main_container, fg_color="transparent")
        content_frame.pack(fill="both", expand=True)

        # Coluna esquerda - KPIs e Gráficos
        left_column = ctk.CTkFrame(content_frame, fg_color="transparent")
        left_column.pack(side="left", fill="both", expand=True, padx=(0, 5))

        # Coluna direita - Métricas detalhadas
        right_column = ctk.CTkFrame(content_frame, fg_color="transparent", width=400)
        right_column.pack(side="right", fill="both", padx=(5, 0))
        right_column.pack_propagate(False)

        # ===== KPIs PRINCIPAIS =====
        kpis_frame = ctk.CTkFrame(left_column, fg_color="#1a1a2e")
        kpis_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            kpis_frame,
            text="📊 INDICADORES DE DESEMPENHO (OEE)",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=15)

        # Grid de KPIs
        kpis_grid = ctk.CTkFrame(kpis_frame, fg_color="transparent")
        kpis_grid.pack(padx=20, pady=(0, 20))

        # OEE Total
        self.kpi_oee = self.criar_kpi_card(
            kpis_grid, "⭐ OEE GERAL", "0%", "#06ffa5", 0, 0, large=True
        )

        # Disponibilidade
        self.kpi_disponibilidade = self.criar_kpi_card(
            kpis_grid, "🟢 DISPONIBILIDADE", "0%", "#4caf50", 0, 1
        )

        # Performance
        self.kpi_performance = self.criar_kpi_card(
            kpis_grid, "🔵 PERFORMANCE", "0%", "#2196f3", 0, 2
        )

        # Qualidade (fixo em 100% por enquanto)
        self.kpi_qualidade = self.criar_kpi_card(
            kpis_grid, "🟡 QUALIDADE", "100%", "#ffc107", 0, 3
        )

        # ===== GRÁFICOS =====

        # Gráfico de Pizza - Tempo Produtivo vs Ocioso
        graph1_frame = ctk.CTkFrame(left_column, fg_color="#1a1a2e")
        graph1_frame.pack(fill="both", expand=True, pady=(0, 10))

        ctk.CTkLabel(
            graph1_frame,
            text="⚖️ TEMPO PRODUTIVO vs TEMPO OCIOSO",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=10)

        self.fig_produtivo, self.ax_produtivo = plt.subplots(figsize=(6, 4), facecolor='#1a1a2e')
        self.ax_produtivo.set_facecolor('#0d1b2a')
        self.canvas_produtivo = FigureCanvasTkAgg(self.fig_produtivo, graph1_frame)
        self.canvas_produtivo.get_tk_widget().pack(fill="both", expand=True, padx=15, pady=15)

        # Gráfico de Barras - Distribuição por Estado
        graph2_frame = ctk.CTkFrame(left_column, fg_color="#1a1a2e")
        graph2_frame.pack(fill="both", expand=True)

        ctk.CTkLabel(
            graph2_frame,
            text="📊 DISTRIBUIÇÃO DE TEMPO POR ESTADO",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=10)

        self.fig_estados, self.ax_estados = plt.subplots(figsize=(6, 4), facecolor='#1a1a2e')
        self.ax_estados.set_facecolor('#0d1b2a')
        self.canvas_estados = FigureCanvasTkAgg(self.fig_estados, graph2_frame)
        self.canvas_estados.get_tk_widget().pack(fill="both", expand=True, padx=15, pady=15)

        # ===== COLUNA DIREITA - MÉTRICAS DETALHADAS =====

        # Resumo do período
        resumo_frame = ctk.CTkFrame(right_column, fg_color="#1a1a2e")
        resumo_frame.pack(fill="both", expand=True, pady=(0, 10))

        ctk.CTkLabel(
            resumo_frame,
            text="📈 RESUMO DO PERÍODO",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=15)

        self.resumo_text = ctk.CTkTextbox(
            resumo_frame,
            fg_color="#0d1b2a",
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word"
        )
        self.resumo_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Análise de produtividade
        produtividade_frame = ctk.CTkFrame(right_column, fg_color="#1a1a2e")
        produtividade_frame.pack(fill="both", expand=True, pady=(0, 10))

        ctk.CTkLabel(
            produtividade_frame,
            text="⚡ ANÁLISE DE PRODUTIVIDADE",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=15)

        self.produtividade_text = ctk.CTkTextbox(
            produtividade_frame,
            fg_color="#0d1b2a",
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word"
        )
        self.produtividade_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Recomendações
        recomendacoes_frame = ctk.CTkFrame(right_column, fg_color="#1a1a2e")
        recomendacoes_frame.pack(fill="both", expand=True)

        ctk.CTkLabel(
            recomendacoes_frame,
            text="💡 INSIGHTS E RECOMENDAÇÕES",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=15)

        self.recomendacoes_text = ctk.CTkTextbox(
            recomendacoes_frame,
            fg_color="#0d1b2a",
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word"
        )
        self.recomendacoes_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Inicializa com dados de hoje
        self.filtro_hoje()
        self.aplicar_filtro_eficiencia()

    def criar_historico(self):
        """Cria aba de histórico de produção"""

        # Controles superiores
        controls = ctk.CTkFrame(self.tab_historico, fg_color="#1a1a2e", height=80)
        controls.pack(fill="x", padx=10, pady=10)
        controls.pack_propagate(False)

        ctk.CTkLabel(
            controls,
            text="📊 HISTÓRICO DE PRODUÇÃO",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#00d9ff"
        ).pack(side="left", padx=20, pady=20)

        btn_frame = ctk.CTkFrame(controls, fg_color="transparent")
        btn_frame.pack(side="right", padx=20)

        ctk.CTkButton(
            btn_frame,
            text="🗑️ Excluir Selecionado",
            command=self.excluir_registro,
            fg_color="#e63946",
            hover_color="#c1121f",
            font=ctk.CTkFont(size=14),
            width=180,
            height=40
        ).pack(side="right", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="📊 Exportar CSV",
            command=self.exportar_csv,
            fg_color="#1d3557",
            hover_color="#14213d",
            font=ctk.CTkFont(size=14),
            width=180,
            height=40
        ).pack(side="right", padx=5)

        # Tabela de histórico
        table_frame = ctk.CTkFrame(self.tab_historico, fg_color="#1a1a2e")
        table_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Criar Treeview com estilo escuro
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background="#0d1b2a",
            foreground="white",
            fieldbackground="#0d1b2a",
            borderwidth=0,
            font=("Consolas", 11)
        )
        style.configure("Treeview.Heading", background="#1a1a2e", foreground="#00d9ff",
                        font=("Consolas", 12, "bold"))
        style.map("Treeview", background=[("selected", "#00d9ff")])

        # Scrollbar
        scrollbar = ttk.Scrollbar(table_frame)
        scrollbar.pack(side="right", fill="y")

        columns = (
            "programa", "origem", "processo",
            "linhas", "perfuracoes",
            "corte", "desloc",
            "pausa", "total", "data"
        )

        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            yscrollcommand=scrollbar.set,
            style="Treeview"
        )
        scrollbar.config(command=self.tree.yview)

        headers = {
            "programa": "PROGRAMA",
            "origem": "ORIGEM",
            "processo": "PROCESSO",
            "linhas": "LINHAS",
            "perfuracoes": "PERFUR.",
            "corte": "T. CORTE",
            "desloc": "T. DESLOC.",
            "pausa": "T. PAUSA",
            "total": "TOTAL",
            "data": "DATA/HORA"
        }

        for col in columns:
            self.tree.heading(col, text=headers[col])
            width = 200 if col == "data" else 150 if col == "programa" else 120
            self.tree.column(col, width=width, anchor="center")

        self.tree.pack(fill="both", expand=True, padx=15, pady=15)

    def criar_configuracao(self):
        """Cria aba de configuração de processos"""

        # Container principal
        config_container = ctk.CTkFrame(self.tab_processos, fg_color="transparent")
        config_container.pack(fill="both", expand=True, padx=20, pady=20)

        # ===== FORMULÁRIO =====
        form_frame = ctk.CTkFrame(config_container, fg_color="#1a1a2e")
        form_frame.pack(fill="x", pady=(0, 20))

        ctk.CTkLabel(
            form_frame,
            text="➕ ADICIONAR PROCESSO",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=20)

        # Grid do formulário
        form_grid = ctk.CTkFrame(form_frame, fg_color="transparent")
        form_grid.pack(padx=40, pady=20)

        # ID do Processo
        ctk.CTkLabel(
            form_grid,
            text="ID do Processo:",
            font=ctk.CTkFont(size=14),
            anchor="w"
        ).grid(row=0, column=0, sticky="w", pady=10, padx=(0, 20))

        self.entry_id = ctk.CTkEntry(
            form_grid,
            width=400,
            height=40,
            font=ctk.CTkFont(size=14),
            placeholder_text="Ex: 12345"
        )
        self.entry_id.grid(row=0, column=1, pady=10)

        # Nome do Processo
        ctk.CTkLabel(
            form_grid,
            text="Nome do Processo:",
            font=ctk.CTkFont(size=14),
            anchor="w"
        ).grid(row=1, column=0, sticky="w", pady=10, padx=(0, 20))

        self.entry_nome = ctk.CTkEntry(
            form_grid,
            width=400,
            height=40,
            font=ctk.CTkFont(size=14),
            placeholder_text="Ex: Corte de Chapa 5mm"
        )
        self.entry_nome.grid(row=1, column=1, pady=10)

        # Botão Salvar
        ctk.CTkButton(
            form_frame,
            text="💾 Salvar Processo",
            command=self.salvar_processo,
            fg_color="#06ffa5",
            hover_color="#05d98a",
            text_color="#000000",
            font=ctk.CTkFont(size=16, weight="bold"),
            width=300,
            height=50
        ).pack(pady=(10, 30))

        # ===== LISTA DE PROCESSOS =====
        lista_frame = ctk.CTkFrame(config_container, fg_color="#1a1a2e")
        lista_frame.pack(fill="both", expand=True)

        ctk.CTkLabel(
            lista_frame,
            text="📋 PROCESSOS CADASTRADOS",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#00d9ff"
        ).pack(pady=20)

        self.lista_proc = ctk.CTkTextbox(
            lista_frame,
            fg_color="#0d1b2a",
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="none"
        )
        self.lista_proc.pack(fill="both", expand=True, padx=20, pady=(0, 20))

    def criar_kpi_card(self, parent, label, value, color, row, col, large=False):
        """Cria um card de KPI"""
        card = ctk.CTkFrame(parent, fg_color="#0d1b2a", corner_radius=10)
        card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")

        parent.grid_columnconfigure(col, weight=1)
        parent.grid_rowconfigure(row, weight=1)

        font_size_label = 14 if large else 11
        font_size_value = 48 if large else 32

        ctk.CTkLabel(
            card,
            text=label,
            font=ctk.CTkFont(size=font_size_label),
            text_color="#888888"
        ).pack(pady=(20 if large else 15, 5))

        value_label = ctk.CTkLabel(
            card,
            text=value,
            font=ctk.CTkFont(size=font_size_value, weight="bold"),
            text_color=color
        )
        value_label.pack(pady=(5, 20 if large else 15))

        return value_label

    def criar_metric_card(self, parent, label, value, color, row, col):
        """Cria um card de métrica"""
        card = ctk.CTkFrame(parent, fg_color="#0d1b2a", corner_radius=10)
        card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")

        parent.grid_columnconfigure(col, weight=1)
        parent.grid_rowconfigure(row, weight=1)

        ctk.CTkLabel(
            card,
            text=label,
            font=ctk.CTkFont(size=12),
            text_color="#888888"
        ).pack(pady=(15, 5))

        value_label = ctk.CTkLabel(
            card,
            text=value,
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=color
        )
        value_label.pack(pady=(5, 15))

        return value_label

    def atualizar_info_programa(self):
        """Atualiza informações do programa na interface"""
        if self.programa:
            self.programa_label.configure(text=self.programa)
            self.origem_label.configure(text=f"Origem: {self.origem or 'N/A'}")

            if self.process_id:
                nome_proc = self.processos.get(self.process_id, self.process_id)
                self.processo_label.configure(text=f"Processo: {nome_proc}")

    # ==================== ESTADO ====================

    def mudar_estado(self, novo):
        """Muda o estado da máquina"""
        agora = time.time()

        # Calcula tempo do estado anterior
        if self.estado_inicio:
            delta = agora - self.estado_inicio

            if self.estado == "CUT":
                self.tempo_corte += delta
                self.tempo_por_estado["CUT"] += delta
            elif self.estado == "TRAVERSE":
                self.tempo_traverse += delta
                self.tempo_por_estado["TRAVERSE"] += delta
            elif self.estado == "PAUSE":
                self.tempo_pausa += delta
                self.tempo_por_estado["PAUSE"] += delta

        # Conta perfuração
        if self.estado != "CUT" and novo == "CUT":
            self.perfuracoes += 1
            self.perfuracoes_historico.append((datetime.now(), self.perfuracoes))

        # Registra no histórico
        self.historico_estados.append({
            "timestamp": datetime.now(),
            "estado": novo
        })

        # Atualiza estado
        self.estado = novo
        self.estado_inicio = agora

        # Atualiza UI
        cores_status = {
            "CUT": "#e63946",
            "TRAVERSE": "#1d3557",
            "PAUSE": "#f77f00",
            "IDLE": "#415a77"
        }

        self.status_display.configure(
            text=novo,
            text_color=cores_status.get(novo, "#ffffff")
        )

        # Adiciona evento
        self.adicionar_evento(f"Estado mudou para: {novo}")

        self.salvar_execucao()

    def adicionar_evento(self, texto):
        """Adiciona evento à lista de eventos recentes"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        evento = f"[{timestamp}] {texto}\n"
        self.eventos_text.insert("1.0", evento)

        # Limita a 50 eventos
        linhas = self.eventos_text.get("1.0", "end").split("\n")
        if len(linhas) > 50:
            self.eventos_text.delete("51.0", "end")

    # ==================== TEMPO ====================

    def formatar(self, s):
        """Formata segundos para string legível"""
        s = int(s)
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        if h > 0:
            return f"{h}h {m:02d}m {sec:02d}s"
        if m > 0:
            return f"{m}m {sec:02d}s"
        return f"{sec}s"

    def formatar_hms(self, s):
        """Formata para HH:MM:SS"""
        s = int(s)
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        return f"{h:02d}:{m:02d}:{sec:02d}"

    def loop_tempo(self):
        """Atualiza tempos em tempo real"""
        if self.program_running and self.estado_inicio:
            delta = time.time() - self.estado_inicio

            corte = self.tempo_corte
            trav = self.tempo_traverse
            pausa = self.tempo_pausa

            if self.estado == "CUT":
                corte += delta
            elif self.estado == "TRAVERSE":
                trav += delta
            elif self.estado == "PAUSE":
                pausa += delta

            total = corte + trav + pausa

            # Atualiza métricas
            self.metric_corte.configure(text=self.formatar_hms(corte))
            self.metric_traverse.configure(text=self.formatar_hms(trav))
            self.metric_pausa.configure(text=self.formatar_hms(pausa))
            self.metric_total.configure(text=self.formatar_hms(total))
            self.perfuracoes_label.configure(text=str(self.perfuracoes))

            self.salvar_execucao()

        self.root.after(500, self.loop_tempo)

    # ==================== MQTT ====================

    def iniciar_mqtt(self):
        """Inicia conexão MQTT"""
        self.client = mqtt.Client()
        self.client.username_pw_set(USERNAME, PASSWORD)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        threading.Thread(target=self.loop_mqtt, daemon=True).start()

    def loop_mqtt(self):
        """Loop MQTT"""
        try:
            self.client.connect(BROKER, PORT, 60)
            self.client.loop_forever()
        except Exception as e:
            self.adicionar_evento(f"Erro MQTT: {e}")

    def on_connect(self, client, userdata, flags, rc):
        """Callback de conexão MQTT"""
        self.mqtt_status.configure(
            text="● MQTT: Conectado",
            text_color="#06ffa5"
        )
        self.adicionar_evento("Conectado ao broker MQTT")
        client.subscribe("Phoenix/#")

    def on_message(self, client, userdata, msg):
        """Processa mensagens MQTT"""
        if "Uptime" in msg.topic:
            return

        try:
            data = json.loads(msg.payload.decode(errors="ignore"))
            source = data.get("Properties", {}).get("SourceContext", {}).get("Value", "")
            message = data.get("Message", "")
            self.root.after(0, self.processar_evento, source, message)
        except:
            pass

    # ==================== PROCESSAMENTO ====================

    def processar_evento(self, source, message):
        """Processa eventos MQTT"""

        # Captura arquivo
        if source == "Editor" and ("Read" in message or "Write" in message):
            match = re.search(r'"(.+?)"', message)
            if match:
                caminho = match.group(1)

                if "LastPart.txt" in caminho:
                    return

                nome = os.path.basename(caminho)

                if self.programa != nome:
                    self.reset_tempos()

                self.programa = nome
                self.origem = "Biblioteca" if "ShapeLibrary" in caminho else "Programado"

                self.atualizar_info_programa()
                self.salvar_estado_maquina()
                self.adicionar_evento(f"Programa carregado: {nome}")

        # Captura ID de Processo
        if source == "StationController":
            match = re.search(r'Cache Process:\s*(\d+)', message)
            if match:
                self.process_id = match.group(1)
                self.atualizar_info_programa()
                self.salvar_estado_maquina()

        # Estados
        if "Program_Running turned On" in message:
            self.program_running = True
            self.estado_inicio = time.time()
            self.estado = "TRAVERSE"
            self.adicionar_evento("Programa iniciado")

        if "Traversing" in message:
            self.mudar_estado("TRAVERSE")

        if "Trialing" in message or "Cutting" in message:
            self.mudar_estado("CUT")

        if "Paused" in message:
            self.mudar_estado("PAUSE")

        if "Completed" in message:
            self.finalizar()

    # ==================== FINALIZAÇÃO ====================

    def finalizar(self):
        """Finaliza programa e salva histórico"""
        total = self.tempo_corte + self.tempo_traverse + self.tempo_pausa

        registro = {
            "programa": self.programa,
            "origem": self.origem,
            "processo_id": self.process_id,
            "linhas_eia": self.linhas_eia,
            "perfuracoes": self.perfuracoes,
            "tempo_corte": self.formatar(self.tempo_corte),
            "tempo_deslocamento": self.formatar(self.tempo_traverse),
            "tempo_pausa": self.formatar(self.tempo_pausa),
            "tempo_total": self.formatar(total),
            "data": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        }

        # Salva histórico
        hist = self.carregar_json(ARQ_HIST)
        if not isinstance(hist, list):
            hist = []
        hist.append(registro)
        self.salvar_json(ARQ_HIST, hist)

        # Adiciona na tabela
        self.adicionar_tabela(registro)

        # Limpa execução
        self.limpar_execucao()
        self.reset_tempos()

        self.mudar_estado("IDLE")
        self.adicionar_evento("Programa finalizado com sucesso")

    # ==================== HISTÓRICO ====================

    def adicionar_tabela(self, r):
        """Adiciona registro na tabela de histórico"""
        processo_id = r.get("processo_id", "")
        nome_proc = self.processos.get(processo_id, processo_id)

        self.tree.insert("", 0, values=(
            r.get("programa", ""),
            r.get("origem", ""),
            nome_proc,
            r.get("linhas_eia", 0),
            r.get("perfuracoes", 0),
            r.get("tempo_corte", "0s"),
            r.get("tempo_deslocamento", "0s"),
            r.get("tempo_pausa", "0s"),
            r.get("tempo_total", "0s"),
            r.get("data", "")
        ))

    def carregar_historico(self):
        """Carrega histórico do arquivo"""
        hist = self.carregar_json(ARQ_HIST)
        if isinstance(hist, list):
            for r in hist:
                self.adicionar_tabela(r)

    def excluir_registro(self):
        """Exclui registro selecionado"""
        sel = self.tree.selection()
        if not sel:
            return

        item = sel[0]
        valores = self.tree.item(item)["values"]
        self.tree.delete(item)

        # Remove do arquivo
        hist = self.carregar_json(ARQ_HIST)
        hist = [d for d in hist if d.get("data") != valores[9]]
        self.salvar_json(ARQ_HIST, hist)

        self.adicionar_evento("Registro excluído do histórico")

    def exportar_csv(self):
        """Exporta histórico para CSV"""
        import csv

        hist = self.carregar_json(ARQ_HIST)
        if not hist:
            return

        filename = f"historico_producao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=hist[0].keys())
            writer.writeheader()
            writer.writerows(hist)

        self.adicionar_evento(f"Histórico exportado: {filename}")

    # ==================== CONFIGURAÇÃO ====================

    def salvar_processo(self):
        """Salva novo processo"""
        pid = self.entry_id.get().strip()
        nome = self.entry_nome.get().strip()

        if not pid or not nome:
            return

        self.processos[pid] = nome
        self.salvar_json(ARQ_PROC, self.processos)
        self.atualizar_lista_processos()
        self.recarregar_visual()

        self.entry_id.delete(0, "end")
        self.entry_nome.delete(0, "end")

        self.adicionar_evento(f"Processo cadastrado: {pid} - {nome}")

    def atualizar_lista_processos(self):
        """Atualiza lista de processos"""
        self.lista_proc.delete("1.0", "end")

        texto = "╔════════════════════════════════════════════════════════╗\n"
        texto += "║  ID       │  NOME DO PROCESSO                        ║\n"
        texto += "╠════════════════════════════════════════════════════════╣\n"

        for pid, nome in sorted(self.processos.items()):
            nome_fmt = nome[:40] + "..." if len(nome) > 40 else nome
            texto += f"║  {pid:8s} │  {nome_fmt:40s} ║\n"

        texto += "╚════════════════════════════════════════════════════════╝"

        self.lista_proc.insert("1.0", texto)

    def recarregar_visual(self):
        """Recarrega visualização do histórico"""
        self.tree.delete(*self.tree.get_children())
        self.carregar_historico()

    # ==================== FILTROS DE PERÍODO ====================

    def filtro_hoje(self):
        """Define filtro para hoje"""
        hoje = datetime.now().date()
        self.data_inicio.set_date(hoje)
        self.data_fim.set_date(hoje)

    def filtro_semana(self):
        """Define filtro para esta semana"""
        hoje = datetime.now().date()
        inicio_semana = hoje - timedelta(days=hoje.weekday())
        self.data_inicio.set_date(inicio_semana)
        self.data_fim.set_date(hoje)

    def filtro_mes(self):
        """Define filtro para este mês"""
        hoje = datetime.now().date()
        inicio_mes = hoje.replace(day=1)
        self.data_inicio.set_date(inicio_mes)
        self.data_fim.set_date(hoje)

    def validar_hora_hhmm(self, valor):
        """Valida e normaliza campo de hora no formato HH:MM."""
        valor = (valor or "").strip()
        if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", valor):
            return None
        h, m = valor.split(":")
        return f"{int(h):02d}:{int(m):02d}"

    def segundos_do_dia(self, hhmm):
        """Converte HH:MM para segundos desde 00:00."""
        h, m = [int(x) for x in hhmm.split(":")]
        return h * 3600 + m * 60

    def calcular_tempo_disponivel_turno(self, data_inicio, data_fim):
        """Calcula tempo disponível conforme turno configurado no período."""
        inicio_turno = self.segundos_do_dia(self.config_turno["inicio"])
        fim_turno = self.segundos_do_dia(self.config_turno["fim"])
        almoco_inicio = self.segundos_do_dia(self.config_turno["almoco_inicio"])
        almoco_fim = self.segundos_do_dia(self.config_turno["almoco_fim"])

        if fim_turno <= inicio_turno:
            return 0

        if almoco_fim < almoco_inicio:
            almoco_inicio, almoco_fim = almoco_fim, almoco_inicio

        hoje = datetime.now()
        hoje_data = hoje.date()
        agora_segundos = hoje.hour * 3600 + hoje.minute * 60 + hoje.second

        total = 0
        dia = data_inicio
        while dia <= data_fim:
            if dia > hoje_data:
                break

            limite = fim_turno
            if dia == hoje_data:
                limite = min(fim_turno, agora_segundos)

            if limite > inicio_turno:
                periodo = limite - inicio_turno
                sobreposicao_almoco = max(0, min(limite, almoco_fim) - max(inicio_turno, almoco_inicio))
                total += max(0, periodo - sobreposicao_almoco)

            dia += timedelta(days=1)

        return total

    def salvar_config_turno(self):
        """Salva configuração de turno e atualiza cálculo da eficiência."""
        inicio = self.validar_hora_hhmm(self.turno_inicio_entry.get())
        fim = self.validar_hora_hhmm(self.turno_fim_entry.get())
        almoco_inicio = self.validar_hora_hhmm(self.almoco_inicio_entry.get())
        almoco_fim = self.validar_hora_hhmm(self.almoco_fim_entry.get())

        if not all([inicio, fim, almoco_inicio, almoco_fim]):
            self.adicionar_evento("Formato de hora inválido. Use HH:MM.")
            return

        if self.segundos_do_dia(fim) <= self.segundos_do_dia(inicio):
            self.adicionar_evento("Fim do turno deve ser maior que início.")
            return

        self.config_turno = {
            "inicio": inicio,
            "fim": fim,
            "almoco_inicio": almoco_inicio,
            "almoco_fim": almoco_fim
        }
        self.salvar_json(ARQ_TURNO, self.config_turno)
        self.adicionar_evento(
            f"Turno salvo: {inicio}-{fim} | almoço {almoco_inicio}-{almoco_fim}"
        )
        self.aplicar_filtro_eficiencia()

    def aplicar_filtro_eficiencia(self):
        """Aplica filtro e recalcula métricas de eficiência"""
        try:
            self.data_inicio_filtro = self.data_inicio.get_date()
            self.data_fim_filtro = self.data_fim.get_date()
            self.calcular_metricas_eficiencia()
        except Exception as e:
            self.adicionar_evento(f"Erro ao aplicar filtro: {e}")

    def calcular_metricas_eficiencia(self):
        """Calcula todas as métricas de eficiência para o período filtrado"""

        if not self.data_inicio_filtro or not self.data_fim_filtro:
            return

        # Carrega histórico
        hist = self.carregar_json(ARQ_HIST)
        if not isinstance(hist, list):
            hist = []

        # Filtra por período
        registros_periodo = []
        for r in hist:
            try:
                data_registro = datetime.strptime(r["data"], "%d/%m/%Y %H:%M:%S").date()
                if self.data_inicio_filtro <= data_registro <= self.data_fim_filtro:
                    registros_periodo.append(r)
            except:
                continue

        # Calcula métricas
        total_programas = len(registros_periodo)
        total_perfuracoes = sum(r.get("perfuracoes", 0) for r in registros_periodo)

        # Converte tempos para segundos
        def tempo_para_segundos(tempo_str):
            """Converte string de tempo para segundos"""
            try:
                segundos = 0
                if 'h' in tempo_str:
                    partes = tempo_str.split('h')
                    segundos += int(partes[0]) * 3600
                    tempo_str = partes[1].strip()
                if 'm' in tempo_str:
                    partes = tempo_str.split('m')
                    segundos += int(partes[0]) * 60
                    tempo_str = partes[1].strip()
                if 's' in tempo_str:
                    segundos += int(tempo_str.replace('s', '').strip())
                return segundos
            except:
                return 0

        tempo_corte_total = sum(tempo_para_segundos(r.get("tempo_corte", "0s")) for r in registros_periodo)
        tempo_traverse_total = sum(tempo_para_segundos(r.get("tempo_deslocamento", "0s")) for r in registros_periodo)
        tempo_pausa_total = sum(tempo_para_segundos(r.get("tempo_pausa", "0s")) for r in registros_periodo)
        tempo_total = tempo_corte_total + tempo_traverse_total + tempo_pausa_total

        # Tempo efetivo = corte + deslocamento
        tempo_efetivo = tempo_corte_total + tempo_traverse_total
        tempo_ocioso = tempo_pausa_total

        # Tempo esperado pelo turno configurado (considera dia atual parcial)
        tempo_disponivel = self.calcular_tempo_disponivel_turno(self.data_inicio_filtro, self.data_fim_filtro)

        # Gap operacional: tempo esperado - tempo efetivo
        tempo_parado_operador = max(0, tempo_disponivel - tempo_efetivo)

        # Cálculo OEE simplificado
        # Disponibilidade = Tempo Efetivo / Tempo Disponível
        disponibilidade = (tempo_efetivo / tempo_disponivel * 100) if tempo_disponivel > 0 else 0

        # Performance = Tempo Efetivo / Tempo Total de Operação registrado
        performance = (tempo_efetivo / tempo_total * 100) if tempo_total > 0 else 0

        # Qualidade = 100% (assumindo sem refugo)
        qualidade = 100.0

        # OEE = Disponibilidade × Performance × Qualidade
        oee = (disponibilidade * performance * qualidade) / 10000

        # Atualiza KPIs
        self.kpi_oee.configure(text=f"{oee:.1f}%")
        self.kpi_disponibilidade.configure(text=f"{disponibilidade:.1f}%")
        self.kpi_performance.configure(text=f"{performance:.1f}%")
        self.kpi_qualidade.configure(text=f"{qualidade:.1f}%")

        # Atualiza gráfico de pizza - Produtivo vs Ocioso
        self.ax_produtivo.clear()
        if tempo_total > 0:
            valores = [tempo_efetivo, tempo_ocioso]
            labels = ['Tempo Produtivo', 'Tempo Ocioso']
            colors = ['#06ffa5', '#e63946']
            explode = (0.05, 0)

            self.ax_produtivo.pie(
                valores,
                labels=labels,
                colors=colors,
                autopct='%1.1f%%',
                startangle=90,
                explode=explode,
                textprops={'color': 'white', 'fontsize': 12, 'weight': 'bold'},
                shadow=True
            )
            self.ax_produtivo.set_title(
                f'Esperado: {self.formatar(tempo_disponivel)} | Efetivo: {self.formatar(tempo_efetivo)}',
                color='#00d9ff',
                fontsize=13,
                pad=20
            )
        else:
            self.ax_produtivo.text(
                0.5, 0.5, 'Sem dados no período',
                ha='center', va='center',
                color='#888888',
                fontsize=14
            )

        self.canvas_produtivo.draw()

        # Atualiza gráfico de barras - Distribuição por estado
        self.ax_estados.clear()
        if tempo_total > 0:
            estados = ['Corte', 'Deslocamento', 'Pausa']
            tempos = [tempo_corte_total, tempo_traverse_total, tempo_pausa_total]
            colors = ['#e63946', '#1d3557', '#f77f00']

            bars = self.ax_estados.barh(estados, tempos, color=colors, alpha=0.8)

            # Adiciona valores nas barras
            for bar, tempo in zip(bars, tempos):
                width = bar.get_width()
                self.ax_estados.text(
                    width, bar.get_y() + bar.get_height() / 2,
                    f'  {self.formatar(tempo)}',
                    va='center',
                    color='white',
                    fontsize=11,
                    weight='bold'
                )

            self.ax_estados.set_xlabel('Tempo (segundos)', color='#888888', fontsize=11)
            self.ax_estados.tick_params(colors='#888888')
            self.ax_estados.grid(True, alpha=0.2, axis='x')
            self.ax_estados.set_facecolor('#0d1b2a')
        else:
            self.ax_estados.text(
                0.5, 0.5, 'Sem dados no período',
                ha='center', va='center',
                color='#888888',
                fontsize=14
            )

        self.canvas_estados.draw()

        # Atualiza resumo do período
        resumo = f"╔════════════════════════════════════════╗\n"
        resumo += f"║  PERÍODO ANALISADO                   ║\n"
        resumo += f"╠════════════════════════════════════════╣\n"
        resumo += f"║  {self.data_inicio_filtro.strftime('%d/%m/%Y')} até {self.data_fim_filtro.strftime('%d/%m/%Y')}             ║\n"
        resumo += f"╠════════════════════════════════════════╣\n"
        resumo += f"║  Programas Executados: {total_programas:13d} ║\n"
        resumo += f"║  Total Perfurações:    {total_perfuracoes:13d} ║\n"
        resumo += f"╠════════════════════════════════════════╣\n"
        resumo += f"║  Tempo de Corte:                     ║\n"
        resumo += f"║    {self.formatar(tempo_corte_total):36s} ║\n"
        resumo += f"║  Tempo de Deslocamento:              ║\n"
        resumo += f"║    {self.formatar(tempo_traverse_total):36s} ║\n"
        resumo += f"║  Tempo de Pausa:                     ║\n"
        resumo += f"║    {self.formatar(tempo_pausa_total):36s} ║\n"
        resumo += f"╠════════════════════════════════════════╣\n"
        resumo += f"║  TEMPO TOTAL REGISTRADO:             ║\n"
        resumo += f"║    {self.formatar(tempo_total):36s} ║\n"
        resumo += f"║  Tempo Esperado no Turno:            ║\n"
        resumo += f"║    {self.formatar(tempo_disponivel):36s} ║\n"
        resumo += f"║  Tempo Efetivo (Corte+Desloc.):      ║\n"
        resumo += f"║    {self.formatar(tempo_efetivo):36s} ║\n"
        resumo += f"║  Gap / Máquina Parada:               ║\n"
        resumo += f"║    {self.formatar(tempo_parado_operador):36s} ║\n"
        resumo += f"╚════════════════════════════════════════╝\n"

        self.resumo_text.delete("1.0", "end")
        self.resumo_text.insert("1.0", resumo)

        # Atualiza análise de produtividade
        produtividade = ""

        if total_programas > 0:
            tempo_medio_programa = tempo_efetivo / total_programas
            perfuracoes_media = total_perfuracoes / total_programas
            tempo_por_perfuracao = tempo_efetivo / total_perfuracoes if total_perfuracoes > 0 else 0

            produtividade += "═══════════════════════════════════\n"
            produtividade += "  MÉDIAS POR PROGRAMA\n"
            produtividade += "═══════════════════════════════════\n\n"
            produtividade += f"⏱️  Tempo Médio:\n"
            produtividade += f"   {self.formatar(tempo_medio_programa)}\n\n"
            produtividade += f"🔨 Perfurações Médias:\n"
            produtividade += f"   {perfuracoes_media:.1f} furos\n\n"
            produtividade += f"⚡ Tempo por Perfuração:\n"
            produtividade += f"   {tempo_por_perfuracao:.1f} segundos\n\n"
            produtividade += "═══════════════════════════════════\n"
            produtividade += "  DISTRIBUIÇÃO PERCENTUAL\n"
            produtividade += "═══════════════════════════════════\n\n"

            pct_corte = (tempo_corte_total / tempo_efetivo * 100) if tempo_efetivo > 0 else 0
            pct_traverse = (tempo_traverse_total / tempo_efetivo * 100) if tempo_efetivo > 0 else 0
            pct_pausa = (tempo_pausa_total / tempo_disponivel * 100) if tempo_disponivel > 0 else 0

            produtividade += f"🔴 Corte:        {pct_corte:5.1f}%\n"
            produtividade += f"🔵 Deslocamento: {pct_traverse:5.1f}%\n"
            produtividade += f"🟡 Pausa:        {pct_pausa:5.1f}%\n"
        else:
            produtividade = "\n\n   Nenhum programa executado\n   no período selecionado.\n"

        self.produtividade_text.delete("1.0", "end")
        self.produtividade_text.insert("1.0", produtividade)

        # Gera recomendações
        recomendacoes = "═══════════════════════════════════\n"
        recomendacoes += "  ANÁLISE AUTOMÁTICA\n"
        recomendacoes += "═══════════════════════════════════\n\n"

        if oee >= 85:
            recomendacoes += "✅ EXCELENTE!\n"
            recomendacoes += "   OEE acima de 85%.\n"
            recomendacoes += "   Equipamento operando em\n"
            recomendacoes += "   nível de classe mundial.\n\n"
        elif oee >= 60:
            recomendacoes += "⚠️  BOM\n"
            recomendacoes += "   OEE entre 60-85%.\n"
            recomendacoes += "   Há espaço para melhorias.\n\n"
        else:
            recomendacoes += "🔴 ATENÇÃO!\n"
            recomendacoes += "   OEE abaixo de 60%.\n"
            recomendacoes += "   Requer investigação urgente.\n\n"

        recomendacoes += "───────────────────────────────────\n\n"

        if tempo_total > 0:
            pct_pausa = (tempo_pausa_total / tempo_total * 100)
            pct_gap = (tempo_parado_operador / tempo_disponivel * 100) if tempo_disponivel > 0 else 0
            if pct_pausa > 20:
                recomendacoes += "💡 ALTO TEMPO DE PAUSA\n"
                recomendacoes += f"   {pct_pausa:.1f}% do tempo em pausa.\n"
                recomendacoes += "   Investigue causas:\n"
                recomendacoes += "   • Falta de material?\n"
                recomendacoes += "   • Problemas técnicos?\n"
                recomendacoes += "   • Ajustes excessivos?\n\n"

            if pct_gap > 15:
                recomendacoes += "💡 TEMPO PARADO ELEVADO\n"
                recomendacoes += f"   Gap de {self.formatar(tempo_parado_operador)} ({pct_gap:.1f}%).\n"
                recomendacoes += "   Verifique tempo até o operador iniciar\n"
                recomendacoes += "   cada ciclo e gargalos de setup.\n\n"

            if disponibilidade < 80:
                recomendacoes += "💡 BAIXA DISPONIBILIDADE\n"
                recomendacoes += "   Equipamento subutilizado.\n"
                recomendacoes += "   Ações sugeridas:\n"
                recomendacoes += "   • Revisar programação\n"
                recomendacoes += "   • Aumentar capacidade\n"
                recomendacoes += "   • Otimizar setup\n\n"

            if performance < 85 and total_programas > 0:
                recomendacoes += "💡 PERFORMANCE ABAIXO DO IDEAL\n"
                recomendacoes += "   Revise:\n"
                recomendacoes += "   • Velocidades de corte\n"
                recomendacoes += "   • Otimização de trajetórias\n"
                recomendacoes += "   • Redução de deslocamentos\n\n"

        if total_programas == 0:
            recomendacoes += "ℹ️  SEM DADOS\n"
            recomendacoes += "   Nenhum programa executado\n"
            recomendacoes += "   no período selecionado.\n"

        self.recomendacoes_text.delete("1.0", "end")
        self.recomendacoes_text.insert("1.0", recomendacoes)

    # ==================== DASHBOARD ====================

    def atualizar_dashboard(self):
        """Atualiza gráficos e estatísticas do dashboard"""

        # Atualiza gráfico de distribuição de tempo
        self.ax1.clear()

        if self.program_running:
            tempos = [
                self.tempo_corte,
                self.tempo_traverse,
                self.tempo_pausa
            ]
            labels = ['Corte', 'Deslocamento', 'Pausa']
            colors = ['#e63946', '#1d3557', '#f77f00']

            if sum(tempos) > 0:
                self.ax1.pie(tempos, labels=labels, colors=colors, autopct='%1.1f%%',
                             startangle=90, textprops={'color': 'white', 'fontsize': 12})
                self.ax1.set_title('Distribuição de Tempo', color='#00d9ff', fontsize=14)

        self.canvas1.draw()

        # Atualiza estatísticas de hoje
        self.atualizar_estatisticas()

        # Reagenda
        self.root.after(2000, self.atualizar_dashboard)

    def atualizar_estatisticas(self):
        """Atualiza painel de estatísticas"""
        hoje = datetime.now().date()

        hist = self.carregar_json(ARQ_HIST)
        hist_hoje = [
            r for r in hist
            if datetime.strptime(r["data"], "%d/%m/%Y %H:%M:%S").date() == hoje
        ]

        total_programas = len(hist_hoje)
        total_perfuracoes = sum(r.get("perfuracoes", 0) for r in hist_hoje)

        stats = "╔══════════════════════════════════════════╗\n"
        stats += f"║  Data: {hoje.strftime('%d/%m/%Y'):30s} ║\n"
        stats += "╠══════════════════════════════════════════╣\n"
        stats += f"║  Programas Executados:  {total_programas:14d} ║\n"
        stats += f"║  Total de Perfurações:  {total_perfuracoes:14d} ║\n"
        stats += "╠══════════════════════════════════════════╣\n"

        if total_programas > 0:
            stats += f"║  Média Perfurações/Prog: {total_perfuracoes / total_programas:13.1f} ║\n"

        stats += "╚══════════════════════════════════════════╝\n"

        self.stats_text.delete("1.0", "end")
        self.stats_text.insert("1.0", stats)


def main():
    """Inicializa aplicação"""
    root = ctk.CTk()
    app = PhoenixMESPro(root)
    root.mainloop()


if __name__ == "__main__":
    main()
