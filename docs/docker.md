# Docker Deployment Guide

This guide explains how to run the Booking Agent using Docker and Docker Compose.

## Why Docker?

Running the Booking Agent in a container ensures that:
- The Python environment is consistent across different machines.
- All dependencies are isolated from your host system.
- Deployment is simplified to a single command.

## Prerequisites

Before starting the container, ensure you have the following files in your project root:
- `credentials.json`: Agent account details.
- `client_secret.json`: Google Cloud Project OAuth client secret.
- `token.json`: Generated Google OAuth token.

> [!IMPORTANT]
> The `token.json` file is required for the application to interact with Google Calendar in a headless environment. If you don't have this file, you should run the application locally once to complete the OAuth flow in your browser.

## Getting Started

### 1. Build and Start
To build the image and start the container in the background:
```bash
docker compose up --build -d
```

### 2. View Logs
To monitor the application logs in real-time:
```bash
docker compose logs -f booking-agent
```

### 3. Stop
To stop and remove the containers:
```bash
docker compose down
```

## Troubleshooting Google OAuth in Docker

The Google OAuth flow typically requires a browser to authenticate the user. In a headless Docker environment, this is not possible.

### If `token.json` is missing:
1. Run the application locally (outside Docker) once: `python main.py`.
2. Complete the authentication in your browser.
3. Once `token.json` is generated, شما can start the Docker container.
4. The `docker-compose.yml` mounts `token.json` into the container, so it will be available to the agent.

## Image Architecture (Multi-Stage)

The `Dockerfile` uses a multi-stage build to keep the final image size small:
1. **Builder Stage**: Installs build tools and dependencies.
2. **Runner Stage**: Only contains the Python runtime, the installed packages, and the source code.

This reduces the attack surface and minimizes download/storage requirements.

## Persistence

The following files are mounted as volumes to ensure data persists and is editable from the host:
- `credentials.json`: Agent list.
- `client_secret.json`: API secrets.
- `token.json`: Session token.
- `system.log`: All logging output from the container.
