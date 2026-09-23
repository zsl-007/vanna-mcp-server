# Vanna MCP Server

> 基于 Vanna 的多数据库智能问数 MCP (Model Context Protocol) 服务

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP-green.svg)](https://modelcontextprotocol.io)
[![Vanna](https://img.shields.io/badge/Vanna-2.0+-orange.svg)](https://github.com/vanna-ai/vanna)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 目录

1. [项目简介](#1-项目简介)
2. [功能特性](#2-功能特性)
3. [快速开始](#3-快速开始)
4. [配置说明](#4-配置说明)
5. [数据库配置](#5-数据库配置)
6. [MCP 工具列表](#6-mcp-工具列表)
7. [Hermes-Agent 集成](#7-hermes-agent-集成)
8. [Docker 部署](#8-docker-部署)
9. [API 示例](#9-api-示例)
10. [FAQ](#10-faq)

---

## 1. 项目简介

**Vanna MCP Server** 将 [Vanna](https://github.com/vanna-ai/vanna) 开源 NL2SQL（自然语言转 SQL）框架封装为标准 MCP 服务，使 AI Agent（如 [Hermes-Agent](https://github.com/NousResearch/hermes-agent)）能够通过 MCP 协议调用自然语言问数能力。

### 核心能力

- **自然语言问数**：用户用自然语言提问，系统自动生成 SQL、执行查询并返回结果
- **RAG 训练**：通过 DDL、文档和 SQL 示例训练，持续提升 SQL 生成质量
- **多数据库支持**：MySQL、PostgreSQL、瀚高（HighGo）、达梦（DM）四种数据库
- **国密认证**：支持瀚高数据库 SM3 国密认证方式
- **标准化接口**：通过 MCP 协议暴露 9 个工具，任何 MCP 客户端均可接入

### 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    AI Agent (Hermes-Agent)                    │
│                  MCP Client (streamable-http)                 │
└────────────────────────┬────────────────────────────────────┘
                         │ MCP Protocol (JSON-RPC 2.0 over HTTP)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  Vanna MCP Server (:8000)                     │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ Starlette │  │  FastMCP     │  │  Vanna Instance      │   │
│  │ ASGI App  │──│  (9 Tools)   │──│  (ChromaDB + LLM)   │   │
│  │ + CORS    │  │  + 1 Resource│  │  MultiDbVanna       │   │
│  └──────────┘  └──────────────┘  └──────────┬───────────┘   │
│                                              │               │
│              config.py (配置管理)             │               │
│              DatabaseAdapterFactory           │               │
│                         ┌────────────────────┘               │
│           ┌─────────────┼─────────────┐                      │
│           ▼             ▼             ▼                      │
│     ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│     │  MySQL   │ │PostgreSQL│ │  HighGo  │ │  达梦DM  │    │
│     │(PyMySQL) │ │(psycopg2)│ │(定制psycopg2)│(dmPython)│   │
│     └──────────┘ └──────────┘ └──────────┘ └──────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 项目文件结构

```
vanna-mcp-server/
├── server.py              # MCP Server 主入口（FastMCP + Starlette ASGI）
├── vanna_instance.py      # Vanna 实例初始化（多数据库动态注入）
├── database_adapter.py    # 多数据库适配器工厂（MySQL/PG/瀚高/达梦）
├── config.py              # 配置管理（环境变量 + YAML 双模式）
├── requirements.txt       # Python 依赖
├── run.sh                 # 启动脚本（含依赖检查和环境变量加载）
├── Dockerfile             # Docker 容器化部署文件
├── docker-compose.yml     # Docker Compose 编排文件
├── hermes-config.yaml     # Hermes-Agent MCP 客户端配置示例
├── .env.example           # 环境变量配置示例
└── README.md              # 项目文档（本文件）
```

---

## 2. 功能特性

### 多数据库支持

| 数据库 | 驱动 | 默认端口 | 默认用户 | SQL 方言 | 国密认证 |
|--------|------|---------|---------|---------|---------|
| MySQL | PyMySQL | 3306 | root | MySQL | ❌ |
| PostgreSQL | psycopg2 | 5432 | postgres | PostgreSQL | ❌ |
| 瀚高 (HighGo) | 瀚高定制版 psycopg2 | 5866 | sysdba | PostgreSQL | ✅ SM3 |
| 达梦 (DM) | dmPython | 5236 | SYSDBA | DM (Oracle 兼容) | ❌ |

### SM3 国密认证

- 支持瀚高数据库 SM3 国密认证方式，满足信创合规要求
- SM3 认证由瀚高定制版 libpq 在 C 层自动处理，Python 层面无需额外配置
- 认证流程采用挑战-响应机制，密码不在网络中明文传输
- 标准 PyPI 版 psycopg2 不支持 SM3，必须使用瀚高定制版

### HTTP 传输 (Streamable HTTP)

- 采用 MCP 官方推荐的 `streamable-http` 传输方式，适合生产部署
- 无状态模式 (`stateless_http=True`) + JSON 响应 (`json_response=True`)，最优可扩展性
- 支持多客户端并发访问，可水平扩展（uvicorn 多 worker）
- 客户端连接地址：`http://<host>:8000/vanna/mcp`
- CORS 中间件已配置，支持浏览器客户端访问（暴露 `Mcp-Session-Id` 头部）

### RAG 训练

- 基于 ChromaDB 向量存储，支持三种训练数据类型：
  - **DDL**：表结构定义（CREATE TABLE 语句）
  - **文档**：业务上下文说明
  - **Question-SQL 对**：自然语言问题与对应 SQL 的示例
- 训练数据按数据库类型自动隔离（`./chromadb_data_{db_type}`），避免不同数据库的 DDL/SQL 互相干扰
- 训练数据持久化存储，服务重启后不丢失
- 支持 `auto_train`：`ask` 工具默认启用自动训练，成功的问数自动加入训练数据

### 配置管理

- 支持环境变量和 YAML 配置文件双模式，YAML 优先级高于环境变量
- YAML 中支持 `${VAR_NAME}` 语法引用环境变量，敏感信息不必明文写入
- 配置项涵盖数据库连接、LLM 模型、向量存储、MCP Server 全部参数
- 通过 `CONFIG_YAML_PATH` 环境变量指定 YAML 配置文件路径

---

## 3. 快速开始

### 前置条件

- Python 3.11+
- 目标数据库（MySQL / PostgreSQL / 瀚高 / 达梦）已部署并可访问
- OpenAI API Key（或其他兼容 LLM 的 API Key）

### 安装依赖

```bash
# 克隆项目
git clone <repository-url>
cd vanna-mcp-server

# 安装 Python 依赖
pip install -r requirements.txt
```

> **注意**：如需连接瀚高数据库并使用 SM3 认证，请参阅 [数据库配置 - 瀚高数据库](#52-瀚高数据库-highgo) 章节。

### 配置环境变量

```bash
# 复制环境变量示例文件
cp .env.example .env

# 编辑 .env 文件，填入实际配置
vi .env
```

最小配置示例：

```bash
DB_TYPE=postgresql
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=mydb
DB_USER=postgres
DB_PASSWORD=your_password

LLM_MODEL=gpt-4
OPENAI_API_KEY=sk-your-api-key
```

### 启动服务

```bash
# 方式一：使用启动脚本（推荐，含依赖检查和环境变量加载）
./run.sh
# 生产模式（4 worker）
./run.sh --workers 4

# 方式二：直接运行（开发/调试）
python server.py

# 方式三：使用 uvicorn（生产推荐）
uvicorn server:app --host 0.0.0.0 --port 8000 --workers 4

# 方式四：Docker 部署（见第 8 章）
docker-compose up -d
```

### 接口测试（Mock 模式）

如需在不安装 Vanna 和数据库的情况下测试 MCP 接口：

```bash
# 设置模拟模式
export VANNA_MOCK_MODE=1

# 启动服务
./run.sh

# 所有工具将返回模拟数据，可用于验证 MCP 协议和工具发现
```

### 验证服务

```bash
# 检查服务是否正常响应
curl http://localhost:8000/vanna/mcp

# 输出 MCP 服务信息即表示启动成功
```

---

## 4. 配置说明

### 4.1 环境变量

所有配置通过环境变量管理，参见 `.env.example` 文件：

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `DB_TYPE` | ✅ | `postgresql` | 数据库类型：`mysql` / `postgresql` / `highgo` / `dm` |
| `DB_HOST` | ✅ | `127.0.0.1` | 数据库主机地址 |
| `DB_PORT` | ✅ | 按类型自动 | 数据库端口（未设置时按 DB_TYPE 自动填充默认端口） |
| `DB_NAME` | ✅ | - | 数据库名称（达梦中为 schema 名） |
| `DB_USER` | ✅ | 按类型自动 | 数据库用户名（未设置时按 DB_TYPE 自动填充默认用户） |
| `DB_PASSWORD` | ✅ | - | 数据库密码 |
| `LLM_MODEL` | ✅ | `gpt-4` | LLM 模型名称 |
| `OPENAI_API_KEY` | ✅ | - | OpenAI API Key |
| `CHROMADB_PATH` | ❌ | 自动隔离 | ChromaDB 路径，未设置时自动为 `./chromadb_data_{db_type}` |
| `MCP_HOST` | ❌ | `0.0.0.0` | MCP 服务监听地址 |
| `MCP_PORT` | ❌ | `8000` | MCP 服务监听端口 |
| `MCP_WORKERS` | ❌ | `1` | uvicorn worker 数量 |
| `CONFIG_YAML_PATH` | ❌ | - | YAML 配置文件路径，设置后优先于环境变量 |
| `VANNA_MOCK_MODE` | ❌ | `0` | 模拟模式（`1` 启用，不初始化 Vanna 实例） |

**数据库默认端口和用户自动填充规则**：

| DB_TYPE | 默认端口 | 默认用户 |
|---------|---------|---------|
| `mysql` | 3306 | root |
| `postgresql` | 5432 | postgres |
| `highgo` | 5866 | sysdba |
| `dm` | 5236 | SYSDBA |

### 4.2 YAML 配置文件（可选）

通过 `CONFIG_YAML_PATH` 环境变量指定 YAML 配置文件，YAML 优先级高于环境变量：

```bash
# 使用 YAML 配置文件启动
CONFIG_YAML_PATH=config.yaml python server.py
```

```yaml
# config.yaml
database:
  type: highgo
  host: 192.168.1.100
  port: 5866
  name: highgo
  user: sysdba
  password: ${DB_PASSWORD}    # 支持环境变量引用

llm:
  model: gpt-4
  api_key: ${OPENAI_API_KEY}
  temperature: 0.7

vector_store:
  type: chromadb
  path: ./chromadb_data_highgo

mcp:
  transport: streamable-http
  host: 0.0.0.0
  port: 8000
  stateless: true
  json_response: true
  max_request_body_size: 8388608   # 8MB
```

### 4.3 MCP Server 配置项

以下配置在 `server.py` 中通过 FastMCP 初始化参数设置：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `stateless_http` | `True` | 无状态模式，最优可扩展性 |
| `json_response` | `True` | JSON 响应格式（非 SSE 流式） |
| `streamable_http_path` | `/mcp` | MCP 端点路径（挂载到 `/vanna` 后为 `/vanna/mcp`） |
| `max_request_body_size` | `8MB` | 请求体最大大小，支持大 DDL 训练数据 |
| `host` | `0.0.0.0` | 监听地址 |
| `port` | `8000` | 监听端口 |

---

## 5. 数据库配置

### 5.1 MySQL

```bash
# .env 配置
DB_TYPE=mysql
DB_HOST=192.168.1.100
DB_PORT=3306
DB_NAME=mydb
DB_USER=root
DB_PASSWORD=your_password
```

驱动：PyMySQL，dialect 自动设为 `"MySQL"`。

### 5.2 瀚高数据库 (HighGo)

```bash
# .env 配置
DB_TYPE=highgo
DB_HOST=192.168.1.100
DB_PORT=5866              # 瀚高默认端口（非 5432）
DB_NAME=highgo
DB_USER=sysdba            # 瀚高默认用户（非 postgres）
DB_PASSWORD=your_password
```

**SM3 国密认证配置步骤：**

1. **下载瀚高定制版 psycopg2 驱动**
   - 下载地址：https://pan.baidu.com/s/1xuz6uJz0utRgKWecXhpOiA?pwd=o0tj
   - 技术支持：https://support.highgo.com
   - 版本：基于 psycopg2 2.9.9

2. **安装驱动（Linux 环境）**
   ```bash
   # 将瀚高定制版 psycopg2 文件夹放入 Python 模块路径
   cp -r psycopg2 /usr/local/lib/python3.11/site-packages/

   # 安装瀚高定制版 libpq
   cp libpq.so.5 /usr/lib/

   # 配置动态库路径
   export LD_LIBRARY_PATH=/usr/lib:$LD_LIBRARY_PATH

   # 验证安装
   python -c "import psycopg2; print(psycopg2.__version__)"
   # 预期输出: 2.9.9 (dt dec pq3 ext lo64)
   ```

3. **服务端配置（pg_hba.conf）**
   ```conf
   # 瀚高数据库 pg_hba.conf — SM3 认证配置
   # 文件路径: $HGDB_HOME/data/pg_hba.conf
   host    all    all    0.0.0.0/0    sm3
   ```

4. **服务端配置（postgresql.conf）**
   ```conf
   # 密码加密方式设为 SM3
   password_encryption = sm3
   port = 5866
   listen_addresses = '*'
   ```

> **重要**：标准 `pip install psycopg2-binary` 安装版本**不支持** SM3 认证。必须使用瀚高定制版。SM3 认证由瀚高定制版 libpq 在 C 层自动处理，Python 代码无需额外配置。

### 5.3 PostgreSQL

```bash
# .env 配置
DB_TYPE=postgresql
DB_HOST=192.168.1.100
DB_PORT=5432
DB_NAME=mydb
DB_USER=postgres
DB_PASSWORD=your_password
```

驱动：psycopg2（PyPI 标准版），dialect 自动设为 `"PostgreSQL"`。

### 5.4 达梦数据库 (DM)

```bash
# .env 配置
DB_TYPE=dm
DB_HOST=192.168.1.100
DB_PORT=5236              # 达梦默认端口
DB_NAME=DMTEST
DB_USER=SYSDBA            # 达梦默认用户
DB_PASSWORD=your_password
```

驱动：dmPython（PyPI 包名 `dmPython`，whl 包内置 DPI 运行时），dialect 自动设为 `"DM"`。

> **注意**：达梦数据库使用 Oracle 兼容 SQL 方言，与 PostgreSQL 有显著差异（使用 `DUAL` 伪表、`ROWNUM` 分页、默认大写敏感等）。Vanna 的 `dialect` 会自动设为 `"DM"`，LLM 生成 SQL 时会参考此方言。

### 5.5 数据库连接参数汇总

| 参数 | MySQL | PostgreSQL | 瀚高 | 达梦 |
|------|-------|-----------|------|------|
| 默认端口 | 3306 | 5432 | 5866 | 5236 |
| 默认用户 | root | postgres | sysdba | SYSDBA |
| Python 驱动 | PyMySQL | psycopg2 | 瀚高定制版 psycopg2 | dmPython |
| PyPI 安装 | ✅ | ✅ | ❌（官方下载） | ✅ |
| SQL 方言 | MySQL | PostgreSQL | PostgreSQL | DM (Oracle 兼容) |
| 国密认证 | ❌ | ❌ | ✅ SM3 | ❌ |

---

## 6. MCP 工具列表

Vanna MCP Server 暴露 9 个 MCP 工具，覆盖问数、训练和管理全部能力：

### 6.1 `ask` — 自然语言问数

完整问数流程：生成 SQL → 执行查询 → 返回结果（含自动训练）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | string | ✅ | 自然语言问题 |
| `allow_llm_to_see_data` | boolean | ❌ | 是否允许 LLM 查看数据以处理复杂查询，默认 `false` |

**返回值**：JSON 字符串，包含 `sql`（生成的 SQL）、`results`（Markdown 表格格式结果）、`row_count`（结果行数）。出错时返回 `{"error": "...", "error_type": "..."}`

### 6.2 `generate_sql` — 生成 SQL

根据自然语言问题生成 SQL 查询语句（不执行）。使用 RAG 检索相似 Q-SQL 对、相关 DDL 和文档构建提示词。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | string | ✅ | 自然语言问题 |

**返回值**：SQL 查询字符串

### 6.3 `run_sql` — 执行 SQL

直接执行 SQL 语句并返回结果。SELECT 语句返回数据，非 SELECT 语句返回受影响行数。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sql` | string | ✅ | SQL 查询语句 |

**返回值**：Markdown 表格格式的查询结果

### 6.4 `train_sql` — 训练 Question-SQL 对

添加自然语言问题与对应 SQL 的示例对，提升 RAG 检索质量。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | string | ✅ | 自然语言问题 |
| `sql` | string | ✅ | 对应的 SQL 查询 |

**返回值**：训练数据 ID（如 `abc123-sql`）

### 6.5 `train_ddl` — 训练 DDL

添加表结构定义（CREATE TABLE 语句），帮助 LLM 了解数据库结构。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `ddl` | string | ✅ | DDL 语句（如 `CREATE TABLE customers (...)`） |

**返回值**：训练数据 ID（如 `def456-ddl`）

### 6.6 `train_documentation` — 训练文档

添加业务文档说明，提供业务上下文信息。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `documentation` | string | ✅ | 文档文本 |

**返回值**：训练数据 ID（如 `ghi789-doc`）

### 6.7 `get_training_data` — 获取训练数据

获取所有已训练的数据（DDL、文档、Question-SQL 对）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 无 | - | - | - |

**返回值**：JSON 格式的训练数据列表

### 6.8 `remove_training_data` — 删除训练数据

根据 ID 删除指定的训练数据。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | ✅ | 训练数据 ID（如 `abc123-sql`） |

**返回值**：`"true"` 或 `"false"`

### 6.9 `generate_question` — 从 SQL 生成问题

根据 SQL 查询语句反推自然语言问题（用于自动生成训练数据）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sql` | string | ✅ | SQL 查询语句 |

**返回值**：生成的自然语言问题字符串

### MCP 资源

除 9 个工具外，还暴露 1 个 MCP 资源：

| 资源 URI | 说明 |
|----------|------|
| `vanna://training-data` | 所有训练数据 JSON（允许 MCP 客户端直接读取，无需调用工具） |

---

## 7. Hermes-Agent 集成

### 7.1 前置条件

- Hermes-Agent 已安装（[安装指南](https://hermes-agent.nousresearch.com/docs/)）
- Vanna MCP Server 已启动并可访问

### 7.2 配置步骤

1. **编辑 Hermes 配置文件**

   打开 `~/.hermes/config.yaml`，在 `mcp_servers` 字段下添加 Vanna MCP Server 配置：

   ```yaml
   mcp_servers:
     vanna:
       url: "http://127.0.0.1:8000/vanna/mcp"
       timeout: 120
       connect_timeout: 30
       enabled: true
       tools:
         include:
           - ask
           - generate_sql
           - run_sql
           - train_sql
           - train_ddl
           - train_documentation
           - get_training_data
           - remove_training_data
           - generate_question
         resources: false
         prompts: false
   ```

   完整配置示例参见项目目录下的 `hermes-config.yaml` 文件。

2. **配置敏感信息**

   将 API Key 等敏感信息放在 `~/.hermes/.env` 中：

   ```bash
   # ~/.hermes/.env
   OPENAI_API_KEY=sk-your-api-key
   DB_PASSWORD=your_db_password
   ```

   在 `config.yaml` 中使用 `${VAR_NAME}` 语法引用环境变量。

3. **验证连接**

   ```bash
   hermes mcp test vanna
   ```

4. **重新加载 MCP 配置**（如在运行中修改配置）

   在 Hermes 对话中输入：
   ```
   /reload-mcp
   ```

### 7.3 工具命名规则

Hermes-Agent 为 MCP 工具添加前缀 `mcp_<server_name>_<tool_name>`：

| MCP 工具 | Hermes 注册名 |
|----------|--------------|
| `ask` | `mcp_vanna_ask` |
| `generate_sql` | `mcp_vanna_generate_sql` |
| `run_sql` | `mcp_vanna_run_sql` |
| `train_sql` | `mcp_vanna_train_sql` |
| `train_ddl` | `mcp_vanna_train_ddl` |
| `train_documentation` | `mcp_vanna_train_documentation` |
| `get_training_data` | `mcp_vanna_get_training_data` |
| `remove_training_data` | `mcp_vanna_remove_training_data` |
| `generate_question` | `mcp_vanna_generate_question` |

### 7.4 工具过滤示例

**白名单模式**（推荐，仅暴露问数工具）：

```yaml
mcp_servers:
  vanna:
    url: "http://127.0.0.1:8000/vanna/mcp"
    tools:
      include: [ask, generate_sql]
```

**黑名单模式**（排除危险操作）：

```yaml
mcp_servers:
  vanna:
    url: "http://127.0.0.1:8000/vanna/mcp"
    tools:
      exclude: [run_sql, remove_training_data]
```

### 7.5 远程部署配置

如果 Vanna MCP Server 部署在远程服务器：

```yaml
mcp_servers:
  vanna:
    url: "https://vanna-mcp.internal.example.com/vanna/mcp"
    headers:
      Authorization: "Bearer ${VANNA_MCP_TOKEN}"
    timeout: 120
    tools:
      include: [ask, generate_sql, run_sql]
```

---

## 8. Docker 部署

### 8.1 使用 Docker Compose（推荐）

1. **准备配置文件**

   ```bash
   # 确保以下文件在同一目录：
   # - Dockerfile
   # - docker-compose.yml
   # - .env
   # - server.py, vanna_instance.py, config.py, database_adapter.py, run.sh
   # - requirements.txt

   cp .env.example .env
   vi .env  # 修改为实际配置
   ```

2. **构建并启动**

   ```bash
   # 构建镜像并后台启动
   docker-compose up -d --build

   # 查看日志
   docker-compose logs -f

   # 查看服务状态
   docker-compose ps
   ```

3. **停止服务**

   ```bash
   docker-compose down
   ```

4. **数据持久化**

   ChromaDB 训练数据通过命名卷 `chromadb_data` 持久化，容器删除后数据保留。

### 8.2 使用 Docker 命令

```bash
# 构建镜像
docker build -t vanna-mcp-server .

# 运行容器
docker run -d \
  --name vanna-mcp-server \
  -p 8000:8000 \
  --env-file .env \
  -v vanna_chromadb_data:/app/chromadb_data \
  --restart unless-stopped \
  vanna-mcp-server

# 查看日志
docker logs -f vanna-mcp-server

# 进入容器
docker exec -it vanna-mcp-server bash
```

### 8.3 瀚高 SM3 认证的 Docker 部署

如需在 Docker 中使用瀚高 SM3 认证：

1. **准备瀚高驱动**

   ```bash
   mkdir -p highgo-drivers
   # 将瀚高定制版 psycopg2 文件夹和 libpq.so.5 放入此目录
   cp -r /path/to/highgo-psycopg2 highgo-drivers/psycopg2
   cp /path/to/libpq.so.5 highgo-drivers/libpq.so.5
   ```

2. **取消 Dockerfile 中的注释**

   在 `Dockerfile` 中找到瀚高定制版 psycopg2 部分并取消注释：

   ```dockerfile
   COPY highgo-drivers/psycopg2 /usr/local/lib/python3.11/site-packages/psycopg2
   COPY highgo-drivers/libpq.so.5 /usr/lib/
   RUN pip uninstall -y psycopg2-binary && \
       python -c "import psycopg2; print('HighGo psycopg2:', psycopg2.__version__)"
   ```

3. **或在 docker-compose.yml 中挂载**

   取消 `docker-compose.yml` 中 volumes 部分的注释：

   ```yaml
   volumes:
     - chromadb_data:/app/chromadb_data
     - ./highgo-drivers/psycopg2:/usr/local/lib/python3.11/site-packages/psycopg2
     - ./highgo-drivers/libpq.so.5:/usr/lib/libpq.so.5
   ```

4. **重新构建并启动**

   ```bash
   docker-compose up -d --build
   ```

### 8.4 生产部署建议

- 使用 `--workers 4` 多 worker 部署提高并发能力
- 配置反向代理（nginx）做负载均衡和 TLS 终结
- 已配置 `stateless_http=True` 和 `json_response=True`，支持水平扩展
- 监控容器资源使用，适当调整 `deploy.resources` 限制
- 定期备份 `chromadb_data` 数据卷
- CORS 已配置为 `allow_origins=["*"]`，生产环境应限制为具体域名

---

## 9. API 示例

### 9.1 MCP 协议交互

Vanna MCP Server 使用 JSON-RPC 2.0 协议，通过 HTTP POST 请求调用。

**服务端点**：`POST http://<host>:8000/vanna/mcp`

### 9.2 工具发现

```json
// Request
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {}
}

// Response
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "ask",
        "description": "Ask a question in natural language and get SQL + query results.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "question": {"type": "string", "description": "Natural language question"},
            "allow_llm_to_see_data": {"type": "boolean", "default": false}
          },
          "required": ["question"]
        }
      },
      {
        "name": "generate_sql",
        "description": "Generate a SQL query from a natural language question.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "question": {"type": "string"}
          },
          "required": ["question"]
        }
      }
      // ... 其他 7 个工具
    ]
  }
}
```

### 9.3 调用 ask 工具（自然语言问数）

```json
// Request
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "ask",
    "arguments": {
      "question": "查询销售额最高的前10个客户"
    }
  }
}

// Response
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"sql\": \"SELECT customer_name, SUM(amount) AS total_sales FROM orders JOIN customers ON orders.customer_id = customers.id GROUP BY customer_name ORDER BY total_sales DESC LIMIT 10\", \"results\": \"| customer_name | total_sales |\\n|---|---|\\n| 客户A | 1500000 |\\n| 客户B | 1200000 |\\n...\", \"row_count\": 10}"
      }
    ]
  }
}
```

### 9.4 调用 train_ddl 工具（训练表结构）

```json
// Request
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "train_ddl",
    "arguments": {
      "ddl": "CREATE TABLE customers (id SERIAL PRIMARY KEY, name VARCHAR(100), email VARCHAR(255), created_at TIMESTAMP DEFAULT NOW())"
    }
  }
}

// Response
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "a1b2c3d4-ddl"
      }
    ]
  }
}
```

### 9.5 使用 curl 直接调用

```bash
# 工具发现
curl -X POST http://localhost:8000/vanna/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# 调用 ask 工具
curl -X POST http://localhost:8000/vanna/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "ask",
      "arguments": {"question": "查询所有用户的总数"}
    }
  }'
```

---

## 10. FAQ

### Q1: 如何切换数据库类型？

修改 `.env` 文件中的 `DB_TYPE` 环境变量，重启服务即可：

```bash
DB_TYPE=mysql      # 切换到 MySQL
DB_TYPE=postgresql # 切换到 PostgreSQL
DB_TYPE=highgo     # 切换到瀚高
DB_TYPE=dm         # 切换到达梦
```

未显式设置 `DB_PORT` 和 `DB_USER` 时，系统会根据 `DB_TYPE` 自动填充对应默认值。ChromaDB 训练数据也会按数据库类型自动隔离（`./chromadb_data_{db_type}`）。

### Q2: 瀚高数据库连接报错 "authentication method sm3 not supported"？

这是因为使用了标准 psycopg2 而非瀚高定制版。SM3 认证由瀚高定制版 libpq 在 C 层处理，标准 psycopg2 不支持。

**解决方案**：
1. 从瀚高官方下载定制版 psycopg2 驱动
2. 安装瀚高定制版 libpq.so.5
3. 设置 `LD_LIBRARY_PATH` 指向瀚高 libpq 路径
4. 详见 [数据库配置 - 瀚高数据库](#52-瀚高数据库-highgo) 章节

### Q3: ChromaDB 训练数据存储在哪里？

- **显式设置 `CHROMADB_PATH`**：存储在指定路径
- **未设置 `CHROMADB_PATH`**：自动按数据库类型隔离，路径为 `./chromadb_data_{db_type}`（如 `./chromadb_data_highgo`）
- Docker 部署时使用命名卷 `chromadb_data` 持久化

训练数据包含三种类型：DDL 语句、业务文档、Question-SQL 示例对。

### Q4: 如何提高 SQL 生成质量？

1. **训练 DDL**：使用 `train_ddl` 工具添加所有相关表的 CREATE TABLE 语句
2. **训练文档**：使用 `train_documentation` 工具添加表和字段的业务说明
3. **训练 SQL 示例**：使用 `train_sql` 工具添加常见问题的 SQL 示例
4. **启用 auto_train**：`ask` 工具默认启用自动训练，成功的问题会自动加入训练数据

### Q5: 支持哪些 LLM 模型？

Vanna 支持多种 LLM：OpenAI (GPT-4/GPT-3.5)、Anthropic (Claude)、Ollama (本地模型)、Azure OpenAI、Google Gemini、智谱 AI、DeepSeek 等。当前 MCP Server 默认使用 OpenAI 兼容接口，通过 `LLM_MODEL` 和 `OPENAI_API_KEY` 配置。

### Q6: Docker 容器中如何使用瀚高定制版 psycopg2？

有两种方式：
1. **构建时复制**：在 Dockerfile 中取消瀚高驱动部分的注释，将驱动文件放入 `highgo-drivers/` 目录
2. **运行时挂载**：在 docker-compose.yml 中取消 volumes 挂载部分的注释

详见 [Docker 部署 - 瀚高 SM3 认证](#83-瀚高-sm3-认证的-docker-部署) 章节。

### Q7: 如何在 Hermes-Agent 中验证 Vanna MCP 连接是否正常？

```bash
# 测试连接
hermes mcp test vanna

# 或在 Hermes 对话中查看工具列表
# 启动 Hermes 后，输入 /reload-mcp 重新加载配置
# 然后尝试问一个问题触发 mcp_vanna_ask 工具
```

### Q8: 如何不安装 Vanna 和数据库进行接口测试？

设置 `VANNA_MOCK_MODE=1` 环境变量后启动服务，所有工具将返回模拟数据：

```bash
export VANNA_MOCK_MODE=1
./run.sh
```

适用于验证 MCP 协议交互、工具发现和 Hermes-Agent 集成配置。

### Q9: 如何备份训练数据？

```bash
# Docker 环境
docker run --rm -v vanna-mcp-server_chromadb_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/chromadb_backup.tar.gz -C /data .

# 直接部署
tar czf chromadb_backup.tar.gz chromadb_data*/
```

### Q10: 达梦数据库的 SQL 方言有什么不同？

达梦数据库使用 Oracle 兼容 SQL 方言，与 PostgreSQL 有以下主要区别：
- 使用 `DUAL` 伪表（Oracle 风格）
- 分页使用 `ROWNUM` 或 `LIMIT`（DM8 扩展支持）
- 自增列使用 `IDENTITY`（Oracle 风格），而非 `SERIAL`
- 函数名使用 `SUBSTR` 而非 `SUBSTRING`
- 数据字典使用 `USER_TABLES`、`ALL_TABLES`（Oracle 风格）
- 默认大小写敏感（大写）

Vanna 的 `dialect` 会自动设为 `"DM"`，LLM 生成 SQL 时会参考此方言。

---

## 许可证

MIT License

## 相关链接

- [Vanna 项目](https://github.com/vanna-ai/vanna)
- [MCP 协议规范](https://modelcontextprotocol.io)
- [Hermes-Agent](https://github.com/NousResearch/hermes-agent)
- [瀚高数据库](https://www.highgo.com)
- [瀚高技术支持](https://support.highgo.com)
