# QuAIA™ — Quality Assurance with Intelligent Agents

<img src="static/quaia_logo.png" alt="QuAIA Logo" width="50" style="margin-right: 15px; float:left">

QuAIA™ is an open-source framework for intelligent automation of the most important software testing life cycle 
processes starting with software requirements review and up to generating test execution reports.

The corresponding article on Medium can be
found [here](https://medium.com/@partarstu/the-next-evolution-in-software-testing-from-automation-to-autonomy-1bd7767802e1).

## Demo

Watch a demo of QuAIA™ in action:

[QuAIA™ Framework Demo](https://youtu.be/LUf6ydlKfIU)

## Features

* **Modular Agent Architecture:** Includes specialized agents for:
    * Requirements Review
    * Test Case Generation
    * Test Case Classification
    * Test Case Review
    * UI & API Test Execution (separate project)
    * Incident Report Creation
* **Photon API Relay:** All LLM calls are routed through an internal Anthropic-compatible relay (`PHOTON_API_BASE_URL`), giving access to Claude, Qwen, DeepSeek, Kimi and other models with automatic fallback on API errors.
* **Feishu / Lark Integration:**
    * **Feishu MCP Server** (`scripts/feishu_mcp_server.py`) — reads PRD wiki and docx documents from Feishu, exposed as an MCP tool to agents.
    * **Feishu Project (Meego) write-back** — generated test cases are created as `test_cases` work items in a Feishu Project space, with review comments and status transitions applied by the review agent.
* **Jira Integration:** Duplicate detection during incident creation still uses Jira via its MCP server; the RAG sync reads Jira issues via its REST API.
* **Jira RAG Sync:** Keeps the Qdrant vector store in sync with Jira issues programmatically (triggered via `/update-rag-db`), without invoking an LLM agent.
* **Dedicated Prompt Guard Service:** A dedicated microservice for detecting prompt injection attacks using the ProtectAI model.
* **Web UI Monitoring Dashboard:** Real-time monitoring interface for:
    * Agent status visualization (AVAILABLE, BUSY, BROKEN states)
    * Task history with execution details and duration
    * Error log viewer with filtering capabilities
    * Agent execution logs accessible per task
    * Summary statistics (uptime, task counts, agent health)
* **Agent State Management:** Intelligent agent lifecycle management with:
    * Automatic status tracking (AVAILABLE → BUSY → AVAILABLE/BROKEN)
    * Broken agent detection with reason classification (OFFLINE, TASK_STUCK)
    * Automatic recovery mechanism with task cancellation support
    * Concurrency-safe agent selection with atomic reservation
* **Agent Log Capture:** Captures execution logs from agents and returns them as artifacts for debugging and monitoring.
* **JWT Authentication:** Secure dashboard access with configurable credentials and token expiration.
* **Prompt Injection Protection:** Built-in safeguards to detect and prevent prompt injection attacks.
* **A2A and MCP - compliant:** Adheres to the specifications of Agent2Agent and Model Context protocols.
* **Orchestration Layer:** A central orchestrator manages agent registration, task routing, and workflow execution.
* **Vector Database Integration:** Uses Qdrant for semantic search capabilities, enabling intelligent duplicate detection and RAG-based features.
* **Embedding Service:** Dedicated microservice for generating text embeddings using SentenceTransformer models.
* **Test Management System Integration:** Integrates with Feishu Project (Meego), Zephyr and Xray for test case management operations.
* **Test Reporting:** Generates detailed Allure reports for test execution results.
* **Extensible:** Designed for easy addition of new agents, tools, and integrations.
* **Architecture as Code (CALM):** The system architecture, including its security controls, is described with the [FINOS CALM](https://calm.finos.org/) standard and validated as a blocking CI gate, keeping the model and the running system in sync.

## Architecture

The orchestrator acts as the central hub, managing the lifecycle and interactions of various specialized agents. Agents
expose details about their capabilities to the orchestrator and allow it to identify the tasks they can handle.

When an event occurs (e.g., a Feishu Project webhook indicating a story is ready for requirements review or test case generation), the orchestrator:

1. Receives the event.
2. Identifies the appropriate agent(s) based on the task description and registered agent capabilities.
3. Routes the task to the selected agent(s).
4. Monitors the task execution and collects results.
5. Triggers subsequent agents or workflows as needed (e.g., after test case generation, trigger test case
   classification).

### Agent State Management

The orchestrator maintains detailed state information for each registered agent:

| Status | Description |
|--------|-------------|
| **AVAILABLE** | Agent is ready to accept new tasks. |
| **BUSY** | Agent is currently executing a task. |
| **BROKEN** | Agent is unavailable due to being OFFLINE or having a TASK_STUCK. |

**Broken Agent Classification:**
- `OFFLINE`: Agent is not responding to health checks.
- `TASK_STUCK`: Agent is responsive but a task timed out.

### Concurrency and Agent Selection

The orchestrator uses atomic agent selection with a lock-based mechanism to prevent race conditions when multiple tasks
compete for the same available agents. Key features include:

* **Atomic Reservation:** Agent selection and status update happen within a single lock to prevent double-booking.
* **Wait-and-Retry:** If no suitable agent is available, the orchestrator waits with exponential backoff.
* **LLM-Based Selection Caching:** The orchestrator caches LLM decisions for agent sets to avoid redundant API calls.

### Broken Agent Recovery

A background task continuously monitors broken agents and attempts recovery:

1. For `OFFLINE` agents: Periodically checks if the agent responds to card fetch requests.
2. For `TASK_STUCK` agents: Attempts to cancel the stuck task using the A2A protocol before marking the agent as available.
3. Agents that remain unrecoverable for 24 hours are given up on.

For a visual representation of the system's architecture and data flow, please refer to the following diagrams:

* [Architectural Diagram](architectural_diagram.html) ([German Version](architectural_diagram_DE.html))
* [Flow Diagram](flow_diagram.html) ([German Version](flow_diagram_DE.html))

### Architecture as Code (CALM)

The architecture above is also maintained as machine-readable **architecture as code** using the
[FINOS CALM](https://calm.finos.org/) (Common Architecture Language Model) standard, under the [`calm/`](calm/) directory.
This makes the architecture a first-class, version-controlled artifact rather than a static diagram that drifts out of
date.

The model captures every service and external system as `nodes`, the integration edges between them (A2A, MCP, HTTPS) as
`relationships`, and the framework's security mechanisms as `controls` attached to the relevant nodes and edges:

| Control | Applies to | Mechanism |
|---|---|---|
| Orchestrator API key | Orchestrator | `X-API-Key` on control/webhook endpoints (`ORCHESTRATOR_API_KEY`) |
| Dashboard JWT | Orchestrator | JWT on dashboard endpoints (`DASHBOARD_JWT_SECRET`) |
| Execution agent bearer token | Orchestrator → execution agents | `Authorization: Bearer` header (`REMOTE_EXECUTION_AGENT_AUTH_TOKEN`) |
| Prompt-injection guard | Every agent | Prompt-injection screening (`PROMPT_INJECTION_CHECK_ENABLED`) |
| Internal service API key | Embedding & Prompt Guard services | Shared `X-API-Key` (`INTERNAL_SERVICE_API_KEY`) |

One integration edge is intentionally left without an auth control: Feishu Project's native "send HTTP request"
automation action (which calls the orchestrator's `/requirement-ready-for-review` webhook) cannot attach custom
headers, so that endpoint is unauthenticated by design. Abuse is bounded instead by an in-memory per-work-item
dedup window and a global submission rate cap (`REQUIREMENT_REVIEW_DEDUP_WINDOW_SECONDS` /
`REQUIREMENT_REVIEW_MAX_PER_MINUTE`).

A governance **pattern** (`calm/patterns/quaia.pattern.json`) asserts that every required node, relationship and control
is present. The CI pipeline runs this validation as a **blocking** `Architecture (CALM)` job, so removing an agent or
dropping a security control makes the build fail. See [`calm/README.md`](calm/README.md) for the full layout and for how
to run the validation locally (requires Node.js 20+).

## Getting Started

### Prerequisites

* Python 3.14+
* Docker
* [`uv`](https://docs.astral.sh/uv/) (Python package and project manager)
* [Node.js](https://nodejs.org/) 20+ (only needed to validate the CALM architecture model locally; see
  [Architecture as Code (CALM)](#architecture-as-code-calm))

### Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/partarstu/agentic-qa-framework.git
   cd agentic-qa-framework
   ```

2. **Install `uv`** (if not already installed):
   ```bash
   # macOS / Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh
   # Windows (PowerShell)
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

3. **Create the virtual environment and install dependencies:**
   ```bash
   uv sync
   ```
   This creates a `.venv` and installs the locked runtime and development
   dependencies. Run commands inside the environment with `uv run`, e.g.
   `uv run pytest`. The optional, machine-learning dependencies of the
   embedding and prompt-guard services are installed on demand via
   `uv sync --extra embedding-service` or `uv sync --extra prompt-guard-service`.

### Docker Images

The project utilizes Docker for containerization of the orchestrator and agent services. A common base image, `agentic-qa-base:latest`, is built from `Dockerfile.base` to ensure consistency and reduce build times.

Each service runs using `gunicorn` as the WSGI server. The command for agents is
`gunicorn -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT agents.<agent_name>.main:app`, and for the
orchestrator, it is `gunicorn -w 1 -k uvicorn.workers.UvicornWorker orchestrator.main:orchestrator_app`. Note that
`$PORT` refers to the internal port the agent listens on, while the `AgentCard` will use the `EXTERNAL_PORT` for its
URL.

### Environment Variables

Create a `.env` file in the project root and configure the following environment variables. These variables control the
behavior of the orchestrator and agents.

```
# Logging
LOG_LEVEL=INFO # Default: INFO. Controls the verbosity of logging.
GOOGLE_CLOUD_LOGGING_ENABLED=False # Default: False. Set to "True" to enable Google Cloud Logging.

# Orchestrator
ORCHESTRATOR_HOST=localhost # Default: localhost. The host where the orchestrator runs.
ORCHESTRATOR_PORT=8000 # Default: 8000. The port the orchestrator listens on.
ORCHESTRATOR_URL=http://localhost:8000 # Default: http://localhost:8000. The full URL of the orchestrator.
ORCHESTRATOR_API_KEY=YOUR_ORCHESTRATOR_API_KEY # Required. Authenticates the orchestrator's control/webhook endpoints.
                                 # Requests must include an 'X-API-Key' header with this value. If it is left unset, those
                                 # endpoints fail closed and return HTTP 503 (authentication not configured).
                                 # /requirement-ready-for-review is exempt — see REQUIREMENT_REVIEW_* below.
REQUIREMENT_REVIEW_DEDUP_WINDOW_SECONDS=300 # Default: 300. Per-work-item dedup window for the unauthenticated
                                 # /requirement-ready-for-review webhook (see "Architecture as Code (CALM)" above).
REQUIREMENT_REVIEW_MAX_PER_MINUTE=5 # Default: 5. Global submission rate cap for /requirement-ready-for-review.
JIRA_MCP_SERVER_URL=http://localhost:9000/sse # Default: http://localhost:9000/sse. The URL of the Jira MCP server.
FEISHU_MCP_SERVER_URL=http://localhost:9010/sse # Default: http://localhost:9010/sse. The URL of the Feishu MCP server.

# Photon API Relay (internal LLM proxy, Anthropic-compatible protocol)
PHOTON_API_BASE_URL=https://coding.corp.photontech.cc # Default. Internal LLM relay base URL.
PHOTON_API_KEY=YOUR_PHOTON_API_KEY # Required. API key for the Photon relay.

# Feishu (Lark) App credentials — used by the Feishu MCP server to read PRD documents.
FEISHU_APP_ID=YOUR_FEISHU_APP_ID # Required for the Feishu MCP server.
FEISHU_APP_SECRET=YOUR_FEISHU_APP_SECRET # Required for the Feishu MCP server.

# Feishu Project (Meego) — used when TEST_MANAGEMENT_SYSTEM=meego
MEEGO_BASE_URL=https://project.feishu.cn # Default. Feishu Project Open API base URL.
MEEGO_PLUGIN_ID=YOUR_MEEGO_PLUGIN_ID # Required for test case write-back to Feishu Project.
MEEGO_PLUGIN_SECRET=YOUR_MEEGO_PLUGIN_SECRET # Required for test case write-back to Feishu Project.
MEEGO_PROJECT_KEY=union-platform # Default. The Feishu Project space key.
MEEGO_USER_KEY= # Optional. Feishu user key for attributing work items.

# Dashboard Authentication
# These settings control access to the UI monitoring dashboard at /api/dashboard/*
DASHBOARD_USERNAME=admin # Required. Username for dashboard login. Dashboard auth fails closed if this is unset.
DASHBOARD_PASSWORD=admin # Required. Password for dashboard login. CHANGE THIS IN PRODUCTION! Auth fails closed if unset.
DASHBOARD_JWT_SECRET=change-me-in-production-please # Required. Secret key for JWT token signing. CHANGE THIS IN PRODUCTION!
DASHBOARD_JWT_EXPIRE_HOURS=24 # Default: 24. Number of hours before JWT tokens expire.

# Zephyr Test Management System (used when TEST_MANAGEMENT_SYSTEM=zephyr)
ZEPHYR_BASE_URL=YOUR_ZEPHYR_BASE_URL # Required for Zephyr. The base URL of your Zephyr instance.
ZEPHYR_API_TOKEN=YOUR_ZEPHYR_API_TOKEN # Required for Zephyr. API token for Zephyr authentication.

# Agent Configuration
AGENT_BASE_URL=http://localhost # Default: http://localhost. Base URL for agents.
PORT=8001 # Default: 8001. The internal port an agent listens on.
EXTERNAL_PORT=8001 # Default: 8001. The externally accessible port for the agent.

# Agent Discovery (for remote agents)
REMOTE_EXECUTION_AGENT_HOSTS=http://localhost # Default: http://localhost. Comma-separated URLs of remote agent hosts.
AGENT_DISCOVERY_PORTS=8001-8007 # Default: 8001-8007. Port range for agent discovery.
REMOTE_EXECUTION_AGENT_AUTH_TOKEN= # Optional. Shared bearer token sent to the execution agents' main A2A endpoint.

# Task Execution
TASK_EXECUTION_TIMEOUT=1200 # Default: 1200 (20 min). Max seconds to wait for the full generation→classification→review pipeline.

# Google Cloud Storage (via Volume Mounts)
ATTACHMENTS_LOCAL_DESTINATION_FOLDER_PATH=/tmp # Default: /tmp. Path where attachments are read from.
MCP_SERVER_ATTACHMENTS_FOLDER_PATH=/tmp # Default: /tmp. Path where MCP server stores attachments.
JIRA_ATTACHMENT_SKIP_POSTFIX=_SKIP # Default: _SKIP. Attachments with filenames ending in this postfix are excluded.

# OpenTelemetry (for tracing — leave unset to disable)
OTEL_EXPORTER_OTLP_ENDPOINT= # Optional. OTLP HTTP endpoint, e.g. http://tempo:4318. Tracing is disabled when unset.

# Test Management System
TEST_MANAGEMENT_SYSTEM=meego # Options: meego (Feishu Project), zephyr, xray. Default: zephyr.

# Test Reporting
TEST_REPORTER=allure # Default: allure.
ALLURE_RESULTS_DIR=allure-results
ALLURE_REPORT_DIR=allure-report

# Common Model Configuration
TOP_P=1.0
TEMPERATURE=0.0

# Qdrant Vector Database (for RAG and semantic search)
QDRANT_URL=http://localhost # Default: http://localhost.
QDRANT_PORT=6333
QDRANT_API_KEY= # Optional.
QDRANT_COLLECTION_NAME=jira_issues
QDRANT_METADATA_COLLECTION_NAME=rag_metadata
RAG_MIN_SIMILARITY_SCORE=0.7
RAG_MAX_RESULTS=5
RAG_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
EMBEDDING_SERVICE_URL= # Required for agents using Vector DB.
EMBEDDING_SERVICE_TIMEOUT_SECONDS=120.0
EMBEDDING_SERVICE_MAX_RETRIES=6
EMBEDDING_SERVICE_RETRY_BACKOFF_CAP_SECONDS=32.0

# Incident Creation Agent Configuration
INCIDENT_AGENT_MIN_SIMILARITY_SCORE=0.7
ISSUE_PRIORITY_FIELD_ID=priority
ISSUE_SEVERITY_FIELD_NAME=customfield_10124

# Prompt Injection Detection
PROMPT_INJECTION_CHECK_ENABLED=True
PROMPT_GUARD_PROVIDER=protect_ai
PROMPT_GUARD_SERVICE_URL= # Required if PROMPT_INJECTION_CHECK_ENABLED is True.
INTERNAL_SERVICE_API_KEY= # Optional shared secret for embedding and prompt-guard services.
PROMPT_INJECTION_MIN_SCORE=0.8
PROMPT_INJECTION_MODEL_NAME=ProtectAI/deberta-v3-base-prompt-injection-v2

**Note on Local Models:**
If you are running the orchestrator or agents locally (not in a Docker container deployed to the cloud), you must manually download the necessary models:
1. **Prompt Injection Detection Model:** Required if `PROMPT_INJECTION_CHECK_ENABLED` is set to `True`. Run `scripts/download_prompt_guard_model.py`.
2. **Embedding Model:** Required for components using the Vector DB (the Incident Creation agent and the Orchestrator). Run `scripts/download_embedding_model.py`.
```

### Jira MCP Server Setup

The QuAIA™ framework integrates with Jira via a Model Context Protocol (MCP) server. This server acts as an
intermediary, handling communication between Jira webhooks and the orchestrator.

To run the Jira MCP server, you will need Docker installed.

1. **Create a `.env` file for the MCP server:**
   The MCP server uses its own `.env` file for configuration. Create a file named `.env` in the `mcp/jira/` directory
   with the following content:

   ```
   JIRA_URL=YOUR_JIRA_INSTANCE_URL
   JIRA_API_TOKEN=YOUR_JIRA_API_TOKEN
   JIRA_USERNAME=YOUR_JIRA_USERNAME
   ```

2. **Run the MCP Server using Docker:**
   Navigate to the `mcp/jira/` directory and execute the `start_mcp_server.bat` script (Windows only):

   ```bash
   cd mcp/jira
   start_mcp_server.bat
   ```
   This starts the Docker container for the MCP server on port `9000`. In the cloud, mount a GCS bucket to the
   container so downloaded attachments are accessible by agents.

### Feishu MCP Server Setup

The Feishu MCP server reads PRD wiki and docx documents from Feishu and exposes them as a tool to agents.
It is used by the Test Case Generation and Review agents to fetch the source PRD.

1. **Set the credentials in your `.env` file:**
   ```
   FEISHU_APP_ID=YOUR_FEISHU_APP_ID
   FEISHU_APP_SECRET=YOUR_FEISHU_APP_SECRET
   FEISHU_MCP_SERVER_URL=http://localhost:9010/sse
   ```

2. **Start the Feishu MCP server:**
   ```bash
   uv run gunicorn -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:9010 scripts.feishu_mcp_server:app
   ```
   The server listens on port `9010` by default and resolves both wiki token URLs
   (`/wiki/TOKEN`) and docx URLs automatically. A Docker image is also available
   in `scripts/Dockerfile` for container deployment.

### Starting agents locally

1. **Start Qdrant Vector Database (required for RAG features):**
   The Incident Creation agent and the Orchestrator's Jira RAG sync require a running Qdrant instance.
   ```bash
   scripts/start_qdrant.bat
   ```
   This script will start Qdrant in a Docker container on port 6333.

2. **Start the Feishu MCP Server (required for requirements review, test case generation and review):**
   ```bash
   uv run gunicorn -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:9010 scripts.feishu_mcp_server:app
   ```

3. **Start the Jira MCP Server (required for incident creation):**
   See [Jira MCP Server Setup](#jira-mcp-server-setup) above.

4. **Start the Embedding Service (optional):**
   If you want to use a dedicated embedding service instead of loading the model in each agent:
   ```bash
   uv run python services/embedding_service/main.py
   ```

5. **Start the Prompt Guard Service (optional):**
   Required if prompt injection checks are enabled.
   ```bash
   uv run python services/prompt_guard_service/main.py
   ```

6. **Start Individual Agents:**
   Open separate terminal windows for each agent you want to run:

    * **Requirements Review Agent:**
      ```bash
      uv run python agents/requirements_review/main.py
      ```
    * **Test Case Generation Agent:**
      ```bash
      uv run python agents/test_case_generation/main.py
      ```
    * **Test Case Classification Agent:**
      ```bash
      uv run python agents/test_case_classification/main.py
      ```
    * **Test Case Review Agent:**
      ```bash
      uv run python agents/test_case_review/main.py
      ```
    * **Incident Creation Agent:**
      ```bash
      uv run python agents/incident_creation/main.py
      ```

7. **Start the Orchestrator:**
   ```bash
   uv run python orchestrator/main.py
   ```

### Web UI Monitoring Dashboard

The orchestrator includes a built-in web UI for monitoring agent status, tasks, and logs in real-time.

#### Accessing the Dashboard

Once the orchestrator is running, navigate to `http://localhost:8000/` (or your configured orchestrator URL) to
access the dashboard. You will be prompted to log in with your configured credentials.

**Default Credentials:**
- Username: `admin`
- Password: `admin`

> **⚠️ Important:** Change the default credentials in production by setting the `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`, and `DASHBOARD_JWT_SECRET` environment variables.

#### Dashboard Features

* **Summary View:** Displays orchestrator uptime, total tasks processed, success/failure rates, agent health overview, and the aggregated token consumption and estimated cost across recorded tasks.
* **Agent Grid:** Shows all registered agents with their current status (AVAILABLE, BUSY, BROKEN), capabilities, and last activity. Includes a manual "Discover Agents" button to trigger re-discovery on demand.
* **Task History:** Lists recent tasks with execution details, duration, assigned agent, status, and the tokens consumed and estimated cost per task. Click on a task to view its execution logs.
* **Error Log:** Displays recent errors with context, including traceback snippets and related task/agent information.
* **Log Viewer:** Filterable log viewer supporting level filtering (INFO, WARNING, ERROR), task/agent-specific log queries, and paginated log loading ("Load More").

#### Starting the UI Development Server (For Development Only)

If you want to run the UI in development mode with hot-reloading:

```bash
cd orchestrator/ui
npm install
npm run dev
```

The development server runs on port 5173 with a proxy to the orchestrator backend.

#### Building the UI for Production

To build the UI and integrate it with the orchestrator:

```bash
cd orchestrator/ui
# Windows
start.bat

# Linux/macOS
./start.sh
```

This will build the React application and copy the static files to `orchestrator/static/` for serving by the orchestrator.

### Token Budget and Cost Oversight

Every LLM call made by the agents and the orchestrator is metered. After each agent run, the consumed token counts
(input/output/total, requests, tool calls) and an estimated USD cost are:

* **logged** as a one-line summary by the agent and by the orchestrator, and
* **surfaced in the dashboard** — per task (Tokens/Cost columns) and as an aggregate on the summary cards.

The orchestrator's own routing/extraction LLM runs are logged as well.

#### Hard per-task limit

Each agent run is capped at a **total token budget per task**. When a run exceeds it, the run is aborted with
pydantic-ai's `UsageLimitExceeded` and the task is reported as failed. The cap is **token-based** because pydantic-ai
enforces token limits, not monetary ones — the USD figure is for oversight only.

| Variable | Description | Default |
|----------|-------------|---------|
| `TOTAL_TOKENS_LIMIT_PER_TASK` | Maximum total tokens an agent may consume in a single task. | `1000000` |

#### Cost estimation

USD cost is derived from a static price table, `BudgetConfig.MODEL_PRICING` in `config.py`, keyed by the pydantic-ai
model name and expressed in USD per 1,000,000 tokens (`input`/`output`). Keep it current with your provider's published
pricing. Models that are not present in the table report a `null` cost (their tokens are still counted).

### Deployment to Google Cloud Run

This project is already configured for deployment to Google Cloud Run. The `cloudbuild.yaml` file orchestrates the
building of Docker images and their deployment as separate services. You need to have the gcloud CLI installed before
you run any of the commands below.

#### Preconditions:

1. Existing VPC network. This one can be created with the following commands:
    ```bash
   gcloud compute networks create agent-network --subnet-mode=custom
   gcloud compute networks subnets create SUBNET_NAME --network=NETWORK_NAME --range=IP_RANGE --region=REGION
    ```
   The target subnetwork network also must have Private Google Access activated so that agents running in Google Cloud
   Run could reach
   other agents which have "internal" ingress (basically all agents have it except orchestrator).
2. Access to the Secrets Manager. This one can be created with the following command:
    ```bash
   gcloud projects add-iam-policy-binding <project_id> --member="serviceAccount:<project_number>-compute@developer.gserviceaccount.com" --role="roles/secretmanager.secretAccessor" 
    ```
3. Cloud NAT in order to route requests from the VPC network out to the internet. This one can be created with the
   following commands:
    ```bash
   gcloud compute routers create ROUTER_NAME --network=NETWORK_NAME --region=REGION
   gcloud compute routers nats create NAT_GATEWAY_NAME --router=ROUTER_NAME --region=REGION --nat-all-subnet-ip-ranges 
    ```
4. The following **secrets in the Google Secrets Manager** with corresponding values need to be added:
    * `PHOTON_API_KEY`
    * `FEISHU_APP_ID`
    * `FEISHU_APP_SECRET`
    * `MEEGO_PLUGIN_ID`
    * `MEEGO_PLUGIN_SECRET`
    * `JIRA_API_TOKEN`
    * `JIRA_USERNAME`
    * `JIRA_URL`
    * `ZEPHYR_API_TOKEN`
    * `ZEPHYR_BASE_URL`
    * `JIRA_MCP_SERVER_URL`
    * `ORCHESTRATOR_API_KEY`
5. Cloud Storage bucket for general operations (with all needed folders created, see "Substitution Variables").
6. Cloud Storage bucket for storing and publicly serving test execution reports (this bucket needs to have public
   access)

#### Deployment:

After having all preconditions fulfilled, you can execute the following command:

```bash
gcloud builds submit --config 'path/to/your/cloudbuild.yaml' --substitutions "^;^_BUCKET_NAME=YOUR_GCS_BUCKET_NAME;_ALLURE_REPORTS_BUCKET=YOUR_ALLURE_REPORTS_BUCKET_NAME;_REQUIREMENTS_REVIEW_AGENT_BASE_URL=YOUR_REQUIREMENTS_REVIEW_AGENT_URL;_TEST_CASE_GENERATION_AGENT_BASE_URL=YOUR_TEST_CASE_GENERATION_AGENT_URL;_TEST_CASE_CLASSIFICATION_AGENT_BASE_URL=YOUR_TEST_CASE_CLASSIFICATION_AGENT_URL;_TEST_CASE_REVIEW_AGENT_BASE_URL=YOUR_TEST_CASE_REVIEW_AGENT_URL;_INCIDENT_CREATION_AGENT_BASE_URL=YOUR_INCIDENT_CREATION_AGENT_URL;_REMOTE_EXECUTION_AGENT_HOSTS=YOUR_COMMA_SEPARATED_AGENT_HOSTS;_PROMPT_GUARD_SERVICE_URL=YOUR_PROMPT_GUARD_SERVICE_URL;_DEPLOY_ALL_SERVICES=true" .
```

```powershell
gcloud builds submit --config 'path/to/your/cloudbuild.yaml' --substitutions "`^;`^_BUCKET_NAME=YOUR_GCS_BUCKET_NAME;_ALLURE_REPORTS_BUCKET=YOUR_ALLURE_REPORTS_BUCKET_NAME;_REQUIREMENTS_REVIEW_AGENT_BASE_URL=YOUR_REQUIREMENTS_REVIEW_AGENT_URL;_TEST_CASE_GENERATION_AGENT_BASE_URL=YOUR_TEST_CASE_GENERATION_AGENT_URL;_TEST_CASE_CLASSIFICATION_AGENT_BASE_URL=YOUR_TEST_CASE_CLASSIFICATION_AGENT_URL;_TEST_CASE_REVIEW_AGENT_BASE_URL=YOUR_TEST_CASE_REVIEW_AGENT_URL;_INCIDENT_CREATION_AGENT_BASE_URL=YOUR_INCIDENT_CREATION_AGENT_URL;_REMOTE_EXECUTION_AGENT_HOSTS=YOUR_COMMA_SEPARATED_AGENT_HOSTS;_PROMPT_GUARD_SERVICE_URL=YOUR_PROMPT_GUARD_SERVICE_URL;_DEPLOY_ALL_SERVICES=true" .
```

**Substitution Variables:**
* `_BUCKET_NAME`: The name of the Google Cloud Storage bucket used for storing attachments downloaded by Jira MCP
  server.
* `_JIRA_ATTACHMENTS_FOLDER`: The name of the folder where attachments from Jira MCP server will be saved, must be the
  same as 'JIRA_ATTACHMENTS_CLOUD_STORAGE_FOLDER' environment variable
* `_ALLURE_REPORTS_BUCKET`: The GCS bucket where test execution HTML reports will be stored.
* `_REQUIREMENTS_REVIEW_AGENT_BASE_URL`: The URL of the deployed Requirements Review Agent.
* `_TEST_CASE_GENERATION_AGENT_BASE_URL`: The URL of the deployed Test Case Generation Agent.
* `_TEST_CASE_CLASSIFICATION_AGENT_BASE_URL`: The URL of the deployed Test Case Classification Agent.
* `_TEST_CASE_REVIEW_AGENT_BASE_URL`: The URL of the deployed Test Case Review Agent.
* `_INCIDENT_CREATION_AGENT_BASE_URL`: The URL of the deployed Incident Creation Agent.
* `_REMOTE_EXECUTION_AGENT_HOSTS`: A comma-separated list of URLs for all deployed agents that the orchestrator will
  interact with.
* `_PROMPT_GUARD_SERVICE_URL`: The URL of the deployed Prompt Guard Service.
* `_DEPLOY_ALL_SERVICES`: Set to `true` to deploy all services. Individual service flags (e.g., `_DEPLOY_JIRA_MCP`) are available for granular deployment.

**Important**: Before the initial deployment of the framework into Google Cloud Run it's quite hard to know which URL
will be assigned to each agent and orchestrator. That's why most probably you'll have to run the deployment command
once, then identify the assigned URL of each service, update the substitution values in the command and run it again.

### Hermetic smoke tests

The smoke suite is a self-contained integration test, independent of any Cloud Run deployment. It runs the real
orchestrator and the QA agents (requirements review, test-case generation, classification, review and incident creation)
under `docker-compose.smoke.yml`, driven by real models via the Photon API relay, with only the external boundaries
replaced by mocks under `tests/smoke/mocks/` (Jira MCP, Jira REST, Feishu MCP, Feishu Project / Meego, Zephyr and Qdrant). A mock test-execution agent stands in for the
VM-hosted real executors. It drives the system through the orchestrator's public webhooks and asserts on what reaches
each mocked boundary:

* **Requirements review** (`POST /requirement-ready-for-review`) → a non-empty review comment reaches the reviewed
  work item in Feishu Project (Meego mock), and the agent first fetched the requirement document via the Feishu MCP.
* **Test-case generation** (`POST /story-ready-for-test-case-generation`) → real test cases (name + steps) are created in Feishu Project (Meego mock),
  linked back to the originating story ID.
* **Test-case classification** (same webhook) → labels are applied to the test case work items in Feishu Project.
* **Test-case review** (same webhook) → review comments and status transitions reach the test case work items in Feishu Project.
* **Test execution / incident creation** (`POST /execute-tests`) → a failed automated test drives a real Bug issue into
  the seeded Jira project, the failed execution is reported to Zephyr inside a fresh test cycle, the bug is linked to
  that execution, and the duplicate search consulted the vector DB.
* **RAG DB update** (`POST /update-rag-db`) → the sync pushes the seeded Jira story into the mocked vector DB
  (collection creation + point upsert).
* **Negative paths** → the three authenticated webhooks reject an invalid API key (401); `/requirement-ready-for-review`
  is unauthenticated by design and instead silently drops (204) a malformed payload or one with no PRD link filled in;
  a missing `story_id`/`feishu_doc` fails with 400, a missing `project_key` fails with 422, and the dashboard API
  rejects a missing token (401) — all without dispatching to an agent.

The four webhooks are fired once, concurrently (the flows are mutually independent), so the suite's wall time is the
longest flow rather than the sum of all flows.

It runs in GitHub Actions (the `smoke` job in `.github/workflows/ci.yml`) on pushes to `main` and on manual
`workflow_dispatch` only — never on pull requests — because every run makes real, billed LLM calls via the Photon API
relay. The job needs a `PHOTON_API_KEY` repository secret. To run it locally:

```bash
docker build -t agentic-qa-base:latest -f Dockerfile.base .
PHOTON_API_KEY=<your-key> docker compose -f docker-compose.smoke.yml up -d --build --wait
uv run pytest tests/smoke -m smoke -v
docker compose -f docker-compose.smoke.yml down -v
```

## Invoking Orchestrator Workflows

### Triggering Workflows via Feishu Project Webhooks

The orchestrator listens for webhooks from Feishu Project automation rules to initiate automated workflows.

* **Story Ready for Requirements Review:**
  Configure a Feishu Project automation rule that fires a "send HTTP request" action to
  `/requirement-ready-for-review` when a story enters the requirement-design status. Feishu sends its native
  `WorkFlowNodeStatusEvent` payload — the orchestrator extracts the work item ID, project key, and the PRD document
  link (the first `link`-type field in the work item's form) from it. This request is **unauthenticated** (Feishu's
  automation action cannot attach custom headers) and is bounded instead by a per-work-item dedup window and a
  global rate cap — see [Architecture as Code (CALM)](#architecture-as-code-calm). A request with no PRD link filled
  in yet, or one that fails the dedup/rate check, is silently dropped (204) rather than treated as an error.

* **Story Ready for Test Case Generation:**
  Send a POST request to `/story-ready-for-test-case-generation` with a JSON payload containing the
  Feishu Project `story_id`, the `project_key` of the space, and the `feishu_doc` URL or token of
  the PRD document. This triggers the test case generation, classification, and review workflows,
  and writes the resulting test cases back to Feishu Project as work items.

  Example payload:
  ```json
  {
      "story_id": "7082225030",
      "project_key": "union-platform",
      "feishu_doc": "https://photonpay.feishu.cn/wiki/SKNKw8iili9Q2ike5f5cdqgPnhb"
  }
  ```

### Executing Automated Tests

You can trigger the execution of automated tests for a specific project.

* **Execute Tests:**
  Send a POST request to `/execute-tests` with a JSON payload containing the `project_key` of the Jira project. This
  will execute all test cases labeled as "automated" within that project. For any failed tests, the orchestrator will
  automatically trigger incident creation using the Incident Creation Agent.

  Example payload:
  ```json
  {
      "project_key": "SCRUM"
  }
  ```
  The results will be reported back to Zephyr and an Allure report will be generated.

### Updating the RAG Vector Database

To keep the vector database synchronized with Jira issues for duplicate detection:

* **Update RAG DB:**
  Send a POST request to `/update-rag-db` with a JSON payload containing the `project_key` of the Jira project. The
  orchestrator then syncs the project's issues from Jira (read directly via the Jira REST API) into the Qdrant vector
  database, enabling semantic search for duplicate detection. The sync runs programmatically — no LLM agent is involved.

  Example payload:
  ```json
  {
      "project_key": "SCRUM"
  }
  ```

### Dashboard API Endpoints

The dashboard exposes REST API endpoints for programmatic access to monitoring data. All dashboard endpoints require JWT authentication.

**Authentication:**

* `POST /api/auth/login` - Authenticate and receive a JWT token.
  ```json
  {"username": "admin", "password": "admin"}
  ```
* `POST /api/auth/logout` - Logout (client-side token removal).
* `GET /api/auth/verify` - Verify if the current token is valid.

**Dashboard Data:**

* `GET /api/dashboard/summary` - Get high-level statistics (uptime, task counts, agent health).
* `GET /api/dashboard/agents` - Get detailed status of all registered agents.
* `GET /api/dashboard/tasks?limit=50` - Get recent tasks with execution details.
* `GET /api/dashboard/errors?limit=20` - Get recent errors with context.
* `GET /api/dashboard/logs?limit=100&offset=0&level=ERROR&task_id=xxx&agent_id=yyy` - Get filtered application logs (supports pagination via `offset`).
* `POST /api/dashboard/discovery` - Manually trigger agent discovery.

## A2A Streaming Contract

QuAIA™ uses the A2A artifact mechanism to push live updates from agents to the orchestrator
dashboard while a task is running.

### `report_activity` Tool

Every agent created via `AgentBase` automatically receives a `report_activity` tool and a
one-line instruction snippet appended to its system prompt. Developers writing agent prompt
templates **do not** need to include these manually — they are injected by
`AgentBase.__init__`.

The LLM calls `report_activity(description)` with a short sentence (≤ 120 chars) describing
the current reasoning phase or the tool it is about to invoke. Each call is forwarded to the
dashboard as an `agent_activity` artifact.

### Streaming Artifacts

The two artifact types below are recognised by the orchestrator's chunk-handling loop.
`agent_activity` is emitted by every `AgentBase` agent automatically. `agent_logs_stream`
is **OPTIONAL** — missing it is not an error; the dashboard degrades gracefully.

All payload schemas carry a `version` field so the wire format can evolve without breaking
external consumers.

#### `agent_activity`

Emitted on every `report_activity` call. Only the latest text is shown — the dashboard
overwrites the previous activity on each new event (no history is retained).

```json
{
  "version": 1,
  "type": "agent_activity",
  "task_id": "<internal-task-id>",
  "agent_id": "<agent-id>",
  "text": "Fetching Jira issue PROJ-123"
}
```

#### `agent_logs_stream` (OPTIONAL)

Log batches flushed every 2 s by `DefaultAgentExecutor`. External agents that do not use
this executor will not emit it; the dashboard falls back to polling for logs.

```json
{
  "version": 1,
  "type": "log_batch",
  "task_id": "<internal-task-id>",
  "lines": ["2025-05-17 12:00:01 INFO  fetching issue", "..."]
}
```

#### `agent_usage` (OPTIONAL)

A single `application/json` artifact (name `agent_usage`) emitted by `DefaultAgentExecutor` once a run completes,
carrying the run's token usage and estimated cost. The orchestrator records it on the task and aggregates it for the
dashboard. Missing it is not an error.

```json
{
  "model_name": "claude-sonnet-5",
  "input_tokens": 1200,
  "output_tokens": 340,
  "total_tokens": 1540,
  "cache_read_tokens": 0,
  "requests": 2,
  "tool_calls": 3,
  "cost_usd": 0.0012
}
```

### Dashboard SSE Streams

The dashboard receives streaming updates via two Server-Sent Event (SSE) endpoints.

#### Stream-Token Authentication

SSE endpoints use a separate short-lived token instead of the long-lived JWT so that a
captured URL (browser history, proxy logs) cannot be replayed once the stream expires.

**Flow:**

1. The UI POSTs to `POST /api/dashboard/stream-token` with the standard
   `Authorization: Bearer <jwt>` header.
2. The server mints an opaque token with a **5-minute TTL** and returns
   `{"stream_token": "...", "expires_at": "..."}`.
3. The UI opens `EventSource` with `?stream_token=<token>` as a query parameter.
4. Every 15 s the server sends a `heartbeat` frame and re-validates the token expiry.
   On expiry it emits a one-shot `event: auth_error` frame and closes the connection;
   the UI's 401 handler routes to the login page.

#### SSE Endpoints

| Endpoint | Description |
|---|---|
| `POST /api/dashboard/stream-token` | Mint a 5-min stream token (requires Bearer JWT). |
| `GET /api/dashboard/stream?stream_token=<token>` | Global stream: initial `snapshot` frame + live `agent_activity`, `task_done`, and `gap` events. |
| `GET /api/dashboard/agents/{agent_id}/stream?stream_token=<token>` | Per-agent stream: live `log_batch` events used by the log modal. |

The first frame on the global stream has `event: snapshot` and carries the current agent
registry plus all running tasks with their latest activity text.

---

## Running Tests

The project includes a comprehensive test suite. To run the tests:

```bash
# Run all tests
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Run tests for a specific module
uv run pytest tests/agents/
uv run pytest tests/orchestrator/
uv run pytest tests/common/
```

The suite under `tests/smoke/` is marked `smoke` and drives the hermetic docker-compose topology described in
[Hermetic smoke tests](#hermetic-smoke-tests) above, not local code in isolation. Because it needs that stack running, it
is excluded from a bare `uv run pytest` by default (via `addopts` in `pytest.ini`), so local runs stay harmless. Once the
stack is up, run it explicitly with `uv run pytest -m smoke`. It runs in CI on pushes to `main` and on manual
`workflow_dispatch` only (see *Hermetic smoke tests* above).

## Contributing

We welcome contributions to QuAIA™! Please see our [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on
how to contribute.

## License

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0) - see the [LICENSE](LICENSE) file for details.
