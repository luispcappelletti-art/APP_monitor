# APP Monitor

Aplicativo em Python para transformar o log da máquina Phoenix em um **dashboard operacional e técnico**, com foco em produção, saúde do sistema e rastreabilidade.

## O que o app entrega agora

- dashboard visual mais executivo e profissional, organizado em abas;
- aba exclusiva de **gráficos** com sessões, eficiência, estados CNC e distribuição de eventos;
- visão de **programas detectados** com duração, modo, arcos, eficiência e eventos;
- trilha de **incidentes / erros** com categorização operacional;
- monitoramento de **serviços Phoenix / Managed / Rtos** por status Online/Offline;
- extração de **inventário técnico e versões** encontradas no log;
- sugestões automáticas de **novos registros operacionais** baseadas no próprio log analisado;
- exportação do resumo completo em JSON.

## Como funciona

O app lê um arquivo de log com o mesmo formato do `log_exemplo.txt` e usa estas regras principais:

- `Output 6, Program_Running turned On/Off` para abrir e fechar uma sessão de programa;
- `Output 1, Cut_Control turned On/Off` para contar abertura de arco e calcular o tempo de arco;
- `Update Cnc State to ...` para montar o histórico de estados CNC e a estimativa de permanência em cada estado;
- tópicos `.../Status` para mapear disponibilidade dos serviços;
- mensagens com `error`, `fault`, `alarm`, `collision`, `Fast Stop` e `publish xpr error` para listar falhas;
- mensagens de versão / branch / softwares instalados para formar um inventário técnico.

## Registros recomendados extraídos automaticamente

Com base no log, o app já sugere e implementa registros como:

- registro de eficiência por programa;
- registro de disponibilidade dos serviços;
- registro de incidentes de segurança;
- registro de saúde Fieldbus / CAN;
- registro de inventário técnico e versões;
- registro de erros fora de programa;
- registro por origem técnica (`SourceContext`).

## Executar a interface gráfica

```bash
python3 monitor_app.py
```

Ou já abrindo um log específico:

```bash
python3 monitor_app.py log_exemplo.txt
```

## Executar somente o resumo no terminal

```bash
python3 monitor_app.py log_exemplo.txt --summary
```

## Exportação

Na interface gráfica é possível:

- abrir outro arquivo de log;
- exportar um resumo estruturado em JSON com KPIs, estados, serviços, registros sugeridos, inventário e top erros.

## Observação importante

As métricas de arco usam o sinal `Cut_Control`. Se no seu ambiente o evento real de arco usar outro sinal, eu posso ajustar rapidamente a regra para o nome correto do seu log.
