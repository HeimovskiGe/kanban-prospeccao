# 🎛️ Painel de Disparos — PAM-GEH

Servidor local (Flask) pra gerenciar os disparos de prospecção (WhatsApp via Evolution
e Email via Resend) com **controle visual ao vivo**: segmentar, iniciar, pausar, retomar e parar.

> ⚠️ Isto é um **servidor local**, não roda no GitHub Pages. Rode na sua máquina.

## Rodar
```bash
cd painel
python3 -m pip install -r requirements.txt
cp .env.example .env     # preencha com suas chaves (NUNCA comite o .env)
python3 painel.py
# abre http://localhost:5001
```

## O que faz
- **Segmentar:** canal (WhatsApp/Email), cidade, status de origem, quantidade e delays
- **Controlar ao vivo:** ▶ iniciar · ⏸ pausar · ▶ retomar · ⏹ parar
- **Acompanhar:** barra de progresso, contadores (enviados/falhas/pulados), lead atual, ETA, log em tempo real
- **Ver o funil** da base (WhatsApp e Email) por status

## Segurança
- O `.env` (com Supabase service_role, Evolution apikey, Resend key) é **gitignored**.
- Nenhuma chave fica no código — tudo via variáveis de ambiente.

## Anti-bloqueio
- WhatsApp: delays 50–80s (já padrão). Número novo? faça ramp-up.
- Email: domínio novo aquece devagar (20–50/dia primeiro). Resend free = 100/dia.
