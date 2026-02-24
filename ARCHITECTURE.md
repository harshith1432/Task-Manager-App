# 🏗️ Taskly Architecture Guide

This document explains the internal logic, data flows, and architectural design of Taskly.

## 1. System Overview

Taskly follows a **Client-Server architecture** with a RESTful API.

- **Frontend**: A single-page application (SPA) focused on high-end visual feedback and real-time state management.
- **Backend**: A Flask-based REST API that manages data persistence, authentication logic, and background automation.
- **Data Layer**: A PostgreSQL database (NeonDB) stores all persistent entities (Users & Tasks).

## 2. Technical Component Breakdown

### 📱 Frontend Logic (`index.html`)
- **State Management**: The application maintains a local `tasks` array. Every UI update (render) is derived from this state.
- **Persistence**: Upon login, `user_id` and `user_name` are cached in `localStorage`. This allows for session persistence across page refreshes.
- **Visual Engine**:
    - **CSS Variables**: All colors and effects are defined as atomic variables to support themes and glassmorphism.
    - **AJAX (Fetch API)**: All interactions (Login, Add, Toggle, Delete) are performed asynchronously to ensure zero page reloads.

### ⚙️ Backend Logic (`app.py`)
- **Flask REST Endpoints**: 
    - `/api/auth/login`: Handles both login and auto-registration logic.
    - `/api/tasks`: CRUD operations filtered by `user_id`.
- **Database Connection Pool**: Uses `psycopg2` with `RealDictCursor` for convenient dictionary-based API responses.
- **Security Logic**: Every database query is parameterized to prevent SQL injection. Tasks are always gated by `user_id` in the SQL `WHERE` clause.

## 3. Core Workflows

### 🔐 Authentication & Multi-user Support
1. **Login Event**: User submits `Name` and `PIN`.
2. **Backend Match**: 
    - If `Name` exists: Check `PIN`. If exact match, log in.
    - If `Name` is new: Create a new user record with that `PIN`.
3. **Session Context**: The frontend receives the `user_id` and includes it in all subsequent API requests. This ensures User A never sees User B's tasks.

### 🔔 Notification Pipeline
Taskly uses a dual-layer notification system:

1. **Transaction Alerts**: When a task is created, `app.py` triggers an immediate `twilio_client.messages.create` call to the user's specific WhatsApp number.
2. **Background Scheduler (APScheduler)**:
    - Runs every 60 seconds.
    - Scans the `tasks` table for pending missions with deadlines.
    - Efficiently filters for missions due in exactly 2 hours or 1 hour.
    - Marks `notified_2h` or `notified_1h` flags to avoid duplicate alerts.

## 4. Database Schema

### `users` table
| Column | Type | Description |
|---|---|---|
| `id` | SERIAL | Primary Key |
| `name` | TEXT | Unique Username |
| `pin` | TEXT | 4-Digit Security PIN |
| `phone_number` | TEXT | WhatsApp Number (Optional) |

### `tasks` table
| Column | Type | Description |
|---|---|---|
| `id` | SERIAL | Primary Key |
| `user_id` | INT | Foreign Key to `users.id` |
| `title` | TEXT | Mission Name |
| `deadline` | TIMESTAMP | Optional Completion Target |
| `completed` | BOOLEAN | Task Status |
| `notified_2h` | BOOLEAN | Reminder Sent Flag |
| `notified_1h` | BOOLEAN | Reminder Sent Flag |
