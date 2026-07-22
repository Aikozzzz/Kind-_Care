> [!WARNING]
> **Archived historical guidance.** This file is retained for provenance and may
> describe planned or obsolete behavior. Use the canonical root [`AGENTS.md`](AGENTS.md)
> for the current stack, routes, security boundary, environment, and engineering rules.

# AGENTS.md

## Project Name

KindCare

## Project Description

KindCare is a Python-based distributed elderly care monitoring and companion system. It uses distributed client nodes, a FastAPI backend, message brokers, worker services, MongoDB, and a caregiver dashboard to monitor elderly people who live alone or need regular care.

The system collects health, activity, reminder, emergency, and device-status data from elderly client nodes. It processes the data asynchronously, detects abnormal conditions, stores records in MongoDB, and displays results through a caregiver dashboard.

---

## Main Goal

When working on this project, always maintain the goal of building a reliable distributed monitoring system for elderly care.

The system should support:

* Distributed elderly client nodes
* Health and activity data collection
* Reminder tracking
* Emergency alert detection
* Background processing with workers
* NoSQL data storage
* Caregiver dashboard
* Clear documentation

---

## Tech Stack

Use the following technologies unless the user clearly asks for changes:

| Component              | Technology              |
| ---------------------- | ----------------------- |
| Programming Language   | Python                  |
| Backend API            | FastAPI                 |
| Database               | MongoDB                 |
| MongoDB Driver         | Motor or PyMongo        |
| Distributed Task Queue | Celery                  |
| Message Broker         | RabbitMQ                |
| IoT Messaging          | MQTT                    |
| MQTT Broker            | Mosquitto               |
| Dashboard              | Streamlit               |
| Data Analysis          | Pandas / NumPy          |
| Machine Learning       | Scikit-learn            |
| Real-time Updates      | WebSocket               |
| Containerization       | Docker / Docker Compose |

---

## Expected Folder Structure

Follow this structure when creating or updating files:

```text
kindcare/
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── database.py
│   │   ├── config.py
│   │   ├── routes/
│   │   ├── models/
│   │   ├── services/
│   │   └── websocket.py
│   ├── requirements.txt
│   └── .env.example
│
├── workers/
│   ├── celery_app.py
│   ├── health_worker.py
│   ├── activity_worker.py
│   ├── reminder_worker.py
│   ├── alert_worker.py
│   └── requirements.txt
│
├── client_nodes/
│   ├── elderly_node.py
│   ├── mqtt_node.py
│   └── simulator.py
│
├── dashboard/
│   ├── app.py
│   ├── pages/
│   ├── components/
│   └── requirements.txt
│
├── analysis/
│   ├── risk_analysis.py
│   ├── trend_analysis.py
│   └── prediction_model.py
│
├── notification/
│   ├── email_service.py
│   ├── sms_service.py
│   └── push_service.py
│
├── docs/
│   ├── architecture.md
│   ├── database-design.md
│   └── api-documentation.md
│
├── docker-compose.yml
├── README.md
└── AGENTS.md
```

---

## Coding Rules

### General Rules

* Use clean, readable Python code.
* Keep functions small and focused.
* Use meaningful variable and function names.
* Avoid hardcoding sensitive values.
* Store configuration in `.env` files.
* Keep business logic inside service files.
* Keep API route files simple.
* Add comments only when they explain important logic.
* Do not add unnecessary dependencies.
* Do not rewrite the whole project unless required.

---

## Backend Rules

The backend must use **FastAPI**.

Backend responsibilities:

* Receive data from elderly client nodes.
* Validate incoming data.
* Store data in MongoDB.
* Publish events to RabbitMQ or MQTT.
* Provide APIs for the dashboard.
* Manage elderly profiles, caregivers, reminders, and alerts.

Use this route style:

```text
/api/health
/api/activity
/api/reminders
/api/alerts
/api/elderly
/api/dashboard
```

