# APP Monitor

Dashboard profissional em Python/Tkinter para análise visual de logs Phoenix, com foco em operação e corte.

## O que o app entrega

- dashboard executivo com visual escuro, cards e gráficos;
- gráfico de utilização por programa;
- gráfico de aberturas de arco por execução;
- ranking visual dos erros mais recorrentes;
- tabela detalhada de programas com duração, modo, erros e utilização;
- timeline visual dos arcos detectados em cada programa;
- exportação estruturada em JSON;
- modo CLI para gerar resumo automático no terminal.

## Regras usadas para análise

O monitor usa, por padrão, estas regras sobre o log:

- `Output 6, Program_Running turned On/Off` para detectar início e fim de programa;
- `Output 1, Cut_Control turned On/Off` para detectar abertura de arco e calcular tempo de arco;
- `Update Cnc State to ...` para histórico de estados CNC;
- `Update Cut Mode to ...` para identificar o modo de corte;
- mensagens com `error`, `fault`, `alarm`, `collision`, `Fast Stop` e `publish xpr error` para classificar falhas.

## Como abrir o dashboard

```bash
python3 monitor_app.py
```

Ou carregando diretamente um log:

```bash
python3 monitor_app.py log_exemplo.txt
```

## Como gerar somente o resumo JSON

```bash
python3 monitor_app.py log_exemplo.txt --summary
```

## Exportação

Pela interface você pode:

- abrir qualquer log `.txt` ou `.log`;
- exportar um resumo analítico em JSON;
- navegar pelos programas detectados e seus erros associados.

## Observação importante

As regras atuais consideram `Cut_Control` como sinal de arco ativo e `Program_Running` como referência de execução do programa. Se o seu ambiente usar outros sinais, o dashboard pode ser ajustado rapidamente para o padrão real da sua máquina.
