# APP Monitor

Aplicativo em Python para monitorar logs da máquina Phoenix e resumir automaticamente:

- quantos programas foram executados/cortados;
- quanto tempo cada programa durou;
- quantas aberturas de arco aconteceram;
- quanto tempo total de arco cada programa teve;
- quais erros apareceram durante cada execução.

## Como funciona

O app lê um arquivo de log com o mesmo formato do `log_exemplo.txt` e usa estas regras principais:

- `Output 6, Program_Running turned On/Off` para abrir e fechar uma sessão de programa;
- `Output 1, Cut_Control turned On/Off` para contar abertura de arco e calcular o tempo de arco;
- `Update Cnc State to ...` para montar o histórico de estados CNC;
- mensagens com `error`, `fault`, `alarm`, `collision`, `Fast Stop` e `publish xpr error` para listar falhas.

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
- exportar um resumo estruturado em JSON.

## Observação importante

As métricas de arco usam o sinal `Cut_Control`. Se no seu ambiente o evento real de arco usar outro sinal, eu posso ajustar rapidamente a regra para o nome correto do seu log.