Use Pydantic models for request validation.

Example:

```python
from pydantic import BaseModel
from datetime import datetime

class HealthLog(BaseModel):
    elderly_id: str
    heart_rate: int
    temperature: float
    oxygen_level: int
    movement_status: str
    medicine_status: str
    emergency_pressed: bool = False
    recorded_at: datetime
```

---

## Distributed Programming Rules

KindCare must clearly show distributed programming concepts.

When adding features, prefer this flow:

```text
Client Node
    ↓
FastAPI Backend
    ↓
Message Broker
    ↓
Worker Service
    ↓
MongoDB
    ↓
Dashboard / Notification
```

Use distributed components properly:

* Client nodes should send data independently.
* Backend should not do all heavy processing directly.
* Worker services should process health, activity, reminder, and emergency events.
* RabbitMQ should be used for asynchronous task distribution.
* MQTT can be used for IoT-style client node communication.
* MongoDB should store processed logs and alerts.
* Dashboard should read summarized data from the backend.

---

## Worker Rules

Workers should be used for background processing.

Worker responsibilities:

* Analyze health data.
* Detect abnormal conditions.
* Generate alerts.
* Calculate risk levels.
* Process reminder status.
* Update MongoDB records.
* Trigger notifications.

Use Celery for task workers.

Example:

```python
from celery import Celery

celery_app = Celery(
    "kindcare_worker",
    broker="amqp://guest:guest@localhost:5672//"
)

@celery_app.task
def process_health_data(data):
    alerts = []

    if data.get("heart_rate", 0) > 120:
        alerts.append("High heart rate detected")

    if data.get("oxygen_level", 100) < 92:
        alerts.append("Low oxygen level detected")

    return alerts
```

---

## MongoDB Rules

Use MongoDB as the main NoSQL database.

Main collections:

```text
users
elderly_profiles
health_logs
activity_logs
reminders
alerts
device_status
chat_messages
```

Document design should be flexible and suitable for health monitoring data.

Use timestamps for all logs.

Recommended timestamp fields:

```text
created_at
updated_at
recorded_at
resolved_at
```

Avoid relational-style over-normalization. Use references when needed, but keep frequently accessed monitoring data easy to query.

---

## Data Analysis Rules

Risk analysis should start simple and understandable.

Use rule-based detection first.

Example conditions:

```text
heart_rate > 120 → emergency
heart_rate < 50 → emergency
oxygen_level < 92 → emergency
temperature > 38 → warning
movement_status = inactive for long time → warning
medicine_status = missed → warning
emergency_pressed = true → emergency
```

Later, machine learning can be added using historical logs.

Possible ML features:

* Abnormal behavior detection
* Daily activity prediction
* Missed medicine prediction
* Health risk scoring
* Elderly routine pattern analysis

---

## Dashboard Rules

The dashboard should be simple and useful for caregivers.

Use Streamlit unless another dashboard framework is requested.

Dashboard should show:

* Elderly profile
* Current risk level
* Latest health status
* Health charts
* Activity logs
* Reminder status
* Alert history
* Device online/offline status

Avoid overcomplicated UI. Focus on clarity.

---

## Client Node Rules

Client nodes simulate elderly monitoring devices.

Each client node should:

* Have a unique `elderly_id`
* Generate sample health data
* Send health data periodically
* Send activity status
* Send reminder status
* Send emergency button status
* Support HTTP or MQTT communication

Example client data:

```json
{
  "elderly_id": "E001",
  "heart_rate": 86,
  "temperature": 36.7,
  "oxygen_level": 97,
  "movement_status": "active",
  "medicine_status": "taken",
  "emergency_pressed": false
}
```

---

## Notification Rules

Notifications should be triggered only for warning or emergency cases.

Alert severity levels:

```text
normal
warning
emergency
```

Alert statuses:

```text
unresolved
acknowledged
resolved
```

Possible notification methods:

