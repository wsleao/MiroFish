# NSC Novix Simulation Core - Plano B Refactor

## Direção do produto

NSC Novix Simulation Core é um projeto pessoal, independente da SCADIAgro, criado para simular decisões em qualquer segmento de negócio.

O produto deve funcionar como SaaS e aceitar múltiplas fontes de dados: arquivos, texto, links, APIs, bancos de dados e webhooks.

## Decisão arquitetural

O projeto deixa de tentar reproduzir o MiroFish original como simulação social baseada em OASIS e passa a assumir um núcleo próprio:

1. Ingestão multi-fonte.
2. Persistência PostgreSQL/Supabase.
3. Modelo canônico de simulação.
4. GraphRAG de negócio.
5. Agentes especialistas.
6. Rodadas de simulação estruturadas.
7. Relatório executivo orientado a decisão.
8. Histórico e interação SaaS.

## Mudanças já aplicadas

- Novo nome visual: NSC Novix Simulation Core.
- Tela inicial redesenhada para experiência SaaS.
- Login MVP no frontend.
- Rotas protegidas no frontend.
- Entrada multi-fonte na página principal.
- Suporte a simulação sem arquivo físico, usando contexto textual sintético.
- Entry point backend `backend.nsc_core`.
- Dockerfile apontando para o entry point NSC.
- Schema PostgreSQL inicial em `backend/sql/001_nsc_saas_schema.sql`.
- Helpers de persistência em `backend/nsc_store.py`.
- Endpoints de produto:
  - `GET /api/nsc/info`
  - `GET /api/nsc/data-source-types`
  - `GET /api/nsc/persistence-status`
  - `GET /api/nsc/refactor-status`

## Próximas entregas técnicas

### Fase 1 - Persistência real

- Persistir tenants, workspaces e usuários.
- Persistir fontes de dados.
- Persistir simulações, agentes, ações e relatórios.
- Substituir gradualmente os dicionários em memória.

### Fase 2 - Motor de simulação limpo

- Criar `SimulationEngine` separado do backend HTTP.
- Criar schema obrigatório para saída dos agentes.
- Consolidar consenso, divergência, riscos, premissas e recomendação.
- Separar agentes especialistas de agentes virtuais.

### Fase 3 - Conectores

- Conector texto.
- Conector arquivos.
- Conector URL.
- Conector API REST.
- Conector banco PostgreSQL.
- Webhook receiver.

### Fase 4 - SaaS

- Login no backend.
- Sessão persistente.
- Controle por tenant.
- Controle por workspace.
- Perfis owner, admin, analyst e viewer.

### Fase 5 - Produto

- Histórico de simulações com busca.
- Comparação de cenários.
- Biblioteca de agentes.
- Biblioteca de templates de simulação.
- Exportação de relatório.

## Critério de estabilização

O sistema será considerado estabilizado quando uma simulação puder ser executada com uma fonte de texto, uma fonte de API ou uma fonte de arquivo, gerando:

- ontologia;
- grafo;
- agentes;
- ações por rodada;
- relatório executivo;
- registro persistido em PostgreSQL;
- consulta no histórico.
