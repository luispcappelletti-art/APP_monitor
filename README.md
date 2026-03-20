# APP Monitor Next

Aplicativo em Python para transformar o log da máquina Phoenix em um **command center operacional e técnico** com uma interface completamente refeita em **Qt / PySide6**.

## O que mudou

- saiu o **Tkinter** e entrou uma interface **Qt moderna**, com visual dark, painéis glassmorphism e estrutura muito mais flexível para evoluir animações e widgets ricos;
- o app foi reorganizado como um cockpit com abas de **visão geral**, **programas**, **alertas e timeline** e **inventário técnico**;
- os gráficos agora são widgets customizados em Qt, abrindo espaço para animações, transições e novos componentes visuais sem ficar preso às limitações do Tkinter;
- o motor de parsing e análise do log foi mantido e reaproveitado, então a leitura operacional continua consistente.

## Principais telas

### 1. Visão geral
- cards com KPIs principais;
- gauge animado com score operacional;
- resumo executivo em linguagem de negócio;
- gráficos de tendência, estados CNC e mix de categorias.

### 2. Programas
- tabela de programas detectados;
- painel lateral com detalhes completos da sessão;
- tabela com os eventos mais recentes do programa selecionado.

### 3. Alertas e timeline
- recomendações automáticas priorizadas;
- trilha de falhas/incidentes;
- timeline conjunta com estados CNC e status de serviços.

### 4. Inventário técnico
- tabela com versões e inventário extraídos do log;
- ranking de tópicos e módulos (`SourceContext`);
- highlights técnicos resumidos.

## Como executar

Instale a dependência principal:

```bash
python3 -m pip install PySide6
```

Depois rode a interface:

```bash
python3 monitor_app.py
```

Ou já abrindo um log específico:

```bash
python3 monitor_app.py log_exemplo.txt
```

## Resumo em JSON no terminal

```bash
python3 monitor_app.py log_exemplo.txt --summary
```

## Exportação

Na interface gráfica é possível:

- abrir outro arquivo de log;
- exportar um resumo estruturado em JSON com KPIs, estados, serviços, registros sugeridos, inventário e top erros.

## Observação

As métricas de arco continuam usando o sinal `Cut_Control`. Se no seu ambiente o evento real de arco usar outro sinal, a regra pode ser ajustada rapidamente.