* Dashboard alert
* Email alert
* SMS alert
* Telegram bot alert
* Push notification

For the first version, dashboard alerts are enough.

---

## Documentation Rules

Whenever code changes, update documentation if needed.

Important documentation files:

```text
README.md
docs/architecture.md
docs/database-design.md
docs/api-documentation.md
```

Documentation should explain:

* What changed
* How to run the system
* API endpoints
* Database collections
* Distributed system flow
* Example data format

---

## Environment Variable Rules

Do not hardcode secrets.

Use `.env.example` to show required variables.

Example:

```env
MONGO_URI=mongodb://localhost:27017
DATABASE_NAME=kindcare_db
RABBITMQ_URL=amqp://guest:guest@localhost:5672//
MQTT_BROKER=localhost
MQTT_PORT=1883
JWT_SECRET=change_this_secret
```

Never commit real secrets, passwords, tokens, or private keys.

---

## API Design Rules

Use REST-style APIs.

Recommended API endpoints:

```text
POST /api/health
GET  /api/health/{elderly_id}

POST /api/activity
GET  /api/activity/{elderly_id}

POST /api/reminders
GET  /api/reminders/{elderly_id}
PATCH /api/reminders/{reminder_id}

POST /api/alerts
GET  /api/alerts/{elderly_id}
PATCH /api/alerts/{alert_id}

GET  /api/dashboard/{elderly_id}
```

Return clear JSON responses.

Example response:

```json
{
  "success": true,
  "message": "Health data received successfully",
  "data": {
    "elderly_id": "E001",
    "risk_level": "normal"
  }
}
```

---

## Error Handling Rules

Handle errors clearly.

Common errors:

* Missing elderly ID
* Invalid health data
* Database connection failure
* Message broker unavailable
* Worker processing failure
* Alert creation failure

Return useful error responses.

Example:

```json
{
  "success": false,
  "message": "Invalid heart rate value"
}
```

---

## Testing Rules

Add simple tests when possible.

Test important parts:

* Health data validation
* Risk analysis logic
* Alert generation
* API endpoint responses
* MongoDB insert/read operations
* Worker task execution

Recommended tools:

```text
pytest
httpx
pytest-asyncio
```

---

## Docker Rules

Use Docker Compose to run distributed services together.

Expected services:

```text
backend
worker
mongodb
rabbitmq
mosquitto
dashboard
```

Do not make Docker setup too complicated. It should be easy for students to run.

---

## Git Rules

Use meaningful commit messages.

Examples:

```text
feat: add health data API
feat: add celery health worker
fix: handle missing oxygen level
docs: update database design
refactor: move risk logic to analysis module
```

Avoid committing:

* `.env`
* virtual environments
* cache files
* database dump files
* logs
* large generated files

---

## Development Priority

Build the project in this order:

1. FastAPI backend setup
2. MongoDB connection
3. Health data API
4. Client node simulator
5. Basic risk analysis
6. Alert collection
7. Streamlit dashboard
8. RabbitMQ + Celery worker
9. MQTT client node
10. Notification service
11. Docker Compose
12. Documentation

---

## Important Notes for AI Agents

Before modifying code:

* Read the existing folder structure.
* Understand the current implementation.
* Make minimal but correct changes.
* Do not remove existing working features.
* Keep the project beginner-friendly.
* Preserve the distributed programming concept.
* Update related documentation after changes.
* Explain major changes clearly.

When generating code:

* Include complete imports.
* Use clear file paths.
* Avoid unexplained placeholders.
* Keep examples runnable.
* Prefer simple architecture over complex enterprise patterns.

---

## Final Instruction

KindCare must remain a Python-based distributed elderly care monitoring system using FastAPI, MongoDB, distributed workers, message brokers, and a caregiver dashboard.

Every change should support the main idea:

**Collect elderly care data from distributed nodes, process it asynchronously, detect risks, store results in NoSQL, and help caregivers respond quickly.**
